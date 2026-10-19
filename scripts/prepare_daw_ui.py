"""Independent UI fixture and real sfizz render; never uses user music or a provider."""

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import struct
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from music_learning import contracts as c  # noqa: E402
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import ROOT, Settings  # noqa: E402
from music_learning.storage import digest  # noqa: E402


def prepare(output):
    output.mkdir(exist_ok=False, parents=True)
    bank = output / "library/tone"
    bank.mkdir(parents=True)
    with wave.open(str(bank / "tone.wav"), "wb") as stream:
        stream.setparams((1, 2, 44100, 44100, "NONE", "not compressed"))
        stream.writeframes(
            b"".join(
                struct.pack("<h", int(6000 * math.sin(i * 2 * math.pi * 440 / 44100))) for i in range(44100)
            )
        )
    sfz = bank / "tone.sfz"
    sfz.write_text(
        "<region> sample=tone.wav pitch_keycenter=69 ampeg_attack=0.01 ampeg_release=0.1", encoding="utf-8"
    )
    plugin = ROOT / "plugins/sfizz-1.2.3/commoncf/VST3/sfizz.vst3"
    binary = next((plugin / "Contents/x86_64-win").glob("*.vst3"))
    entry = dict(
        id="ui-tone",
        name="UI test tone",
        bank_root="tone",
        backend="sfz",
        entrypoint="tone.sfz",
        family="synth-keys",
        instrument_ids=["synth-keys"],
        key_range=[0, 127],
        gm_programs=[],
        usage="Synthetic test fixture, not a piano sample.",
        audio_verified=False,
        default=False,
        tags=[],
        plugin_path=str(plugin),
        identifier="VST3-sfizz-fa6eb9e3-5da1643a",
        plugin_sha256=digest(binary),
        resources={p.name: dict(sha256=digest(p), bytes=p.stat().st_size) for p in (bank / "tone.wav", sfz)},
    )
    (output / "library/catalog.json").write_text(
        json.dumps(dict(schema_version=1, presets=[entry])), encoding="utf-8"
    )
    with wave.open(str(output / "source.wav"), "wb") as stream:
        stream.setparams((2, 2, 44100, 44100 * 12, "NONE", "not compressed"))
        stream.writeframes(
            b"".join(
                struct.pack("<hh", int(1000 * math.sin(i * 2 * math.pi * 220 / 44100)), 0)
                for i in range(44100 * 12)
            )
        )
    settings = asdict(
        Settings(
            data_root=output / "app-data",
            port=8767,
            plugin_roots=[ROOT / "plugins"],
            instrument_library=output / "library",
        )
    )
    (output / "app.local.json").write_text(json.dumps(settings, default=str), encoding="utf-8")
    print(json.dumps(dict(config=str(output / "app.local.json"), port=8767)))


def populate(output):
    settings = Settings.load(output / "app.local.json")
    with Client(
        f"http://127.0.0.1:{settings.port}", token_file=settings.path("session-token.local")
    ) as client:
        project = client.call("projects.create", dict(title="FL 风格界面 · 独立交互验证", kind="composition"))
        imported = client.wait(
            client.import_audio(
                output / "source.wav", project_id=project["id"], expected_revision=project["current_revision"]
            )["id"]
        )
        assert imported["status"] == "succeeded", imported
        revision = client.call("revisions.get", dict(project_id=project["id"]))
        capture = client.wait(
            client.capture_instrument(project["id"], "ui-tone", expected_revision=revision["id"])["id"]
        )
        assert capture["status"] == "succeeded", capture
        snapshot = revision["snapshot"]
        melody, harmony, audio, clip = [c.new_id() for _ in range(4)]
        prov = dict(origin="rule", source_id="independent-ui-fixture", review="unreviewed")
        snapshot["tracks"] = [
            dict(id=id, name=name, kind=kind, gain_db=-12, provenance=prov)
            for id, name, kind in [
                (melody, "主旋律", "instrument"),
                (harmony, "和声", "instrument"),
                (audio, "音频素材", "audio"),
            ]
        ]
        snapshot["track_order"] = [melody, harmony, audio]
        snapshot["routing"] = dict(
            id=c.new_id(),
            nodes=[melody, harmony, audio, "0" * 32],
            edges=[
                dict(source=track, target="0" * 32, kind="main", gain_db=0)
                for track in [melody, harmony, audio]
            ],
        )
        snapshot["timing_map"] = dict(
            id=c.new_id(),
            sample_rate=44100,
            anchors=[
                dict(sample=0, beat=dict(n=0, d=1)),
                dict(sample=snapshot["duration_samples"], beat=dict(n=24, d=1)),
            ],
            meters=[dict(beat=dict(n=0, d=1), bar=1, numerator=4, denominator=4)],
            provenance=prov,
        )
        snapshot["notes"] = [
            dict(
                id=c.new_id(),
                track_id=track,
                clip_id=clip if track == melody else None,
                pitch=pitch,
                start_sample=int(start * 44100),
                end_sample=int(end * 44100),
                velocity=0.75,
                provenance=prov,
            )
            for track, pitch, start, end in [
                (melody, 60, 2, 2.5),
                (melody, 64, 3, 3.5),
                (melody, 67, 4, 5),
                (harmony, 48, 0.5, 1.5),
                (harmony, 55, 5, 6),
            ]
        ]
        snapshot["clips"] = [
            dict(
                id=clip, track_id=melody, start_sample=44100 * 2, duration_samples=44100 * 4, provenance=prov
            ),
            dict(
                id=c.new_id(),
                track_id=audio,
                asset_id=snapshot["source_asset_id"],
                start_sample=44100 * 7,
                duration_samples=44100 * 3,
                source_offset_samples=44100,
                gain_db=-6,
                provenance=prov,
            ),
        ]
        snapshot["notes"][1].update(
            timing_mode="beat",
            timing_map_id=snapshot["timing_map"]["id"],
            notation_beat=dict(n=6, d=1),
            notation_duration=dict(n=1, d=1),
        )
        snapshot["plugins"] = [
            dict(capture["result"]["plugin"], id=c.new_id(), track_id=track, order=0)
            for track in [melody, harmony]
        ]
        seeded = client.call(
            "revisions.commit",
            dict(
                project_id=project["id"],
                snapshot=snapshot,
                author="fixture",
                description="Independent piano, MIDI clip and audio track fixture",
            ),
            expected_revision=revision["id"],
        )
        render = client.wait(client.render(project["id"], expected_revision=seeded["id"])["id"])
        assert render["status"] == "succeeded", render
        report = dict(
            project_id=project["id"],
            seed_revision=seeded["id"],
            melody=melody,
            harmony=harmony,
            audio=audio,
            midi_clip=clip,
            note_ids=[n["id"] for n in snapshot["notes"]],
            render_job_id=render["id"],
            source_sha256=snapshot["source_asset_id"],
            config_path=str(output / "app.local.json"),
            status="succeeded",
            provider_calls=0,
        )
        (output / "fixture.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(dict(status="succeeded", project_id=project["id"], provider_calls=0)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "populate"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    (prepare if args.action == "prepare" else populate)(args.output.resolve())
