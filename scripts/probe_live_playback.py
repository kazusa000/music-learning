"""Create an isolated 305s sfizz session and verify live edits without render jobs.

Never points at the user's project DB, library or running Keyscape verifier.
The generated config can also be used for the browser workflow acceptance.
"""

import argparse

# ruff: noqa: E402
import base64
from array import array
import json
import math
from pathlib import Path
import struct
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning import contracts as c
from music_learning.database import Database
from music_learning.service import Service
from music_learning.settings import Settings
from native_host import NativeHost

PROV = dict(origin="rule", source_id="isolated-live-probe")


def invoke(service, op, args=None, revision=None):
    return service.execute(
        dict(
            operation=op,
            arguments=args or {},
            expected_revision=revision,
            idempotency_key=c.new_id() if c.OPERATIONS[op][1] else None,
        )
    )["result"]


def run(directory, port):
    directory = directory.resolve()
    if (directory / "app-data/projects.sqlite3").exists():
        raise ValueError("Use a new independent probe directory")
    directory.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_root=directory / "app-data", port=port, plugin_roots=[ROOT / "plugins"])
    database = Database(settings)
    database.migrate()
    service = Service(database)
    try:
        project = invoke(service, "projects.create", dict(title="逐轨播放验收"))

        def asset(data, media="application/octet-stream", metadata=None):
            sha = service.storage.put(data)
            with database.transaction() as db:
                service.register_asset(db, project["id"], sha, len(data), media, metadata or {}, PROV)
            return sha

        path = directory / "tone.wav"
        rate = 44100
        cycle = b"".join(
            struct.pack("<h", round(math.sin(i * 2 * math.pi * 440 / 44100) * 10000)) for i in range(4410)
        )
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            out.writeframes(cycle)
        sample_id = asset(path.read_bytes(), "audio/wav")
        sfz_id = asset(
            b"<region> sample=tone.wav lokey=0 hikey=127 pitch_keycenter=69 loop_mode=loop_continuous loop_start=0 loop_end=4409 ampeg_release=0.01"
        )
        plugin_path = ROOT / "plugins/sfizz-1.2.3/commoncf/VST3/sfizz.vst3"
        host = NativeHost(diagnostics=directory / "inspect")
        classes = host.call(dict(schema_version=1, action="scan", plugin_path=str(plugin_path)))["plugins"]
        chosen = next(p for p in classes if p.get("is_instrument"))
        descriptor = host.call(
            dict(
                schema_version=1,
                action="inspect",
                plugin=dict(path=str(plugin_path), identifier=chosen["identifier"]),
            )
        )["plugin"]
        descriptor_id = asset(json.dumps(descriptor).encode())
        state_id = asset(base64.b64decode(descriptor["state_base64"]))
        tracks = [
            c.Track(id=c.new_id(), name=name, kind="instrument", gain_db=-9, provenance=PROV)
            for name in ["钢琴", "合成器"]
        ]
        plugins = [
            c.PluginInstance(
                id=c.new_id(),
                track_id=t.id,
                class_id=descriptor["vst3_class_id"],
                version=descriptor["version"],
                binary_sha256=descriptor["binary_sha256"],
                state_asset_id=state_id,
                descriptor_asset_id=descriptor_id,
                resource_paths={"tone.sfz": sfz_id, "tone.wav": sample_id},
                resource_asset_ids=[sfz_id, sample_id],
                resource_entrypoint="tone.sfz",
                order=0,
            )
            for t in tracks
        ]
        notes = [
            c.NoteEvent(
                id=c.new_id(),
                track_id=t.id,
                pitch=69 - i * 12,
                start_sample=0,
                end_sample=rate * 304,
                velocity=0.8,
                provenance=PROV,
            )
            for i, t in enumerate(tracks)
        ]
        for i in range(9376):
            at = round(i * rate * 270 / 9376)
            notes.append(
                c.NoteEvent(
                    id=c.new_id(),
                    track_id=tracks[i % 2].id,
                    pitch=84 + i % 12,
                    start_sample=at,
                    end_sample=at + round(rate * 0.04),
                    velocity=0.3,
                    provenance=PROV,
                )
            )
        # Independent synthetic original, distinct from any user's recording.
        original = directory / "original.wav"
        with wave.open(str(original), "wb") as out:
            out.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            for _ in range(3050):
                out.writeframesraw(cycle)
        source_id = asset(
            original.read_bytes(),
            "audio/wav",
            dict(sample_rate=rate, duration_samples=rate * 305, channels=1),
        )
        s = c.Snapshot(
            sample_rate=rate,
            duration_samples=rate * 305,
            source_asset_id=source_id,
            tracks=tracks,
            plugins=plugins,
            notes=notes,
        )
        revision = invoke(
            service,
            "revisions.commit",
            dict(
                project_id=project["id"],
                snapshot=s.model_dump(mode="json"),
                author="test",
                description="Independent live session",
            ),
            project["current_revision"],
        )
        client = c.new_id()

        def opened(rev, track, seq):
            started = time.monotonic()
            live = invoke(
                service,
                "playback.live.open",
                dict(
                    project_id=project["id"],
                    revision_id=rev["id"],
                    track_id=track,
                    client_id=client,
                    client_sequence=seq,
                ),
            )
            deadline = time.monotonic() + 30
            while live["status"] == "loading" and time.monotonic() < deadline:
                time.sleep(0.01)
                live = invoke(service, "playback.live.status", dict(session_id=live["session_id"]))
            assert live["status"] == "ready", live
            return live, time.monotonic() - started

        def chunk(live, sample):
            started = time.monotonic()
            value = invoke(
                service,
                "playback.live.chunk",
                dict(
                    session_id=live["session_id"], generation=live["generation"], sample=sample, frames=8192
                ),
            )
            values = array("f", base64.b64decode(value["pcm_f32le"]))[::2]
            rms = math.sqrt(sum(v * v for v in values) / len(values))
            crossings = sum(a <= 0 < b for a, b in zip(values, values[1:]))
            return dict(
                rms=rms, hz=crossings * rate / len(values), elapsed_ms=(time.monotonic() - started) * 1000
            )

        a, load_a = opened(revision, tracks[0].id, 1)
        b, load_b = opened(revision, tracks[1].id, 1)
        before_a = chunk(a, rate * 290)
        before_b = chunk(b, rate * 290)
        assert before_a["rms"] > 0.01 and abs(before_a["hz"] - 440) < 10, before_a
        assert before_b["rms"] > 0.01 and abs(before_b["hz"] - 220) < 10, before_b
        pid = service.live_playback.sessions[a["session_id"]].owner.process.pid
        edit_started = time.monotonic()
        updated = invoke(
            service,
            "edits.apply",
            dict(
                project_id=project["id"],
                scope=dict(track_ids=[tracks[0].id], start_sample=0, end_sample=rate * 305),
                operations=[dict(op="patch_note", id=notes[0].id, pitch=81)],
                description="Live octave change",
            ),
            revision["id"],
        )
        edit_elapsed = time.monotonic() - edit_started
        new_a, update_time = opened(updated, tracks[0].id, 2)
        after_a = chunk(new_a, rate * 290)
        after_b = chunk(b, rate * 290 + 8192)
        assert (
            new_a["session_id"] == a["session_id"]
            and service.live_playback.sessions[a["session_id"]].owner.process.pid == pid
        )
        assert abs(after_a["hz"] - 880) < 15 and abs(after_b["hz"] - 220) < 10, (after_a, after_b)
        # Restoring an earlier working state is internal undo, not a saved version.
        restored = invoke(
            service,
            "revisions.restore",
            dict(
                project_id=project["id"],
                revision_id=revision["id"],
                description="Restore probe working state",
            ),
            updated["id"],
        )
        assert not invoke(service, "jobs.list", dict(project_id=project["id"]))
        assert not invoke(service, "versions.list", dict(project_id=project["id"]))
        result = dict(
            project_id=project["id"],
            revision_id=restored["id"],
            track_ids=[t.id for t in tracks],
            note_ids=[n.id for n in notes[:2]],
            cold_load_seconds=[load_a, load_b],
            note_count=len(notes),
            edit_commit_seconds=edit_elapsed,
            note_update_seconds=update_time,
            before=[before_a, before_b],
            after=[after_a, after_b],
            jobs=0,
            saved_versions=0,
        )
        (directory / "probe.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        from dataclasses import asdict

        (directory / "app.local.json").write_text(
            json.dumps(asdict(settings), default=str, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
    finally:
        if service.live_playback:
            service.live_playback.close()
        database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    run(args.directory, args.port)
