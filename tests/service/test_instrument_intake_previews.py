import json

import pytest

from music_learning.instrument_intake_previews import enqueue_published_previews
from music_learning.instrument_intake_publication import json_fingerprint


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
