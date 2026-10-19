"""Installed, fingerprinted presets and explicit mapping advice. No silent default synth."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from . import contracts as c
from .storage import digest


def catalog(settings):
    if settings.instrument_library is None:
        return []
    path = settings.instrument_library / "catalog.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_bytes())
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported instrument catalog")
    return data["presets"]


def resource_path(root, relative):
    p = PurePosixPath(relative)
    if p.is_absolute() or ".." in p.parts or "\\" in relative or ":" in relative:
        raise ValueError("Invalid instrument resource path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Instrument resource escaped library")
    return path


def public(entry):
    return {
        k: v for k, v in entry.items() if k not in ("resources", "entrypoint", "bank_root", "plugin_path")
    }


def query(settings, args):
    found = [
        p
        for p in catalog(settings)
        if (not args.family or p["family"] == args.family)
        and args.search.casefold()
        in (
            p["name"] + " " + p["id"] + " " + p.get("usage", "") + " " + " ".join(p.get("tags", []))
        ).casefold()
    ]
    return {
        "total": len(found),
        "items": [public(p) for p in found[args.offset : args.offset + args.limit]],
        "next_offset": args.offset + args.limit if args.offset + args.limit < len(found) else None,
    }


def choose(presets, gm, drum, durations):
    choices = [
        p
        for p in presets
        if p.get("default")
        and p.get("audio_verified")
        and ((drum and p["family"] == "drums") or (not drum and gm in p.get("gm_programs", [])))
    ]
    selected = choices[0] if choices else None
    short_fraction = sum(d <= 0.22 for d in durations) / len(durations) if durations else 0
    if selected and gm in (48, 49) and short_fraction >= 0.7:
        staccato = next(
            (p for p in presets if p["id"] == "strings-staccato" and p.get("audio_verified")), None
        )
        if staccato:
            return staccato, "大多数事件短于 220 ms，建议短弓；这是事件驱动的奏法建议，仍须听辨确认。"
    return selected, selected[
        "usage"
    ] if selected else "No verified matching preset. Select an explicit alternative; never substitute INIT."


def recommend(settings, snapshot, track_id=None):
    result = []
    presets = catalog(settings)
    for track in snapshot["tracks"]:
        if track_id and track["id"] != track_id:
            continue
        notes = [n for n in snapshot["notes"] if n["track_id"] == track["id"]]
        if not notes:
            continue
        gm = next(
            (int(x[3:]) for x in track["instrument_candidates"] if x.startswith("GM:") and x[3:].isdigit()),
            None,
        )
        drum = track.get("role") == "drums"
        durations = [(n["end_sample"] - n["start_sample"]) / snapshot["sample_rate"] for n in notes]
        selected, reason = choose(presets, gm, drum, durations)
        low, high = min(n["pitch"] for n in notes), max(n["pitch"] for n in notes)
        result.append(
            {
                "track_id": track["id"],
                "model_candidates": track["instrument_candidates"],
                "note_count": len(notes),
                "pitch_range": [low, high],
                "median_duration_seconds": sorted(
                    (n["end_sample"] - n["start_sample"]) / snapshot["sample_rate"] for n in notes
                )[len(notes) // 2],
                "preset": public(selected) if selected else None,
                "out_of_range_notes": sum(
                    not selected["key_range"][0] <= n["pitch"] <= selected["key_range"][1] for n in notes
                )
                if selected
                else None,
                "reason": reason,
                "review": "unreviewed",
                "warning": "GM and role are model candidates; range/density do not establish original instrumentation.",
            }
        )
    return {
        "assignments": result,
        "policy": "Preserve notes/time; select an explicit tested preset; report range or identity uncertainty. Use short articulations for detached patterns, sustain for held layers, sampled sources for acoustic instruments and synth presets for intentional electronic roles.",
    }


def pin(settings, payload):
    preset_id = c.CaptureInstrument.model_validate(payload).preset_id
    entry = next((e for e in catalog(settings) if e["id"] == preset_id), None)
    if not entry:
        raise ValueError("Unknown installed preset")
    # The job pins the exact inventory, not a mutable preset label.
    return {"preset_id": preset_id, "preset": entry}


def publish(service, db, job, result):
    for item in result["files"]:
        source = service.settings.path(item["relative_path"])
        if (
            not source.is_relative_to(service.settings.path("tasks", job["id"]))
            or source.stat().st_size != item["bytes"]
        ):
            raise ValueError("Preset artifact escaped owned task or changed size")
        sha = service.storage.publish(source, expected=item["sha256"])
        service.register_asset(
            db,
            job["project_id"],
            sha,
            item["bytes"],
            item["media_type"],
            {},
            c.Provenance(origin="rule", source_id="instrument:" + job["id"]).model_dump(),
        )
    return {**result["publication"], "project_id": job["project_id"], "job_id": job["id"]}


def captured_plugin(service, db, project_id, job_id):
    from . import schema_v1 as t
    from .service import row

    job = row(db, t.jobs, "id", job_id)
    if job["project_id"] != project_id or job["kind"] != "instrument.capture" or job["status"] != "succeeded":
        raise ValueError("Preset requires a successful capture in this project")
    return job["result"]["plugin"]


def voice_preset(settings, voice):
    selected, _ = choose(
        catalog(settings),
        voice["program"],
        voice["is_drum"],
        [n["duration_samples"] / voice.get("sample_rate", 44100) for n in voice.get("notes", [])],
    )
    if selected is None:
        raise ValueError(
            f"No verified preset mapping for voice {voice['id']} GM:{voice['program']}; explicit selection required"
        )
    outside = sorted(
        {
            n["note"]
            for n in voice.get("notes", [])
            if not selected["key_range"][0] <= n["note"] <= selected["key_range"][1]
        }
    )
    if outside:
        raise ValueError(
            f"Preset {selected['id']} does not cover pitches {outside}; explicit mapping required, notes were not transposed"
        )
    return selected


def materialize(settings, entry, destination):
    """Copy a pinned preset's dependency closure, retaining include-relative paths."""
    import shutil

    source_root = resource_path(settings.instrument_library, entry["bank_root"])
    required = sum(meta["bytes"] for meta in entry["resources"].values())
    if shutil.disk_usage(destination.resolve().anchor).free < required + getattr(
        settings, "pipeline_min_free_bytes", 3 * 1024**3
    ):
        raise ValueError("Insufficient space and reserve for instrument resources")
    for relative, meta in entry["resources"].items():
        source = resource_path(source_root, relative)
        if source.stat().st_size != meta["bytes"] or digest(source) != meta["sha256"]:
            raise ValueError("Installed preset dependency changed")
        target = resource_path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return resource_path(destination, entry["entrypoint"])
