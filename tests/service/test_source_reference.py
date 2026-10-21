import copy
import subprocess
import sys
import pytest
from music_learning import contracts as c
from music_learning.source_reference import build
from music_learning.vocal_decision import decide
from music_learning.retention import snapshot_assets
from music_learning.worker import Worker
from music_learning.service import DomainError
from conftest import invoke


def prepared(service, project, upload):
    invoke(
        service,
        "jobs.submit",
        dict(project_id=project["id"], kind="asset.import", payload={"upload_id": upload["upload_id"]}),
        project["current_revision"],
    )
    Worker(service.database).run_once()
    rev = invoke(service, "revisions.get", dict(project_id=project["id"]))
    snapshot = c.Snapshot.model_validate(rev["snapshot"])
    with service.database.transaction() as db:
        wave = service.storage.put(b'{"levels":[]}')
        service.register_asset(
            db, project["id"], wave, 13, "application/json", {}, dict(origin="user", source_id="test")
        )
        original = snapshot.source_asset_id
        snapshot.source_reference = build(
            namespace=rev["id"],
            source=original,
            frames=snapshot.duration_samples,
            run_id=c.new_id(),
            decision="vocal",
            audio=[("vocals", "人声", original, wave), ("accompaniment", "伴奏", original, wave)],
            melody=[dict(sample=0, duration_samples=22050, note=60, velocity=0.8)],
            annotations=[dict(start_sample=0, end_sample=44100, kind="chord", text="C:maj")],
            raw_assets=[wave],
        )
        tid = c.new_id()
        snapshot.tracks = [
            c.Track(
                id=tid, name="改编钢琴", kind="midi", provenance=c.Provenance(origin="user", source_id="test")
            )
        ]
        snapshot.notes = [
            c.NoteEvent(
                id=c.new_id(),
                track_id=tid,
                pitch=67,
                start_sample=0,
                end_sample=22050,
                velocity=0.7,
                provenance=c.Provenance(origin="user", source_id="test"),
            )
        ]
        return service.commit(db, project["id"], rev["id"], snapshot, "test", "reference fixture")


def test_original_and_current_midi_are_separate_query_views(service, project, upload):
    rev = prepared(service, project, upload)
    args = dict(project_id=project["id"], action="events")
    original = invoke(service, "music.query", dict(**args, view="original"))
    arranged = invoke(service, "music.query", args)
    assert original["evidence"]["items"][0]["pitch"] == 60
    assert arranged["evidence"]["items"][0]["pitch"] == 67
    assert original["citations"][0]["id"] != arranged["citations"][0]["id"]
    summary = invoke(service, "music.query", dict(project_id=project["id"]))
    assert summary["evidence"]["original_reference"]["annotations"][0]["text"] == "C:maj"
    assert rev["snapshot"]["plugins"] == []


def test_reference_correction_is_versioned_without_changing_arranged_midi(service, project, upload):
    rev = prepared(service, project, upload)
    invoke(
        service,
        "audition.set",
        dict(
            project_id=project["id"],
            state=dict(revision_id=rev["id"], mode="original", position_sample=100, playing=False),
        ),
        rev["id"],
    )
    before = invoke(service, "jobs.list", dict(project_id=project["id"]))
    labels = copy.deepcopy(rev["snapshot"]["source_reference"]["annotations"])
    labels[0]["text"] = "Am7"
    next = invoke(service, "reference.update", dict(project_id=project["id"], annotations=labels), rev["id"])
    cue = invoke(service, "audition.get", dict(project_id=project["id"]))["desired"]
    assert cue["revision_id"] == next["id"] and cue["mode"] == "original" and cue["position_sample"] == 100
    assert next["snapshot"]["notes"] == rev["snapshot"]["notes"]
    assert next["snapshot"]["source_reference"]["annotations"][0]["text"] == "Am7"
    assert (
        invoke(service, "reference.get", dict(project_id=project["id"], revision_id=rev["id"]))[
            "annotations"
        ][0]["text"]
        == "C:maj"
    )
    assert invoke(service, "jobs.list", dict(project_id=project["id"])) == before
    with pytest.raises(DomainError, match="Expected revision"):
        invoke(service, "reference.update", dict(project_id=project["id"], annotations=labels), rev["id"])


@pytest.mark.parametrize("change", ["source", "missing_wave", "plugin_track", "outside_note", "audio_offset"])
def test_reference_rejects_invalid_identity_and_audio_layout(service, project, upload, change):
    rev = prepared(service, project, upload)
    raw = copy.deepcopy(rev["snapshot"])
    ref = raw["source_reference"]
    if change == "source":
        ref["source_asset_id"] = "f" * 64
    elif change == "missing_wave":
        ref["waveforms"] = {}
    elif change == "plugin_track":
        ref["tracks"][1]["kind"] = "instrument"
    elif change == "outside_note":
        ref["notes"][0]["end_sample"] = 44101
    else:
        ref["clips"][0]["source_offset_samples"] = 1
    with pytest.raises(ValueError):
        c.Snapshot.model_validate(raw)


