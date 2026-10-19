import pytest
from conftest import invoke
from music_learning.service import DomainError


def test_library_archive_title_readback_idempotency_and_music_head_unchanged(service, project):
    before = invoke(service, "revisions.get", {"project_id": project["id"]})
    args = dict(project_id=project["id"], archived=True, display_title="Song")
    result = invoke(service, "projects.organize", args, project["current_revision"], "organize-once")
    assert invoke(service, "projects.organize", args, project["current_revision"], "organize-once") == result
    assert not invoke(service, "projects.list")
    assert invoke(service, "projects.list", {"include_archived": True}) == [result]
    assert invoke(service, "projects.get", {"project_id": project["id"]}) == result
    assert invoke(service, "revisions.get", {"project_id": project["id"]}) == before
    with pytest.raises(DomainError, match="Expected revision"):
        invoke(service, "projects.organize", args, "0" * 32)
    restored = invoke(service, "projects.organize", dict(args, archived=False), project["current_revision"])
    assert invoke(service, "projects.list") == [restored]
