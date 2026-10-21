import pytest

from music_learning.service import DomainError
from conftest import invoke
from test_source_reference import prepared


def test_original_engine_uses_two_audio_tracks_and_no_melody_instrument(service, project, upload):
    revision = prepared(service, project, upload)
    before = invoke(service, "jobs.list", {"project_id": project["id"]})
    try:
        state = invoke(service, "engine.open", dict(project_id=project["id"], view="original"))
        reference = revision["snapshot"]["source_reference"]
        audio = {track["id"] for track in reference["tracks"] if track["kind"] == "audio"}
        assert set(state["monitor"]) == audio
        assert not state["playing"]
        assert state["revision_id"] == revision["id"]
        changed = invoke(service, "engine.monitor", dict(engine_session=state["engine_session"],
            generation=state["generation"], tracks={next(iter(audio)): dict(gain_db=-50, solo=True)}))
        assert changed["generation"] == state["generation"]
        assert invoke(service, "jobs.list", {"project_id": project["id"]}) == before
        assert invoke(service, "revisions.get", {"project_id": project["id"]}) == revision
        with pytest.raises(DomainError, match="会话已变更"):
            invoke(service, "engine.transport", dict(engine_session="f"*48,generation=state["generation"],playing=True))
    finally:
        invoke(service, "engine.close")


def test_unrendered_midi_never_falls_back_to_stale_audio(service, project, upload):
    revision = prepared(service, project, upload)
    with pytest.raises(DomainError, match="实时插件"):
        invoke(service, "engine.open", dict(project_id=project["id"], revision_id=revision["id"], view="arrangement"))
    assert invoke(service, "engine.status")["status"] == "stopped"
