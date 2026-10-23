import pytest

from scripts.instruments import supervise_ample_failed_retry as supervisor


def process(pid, parent, born, command="owned child"):
    return {"pid": pid, "ppid": parent, "created_ticks": born,
            "command_line": command, "name": "python.exe", "executable_path": "python.exe"}


def test_reused_parent_pid_does_not_adopt_a_process_older_than_its_parent():
    rows = [process(10, 1, 1000), process(11, 10, 1100),
            process(12, 11, 200, "unrelated old tail"), process(13, 12, 1200)]
    assert supervisor._descendants(rows, 10) == {10, 11}


def test_birth_order_is_checked_at_every_link_not_only_the_root():
    rows = [process(10, 1, 1000), process(11, 10, 1200),
            process(12, 11, 1100), process(13, 12, 1300)]
    assert supervisor._descendants(rows, 10) == {10, 11}


def test_missing_root_is_not_a_parent_pid_placeholder():
    assert supervisor._descendants([process(11, 10, 1100)], 10) == set()


def test_duplicate_pid_observation_is_rejected():
    with pytest.raises(supervisor.SupervisorError, match="duplicate"):
        supervisor._descendants([process(10, 1, 1000), process(10, 2, 2000)], 10)


def test_checkpoint_survivors_preserve_real_orphans_and_exclude_prebirth_processes():
    root = process(10, 1, 1000, "verified runner")
    old = process(20, 10, 200, "unrelated old tail")
    child = process(30, 10, 1100, "owned native host")
    reused = process(40, 10, 1200, "old exited host")
    observed = {str(row["pid"]): row for row in [root, old, child, reused]}
    rows = [old, child, process(40, 8, 5000, "unrelated reused PID")]
    assert supervisor.verified_observed_survivors(root, observed, rows) == [child]


def test_same_process_with_changed_command_is_not_silently_dropped():
    root = process(10, 1, 1000, "verified runner")
    child = process(30, 10, 1100, "owned native host")
    with pytest.raises(supervisor.SupervisorError, match="command"):
        supervisor.verified_observed_survivors(
            root, {"30": child}, [process(30, 10, 1100, "changed command")])


@pytest.mark.parametrize("born", [None, True, -1])
def test_unknown_birth_time_is_not_used_to_discard_a_possible_owned_process(born):
    root = process(10, 1, 1000, "verified runner")
    child = process(30, 10, born, "owned native host")
    with pytest.raises(supervisor.SupervisorError, match="creation"):
        supervisor.verified_observed_survivors(root, {"30": child}, [])
