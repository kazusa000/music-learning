"""Kontakt factory state capture and strict clean-rack admission evidence.

Conversion, capture, validation, and publication are separate persisted stages.
Neither a successful Lua save nor audible default startup admits an instrument.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import ntpath
import struct
from pathlib import Path
import re
from types import SimpleNamespace

from .instrument_intake_publication import (
    ValidatorPin, _candidate_for_catalog, _param_map, _verify_state_blob,
    artifact_ref, candidate_fingerprint, resource_manifest_fingerprint, sha256_file,
    verify_receipt,
)
from .kontakt_audio_probe import captured_state_roundtrip
from .kontakt_import import atomic_write_json
from .kontakt_native_identity import make_kontakt_native_verifier
from .kontakt_performance import KontaktProbeProfile, profile_phrase, validate_profile
from .vst3_state import state_components, vst3_preset

CORE_SHA256 = "6e6abad7518574e4f4e5e20779860f7c789041df63b75e0f03c29a18d701aeab"
WRAPPER_SHA256 = "75cdaa3b4d187e0e2f21b9057ebd5759400a27988df11301d703922018babe4f"
VST3_UNIQUE_ID = "f852a294"
_IDENTIFIER_PATTERN = re.compile(r"^VST3-Kontakt 8-([0-9a-f]{8})-f852a294$")
CLASS_ID = "5653544E694B386B6F6E74616B742038"
VERSION = "8.10.1"
# Observed in every pinned 8.10.1 empty/single/multi native control. This
# includes native and MIDI-mapped parameters, not just the first 2,049 slots.
PARAMETER_COUNT = 4145
PARAMETER_IDS_SHA256 = "3408749d7ab9b092d7f46dde2b4af54419ace274593bf99d72f7aedbf136413a"
CONTROLLER_PARAMETER_BASE = 1295056896
CONTROLLER_PARAMETER_STRIDE = 256
LIBRARIES = {"kontakt-factory": "Kontakt Factory Library 2", "css": "Cinematic Studio Strings",
             "css-solo": "Cinematic Studio Solo Strings"}


def verifier():
    return make_kontakt_native_verifier(core_sha256=CORE_SHA256, version=VERSION)


def _canonical_plugin_path(value) -> str:
    return ntpath.normcase(ntpath.normpath(str(value)))


def _validate_descriptor(plugin_path: Path, descriptor: dict) -> dict:
    """Accept only the pinned Kontakt class scanned from the requested module path.

    JUCE's descriptor identifier contains a path-derived component, so it must
    come from a scan of the exact runtime module rather than a repository-wide
    constant. The binary digest and native class are still checked after load.
    """
    path = Path(plugin_path).resolve(strict=True)
    if not isinstance(descriptor, dict):
        raise ValueError("Kontakt plugin spec requires the result of a VST3 scan")
    if (_canonical_plugin_path(descriptor.get("path", "")) != _canonical_plugin_path(path)
            or descriptor.get("format") != "VST3"
            or descriptor.get("name") != "Kontakt 8"
            or descriptor.get("manufacturer") != "Native Instruments"
            or descriptor.get("version") != VERSION
            or descriptor.get("is_instrument") is not True
            or not isinstance(descriptor.get("unique_id"), int)
            or (descriptor["unique_id"] & 0xFFFFFFFF) != int(VST3_UNIQUE_ID, 16)):
        raise ValueError("VST3 scan did not identify the pinned Kontakt 8 instrument at this path")
    identifier = descriptor.get("identifier")
    if not isinstance(identifier, str) or not _IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError("Kontakt VST3 scan returned an unexpected descriptor identifier")
    return {
        "path": str(path),
        "identifier": identifier,
        "name": descriptor["name"],
        "manufacturer": descriptor["manufacturer"],
        "format": descriptor["format"],
        "version": descriptor["version"],
        # Keep every field consumed by the strict validator when callers pass
        # this normalized descriptor back through plugin_spec().
        "is_instrument": True,
        "unique_id": descriptor["unique_id"],
    }


def resolve_plugin_descriptor(host, plugin_path: Path, *, timeout=45) -> dict:
    """Scan one exact pinned runtime path and return its validated descriptor."""
    path = Path(plugin_path).resolve(strict=True)
    result = host.call({"schema_version": 1, "action": "scan", "plugin_path": str(path)}, timeout=timeout)
    plugins = result.get("plugins") if isinstance(result, dict) else None
    if not isinstance(plugins, list):
        raise ValueError("Kontakt VST3 scan did not return a descriptor list")
    matches = []
    for descriptor in plugins:
        try:
            matches.append(_validate_descriptor(path, descriptor))
        except (TypeError, ValueError):
            continue
    if len(matches) != 1:
        raise ValueError("Exact runtime path must resolve to one pinned Kontakt 8 descriptor")
    return matches[0]


def plugin_spec(plugin_path: Path, preset_id: str, *, descriptor: dict) -> dict:
    descriptor = _validate_descriptor(plugin_path, descriptor)
    return dict(id=preset_id, path=descriptor["path"], identifier=descriptor["identifier"],
                binary_sha256=WRAPPER_SHA256, version=VERSION,
                descriptor=descriptor)


def _exact_identity(plugin, expected, *, descriptor_identifier, expected_plugin_path):
    _full_parameters(plugin)
    actual = verifier().verify(dict(expected_identity=expected), plugin)
    if actual != expected:
        raise ValueError("Kontakt native instrument identity differs from the official source getters")
    if (not isinstance(descriptor_identifier, str)
            or not _IDENTIFIER_PATTERN.fullmatch(descriptor_identifier)
            or not isinstance(expected_plugin_path, str) or not expected_plugin_path
            or plugin.get("identifier") != descriptor_identifier
            or _canonical_plugin_path(plugin.get("path", "")) != _canonical_plugin_path(expected_plugin_path)
            or plugin.get("binary_sha256") != WRAPPER_SHA256
            or plugin.get("vst3_class_id") != CLASS_ID or plugin.get("version") != VERSION):
        raise ValueError("Kontakt source loaded a different native class/version")


def _full_parameters(plugin):
    parameters = _param_map(plugin)
    ids_sha = hashlib.sha256(json.dumps(sorted(parameters), separators=(",", ":")).encode()).hexdigest()
    if len(parameters) != PARAMETER_COUNT or ids_sha != PARAMETER_IDS_SHA256:
        raise ValueError("Kontakt source requires the complete pinned 4145-parameter ID set")
    return parameters


def _expected_audition_controller_values(profile):
    """Map only the explicitly auditioned MIDI CCs to Kontakt's parameter IDs.

    Kontakt exposes its 131 CCs on 16 channels as parameters after the first
    2,049 native parameters.  The ID layout is part of the pinned 8.10.1
    4,145-ID set: base + 256 * controller + zero-based channel.
    """
    phrase = profile_phrase(validate_profile(profile))
    expected = {}
    for event in phrase["controllers"]:
        key = str(CONTROLLER_PARAMETER_BASE
                  + CONTROLLER_PARAMETER_STRIDE * event["controller"]
                  + event["channel"] - 1)
        value = struct.unpack("<f", struct.pack("<f", event["value"] / 127.0))[0]
        if key in expected and expected[key] != value:
            raise ValueError("Kontakt audition sends conflicting values to one controller parameter")
        expected[key] = value
    return expected


def _validate_audition_parameters(plugin, source_parameters, profile):
    """Require every parameter to retain source state except sent MIDI CCs.

    The audition phrase deliberately sends CCs (currently CC1/CC11).  Kontakt
    reports those controller values as normalized host parameters after MIDI
    processing; compare them to the exact float32-normalized values sent.
    All other parameter values and the full canonical 4,145-ID set stay exact.
    """
    actual = _full_parameters(plugin)
    if set(actual) != set(source_parameters):
        raise ValueError("Kontakt source parameter IDs changed during audition processing")
    controller_values = _expected_audition_controller_values(profile)
    unknown = set(controller_values) - set(source_parameters)
    if unknown:
        raise ValueError("Kontakt audition controller is absent from the pinned parameter set")
    for key, source_value in source_parameters.items():
        expected = controller_values.get(key, source_value)
        if actual[key] != expected:
            raise ValueError("Kontakt source parameters changed outside the audition MIDI context")


def capture_source(host, spec, output: Path, *, startup, expected_identity, timeout_seconds=180):
    """Capture full source state in its own owned startup process, without audio."""
    output.mkdir(parents=True, exist_ok=False)
    if any(spec.get(key) for key in ("state_base64", "preset_path")):
        raise ValueError("Source capture must load the converted factory Multi directly")
    diagnostics = Path(host.diagnostics)
    if not diagnostics.resolve().is_relative_to(output.resolve()):
        raise ValueError("Capture diagnostics escaped their evidence root")
    before = set(diagnostics.iterdir()) if diagnostics.exists() else set()
    descriptor = spec.get("descriptor")
    if not isinstance(descriptor, dict) or spec.get("identifier") != descriptor.get("identifier"):
        raise ValueError("Source capture requires a path-matched VST3 descriptor scan")
    request_plugin = {key: spec[key] for key in ("id", "path", "identifier", "binary_sha256", "version")}
    request = dict(schema_version=1, action="inspect", plugin=request_plugin)
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
    _exact_identity(plugin, expected_identity, descriptor_identifier=spec["identifier"],
                    expected_plugin_path=spec["path"])
    atomic_write_json(output / "state.json", plugin)
    refs = {key: artifact_ref(call / (key + ".json"), output) for key in ("request", "response", "execution")}
    refs["state"] = artifact_ref(output / "state.json", output)
    atomic_write_json(output / "capture.json", dict(schema_version=1, expected_identity=expected_identity,
                                                     plugin_descriptor=descriptor, refs=refs))
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
    descriptor_identifier = source_state.get("identifier")
    _exact_identity(source_state, identity, descriptor_identifier=descriptor_identifier,
                    expected_plugin_path=source_state.get("path", ""))
    context = conversion.get("playback_context")
    if not isinstance(context, dict) or not context.get("instruments"):
        raise ValueError("Kontakt admission requires source MIDI playback getters")
    source_sha = source_item["source"]["sha256"]
    if identity["source_sha256"] != source_sha:
        raise ValueError("Kontakt conversion/source provenance differs")
    performance_source = dict(schema_version=1, kind="midi_context", source_factory_sha256=source_sha,
                              instruments=deepcopy(context["instruments"]),
                              controllers=[dict(cc) for cc in audition_controllers])
    audition_pattern = metadata.get("audition_pattern")
    if audition_pattern is not None:
        performance_source.update(audition_pattern=audition_pattern,
                                  audition_timing={"onset_interval_ms": 500, "gate_ms": 550,
                                                   "overlap_ms": 50})
    performance = validate_profile(performance_source, expected_source_sha256=source_sha)
    if [row["index"] for row in performance["instruments"]] != identity["instrument_indices"]:
        raise ValueError("Kontakt MIDI getter indices differ from semantic identity")
    candidate = dict(metadata)
    candidate.pop("audition_pattern", None)
    ranges = [row["key_range"] for row in performance["instruments"]]
    candidate.update(name=metadata["source_name"], library_name=LIBRARIES[source_item["library_id"]],
                     classification_basis=metadata["model_basis"],
                     key_range=[min(row[0] for row in ranges), max(row[1] for row in ranges)], gm_programs=[],
                     id=source_item["id"], backend="vst3-preset", bank_root=bank_root, entrypoint=entrypoint,
                     resources=deepcopy(resources), runtime_dependencies=deepcopy(runtime_dependencies),
                     installed_library=dict(installed_library), source_manifest_sha256=source_manifest_sha256,
                     source_factory_sha256=source_sha, expected_identity=identity,
                     expected_parameters=_param_map(source_state), plugin_path=source_state["path"],
                     plugin_sha256=WRAPPER_SHA256, identifier=descriptor_identifier, vst3_class_id=CLASS_ID,
                     performance_profile=performance)
    return _candidate_for_catalog(candidate)


def _verify_captured_source(candidate, source_state, resource_root):
    descriptor_identifier = candidate.get("identifier")
    _exact_identity(source_state, candidate["expected_identity"], descriptor_identifier=descriptor_identifier,
                    expected_plugin_path=candidate["plugin_path"])
    if _param_map(source_state) != candidate["expected_parameters"]:
        raise ValueError("Frozen candidate differs from source parameters")
    from .installed_libraries import verify_installed_library

    settings = SimpleNamespace(instrument_library=Path(resource_root))
    verify_installed_library(settings, candidate["installed_library"])
    actual_preset = Path(resource_root) / candidate["bank_root"] / candidate["entrypoint"]
    serialized_sha = hashlib.sha256(preset_bytes(source_state)).hexdigest()
    if (serialized_sha != candidate["resources"][candidate["entrypoint"]]["sha256"]
            or sha256_file(actual_preset) != serialized_sha):
        raise ValueError("Installed full preset is not the captured source state")


def validate_captured(host, candidate, source_state, output: Path, *, empty_startup,
                      validator_pin: ValidatorPin, resource_root: Path, timeout_seconds=180):
    """Both audio processes start empty, then restore the frozen source bytes."""
    _verify_captured_source(candidate, source_state, resource_root)
    index = captured_state_roundtrip(host, source_state, output, empty_startup=empty_startup,
                                    profile=KontaktProbeProfile(candidate["performance_profile"]),
                                    timeout_seconds=timeout_seconds)
    return finalize_captured_evidence(candidate, source_state, output, index=index,
        validator_pin=validator_pin, resource_root=resource_root)


def finalize_captured_evidence(candidate, source_state, output: Path, *, index,
                               validator_pin: ValidatorPin, resource_root: Path):
    """Check persisted first/reopen evidence without starting another sampler.

    Recovery callers must pin the producing attempt and copy its original
    evidence unchanged. All audio, state, source, parameters and identity checks
    remain the same as a newly completed roundtrip.
    """
    _verify_captured_source(candidate, source_state, resource_root)
    descriptor_identifier = candidate.get("identifier")
    # Native identities are checked again after MIDI processing, not merely in
    # the pre-render snapshot used by the shared admission receipt.
    profile = candidate["performance_profile"]
    for phase in ("first", "reopen"):
        response = json.loads((output / index[phase]["response"]["path"]).read_bytes())["result"]
        after = response["tracks"][0]["plugin_readback_after"][0]
        _exact_identity(after, candidate["expected_identity"], descriptor_identifier=descriptor_identifier,
                        expected_plugin_path=candidate["plugin_path"])
        _validate_audition_parameters(after, candidate["expected_parameters"], profile)
    parser = verifier()
    receipt = dict(schema_version=1, preset_id=candidate["id"], candidate_sha256=candidate_fingerprint(candidate),
                   resource_manifest_sha256=resource_manifest_fingerprint(candidate),
                   validator=validator_pin.receipt_fields(),
                   identity_verifier=dict(name=parser.name, version=parser.version, code_sha256=parser.code_sha256),
                   plugin=dict(path=candidate["plugin_path"], identifier=descriptor_identifier,
                               binary_sha256=WRAPPER_SHA256, vst3_class_id=CLASS_ID),
                   first=index["first"], reopen=index["reopen"],
                   startup=dict(first="empty", reopen="empty"), source_state_sha256=source_state["state_sha256"])
    summary = verify_receipt(candidate, receipt, output, parser, validator_pin, resource_root=resource_root)
    from .installed_libraries import verify_installed_library

    settings = SimpleNamespace(instrument_library=Path(resource_root))
    verify_installed_library(settings, candidate["installed_library"])
    atomic_write_json(output / "receipt.json", receipt)
    return dict(receipt=receipt, summary=summary)
