import json

import pytest

from music_learning.instrument_intake_previews import enqueue_published_previews
from music_learning.instrument_intake_publication import json_fingerprint
from music_learning.instrument_presentation_amendments import apply_plan, build_plan


def fixture(tmp_path):
    additions = [dict(id=pid, audio_verified=True, model={"id": model})
                 for pid, model in [("a-original", "a"), ("a-bright", "a"), ("b-original", "b")]]
    plan = dict(additions=additions, preview_outbox=[
        {"preset_id": x["id"], "operation": "instruments.preview.prepare"} for x in additions])
    plan["plan_sha256"] = json_fingerprint(plan)
    bank = tmp_path / "library"
    bank.mkdir()
    catalog = bank / "catalog.json"
    catalog.write_text(json.dumps(dict(presets=additions)))
    return plan, catalog, tmp_path / "maintenance" / "preview-outbox.json"


def test_representatives_first_and_resume_does_not_requeue(tmp_path):
    plan, catalog, checkpoint = fixture(tmp_path)
    calls = []

    def prepare(pid, **kwargs):
        calls.append(pid)
        assert kwargs["retry"] is False
        return dict(preset_id=pid, status="queued")

    before = catalog.read_bytes()
    report = enqueue_published_previews(plan, catalog, checkpoint, prepare)
    assert calls == ["a-original", "b-original", "a-bright"]
    assert report["preview_status_counts"]["ready"] == 0
    assert report["preview_status_counts"]["queued"] == 3
    enqueue_published_previews(plan, catalog, checkpoint, prepare)
    assert len(calls) == 3
    assert before == catalog.read_bytes()


def test_interrupt_reuses_same_idempotency_key(tmp_path):
    plan, catalog, checkpoint = fixture(tmp_path)
    attempted = []

    def interrupt(pid, **kwargs):
        attempted.append((pid, kwargs["idempotency_key"]))
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        enqueue_published_previews(plan, catalog, checkpoint, interrupt)

    def resume(pid, **kwargs):
        attempted.append((pid, kwargs["idempotency_key"]))
        return dict(preset_id=pid, status="ready")

    report = enqueue_published_previews(plan, catalog, checkpoint, resume)
    assert attempted[0] == attempted[1]
    assert report["submitted"] == 3


def test_api_errors_do_not_loop_or_retry_failed_audio(tmp_path):
    plan, catalog, checkpoint = fixture(tmp_path)
    calls = []

    def prepare(pid, **kwargs):
        calls.append(pid)
        if pid == "a-bright":
            raise RuntimeError("offline")
        return dict(preset_id=pid, status="failed")

    report = enqueue_published_previews(plan, catalog, checkpoint, prepare)
    assert report["errors"] == 1
    assert report["preview_status_counts"]["failed"] == 2
    enqueue_published_previews(plan, catalog, checkpoint, prepare)
    assert len(calls) == 3
    enqueue_published_previews(plan, catalog, checkpoint, prepare, retry_errors=True)
    assert calls[-1] == "a-bright" and len(calls) == 4


def test_unpublished_or_changed_entry_never_calls_preview_api(tmp_path):
    plan, catalog, checkpoint = fixture(tmp_path)
    catalog.write_text(json.dumps(dict(presets=plan["additions"][:-1])))
    with pytest.raises(ValueError, match="not been published"):
        enqueue_published_previews(plan, catalog, checkpoint,
                                   lambda *_args, **_kwargs: pytest.fail("unexpected preview"))
    assert not checkpoint.exists()


def presentation_fixture(tmp_path):
    plan, catalog, checkpoint = fixture(tmp_path)
    for entry in plan["additions"]:
        entry.update(source_factory_sha256="a" * 64,
                     expected_identity={"name": entry["id"], "database": "bank"},
                     native_admission={"state_sha256": "c" * 64})
    plan["plan_sha256"] = json_fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"})
    original = json.dumps(dict(schema_version=1, presets=plan["additions"])).encode()
    amendment = build_plan(original, [dict(preset_id="a-original",
                                          source_relative="Presets/Old School Solo.preset",
                                          zh="老派独奏")], evidence_sha256="b" * 64)
    catalog.write_bytes(apply_plan(original, amendment))
    return plan, catalog, checkpoint


def test_reviewed_source_title_after_publication_preserves_preview_handoff_identity(tmp_path):
    plan, catalog, checkpoint = presentation_fixture(tmp_path)
    calls = []
    before = catalog.read_bytes()
    plan_before = json_fingerprint(plan)

    def prepare(pid, **kwargs):
        calls.append((pid, kwargs["idempotency_key"]))
        return dict(preset_id=pid, status="queued")

    result = enqueue_published_previews(plan, catalog, checkpoint, prepare)
    assert result["submitted"] == 3
    assert calls[0] == ("a-original", "intake-preview-" + json_fingerprint(
        {"plan": plan["plan_sha256"], "preset": "a-original"}))
    enqueue_published_previews(plan, catalog, checkpoint, prepare)
    assert len(calls) == 3
    assert catalog.read_bytes() == before
    assert json_fingerprint(plan) == plan_before


@pytest.mark.parametrize("mutation", ["state", "source", "audio", "unreviewed-title"])
def test_title_overlay_cannot_hide_changed_or_unreviewed_admission(tmp_path, mutation):
    plan, catalog, checkpoint = presentation_fixture(tmp_path)
    changed = json.loads(catalog.read_bytes())
    entry = changed["presets"][0]
    if mutation == "state":
        entry["native_admission"]["state_sha256"] = "d" * 64
    elif mutation == "source":
        entry["source_factory_sha256"] = "e" * 64
    elif mutation == "audio":
        entry["audio_verified"] = False
    else:
        entry["presentation_title"]["title"] = "Invented title"
    catalog.write_text(json.dumps(changed))
    with pytest.raises(ValueError):
        enqueue_published_previews(plan, catalog, checkpoint,
                                   lambda *_args, **_kwargs: pytest.fail("unexpected preview"))
    assert not checkpoint.exists()