def test_reference_resources_are_retained(service, project, upload):
    rev = prepared(service, project, upload)
    required = snapshot_assets(rev["snapshot"])
    assert set(rev["snapshot"]["source_reference"]["waveforms"].values()) <= required
    for sha in required:
        assert service.storage.verify(sha).is_file()


def test_instrumental_has_exactly_one_audio_track_and_no_melody():
    ref = build(
        namespace=c.new_id(),
        source="a" * 64,
        frames=100,
        run_id=c.new_id(),
        decision="instrumental",
        audio=[("original", "原曲", "a" * 64, "b" * 64)],
        melody=[],
        annotations=[],
        raw_assets=[],
    )
    snap = c.Snapshot(source_asset_id="a" * 64, duration_samples=100, source_reference=ref)
    assert len(snap.source_reference.tracks) == 1
    assert snap.source_reference.notes == []


@pytest.mark.parametrize("mode,expected", [("instrumental", False), ("vocal", True), ("auto", True)])
def test_explicit_instrumental_skips_even_with_vocal_predictions(mode, expected):
    notes = [dict(sample=i * 100, duration_samples=100) for i in range(10)]
    result = decide(mode, notes, 1000, 100)
    assert result["has_vocals"] == expected
    assert result["separation_skipped"] != expected


def test_empty_auto_vocals_skips():
    assert decide("auto", [], 44100, 44100)["separation_skipped"]


def test_reference_conversion_does_not_import_database_packages():
    # Model workers intentionally have no database driver or SQLAlchemy installed.
    code = """
import builtins
original = builtins.__import__
def guarded(name,*args,**kwargs):
    if name.startswith('sqlalchemy'):
        raise ModuleNotFoundError('Database packages forbidden in model worker')
    return original(name,*args,**kwargs)
builtins.__import__ = guarded
from music_learning.source_reference import build
from music_learning import contracts as c
ref=build(namespace=c.new_id(),source='a'*64,frames=100,run_id=c.new_id(),decision='instrumental',
          audio=[('original','source','a'*64,'b'*64)],melody=[],annotations=[],raw_assets=[])
c.Snapshot(source_asset_id='a'*64,duration_samples=100,source_reference=ref)
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cas_audio_alias_actually_plays_in_native_host(service, wav, tmp_path):
    from music_learning.audio_materialization import wave_alias
    from music_learning.settings import ROOT

    sys.path.insert(0, str(ROOT / "scripts"))
    from native_host import NativeHost

    sha = service.storage.put(wav.read_bytes())
    target = wave_alias(service.storage, sha, tmp_path / "materialized")
    assert target.suffix == ".wav" and target.read_bytes() == wav.read_bytes()
    result = NativeHost(service.settings.native_host, tmp_path / "native").call(
        dict(
            schema_version=1,
            action="render",
            output_path=str(tmp_path / "played.wav"),
            project=dict(
                revision_id="alias-probe",
                sample_rate=44100,
                block_size=256,
                duration_samples=44100,
                tracks=[
                    dict(id="vocal", gain_db=0, plugins=[], audio_clips=[dict(path=str(target), sample=0)])
                ],
            ),
        )
    )
    assert result["frames"] == 44100
    assert result["peak"] > 0.2


def test_original_discussion_focus_is_not_an_arrangement_permission_limit(service, project, upload):
    rev = prepared(service, project, upload)
    tid = rev["snapshot"]["source_reference"]["tracks"][1]["id"]
    turn = invoke(
        service,
        "dialogue.submit",
        dict(
            project_id=project["id"], workspace="original", reference_track_id=tid, text="这里的旋律是什么？"
        ),
        rev["id"],
    )
    assert turn["context"]["workspace"] == "original"
    assert turn["context"]["selected_reference_track"]["id"] == tid
    assert turn["context"]["track_id"] is None
    assert turn["context"]["edit_scope"]["track_ids"] == [rev["snapshot"]["tracks"][0]["id"]]


@pytest.mark.parametrize("workspace", ["original", "arrangement"])
def test_discussion_rejects_unknown_reference_track(service, project, upload, workspace):
    rev = prepared(service, project, upload)
    with pytest.raises(DomainError, match="原曲声部"):
        invoke(
            service,
            "dialogue.submit",
            dict(
                project_id=project["id"],
                workspace=workspace,
                reference_track_id=c.new_id(),
                text="这里是什么？",
            ),
            rev["id"],
        )
