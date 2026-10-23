import json

import pytest

from instruments.publish_ample_preflight import wait_for_storage


def test_busy_storage_persists_progress_then_continues_once_idle(tmp_path):
    observations = iter([{"allowed": False, "reason": "io_pressure"}, {"allowed": True}])
    slept = []
    assert wait_for_storage(tmp_path, wait=True, budget_fn=lambda *a, **kw: next(observations),
                            clock=lambda: 0, sleep=slept.append) == {"allowed": True}
    assert slept == [20]
    assert json.loads((tmp_path / "progress.json").read_bytes())["catalog_written"] is False
    assert json.loads((tmp_path / "budget.json").read_bytes())["allowed"] is True


@pytest.mark.parametrize("wait", [False, True])
def test_denied_storage_wait_is_bounded_and_writes_no_bank_artifact(tmp_path, wait):
    ticks = iter([0, 1, 1])
    with pytest.raises(TimeoutError):
        wait_for_storage(tmp_path, wait=wait, max_seconds=1, budget_fn=lambda *a, **kw: {"allowed": False},
                         clock=lambda: next(ticks), sleep=lambda _: pytest.fail("deadline already reached"))
    assert {path.name for path in tmp_path.iterdir()} == {"budget.json", "progress.json"}
