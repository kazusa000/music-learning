"""Prepare full factory programs through Pianoteq RPC and verify VST3 admission.

Default: cover every instrument family and selected useful variants. --all tests
the complete inventory; --remaining tests only unpublished prepared programs.
--resume continues the immutable queue without exporting
again. Does not submit any music-project command or change system installation.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.instruments import resource_path  # noqa: E402
from music_learning.pianoteq_presets import build_preset, verify_state  # noqa: E402
from music_learning.preset_verification import (  # noqa: E402
    atomic_json, check_entry, implementation_fingerprint, library_lock, run,
)
from music_learning.processes import OwnedProcess  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from music_learning.storage import digest  # noqa: E402
from music_learning.instrument_taxonomy import classify  # noqa: E402
from index_pianoteq import category, GM, LIBRARY, PICKS  # noqa: E402
from native_host import NativeHost  # noqa: E402

USEFUL = PICKS | {"Classical Guitar"}
SYNGULAR = {"Keys": "synth-keys", "Pad": "synth-pad", "Bass": "synth-bass",
            "Brass": "synth-brass", "Lead": "synth-lead", "Perc": "synth-percussion",
            "Seq": "synth-sequence", "Core": "synth-poly", "Init": "synth-poly"}


def factory_mapping(group, name, instrument):
    family = category(group, name)
    if group == "Syngular":
        if instrument != "Syngular" or not name.startswith("Syngular "):
            raise ValueError("Unexpected Syngular factory identity")
        family = SYNGULAR[name.split()[1]]
    # The Original Glockenspiel's actual sounding range is F5-C8 (77-108),
    # measured in both sweep directions. The factory a-la-piano version extends it.
    key_range = [77, 108] if family == "glockenspiel" and "a la piano" not in name else [21, 108]
    return family, key_range


def refresh_mapping(entry):
    family, key_range = factory_mapping(entry["source_category"], entry["name"],
                                       entry["expected_pianoteq"]["instrument"])
    return classify(dict(entry, family=family, instrument_ids=[family], key_range=key_range,
                         default=entry["name"] in USEFUL,
                         tags=[entry["source_category"], family, "physical-modeling",
                               entry["expected_pianoteq"]["instrument"]]))


def pinned_host(settings, bank, diagnostics):
    directory = bank / "preparation-evidence/host"
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / "pin.json"
    executable = directory / "ml_audio_host.exe"
    if receipt.exists():
        if digest(executable) != json.loads(receipt.read_bytes())["sha256"]:
            raise ValueError("Pinned Pianoteq verification host changed")
    else:
        expected = digest(settings.native_host)
        shutil.copyfile(settings.native_host, executable)
        if digest(executable) != expected or digest(settings.native_host) != expected:
            raise ValueError("Native host changed during pinning")
        atomic_json(receipt, dict(sha256=expected))
    return NativeHost(executable, diagnostics)


@contextmanager
def rpc(bank, evidence):
    # Local to this child process; no global environment/registry preference edits.
    appdata = bank / "AppData"
    (appdata / "Modartt/Pianoteq/Presets/Factory Prepared").mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, APPDATA=str(appdata))
    prefs = bank / "probe-profile/intake.prefs"
    prefs.parent.mkdir(parents=True, exist_ok=True)
    with (evidence / "rpc.log").open("ab") as log, OwnedProcess(
        [str(bank / "Pianoteq 9.exe"), "--headless", "--no-audio-prefs", "--prefs", str(prefs),
         "--serve", f"127.0.0.1:{port}"], cwd=bank, env=env, stdout=log, stderr=log,
    ) as process, httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=15) as client:
        def call(method, **params):
            response = client.post("/jsonrpc", json=dict(jsonrpc="2.0", id=1, method=method, params=params))
            response.raise_for_status()
            value = response.json()
            if "error" in value:
                raise ValueError(f"Pianoteq {method}: {value['error']}")
            return value["result"]

        deadline = time.monotonic() + 20
        while True:
            if process.process.poll() is not None:
                raise ValueError("Pianoteq RPC process exited before startup")
            try:
                info = call("getInfo")[0]
                break
            except httpx.ConnectError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Pianoteq RPC startup timeout")
                time.sleep(.1)
        if info["version"] != "9.1.2" or info["plugin_type"] != "sta":
            raise ValueError("Unexpected Pianoteq RPC engine")
        try:
            yield call
        finally:
            if process.process.poll() is None:
                try:
                    call("quit")
                    process.process.wait(timeout=10)
                except (httpx.HTTPError, ValueError, subprocess.TimeoutExpired):
                    pass  # OwnedProcess closes the child job even after an RPC failure.


def prepare(settings, bank, exports, evidence, host):
    inventory_path = bank / "full-factory-inventory.json"
    if inventory_path.exists():
        inventory = json.loads(inventory_path.read_bytes())
        for entry in inventory["presets"]:
            check_entry(settings, entry)
        inventory["presets"] = [refresh_mapping(e) for e in inventory["presets"]]
        atomic_json(inventory_path, inventory)
        return inventory
    exports = exports.resolve()
    paths = sorted(exports.rglob("*.vstpreset"))
    if not paths:
        raise ValueError("No complete factory export supplied")
    baseline = host.call(dict(schema_version=1, action="inspect",
                             plugin=dict(path=str(bank / "VST3/Pianoteq 9.vst3"))), timeout=45)["plugin"]
    # Validates the supported bank layout before starting the export loop.
    from music_learning.pianoteq_presets import check_plugin, program
    from music_learning.vst3_state import state_components
    check_plugin(baseline)
    program(state_components(baseline)["IComponent"])
    atomic_json(evidence / "baseline.json", baseline)
    runtime = {name: dict(sha256=digest(bank / name), bytes=(bank / name).stat().st_size)
               for name in ["Pianoteq 9.vst3plugin", "ptq912p.dll", "ptq912w.dll"]}
    journal = bank / "full-preparation.json"
    entries = []
    prepared = json.loads(journal.read_bytes()) if journal.exists() else {}
    with rpc(bank, evidence) as call:
        factory = [p for p in call("getListOfPresets") if p["file"] == "(builtin)"]
        by_name = {p["name"]: p for p in factory}
        if len(by_name) != len(factory) or len(factory) != len(paths):
            raise ValueError("Factory RPC/export inventory mismatch or ambiguous names")
        for source in paths:
            relative = source.relative_to(exports)
            if len(relative.parts) != 3 or relative.parts[0] != "Factory":
                raise ValueError("Expected Factory/group/name.vstpreset layout")
            group, name = relative.parts[1], source.stem
            meta = by_name[name]
            # Exported group plus RPC's actual selected instrument pin the classification.
            full = bank / "Full VST3 Presets" / relative
            saved = prepared.get(relative.as_posix())
            if saved and full.exists() and digest(full) == saved["sha256"]:
                expected = saved["expected"]
            else:
                call("loadPreset", name=name)
                deadline = time.monotonic() + 15
                while True:
                    info = call("getInfo")[0]
                    if not info["computing_parameter_update"]:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Pianoteq preset preparation timeout: " + name)
                    time.sleep(.02)
                current = info["current_preset"]
                if current["name"] != name or current["instrument"] != meta["instr"]:
                    raise ValueError("Pianoteq RPC selected the wrong instrument: " + name)
                call("savePreset", name=name, bank="Factory Prepared")
                fxp = resource_path(bank / "AppData/Modartt/Pianoteq/Presets/Factory Prepared", name + ".fxp")
                data, expected = build_preset(baseline, fxp.read_bytes())
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_bytes(data)
                expected.update(name=name, instrument=meta["instr"])
                prepared[relative.as_posix()] = dict(sha256=digest(full), expected=expected)
                atomic_json(journal, prepared)
            family, key_range = factory_mapping(group, name, meta["instr"])
            entrypoint = full.relative_to(bank).as_posix()
            entries.append(classify(dict(
                id="pianoteq9-" + hashlib.sha256(relative.as_posix().encode()).hexdigest()[:20],
                name=name, library_id=LIBRARY, library_name="Pianoteq 9", backend="vst3-preset",
                bank_root=bank.relative_to(settings.instrument_library).as_posix(),
                family=family, instrument_ids=[family], source_category=group,
                classification_basis="pianoteq-factory-group-and-model-registry",
                gm_programs=GM.get(family, []), key_range=key_range, default=name in USEFUL,
                audio_verified=False, usage="Pianoteq 物理建模；原厂完整预设。",
                tags=[group, family, "physical-modeling", meta["instr"]], entrypoint=entrypoint,
                resources={entrypoint: dict(sha256=digest(full), bytes=full.stat().st_size)},
                plugin_path=str(bank / "VST3/Pianoteq 9.vst3"), plugin_sha256=baseline["binary_sha256"],
                identifier=baseline["identifier"], runtime_dependencies=runtime, expected_pianoteq=expected,
            )))
            if len(entries) % 100 == 0:
                print(f"PREPARED {len(entries)}/{len(paths)}", flush=True)
    inventory = dict(schema_version=1, presets=entries, factory_count=len(factory))
    atomic_json(inventory_path, inventory)
    return inventory


def publish_verified(settings, entry):
    """Incremental atomic admission; reject one bad identity, preserve other entries."""
    from music_learning.instrument_localization import prepare_import

    entry = prepare_import(classify(entry))
    with library_lock(settings.instrument_library):
        path = settings.instrument_library / "catalog.json"
        catalog = json.loads(path.read_bytes())
        for other in catalog["presets"]:
            if (other["id"] == entry["id"] or other.get("library_id") != LIBRARY
                    or other["family"] == entry["family"]):
                continue
            for field in ["state_sha256", "probe_sha256"]:
                if other.get(field) and other[field] == entry[field]:
                    raise ValueError("Different instruments returned identical state/audio: " + other["id"])
        combined = [p for p in catalog["presets"] if p["id"] != entry["id"]] + [entry]
        if len({p["id"] for p in combined}) != len(combined):
            raise ValueError("Duplicate preset IDs")
        atomic_json(path, dict(catalog, presets=combined))


def select_entries(inventory, catalog, *, remaining=False, resume_ids=None):
    entries = inventory["presets"]
    if resume_ids is not None:
        if resume_ids - {e["id"] for e in entries}:
            raise ValueError("Resume queue references missing prepared programs")
        return [e for e in entries if e["id"] in resume_ids]
    if remaining:
        admitted = {e["id"] for e in catalog["presets"] if e.get("audio_verified")}
        return [e for e in entries if e["id"] not in admitted]
    return entries


def intake(exports=None, all_presets=False, resume=None, output=None, remaining=False):
    settings = Settings.load()
    bank = settings.instrument_library / "Pianoteq 9.1.2"
    with library_lock(bank):
        output = Path(resume or output or bank / "verification-runs" /
                      (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])).resolve()
        if remaining or resume:
            # Existing preparation is immutable during an incremental batch.
            inventory = json.loads((bank / "full-factory-inventory.json").read_bytes())
        else:
            # run() owns creation of its output; preparation uses a separate small journal.
            preparation = bank / "preparation-evidence"
            preparation.mkdir(exist_ok=True)
            host = pinned_host(settings, bank, preparation / "native")
            inventory = prepare(settings, bank, Path(exports), preparation, host)
        catalog = json.loads((settings.instrument_library / "catalog.json").read_bytes())
        ids = None
        if resume:
            ids = {p["id"] for p in json.loads((output / "queue.json").read_bytes())["items"]}
        entries = select_entries(inventory, catalog, remaining=remaining, resume_ids=ids)
        if not resume and not remaining and not all_presets:
            selected = {p["id"]: p for p in entries if p["name"] in USEFUL}
            for family in sorted({p["family"] for p in entries} - {"unclassified"}):
                first = next(p for p in entries if p["family"] == family)
                selected[first["id"]] = first
            entries = list(selected.values())
        from music_learning.instrument_localization import prepare_entries
        entries=prepare_entries(entries,bank)
        host = pinned_host(settings, bank, output / "native")
        implementation = implementation_fingerprint(host, [Path(__file__),
            ROOT / "src/music_learning/pianoteq_presets.py", ROOT / "src/music_learning/vst3_state.py"])
        admitted, report = run(settings, entries, bank, output, host, implementation,
            lambda state, entry: verify_state(state, entry["expected_pianoteq"]), resume=bool(resume),
            on_verified=lambda entry: publish_verified(settings, entry))
        combined = json.loads((settings.instrument_library / "catalog.json").read_bytes())["presets"]
        report.update(inventory=len(inventory["presets"]), selectable=len(combined),
                      families=sorted({p["family"] for p in admitted}))
        atomic_json(output / "report.json", report)
        print(json.dumps(report, ensure_ascii=True), flush=True)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exports", type=Path)
    parser.add_argument("--all", action="store_true", dest="all_presets")
    parser.add_argument("--remaining", action="store_true", help="Verify unpublished prepared presets only")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.remaining and args.all_presets:
        parser.error("--remaining and --all are mutually exclusive")
    if not args.resume and not args.exports and not args.remaining:
        parser.error("--exports is required for preparation")
    result = intake(**vars(args))
    sys.exit(1 if result["failures"] else 0)
