"""Index Pianoteq's own exported VST3 presets; source packages are never modified.

Export first with Pianoteq --export-presets-filter builtin --export-vst3-presets PATH.
Only a successful export should be passed here. Third-party FXP names aren't categories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.instruments import preset_spec, materialize  # noqa: E402
from music_learning.instrument_capture import native_probe  # noqa: E402
from music_learning.storage import digest  # noqa: E402
from music_learning.instrument_taxonomy import classify  # noqa: E402
from native_host import NativeHost  # noqa: E402

LIBRARY = "modartt-pianoteq-9"
PIANOS = {
    "BechsteinDG",
    "Blüthner",
    "Bösendorfer",
    "Grotrian",
    "K2",
    "Kawai",
    "Kremsegg1",
    "Kremsegg2",
    "Petrof",
    "Steingraeber",
    "Steinway B",
    "Steinway D",
    "U4",
    "YC5",
}
# Exact factory groups and model names, not guesses from arbitrary user filenames.
GROUPS = {
    "Electric": "electric-piano",
    "Guitar": "acoustic-guitar",
    "Harp": "harp",
    "Harpsichord": "harpsichord",
    "Steelpans": "steelpan",
    "Vibes": "vibraphone",
    "Synth": "synths",
}
MODELS = {
    "Celeste": {
        "Celesta ": "celesta",
        "Glockenspiel ": "glockenspiel",
        "Kalimba ": "kalimba",
        "Toy Piano ": "toy-piano",
    },
    "Hohner": {
        "Clavinet D6 ": "clavinet",
        "Pianet N ": "electric-piano",
        "Pianet T ": "electric-piano",
        "Electra ": "electric-piano",
    },
    "Xylo": {"Marimba ": "marimba", "Xylophone ": "xylophone"},
    "Karsten": {
        "G. Giusti Harpsichord ": "harpsichord",
        "J. Salodiensis Virginal ": "harpsichord",
        "J. Weimes Pianoforte ": "acoustic-piano",
        "NY Steinway Square ": "acoustic-piano",
        "Ph. Schmidt Square ": "acoustic-piano",
    },
}
GM = {
    "acoustic-piano": [0],
    "electric-piano": [4, 5],
    "clavinet": [7],
    "harpsichord": [6],
    "celesta": [8],
    "glockenspiel": [9],
    "vibraphone": [11],
    "marimba": [12],
    "xylophone": [13],
    "acoustic-guitar": [24],
    "harp": [46],
    "steelpan": [114],
    "kalimba": [108],
}
PICKS = {
    "NY Steinway D Classical",
    "MKI Basic",
    "W1 Basic Stereo",
    "Clavinet D6 Basic",
    "Celesta Original",
    "Glockenspiel Original",
    "Kalimba Original",
    "Toy Piano Original",
    "Marimba Original",
    "Xylophone Original",
    "Vibraphone V-B Original",
}


def category(group, name):
    if group in PIANOS:
        return "acoustic-piano"
    if group in GROUPS:
        return GROUPS[group]
    return next(
        (id for prefix, id in MODELS.get(group, {}).items() if name.startswith(prefix)), "unclassified"
    )


def check_distinct_instruments(presets):
    states = {}
    for entry in presets:
        if not entry.get("audio_verified"):
            continue
        previous = states.setdefault(entry["state_sha256"], entry)
        if previous["family"] != entry["family"]:
            raise ValueError(
                "Different instruments restored the same state: " + previous["name"] + " / " + entry["name"]
            )


def build(library, bank, exports, evidence, verify=False):
    from types import SimpleNamespace

    if not verify:
        raise ValueError("Pianoteq admission requires --verify; exported names alone are not usable sounds")
    library, bank, exports = (Path(p).resolve() for p in (library, bank, exports))
    bank_relative = bank.relative_to(library).as_posix()
    exports.relative_to(bank)
    catalog_path = library / "catalog.json"
    previous = (
        json.loads(catalog_path.read_bytes())
        if catalog_path.exists()
        else {"schema_version": 1, "presets": []}
    )
    if previous.get("schema_version") != 1:
        raise ValueError("Unsupported instrument catalog")
    if evidence.exists():
        raise ValueError("Choose a new evidence directory; previous probes are preserved")
    evidence.mkdir(parents=True)
    plugin = bank / "VST3/Pianoteq 9.vst3"
    host = NativeHost(diagnostics=evidence / "native")
    scan = host.call(dict(schema_version=1, action="scan", plugin_path=str(plugin)), timeout=45)
    descriptor = scan["plugins"][0]
    if (
        descriptor["name"] != "Pianoteq 9"
        or descriptor["version"] != "9.1.0"
        or not descriptor["is_instrument"]
    ):
        raise ValueError("This package inventory is pinned to Pianoteq 9.1.0")
    plugin_hash = digest(plugin)
    runtime = {}
    for relative in ["Pianoteq 9.vst3plugin", "ptq910p.dll", "ptq910w.dll"]:
        p = bank / relative
        runtime[relative] = dict(sha256=digest(p), bytes=p.stat().st_size)
    presets = []
    for path in sorted(exports.rglob("*.vstpreset")):
        relative = path.relative_to(exports)
        if len(relative.parts) != 3 or relative.parts[0] != "Factory":
            raise ValueError("Expected exported factory/group/preset layout")
        data = path.read_bytes()
        if data[:4] != b"VST3" or data[8:40] != b"565354507439717069616E6F74657120":
            raise ValueError("Unexpected VST3 preset class: " + str(path))
        group, name = relative.parts[1], path.stem
        instrument = category(group, name)
        entrypoint = path.relative_to(bank).as_posix()
        presets.append(
            classify(
                dict(
                    id="pianoteq9-" + hashlib.sha256(relative.as_posix().encode()).hexdigest()[:20],
                    name=name,
                    library_id=LIBRARY,
                    library_name="Pianoteq 9",
                    bank_root=bank_relative,
                    backend="vst3-preset",
                    family=instrument,
                    instrument_ids=[instrument],
                    classification_basis="pianoteq-factory-group-and-model-registry",
                    source_category=group,
                    gm_programs=GM.get(instrument, []),
                    key_range=[21, 108],
                    default=name in {"NY Steinway D Classical", "MKI Basic"},
                    audio_verified=False,
                    usage="Pianoteq 物理建模；按原厂乐器和预设选择。",
                    tags=[group, instrument, "physical-modeling"],
                    entrypoint=entrypoint,
                    resources={entrypoint: dict(sha256=digest(path), bytes=len(data))},
                    plugin_path=str(plugin),
                    plugin_sha256=plugin_hash,
                    identifier=descriptor["identifier"],
                    runtime_dependencies=runtime,
                )
            )
        )
    if not presets:
        raise ValueError("No exported VST3 presets found")
    # Probe every instrument group and the selected comparison presets, not all 797 variations.
    representatives = {
        instrument: next(p for p in presets if p["family"] == instrument)
        for instrument in sorted({p["family"] for p in presets} - {"unclassified"})
    }
    selected = {p["id"]: p for p in [*representatives.values(), *(p for p in presets if p["name"] in PICKS)]}
    failures = []
    if verify:
        for entry in selected.values():
            directory = evidence / entry["id"]
            directory.mkdir()
            try:
                local = materialize(
                    SimpleNamespace(instrument_library=library), entry, directory / "resources"
                )
                state, metrics = native_probe(
                    host, dict(preset_spec(entry, local), id="instrument"), directory, entry["key_range"]
                )
                entry.update(audio_verified=True, metrics=metrics, state_sha256=state["state_sha256"])
                (directory / "result.json").write_text(
                    json.dumps(dict(plugin=state, metrics=metrics)), encoding="utf-8"
                )
            except Exception as error:
                entry["validation_error"] = str(error)[:1000]
                failures.append(dict(id=entry["id"], error=str(error)))
                print("FAIL " + entry["name"] + ": " + str(error)[:200], flush=True)
            try:
                check_distinct_instruments(selected.values())
            except ValueError as error:
                (evidence / "rejected.json").write_text(
                    json.dumps(
                        dict(error=str(error), presets=list(selected.values())), ensure_ascii=False, indent=2
                    ),
                    encoding="utf-8",
                )
                raise
            if entry["audio_verified"]:
                print("PROBE " + entry["name"], flush=True)
    retained = [p for p in previous["presets"] if p.get("library_id") != LIBRARY]
    # Only tested presets enter the selectable catalog; the full export is inventory.
    admitted = [p for p in presets if p["audio_verified"]]
    if not admitted:
        raise ValueError("No playable presets admitted; existing catalog was preserved")
    combined = retained + admitted
    if len({p["id"] for p in combined}) != len(combined):
        raise ValueError("Duplicate preset ids; catalog was not changed")
    result = dict(previous, presets=combined)
    temporary = catalog_path.with_suffix(".json.pending")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(catalog_path)
    report = dict(
        presets=len(presets),
        audio_verified=sum(p["audio_verified"] for p in presets),
        by_instrument={
            id: sum(p["family"] == id for p in presets) for id in sorted({p["family"] for p in presets})
        },
        failures=failures,
        plugin=descriptor,
        runtime_dependencies=runtime,
    )
    (evidence / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    report = build(args.library, args.bank, args.exports, args.evidence, args.verify)
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k in {"presets", "audio_verified", "by_instrument", "failures"}
            },
            ensure_ascii=False,
        )
    )
