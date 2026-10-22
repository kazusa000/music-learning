"""Kontakt factory state capture and strict clean-rack admission evidence.

Conversion, capture, validation, and publication are separate persisted stages.
Neither a successful Lua save nor audible default startup admits an instrument.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from .instrument_intake_publication import (
    ValidatorPin, _candidate_for_catalog, _param_map, _verify_state_blob,
    artifact_ref, candidate_fingerprint, resource_manifest_fingerprint, sha256_file,
    verify_receipt,
)
from .kontakt_audio_probe import captured_state_roundtrip
from .kontakt_import import atomic_write_json
from .kontakt_native_identity import make_kontakt_native_verifier
from .kontakt_performance import KontaktProbeProfile, validate_profile
from .vst3_state import state_components, vst3_preset

CORE_SHA256 = "6e6abad7518574e4f4e5e20779860f7c789041df63b75e0f03c29a18d701aeab"
WRAPPER_SHA256 = "75cdaa3b4d187e0e2f21b9057ebd5759400a27988df11301d703922018babe4f"
IDENTIFIER = "VST3-Kontakt 8-9f68cff3-f852a294"
CLASS_ID = "5653544E694B386B6F6E74616B742038"
VERSION = "8.10.1"
LIBRARIES = {"kontakt-factory": "Kontakt Factory Library 2", "css": "Cinematic Studio Strings",
             "css-solo": "Cinematic Studio Solo Strings"}


def verifier():
    return make_kontakt_native_verifier(core_sha256=CORE_SHA256, version=VERSION)


def plugin_spec(plugin_path: Path, preset_id: str) -> dict:
    return dict(id=preset_id, path=str(Path(plugin_path).resolve(strict=True)),
                identifier=IDENTIFIER, binary_sha256=WRAPPER_SHA256, version=VERSION)


def _exact_identity(plugin, expected):
    actual = verifier().verify(dict(expected_identity=expected), plugin)
    if actual != expected:
        raise ValueError("Kontakt native instrument identity differs from the official source getters")
    if (plugin.get("identifier") != IDENTIFIER or plugin.get("binary_sha256") != WRAPPER_SHA256
            or plugin.get("vst3_class_id") != CLASS_ID or plugin.get("version") != VERSION):
        raise ValueError("Kontakt source loaded a different native class/version")


def capture_source(host, spec, output: Path, *, startup, expected_identity, timeout_seconds=180):
    """Capture full source state in its own owned startup process, without audio."""
    output.mkdir(parents=True, exist_ok=False)
    if any(spec.get(key) for key in ("state_base64", "preset_path")):
        raise ValueError("Source capture must load the converted factory Multi directly")
    diagnostics = Path(host.diagnostics)
    if not diagnostics.resolve().is_relative_to(output.resolve()):
        raise ValueError("Capture diagnostics escaped their evidence root")
    before = set(diagnostics.iterdir()) if diagnostics.exists() else set()
    request = dict(schema_version=1, action="inspect", plugin=spec)
    with startup():
        result = host.call(request, timeout=timeout_seconds)
    calls = set(diagnostics.iterdir()) - before
    if len(calls) != 1:
        raise ValueError("Source capture requires one persisted native execution")
    call = calls.pop()
    execution = json.loads((call / "execution.json").read_bytes())
    if execution.get("status") != "completed" or execution.get("returncode") != 0:
        raise ValueError("Source capture did not exit successfully")
    plugin = result["plugin"]
    _verify_state_blob(plugin)
    _param_map(plugin)
    _exact_identity(plugin, expected_identity)
    atomic_write_json(output / "state.json", plugin)
    refs = {key: artifact_ref(call / (key + ".json"), output) for key in ("request", "response", "execution")}
    refs["state"] = artifact_ref(output / "state.json", output)
    atomic_write_json(output / "capture.json", dict(schema_version=1, expected_identity=expected_identity, refs=refs))
    return plugin


def preset_bytes(source_state):
    _verify_state_blob(source_state)
    if source_state.get("vst3_class_id") != CLASS_ID:
        raise ValueError("Cannot serialize a different Kontakt VST3 class")
    components = state_components(source_state)
    return vst3_preset(CLASS_ID, components["IComponent"], components.get("IEditController"))


def make_candidate(metadata, source_item, conversion, source_state, *, bank_root, entrypoint,
                   resources, runtime_dependencies, installed_library, source_manifest_sha256,
                   audition_controllers=()):
    """Freeze the catalog candidate BEFORE either fresh-state audio validation."""
    identity = conversion["observed_identity"]
    _exact_identity(source_state, identity)
    context = conversion.get("playback_context")
    if not isinstance(context, dict) or not context.get("instruments"):
        raise ValueError("Kontakt admission requires source MIDI playback getters")
    source_sha = source_item["source"]["sha256"]
    if identity["source_sha256"] != source_sha:
        raise ValueError("Kontakt conversion/source provenance differs")
    performance = validate_profile(dict(schema_version=1, kind="midi_context", source_factory_sha256=source_sha,
                                        instruments=deepcopy(context["instruments"]),
                                        controllers=[dict(cc) for cc in audition_controllers]),
                                   expected_source_sha256=source_sha)
    if [row["index"] for row in performance["instruments"]] != identity["instrument_indices"]:
        raise ValueError("Kontakt MIDI getter indices differ from semantic identity")
    candidate = dict(metadata)
    ranges = [row["key_range"] for row in performance["instruments"]]
    candidate.update(name=metadata["source_name"], library_name=LIBRARIES[source_item["library_id"]],
                     classification_basis=metadata["model_basis"],
                     key_range=[min(row[0] for row in ranges), max(row[1] for row in ranges)], gm_programs=[],
                     id=source_item["id"], backend="vst3-preset", bank_root=bank_root, entrypoint=entrypoint,
                     resources=deepcopy(resources), runtime_dependencies=deepcopy(runtime_dependencies),
                     installed_library=dict(installed_library), source_manifest_sha256=source_manifest_sha256,
                     source_factory_sha256=source_sha, expected_identity=identity,
                     expected_parameters=_param_map(source_state), plugin_path=source_state["path"],
                     plugin_sha256=WRAPPER_SHA256, identifier=IDENTIFIER, vst3_class_id=CLASS_ID,
                     performance_profile=performance)
    return _candidate_for_catalog(candidate)


def validate_captured(host, candidate, source_state, output: Path, *, empty_startup,
                      validator_pin: ValidatorPin, resource_root: Path, timeout_seconds=180):
    """Both audio processes start empty, then restore the frozen source bytes."""
    _exact_identity(source_state, candidate["expected_identity"])
    if _param_map(source_state) != candidate["expected_parameters"]:
        raise ValueError("Frozen candidate differs from source parameters")
    from .installed_libraries import verify_installed_library

    settings = SimpleNamespace(instrument_library=Path(resource_root))
    verify_installed_library(settings, candidate["installed_library"])
    actual_preset = Path(resource_root) / candidate["bank_root"] / candidate["entrypoint"]
    import hashlib

    serialized_sha = hashlib.sha256(preset_bytes(source_state)).hexdigest()
    if (serialized_sha != candidate["resources"][candidate["entrypoint"]]["sha256"]
            or sha256_file(actual_preset) != serialized_sha):
        raise ValueError("Installed full preset is not the captured source state")
    index = captured_state_roundtrip(host, source_state, output, empty_startup=empty_startup,
                                    profile=KontaktProbeProfile(candidate["performance_profile"]),
                                    timeout_seconds=timeout_seconds)
    # Native identities are checked again after MIDI processing, not merely in
    # the pre-render snapshot used by the shared admission receipt.
    for phase in ("first", "reopen"):
        response = json.loads((output / index[phase]["response"]["path"]).read_bytes())["result"]
        after = response["tracks"][0]["plugin_readback_after"][0]
        _exact_identity(after, candidate["expected_identity"])
        if _param_map(after) != candidate["expected_parameters"]:
            raise ValueError("Kontakt source parameters changed during audition processing")
    parser = verifier()
    receipt = dict(schema_version=1, preset_id=candidate["id"], candidate_sha256=candidate_fingerprint(candidate),
                   resource_manifest_sha256=resource_manifest_fingerprint(candidate),
                   validator=validator_pin.receipt_fields(),
                   identity_verifier=dict(name=parser.name, version=parser.version, code_sha256=parser.code_sha256),
                   plugin=dict(path=candidate["plugin_path"], identifier=IDENTIFIER,
                               binary_sha256=WRAPPER_SHA256, vst3_class_id=CLASS_ID),
                   first=index["first"], reopen=index["reopen"],
                   startup=dict(first="empty", reopen="empty"), source_state_sha256=source_state["state_sha256"])
    summary = verify_receipt(candidate, receipt, output, parser, validator_pin, resource_root=resource_root)
    verify_installed_library(settings, candidate["installed_library"])
    atomic_write_json(output / "receipt.json", receipt)
    return dict(receipt=receipt, summary=summary)
