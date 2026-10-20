"""Prepare Keyscape factory presets and admit only real audio/state round trips.

The installed STEAM tree stays in the configured library. Only small standard
VST3 presets and an immutable sample manifest enter project resources.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.instrument_capture import native_probe  # noqa: E402
from music_learning.instruments import materialize, preset_spec  # noqa: E402
from music_learning.keyscape_library import read_presets, read_container_member  # noqa: E402
from music_learning.keyscape_presets import build_preset, verify_state  # noqa: E402
from music_learning.storage import digest  # noqa: E402
from music_learning.preset_verification import library_lock  # noqa: E402
from music_learning.instrument_models import keyscape_model  # noqa: E402
from music_learning.instrument_taxonomy import classify  # noqa: E402
from native_host import NativeHost  # noqa: E402

LIBRARY_ID = "spectrasonics-keyscape"
GM = {"acoustic-piano": [0, 1], "electric-piano": [4, 5], "clavinet": [7],
      "harpsichord": [6], "celesta": [8], "vibraphone": [11], "keyboard-bass": [38]}
PICKS = {"LA Custom C7 - Natural", "LA Custom C7 - Studio", "Rhodes - Classic",
         "Wurlitzer 200A", "Clavinet C", "Celeste - Dry", "Clavichord - Bright Octave NR",
         "Duo - Pianet Vibrabell", "Toy Piano - Classic", "Rhodes Piano Bass - Solid"}
DEFAULTS = {"acoustic-piano": "LA Custom C7 - Natural", "electric-piano": "Rhodes - Classic",
            "clavinet": "Clavinet C", "celesta": "Celeste - Dry", "toy-piano": "Toy Piano - Classic",
            "keyboard-bass": "Rhodes Piano Bass - Solid"}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def reconcile(presets, combined, reference):
    """Keep earlier audio receipts; choose one pure default for each instrument."""
    current = [p for p in combined if p.get("library_id") == LIBRARY_ID]
    for item in current:
        if len(item["instrument_ids"]) > 1:
            item["default"] = False
    for kind in sorted({i for p in current for i in p["instrument_ids"]}):
        choices = [p for p in current if p["instrument_ids"] == [kind] and p["audio_verified"]]
        if not choices:
            continue
        preferred = next((p for p in choices if p["name"] == DEFAULTS.get(kind)),
                         next((p for p in choices if p.get("default")), choices[0]))
        for item in choices:
            item["default"] = item is preferred
    indexed = {p["id"]: p for p in current}
    for item in current:
        if item.get("installed_library", {}).get("manifest_sha256") == reference["manifest_sha256"]:
            item["installed_library"] = reference
    for item in presets:
        saved = indexed.get(item["id"])
        if saved and all(saved.get(key) == item.get(key) for key in
                         ("plugin_sha256", "resources", "expected_keyscape")):
            for key in ("audio_verified", "default", "metrics", "state_sha256", "probe_sha256"):
                if key in saved:
                    item[key] = saved[key]


def installed_manifest(library, bank, copy_manifest):
    copied = json.loads(copy_manifest.read_bytes())
    if Path(copied["target"]).resolve() != (bank / "STEAM/Keyscape").resolve():
        raise ValueError("Copy evidence belongs to another installation")
    records = []
    source = Path(copied["source"])
    for item in copied["files"]:
        original = source / item["source"]
        if (original.stat().st_size, original.stat().st_mtime_ns) != (item["bytes"], item["mtime_ns"]):
            raise ValueError("Source changed since the verified copy")
        relative = item["path"]
        if relative.endswith("/zmap.index.xml"):
            relative = relative[:-4]
        target = bank / "STEAM/Keyscape" / relative
        if target.stat().st_size != item["bytes"]:
            raise ValueError("Installed copy size differs")
        # The additional normalized index was not part of the original copy loop.
        if relative != item["path"] and digest(target) != item["sha256"]:
            raise ValueError("Normalized sample index differs")
        records.append(dict(path="STEAM/Keyscape/" + relative, bytes=item["bytes"],
                            sha256=item["sha256"], mtime_ns=target.stat().st_mtime_ns))
    data = json.dumps(dict(schema_version=1, library_id=LIBRARY_ID, files=records),
                      sort_keys=True, ensure_ascii=False).encode()
    sha = hashlib.sha256(data).hexdigest()
    relative = "manifests/" + sha + ".json"
    path = bank / relative
    if path.exists() and path.read_bytes() != data:
        raise ValueError("Immutable manifest collision")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    return dict(library_id=LIBRARY_ID, bank_root=bank.relative_to(library).as_posix(),
                manifest_path=relative, manifest_sha256=sha,
                locator=dict(path=str(Path(os.environ["ProgramData"]) / "Spectrasonics/STEAM"), target="STEAM"))


def build(library, bank, plugin, baseline, copy_manifest, evidence, all_presets=False, names=()):
    with library_lock(bank):
        return _build(library, bank, plugin, baseline, copy_manifest, evidence, all_presets, names)


def _build(library, bank, plugin, baseline, copy_manifest, evidence, all_presets=False, names=()):
    library, bank, plugin, evidence = (Path(p).resolve() for p in (library, bank, plugin, evidence))
    bank_relative = bank.relative_to(library).as_posix()
    plugin.relative_to(bank)
    if evidence.exists():
        raise ValueError("Use a new evidence directory")
    evidence.mkdir(parents=True)
    base = json.loads(Path(baseline).read_bytes())
    base = base.get("plugin", base)
    reference = installed_manifest(library, bank, Path(copy_manifest))
    database = bank / "STEAM/Keyscape/Settings Library/Patches/Factory/Keyscape Library.db"
    inventory = read_presets(database)
    plugin_hash = digest(plugin)
    presets = []
    for item in inventory:
        if item.get("preset_parse_error") or "unclassified" in item["classification"]["instrument_ids"]:
            raise ValueError("Unresolved factory preset: " + item["name"])
        data = read_container_member(database, item["member_path"])
        preset, expected = build_preset(base, data)
        identifier = "keyscape-" + hashlib.sha256(item["member_path"].encode()).hexdigest()[:20]
        relative = "presets/" + identifier + ".vstpreset"
        path = bank / relative
        path.parent.mkdir(exist_ok=True)
        if path.exists() and path.read_bytes() != preset:
            raise ValueError("Prepared preset changed; retain it and use a new bank version")
        path.write_bytes(preset)
        ids = item["classification"]["instrument_ids"]
        presets.append(classify(dict(
            id=identifier, name=item["name"], library_id=LIBRARY_ID, library_name="Keyscape",
            bank_root=bank_relative, backend="vst3-preset", family=ids[0], instrument_ids=ids,
            classification_basis=item["classification"]["classification_basis"],
            source_category=item["types"], tags=item["models"] + item["subcategories"],
            model_spec=keyscape_model(item["models"]), model_basis="keyscape-factory-model",
            gm_programs=GM.get(ids[0], []), key_range=[21, 108], default=False, audio_verified=False,
            usage="按原厂键盘型号与奏法选择；复合音色保留其层叠配器。", entrypoint=relative,
            resources={relative: dict(sha256=digest(path), bytes=path.stat().st_size),
                       reference["manifest_path"]: dict(sha256=reference["manifest_sha256"],
                                                        bytes=(bank / reference["manifest_path"]).stat().st_size)},
            plugin_path=str(plugin), plugin_sha256=plugin_hash, installed_library=reference,
            expected_keyscape=expected,
        )))
    write_json(bank / "factory-inventory.json", dict(schema_version=1, presets=presets))
    kinds = sorted({i for p in presets for i in p["instrument_ids"]})
    requested = set(names) if names else PICKS
    selected = {p["id"]: p for p in presets if all_presets or p["name"] in requested}
    if names:
        if set(names) - {p["name"] for p in presets}:
            raise ValueError("Unknown requested factory preset")
    else:
        for kind in kinds:
            candidate = next((p for p in presets if p["instrument_ids"] == [kind]),
                             next(p for p in presets if kind in p["instrument_ids"]))
            selected[candidate["id"]] = candidate
    host = NativeHost(diagnostics=evidence / "native")
    failures, admitted = [], []
    for entry in selected.values():
        directory = evidence / entry["id"]
        directory.mkdir()
        try:
            local = materialize(SimpleNamespace(instrument_library=library), entry, directory / "resources")
            state, metrics = native_probe(host, dict(preset_spec(entry, local), id="instrument"),
                                          directory, entry["key_range"],
                                          validate_state=lambda state: verify_state(state, entry["expected_keyscape"]))
            verify_state(state, entry["expected_keyscape"])
            entry.update(audio_verified=True, metrics=metrics, state_sha256=state["state_sha256"],
                         probe_sha256=digest(directory / "probe.wav"))
            # Distinct instruments must not silently fall back to the same state or PCM.
            for previous in admitted:
                if previous["instrument_ids"] != entry["instrument_ids"] and (
                    previous["state_sha256"] == entry["state_sha256"]
                    or previous["probe_sha256"] == entry["probe_sha256"]
                ):
                    raise ValueError("Different instruments returned identical state/audio")
            admitted.append(entry)
            write_json(directory / "result.json", dict(plugin=state, metrics=metrics))
            print("PASS " + entry["name"], flush=True)
        except Exception as error:
            failures.append(dict(name=entry["name"], error=str(error)))
            print("FAIL " + entry["name"] + ": " + str(error)[:240], flush=True)
    if not admitted:
        write_json(evidence / "rejected.json", dict(failures=failures))
        raise ValueError("No Keyscape preset passed; catalog unchanged")
    for kind in kinds:
        preferred = next((p for p in admitted if p["instrument_ids"] == [kind]), None)
        if preferred:
            preferred["default"] = True
    catalog = library / "catalog.json"
    previous = json.loads(catalog.read_bytes()) if catalog.exists() else dict(schema_version=1, presets=[])
    if previous.get("schema_version") != 1:
        raise ValueError("Unsupported instrument catalog")
    ids = {p["id"] for p in admitted}
    combined = [p for p in previous["presets"] if p["id"] not in ids] + admitted
    if len({p["id"] for p in combined}) != len(combined):
        raise ValueError("Duplicate preset ids")
    reconcile(presets, combined, reference)
    pending = catalog.with_suffix(".json.pending")
    write_json(pending, dict(previous, presets=combined))
    pending.replace(catalog)
    report = dict(inventory=len(presets), admitted=len(admitted), selectable=len(combined), failures=failures,
                  by_instrument={k: sum(k in p["instrument_ids"] for p in admitted) for k in kinds},
                  installed_library=reference, plugin_sha256=digest(plugin))
    write_json(evidence / "report.json", report)
    write_json(bank / "factory-inventory.json", dict(schema_version=1, presets=presets))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("library", "bank", "plugin", "baseline", "copy-manifest", "evidence"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--all", action="store_true", dest="all_presets")
    parser.add_argument("--preset", action="append", default=[], dest="names")
    args = vars(parser.parse_args())
    print(json.dumps(build(**args), ensure_ascii=False))
