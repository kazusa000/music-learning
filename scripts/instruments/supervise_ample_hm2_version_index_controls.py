"""Durably run distinct Ample HM2 version/index controls behind shared gates.

The supervisor never changes factory sources, fixed queues, catalog entries, or
formal projects. Each version/index candidate is rendered in a fresh E-side
attempt directory; product InstDir changes are scoped by the diagnostic probe
and must be restored byte-for-byte.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = (ROOT / "data/maintenance/instrument-intake").resolve()
RUNTIME_ROOT = (ROOT / "runtime").resolve()
MAX_WAIT_SECONDS = 24 * 60 * 60
POLL_SECONDS = 20
PRODUCT_ORDER = ("AGSC", "AGLP")
CONTROL_CODE_PATHS = (
    "scripts/instruments/supervise_ample_hm2_version_index_controls.py",
    "src/music_learning/ample_retry_planning.py",
    "scripts/instruments/probe_ample_hm2_migration.py",
    "scripts/probe_staged_ample.py",
    "scripts/native_host.py",
    "scripts/instruments/supervise_ample_failed_retry.py",
    "src/music_learning/ample_presets.py",
    "src/music_learning/vst3_state.py",
    "src/music_learning/instrument_probe_budget.py",
)
PLUGIN_HOST_NAMES = {
    "instrument_probe_host.exe", "ml_audio_host.exe", "ml_track_host.exe",
    "ml_graph_runner.exe", "plugin-worker.exe",
}
EXCLUSIVE_PROBE_TOKENS = (
    "probe_ample_hm2_migration.py", "probe_ample_seq_state_controls.py",
    "probe_ample_strummer_offset_control.py", "run_ample_preview_state_ab.py",
    "probe_staged_ample.py", "instrument_intake.py",
    "supervise_ample_failed_retry.py", "supervise_ample_final_bank_controls.py",
    "supervise_ample_final_bank_riff_retry.py", "supervise_ample_phrase_duration_retest.py",
    "run_ample_factory_chain.py", "supervise_ample_hm2_version_index_controls.py",
)
LOCATOR_NAMES = {
    "ample-hm2-version-index-controls.local.json",
    "ample-hm2-version-index-controls-06.local.json",
    "ample-hm2-version-index-controls-07.local.json",
}
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from instruments.probe_ample_hm2_migration import (  # noqa: E402
    VARIANTS, _final_bank_path, _sha, _source_item, build_variants,
)
from instruments.supervise_ample_failed_retry import (  # noqa: E402
    SupervisorError, _descendants, process_inventory, supervisor_lock,
)
from music_learning.ample_retry_planning import (  # noqa: E402
    process_row_matches_ancestor_snapshot,
)
from music_learning.instrument_probe_budget import loading_budget  # noqa: E402


class HM2SupervisorError(RuntimeError):
    """Fixed-plan, process-identity, resource, or evidence error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _code_fingerprints(root: Path = ROOT) -> dict[str, str]:
    fingerprints = {}
    for relative in CONTROL_CODE_PATHS:
        path = (Path(root) / relative).resolve(strict=True)
        if not path.is_relative_to(Path(root).resolve(strict=True)) or not path.is_file():
            raise HM2SupervisorError(f"Frozen control dependency is not a regular workspace file: {relative}")
        fingerprints[relative] = _sha_file(path)
    return fingerprints


def _verify_code_fingerprints(expected: object, *, root: Path = ROOT) -> dict[str, str]:
    if not isinstance(expected, dict) or set(expected) != set(CONTROL_CODE_PATHS):
        raise HM2SupervisorError("HM2 plan is missing a complete shared-code fingerprint set")
    actual = _code_fingerprints(root)
    if expected != actual:
        changed = sorted(key for key in CONTROL_CODE_PATHS if expected.get(key) != actual[key])
        raise HM2SupervisorError("Frozen HM2 shared-code inputs changed: " + ", ".join(changed))
    return actual


def _inside_intake(path: Path, label: str, *, strict: bool = True) -> Path:
    resolved = Path(path).resolve(strict=strict)
    if not resolved.is_relative_to(INTAKE_ROOT) or resolved == INTAKE_ROOT:
        raise HM2SupervisorError(f"{label} must remain below instrument-intake")
    return resolved


def distinct_variant_groups(control: dict) -> list[dict]:
    """Group only byte-identical candidates; aliases are never counted as runs."""
    variants = control.get("variants")
    if not isinstance(variants, dict) or set(variants) != set(VARIANTS):
        raise HM2SupervisorError("HM2 control plan must contain all four named quadrants")
    groups: dict[str, list[str]] = {}
    for name in VARIANTS:
        row = variants[name]
        digest = row.get("candidate_sha256") if isinstance(row, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise HM2SupervisorError("HM2 candidate has no valid frozen SHA-256")
        groups.setdefault(digest, []).append(name)
    return [{"candidate_sha256": digest, "variants": names,
             "representative": names[0], "byte_identical_aliases": names[1:],
             "status": "pending"}
            for digest, names in groups.items()]


def _verify_plan(path: Path) -> tuple[dict, Path]:
    path = _inside_intake(path, "HM2 control plan")
    if path.is_symlink() or not path.is_file():
        raise HM2SupervisorError("HM2 control plan must be a regular file")
    plan = json.loads(path.read_bytes())
    if plan.get("schema_version") != 2 or plan.get("status") != "prepared_e_only":
        raise HM2SupervisorError("HM2 plan is not the fixed E-only prepared plan")
    if plan.get("plan_sha256") != _canonical_sha(
            {key: value for key, value in plan.items() if key != "plan_sha256"}):
        raise HM2SupervisorError("HM2 plan canonical fingerprint does not match")
    queue_path = Path(plan.get("queue_path", "")).resolve(strict=True)
    if not queue_path.is_relative_to(INTAKE_ROOT) or _sha_file(queue_path) != plan.get("queue_sha256"):
        raise HM2SupervisorError("The preserved failed-source queue changed after planning")
    controls = plan.get("controls")
    if not isinstance(controls, dict) or set(controls) != set(PRODUCT_ORDER):
        raise HM2SupervisorError("HM2 plan must contain exactly the independent SC and LP controls")
    probe_path = ROOT / "scripts/instruments/probe_ample_hm2_migration.py"
    if _sha_file(probe_path) != plan.get("diagnostic_script_sha256"):
        raise HM2SupervisorError("HM2 diagnostic implementation changed after candidate preparation")
    _verify_code_fingerprints(plan.get("code_sha256"))
    for product in PRODUCT_ORDER:
        control = controls[product]
        item_id = control.get("preset_id")
        item, source = _source_item(queue_path, item_id)
        if item.get("instrument") != product or item.get("status") != "failed":
            raise HM2SupervisorError("HM2 controls must reference an unchanged failed source item")
        if str(source) != control.get("factory_source") or _sha_file(source) != control.get("factory_source_sha256"):
            raise HM2SupervisorError("Factory source path or SHA changed after planning")
        attempt = Path(item["attempts"][-1]["output"]).resolve(strict=True)
        captured_path = attempt / "probe" / product / "evidence/first/captured-state.json"
        captured_raw = captured_path.read_bytes()
        if (_sha_file(captured_path) != control.get("captured_state_file_sha256")
                or str(captured_path) != control.get("captured_state_path")):
            raise HM2SupervisorError("Preserved captured-state evidence changed after planning")
        captured = json.loads(captured_raw)["plugin"]
        plugin_path = Path(item["plugin"]).resolve(strict=True)
        host_path = (queue_path.parent / "pinned/instrument_probe_host.exe").resolve(strict=True)
        if (str(plugin_path) != control.get("plugin_path")
                or _sha_file(plugin_path) != control.get("plugin_sha256")
                or str(host_path) != control.get("host_path")
                or _sha_file(host_path) != control.get("host_sha256")):
            raise HM2SupervisorError("Frozen plugin or host bytes changed after planning")
        if str(_final_bank_path(product)) != control.get("final_bank_path"):
            raise HM2SupervisorError("Final bank target differs from the authorized product bank")
        prepared = build_variants(captured, source.read_bytes())
        if set(prepared) != set(VARIANTS):
            raise HM2SupervisorError("Diagnostic builder did not reproduce all HM2 quadrants")
        for name, (candidate, details) in prepared.items():
            row = control["variants"][name]
            candidate_path = Path(row.get("candidate_path", "")).resolve(strict=True)
            if (not candidate_path.is_relative_to(INTAKE_ROOT)
                    or _sha(candidate) != row.get("candidate_sha256")
                    or _sha_file(candidate_path) != row.get("candidate_sha256")
                    or details != row.get("state_dimensions")):
                raise HM2SupervisorError(f"Frozen {product} {name} candidate no longer matches the source")
        distinct_variant_groups(control)
    return plan, queue_path


def _request_plugin_paths(request: dict) -> set[str]:
    found: set[str] = set()

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"path", "plugin_path"} and isinstance(child, str) and child.casefold().endswith(".vst3"):
                    found.add(str(Path(child).resolve(strict=False)).casefold())
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(request)
    return found


def _active_product_conflicts(rows: list[dict], *, product: str, plugin_path: Path,
                              ignored_pids: set[int] | None = None,
                              current_pid: int | None = None) -> list[dict]:
    """Find a live matching plugin host; unknown generic hosts fail closed."""
    ignored = ignored_pids or set()
    target = str(plugin_path.resolve(strict=False)).casefold()
    conflicts = []
    for row in rows:
        if row["pid"] in ignored:
            continue
        if (current_pid is not None
                and process_row_matches_ancestor_snapshot(row, current_pid, rows)):
            continue
        name = row.get("name", "").casefold()
        command = row.get("command_line", "")
        executable = row.get("executable_path", "")
        if any(token in command.casefold() for token in EXCLUSIVE_PROBE_TOKENS):
            conflicts.append(row)
            continue
        is_known_host = name in PLUGIN_HOST_NAMES or any(
            host in command.casefold() for host in PLUGIN_HOST_NAMES)
        if is_known_host:
            if target in command.casefold() or target in executable.casefold():
                conflicts.append(row)
                continue
            json_tokens = re.findall(r'"([^"\r\n]+\.json)"|([^\s"\r\n]+\.json)', command,
                                     flags=re.IGNORECASE)
            request_paths = [Path(left or right) for left, right in json_tokens]
            if not request_paths:
                conflicts.append(row)
                continue
            understood = False
            for request_path in request_paths:
                try:
                    resolved = request_path.resolve(strict=True)
                    if not resolved.is_relative_to(INTAKE_ROOT) or resolved.stat().st_size > 4 * 1024 * 1024:
                        continue
                    request = json.loads(resolved.read_bytes())
                    paths = _request_plugin_paths(request)
                    if paths:
                        understood = True
                        if target in paths:
                            conflicts.append(row)
                            break
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
            if not understood:
                conflicts.append(row)
            continue
        if any(token in name or token in command.casefold() or token in executable.casefold()
               for token in ("ml_audio_host", "ml_track_host", "ml_graph_runner", "plugin-worker")):
            # These engine workers may not expose a JSON request path. Do not
            # guess their plugin ownership from a generic process name.
            if target in command.casefold() or target in executable.casefold():
                conflicts.append(row)
            elif product.casefold() in command.casefold():
                conflicts.append(row)
            else:
                conflicts.append(row)
    return conflicts


def _config_instdir(config: Path) -> tuple[str, str]:
    raw = config.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise HM2SupervisorError("Refusing an InstDir XML with an entity declaration")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(raw)
    nodes = root.findall("VALUE[@name='InstDir']")
    if root.tag != "PROPERTIES" or len(nodes) != 1 or not nodes[0].get("val"):
        raise HM2SupervisorError("Product InstDir config has no unique path value")
    observed = str(Path(nodes[0].get("val")).resolve(strict=False)).casefold()
    return hashlib.sha256(raw).hexdigest(), observed


def _three_idle_samples(budget_fn, sleep_fn, poll_seconds: float, state: dict) -> bool:
    observations = []
    for index in range(3):
        try:
            value = budget_fn("H:")
        except Exception as error:
            value = {"allowed": False, "reason": "io_counters_unavailable",
                     "error": f"{type(error).__name__}: {error}"}
        if not isinstance(value, dict) or value.get("allowed") is not True:
            state["last_budget"] = {"allowed": False, "samples": observations + [value]}
            return False
        observations.append(value)
        if index < 2:
            sleep_fn(min(2, poll_seconds))
    state["last_budget"] = {"allowed": True, "samples": observations}
    return True


def _own_row(rows: list[dict]) -> dict:
    row = next((item for item in rows if item["pid"] == os.getpid()), None)
    if row is None or Path(__file__).name.casefold() not in row["command_line"].casefold():
        raise HM2SupervisorError("Cannot establish exact supervisor PID/creation/command identity")
    return row


def _write_locator(locator: Path, state: dict, own: dict) -> None:
    _atomic_json(locator, {
        "status": state.get("status"), "pid": own["pid"],
        "creation_ticks": own["created_ticks"], "command_line": own["command_line"],
        "plan": state.get("fixed_inputs", {}).get("plan_path"),
        "plan_sha256": state.get("fixed_inputs", {}).get("plan_sha256"),
        "job_dir": state.get("job_dir"), "checkpoint": str(Path(state["job_dir"]) / "checkpoint.json"),
        "current_product": state.get("current_product"),
        "active_attempt": state.get("active_attempt"), "updated_at": state.get("updated_at"),
    })


def _update(job_dir: Path, locator: Path, state: dict, own: dict, *, status: str, **fields) -> None:
    state.update(status=status, updated_at=_now(), **fields)
    _atomic_json(job_dir / "checkpoint.json", state)
    _write_locator(locator, state, own)


def _config_gate(item: dict, product: str, baseline_sha: str | None) -> tuple[bool, str | None, str | None]:
    config = Path(item["config"]).resolve(strict=True)
    if config.is_symlink() or config.name != f"{product}_inst.xml":
        raise HM2SupervisorError("Only the product's regular InstDir XML may be checked")
    digest, observed_dir = _config_instdir(config)
    expected_dir = str(_final_bank_path(product).resolve(strict=True)).casefold()
    if observed_dir != expected_dir:
        return False, digest, "InstDir is not currently mapped to the authorized final bank"
    if baseline_sha is not None and digest != baseline_sha:
        return False, digest, "InstDir XML changed after the supervisor recorded its baseline"
    return True, digest, None


def _product_item(queue_path: Path, product: str, item_id: str) -> dict:
    item, _ = _source_item(queue_path, item_id)
    if item.get("instrument") != product or item.get("status") != "failed":
        raise HM2SupervisorError("Control source is not a preserved failed item for its product")
    return item


def _child_command(plan: dict, product: str, variant: str, output: Path,
                   queue_path: Path) -> list[str]:
    control = plan["controls"][product]
    python = ROOT / "data/envs/app/Scripts/python.exe"
    script = ROOT / "scripts/instruments/probe_ample_hm2_migration.py"
    return [str(python), str(script), "--queue", str(queue_path),
            "--item-id", control["preset_id"], "--host", control["host_path"],
            "--library", control["final_bank_path"], "--output", str(output),
            "--max-wait-seconds", "15", "--variant", variant]


def _spawn_control(*, plan: dict, product: str, variant_group: dict, group_index: int,
                   output_index: int, queue_path: Path, job_dir: Path,
                   locator: Path, state: dict, own: dict, inventory_fn,
                   spawn_fn, sleep_fn, now_fn, budget_fn, poll_seconds: float,
                   deadline_epoch: float,
                   config_baseline_sha: str | None) -> tuple[dict, str | None]:
    control = plan["controls"][product]
    variant = variant_group["representative"]
    attempt = job_dir / "attempts" / product / f"{group_index:02d}-{variant}" / f"attempt-{output_index:02d}"
    attempt.mkdir(parents=True, exist_ok=False)
    log_path = attempt / "supervisor-child.log"
    command = _child_command(plan, product, variant, attempt / "probe", queue_path)
    while True:
        remaining = deadline_epoch - time.time()
        if remaining <= 0:
            return {"status": "wait_limit_reached", "output": str(attempt)}, config_baseline_sha
        if not _three_idle_samples(budget_fn, sleep_fn, poll_seconds, state):
            _update(job_dir, locator, state, own, status="waiting_for_h_idle",
                    current_product=product, current_variant=variant,
                    wait_remaining_seconds=remaining)
            sleep_fn(min(poll_seconds, remaining))
            continue
        try:
            rows = inventory_fn()
        except (OSError, SupervisorError) as error:
            _update(job_dir, locator, state, own, status="waiting_for_process_inventory",
                    current_product=product, current_variant=variant,
                    process_inventory_error=f"{type(error).__name__}: {error}",
                    wait_remaining_seconds=remaining)
            sleep_fn(min(poll_seconds, remaining))
            continue
        conflicts = _active_product_conflicts(rows, product=product,
                                              plugin_path=Path(control["plugin_path"]),
                                              ignored_pids={own["pid"]},
                                              current_pid=own["pid"])
        if conflicts:
            _update(job_dir, locator, state, own, status="waiting_for_same_product_host",
                    current_product=product, current_variant=variant,
                    conflicting_processes=[{key: row[key] for key in
                                            ("pid", "ppid", "created_ticks", "name")}
                                           for row in conflicts],
                    wait_remaining_seconds=remaining)
            sleep_fn(min(poll_seconds, remaining))
            continue
        item = _product_item(queue_path, product, control["preset_id"])
        config_ok, observed_config_sha, reason = _config_gate(item, product, config_baseline_sha)
        if not config_ok:
            _update(job_dir, locator, state, own, status="waiting_for_product_config_lease",
                    current_product=product, current_variant=variant,
                    observed_config_sha256=observed_config_sha, config_wait_reason=reason,
                    wait_remaining_seconds=remaining)
            sleep_fn(min(poll_seconds, remaining))
            continue
        if config_baseline_sha is None:
            config_baseline_sha = observed_config_sha
        # Recheck process identity, storage budget, and config immediately
        # before spawning; a busy or unknown state remains a wait, never a test.
        rows = inventory_fn()
        conflicts = _active_product_conflicts(rows, product=product,
                                              plugin_path=Path(control["plugin_path"]),
                                              ignored_pids={own["pid"]},
                                              current_pid=own["pid"])
        try:
            last_budget = budget_fn("H:")
        except Exception as error:
            last_budget = {"allowed": False,
                           "error": f"{type(error).__name__}: {error}"}
        if conflicts or not isinstance(last_budget, dict) or last_budget.get("allowed") is not True:
            _update(job_dir, locator, state, own, status="waiting_for_launch_gate",
                    current_product=product, current_variant=variant,
                    conflicting_processes=[{"pid": row["pid"], "name": row["name"]}
                                           for row in conflicts],
                    last_launch_budget=last_budget,
                    wait_remaining_seconds=remaining)
            sleep_fn(min(poll_seconds, remaining))
            continue
        config_ok, observed_config_sha, reason = _config_gate(item, product, config_baseline_sha)
        if not config_ok:
            _update(job_dir, locator, state, own, status="waiting_for_product_config_lease",
                    current_product=product, current_variant=variant,
                    observed_config_sha256=observed_config_sha, config_wait_reason=reason,
                    wait_remaining_seconds=remaining)
            sleep_fn(min(poll_seconds, remaining))
            continue
        break

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "scripts") + ";" + str(ROOT / "src")
    active = {"pid": None, "created_ticks": None, "command_line": None,
              "command": command, "product": product, "variant": variant,
              "output": str(attempt), "started_at": now_fn(),
              "candidate_sha256": variant_group["candidate_sha256"],
              "identity_status": "launch_pending"}
    state["products"][product]["config_baseline_sha256"] = config_baseline_sha
    state["active_attempt"] = active
    _update(job_dir, locator, state, own, status="launching_control_child",
            current_product=product, current_variant=variant)
    with log_path.open("ab") as log:
        try:
            child = spawn_fn(command, cwd=ROOT, env=env, stdout=log,
                             stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as error:
            state["active_attempt"] = None
            _update(job_dir, locator, state, own, status="blocked_control_spawn",
                    current_product=product, current_variant=variant,
                    spawn_error=f"{type(error).__name__}: {error}")
            return {"status": "blocked_control_spawn", "output": str(attempt)}, config_baseline_sha
        active["pid"] = int(child.pid)
        _update(job_dir, locator, state, own, status="identifying_control_child",
                current_product=product, current_variant=variant)
        started = time.monotonic()
        child_row = None
        while time.monotonic() - started < 30:
            try:
                rows = inventory_fn()
            except (OSError, SupervisorError) as error:
                active["identity_error"] = f"{type(error).__name__}: {error}"
                _update(job_dir, locator, state, own, status="waiting_for_child_identity",
                        current_product=product, current_variant=variant)
                sleep_fn(0.5)
                continue
            child_row = next((row for row in rows if row["pid"] == int(child.pid)), None)
            if child_row and str(attempt / "probe").casefold() in child_row["command_line"].casefold():
                break
            if child.poll() is not None:
                break
            sleep_fn(0.2)
        if child_row is None or str(attempt / "probe").casefold() not in child_row["command_line"].casefold():
            if child.poll() is None:
                child.wait()
            return {"status": "blocked_child_identity_unknown", "pid": int(child.pid),
                    "output": str(attempt), "command": command}, config_baseline_sha
        active.update(ppid=child_row["ppid"], created_ticks=child_row["created_ticks"],
                      command_line=child_row["command_line"], identity_status="verified")
        _update(job_dir, locator, state, own, status="running_control_child",
                current_product=product, current_variant=variant)
        while child.poll() is None:
            rows = inventory_fn()
            root_row = next((row for row in rows if row["pid"] == active["pid"]), None)
            if root_row is not None and (root_row["created_ticks"] != active["created_ticks"]
                                         or root_row["command_line"] != active["command_line"]):
                raise HM2SupervisorError("Control child PID/creation/command identity changed")
            state["observed_child_tree"] = [
                {key: row[key] for key in ("pid", "ppid", "created_ticks", "name", "command_line")}
                for row in rows if row["pid"] in _descendants(rows, active["pid"])]
            state["updated_at"] = now_fn()
            _atomic_json(job_dir / "checkpoint.json", state)
            sleep_fn(2)
        returncode = child.wait()
    deadline = time.monotonic() + 60
    while True:
        rows = inventory_fn()
        tree = [row for row in rows if row["pid"] in _descendants(rows, active["pid"])]
        if not [row for row in tree if row["pid"] != active["pid"]]:
            break
        if time.monotonic() >= deadline:
            return {"status": "blocked_orphan_child", "active_attempt": active,
                    "returncode": returncode,
                    "orphan_processes": [{"pid": row["pid"], "name": row["name"]}
                                         for row in tree]}, config_baseline_sha
        sleep_fn(1)
    report_path = attempt / "probe/report.json"
    if not report_path.is_file() or report_path.is_symlink():
        return {"status": "blocked_missing_report", "active_attempt": active,
                "returncode": returncode}, config_baseline_sha
    report_raw = report_path.read_bytes()
    report = json.loads(report_raw)
    record = report.get("variants", {}).get(variant, {})
    if report.get("status") == "waiting_for_idle" or record.get("status") == "deferred_io":
        outcome = "deferred_io"
    elif report.get("status") == "completed_diagnostic" and record.get("status") in {
            "passed_control", "failed_control"} and returncode == 0:
        outcome = record["status"]
    else:
        outcome = "failed_diagnostic"
    if _config_gate(item, product, config_baseline_sha)[0] is not True:
        return {"status": "blocked_config_not_restored", "report": str(report_path),
                "returncode": returncode}, config_baseline_sha
    active.update(returncode=returncode, status=outcome, report=str(report_path),
                  report_sha256=hashlib.sha256(report_raw).hexdigest(),
                  native_control_status=record.get("status"),
                  first=record.get("first"), reopen=record.get("reopen"))
    state["active_attempt"] = None
    state.setdefault("attempts", []).append(active)
    return active, config_baseline_sha


def classify_product_results(groups: list[dict]) -> dict:
    by_variant = {}
    for group in groups:
        if group.get("status") in {"passed_control", "failed_control"}:
            for name in group.get("variants", []):
                by_variant[name] = group["status"] == "passed_control"
    names = set(by_variant)
    if names == set(VARIANTS):
        source_version_source_indices = by_variant["source_version_source_indices"]
        current_version_source_indices = by_variant["current_version_source_indices"]
        source_version_current_indices = by_variant["source_version_current_indices"]
        current_version_current_indices = by_variant["current_version_current_indices"]
        version_effect_at_source_indices = (
            source_version_source_indices != current_version_source_indices)
        version_effect_at_current_indices = (
            source_version_current_indices != current_version_current_indices)
        index_effect_at_source_version = (
            source_version_source_indices != source_version_current_indices)
        index_effect_at_current_version = (
            current_version_source_indices != current_version_current_indices)
        version_effect = (version_effect_at_source_indices
                          and version_effect_at_current_indices)
        index_effect = (index_effect_at_source_version
                        and index_effect_at_current_version)
        interaction = (version_effect_at_source_indices != version_effect_at_current_indices
                       or index_effect_at_source_version != index_effect_at_current_version)
        outcomes = (source_version_source_indices, current_version_source_indices,
                    source_version_current_indices, current_version_current_indices)
        if all(outcomes):
            cause = "all_variants_passed"
        elif not any(outcomes):
            cause = "no_control_variant_passed"
        elif interaction:
            cause = "version_and_index_interaction"
        elif version_effect and not index_effect:
            cause = "state_version_axis"
        elif index_effect and not version_effect:
            cause = "parameter_index_axis"
        elif version_effect and index_effect:
            cause = "version_and_index_interaction"
        else:
            cause = "not_explained_by_version_or_index_alone"
        return {"status": "classified", "candidate_shared_cause": cause,
                "version_axis": {
                    "source_version_source_indices": source_version_source_indices,
                    "current_version_source_indices": current_version_source_indices,
                    "source_version_current_indices": source_version_current_indices,
                    "current_version_current_indices": current_version_current_indices,
                    "effect_at_source_indices": version_effect_at_source_indices,
                    "effect_at_current_indices": version_effect_at_current_indices,
                },
                "index_axis": {
                    "source_version_source_indices": source_version_source_indices,
                    "current_version_source_indices": current_version_source_indices,
                    "source_version_current_indices": source_version_current_indices,
                    "current_version_current_indices": current_version_current_indices,
                    "effect_at_source_version": index_effect_at_source_version,
                    "effect_at_current_version": index_effect_at_current_version,
                }}
    if {"source_version_source_indices", "current_version_source_indices"} <= names:
        old = by_variant["source_version_source_indices"]
        new = by_variant["current_version_source_indices"]
        cause = "state_version_axis" if old != new else "not_explained_by_version_or_index_alone"
        return {"status": "classified", "candidate_shared_cause": cause,
                "version_axis": {"source_indices": old, "current_indices": new},
                "index_axis": "source and current indices are byte-identical for this factory state"}
    return {"status": "incomplete", "candidate_shared_cause": None,
            "observed_variants": sorted(names)}


def _remaining_seconds(state: dict, now_epoch: float | None = None) -> float:
    now_epoch = time.time() if now_epoch is None else now_epoch
    start = float(state["started_epoch"])
    limit = float(state["max_wait_seconds"])
    return max(0.0, start + limit - now_epoch)


def _record_report_outcome(active: dict, plan: dict, queue_path: Path,
                           config_baseline_sha: str | None) -> dict:
    attempt = _inside_intake(Path(active["output"]), "Recovered HM2 attempt")
    report_path = attempt / "probe/report.json"
    if not report_path.is_file() or report_path.is_symlink():
        return {"status": "blocked_missing_report", "active_attempt": active}
    report_raw = report_path.read_bytes()
    report = json.loads(report_raw)
    product = active["product"]
    item = _product_item(queue_path, product, plan["controls"][product]["preset_id"])
    record = report.get("variants", {}).get(active["variant"], {})
    if report.get("status") == "waiting_for_idle" or record.get("status") == "deferred_io":
        outcome = "deferred_io"
    elif report.get("status") == "completed_diagnostic" and record.get("status") in {
            "passed_control", "failed_control"}:
        outcome = record["status"]
    else:
        return {"status": "blocked_unclassified_report", "active_attempt": active,
                "report": str(report_path), "report_status": report.get("status"),
                "variant_status": record.get("status")}
    config_ok, observed_sha, reason = _config_gate(item, product, config_baseline_sha)
    if not config_ok:
        return {"status": "blocked_config_not_restored", "active_attempt": active,
                "report": str(report_path), "observed_config_sha256": observed_sha,
                "reason": reason}
    active.update(returncode=(0 if report.get("status") == "completed_diagnostic" else None),
                  returncode_recovered=True, status=outcome, report=str(report_path),
                  report_sha256=hashlib.sha256(report_raw).hexdigest(),
                  native_control_status=record.get("status"),
                  first=record.get("first"), reopen=record.get("reopen"))
    return active


def _recover_finished_attempt(*, state: dict, plan: dict, queue_path: Path,
                              job_dir: Path, locator: Path, own: dict,
                              inventory_fn, now_fn) -> dict | None:
    active = state.get("active_attempt")
    if active is None:
        return None
    rows = inventory_fn()
    pid = active.get("pid")
    token = str(Path(active["output"]) / "probe").casefold()
    if pid is not None:
        same_pid = next((row for row in rows if row["pid"] == int(pid)), None)
        if same_pid is not None:
            if (active.get("created_ticks") is None
                    or same_pid["created_ticks"] != active["created_ticks"]
                    or (active.get("command_line")
                        and same_pid["command_line"] != active["command_line"])):
                return {"status": "blocked_child_pid_reused", "active_attempt": active,
                        "observed_process": same_pid}
            return {"status": "blocked_active_child_still_running", "active_attempt": active,
                    "observed_process": {key: same_pid[key] for key in
                                         ("pid", "created_ticks", "command_line")}}
    matching_commands = [row for row in rows if token in row.get("command_line", "").casefold()]
    if matching_commands:
        return {"status": "blocked_untracked_child_still_running", "active_attempt": active,
                "observed_processes": [{key: row[key] for key in
                                         ("pid", "created_ticks", "command_line")}
                                        for row in matching_commands]}
    if pid is not None:
        descendant_ids = _descendants(rows, int(pid))
        descendants = [row for row in rows if row["pid"] != int(pid)
                       and row["pid"] in descendant_ids]
        if descendants:
            return {"status": "blocked_orphan_child_still_running", "active_attempt": active,
                    "observed_processes": [{key: row[key] for key in
                                             ("pid", "created_ticks", "command_line")}
                                            for row in descendants]}
    result = _record_report_outcome(active, plan, queue_path,
                                    state["products"][active["product"]].get(
                                        "config_baseline_sha256"))
    if result.get("status", "").startswith("blocked_"):
        return result
    product_state = state["products"][active["product"]]
    group = next((row for row in product_state["groups"]
                  if row["candidate_sha256"] == active["candidate_sha256"]), None)
    if group is None or group.get("status") in {"passed_control", "failed_control"}:
        return {"status": "blocked_checkpoint_group_mismatch", "active_attempt": active}
    result["output"] = active["output"]
    result["candidate_sha256"] = active["candidate_sha256"]
    group.setdefault("attempts", []).append(result)
    if result["status"] in {"passed_control", "failed_control"}:
        group["status"] = result["status"]
    else:
        group["status"] = "pending"
    state["active_attempt"] = None
    _update(job_dir, locator, state, own,
            status="recovered_completed_attempt" if result["status"] != "deferred_io"
            else "recovered_deferred_attempt",
            current_product=active["product"], current_variant=active["variant"],
            last_attempt=result)
    return None


def _fixed_inputs(plan_path: Path, plan: dict, queue_path: Path,
                  groups_by_product: dict[str, list[dict]]) -> dict:
    return {
        "plan_path": str(Path(plan_path).resolve(strict=True)),
        "plan_sha256": plan["plan_sha256"], "queue_path": str(queue_path),
        "queue_sha256": plan["queue_sha256"],
        "diagnostic_script_sha256": _sha_file(ROOT / "scripts/instruments/probe_ample_hm2_migration.py"),
        "code_sha256": _code_fingerprints(),
        "supervisor_script_sha256": _sha_file(Path(__file__).resolve(strict=True)),
        "controls": {product: {"preset_id": plan["controls"][product]["preset_id"],
                               "source_sha256": plan["controls"][product]["factory_source_sha256"],
                               "plugin_sha256": plan["controls"][product]["plugin_sha256"],
                               "host_sha256": plan["controls"][product]["host_sha256"],
                               "candidate_sha256s": [group["candidate_sha256"]
                                                      for group in groups_by_product[product]]}
                    for product in PRODUCT_ORDER},
    }


def _supervise_locked(*, plan_path: Path, output: Path, locator: Path,
                      max_wait_seconds: float, poll_seconds: float, resume: bool,
                      budget_fn, inventory_fn, spawn_fn, sleep_fn, now_fn) -> dict:
    plan, queue_path = _verify_plan(plan_path)
    output = _inside_intake(output, "HM2 supervisor output", strict=False)
    if not 60 <= max_wait_seconds <= MAX_WAIT_SECONDS or not 10 <= poll_seconds <= 60:
        raise HM2SupervisorError("HM2 wait limits are outside the bounded maintenance range")
    groups_by_product = {product: distinct_variant_groups(plan["controls"][product])
                         for product in PRODUCT_ORDER}
    fixed = _fixed_inputs(plan_path, plan, queue_path, groups_by_product)
    own = _own_row(inventory_fn())

    if resume:
        if not output.is_dir() or output.is_symlink():
            raise HM2SupervisorError("Cannot resume a missing or linked HM2 supervisor directory")
        saved_plan = output / "plan.json"
        checkpoint = output / "checkpoint.json"
        saved_value = json.loads(saved_plan.read_bytes()) if saved_plan.is_file() else None
        if (not isinstance(saved_value, dict)
                or saved_value.get("plan_sha256") != plan.get("plan_sha256")
                or _canonical_sha({key: value for key, value in saved_value.items()
                                   if key != "plan_sha256"}) != plan["plan_sha256"]):
            raise HM2SupervisorError("The saved supervisor plan differs from the selected fixed plan")
        if not checkpoint.is_file() or checkpoint.is_symlink():
            raise HM2SupervisorError("Cannot resume without the regular durable checkpoint")
        state = json.loads(checkpoint.read_bytes())
        if (state.get("schema_version") != 1 or state.get("job_dir") != str(output)
                or state.get("fixed_inputs") != fixed):
            raise HM2SupervisorError("Durable checkpoint no longer matches frozen source/host/code inputs")
        if state.get("status") in {"completed_diagnostic", "failed_diagnostic"}:
            raise HM2SupervisorError("A terminal HM2 diagnostic job cannot be resumed")
    else:
        if output.exists():
            raise HM2SupervisorError("HM2 supervisor output must be a new independent directory")
        output.mkdir(parents=True, exist_ok=False)
        state = {"schema_version": 1, "status": "starting", "started_at": now_fn(),
                 "started_epoch": time.time(), "max_wait_seconds": max_wait_seconds,
                 "job_dir": str(output), "fixed_inputs": fixed,
                 "products": {product: {"status": "pending", "groups": groups_by_product[product],
                                        "config_baseline_sha256": None}
                              for product in PRODUCT_ORDER},
                 "attempts": [], "active_attempt": None,
                 "formal_project_writes": 0, "catalog_writes": 0,
                 "sample_library_writes": 0, "nas_access": False}
        (output / "plan.json").write_bytes(Path(plan_path).read_bytes())

    locator = Path(locator).resolve(strict=False)
    if locator.parent != RUNTIME_ROOT or locator.name not in LOCATOR_NAMES:
        raise HM2SupervisorError("Use the dedicated runtime HM2 controls locator")
    if resume and state.get("fixed_inputs", {}).get("plan_path") != str(Path(plan_path).resolve(strict=True)):
        raise HM2SupervisorError("Resume plan path changed")
    product_locks = {product: output / "config-leases" / f"{product}.lock"
                     for product in PRODUCT_ORDER}
    with supervisor_lock(output / "supervisor.lock"):
        _update(output, locator, state, own, status="resuming" if resume else "waiting_for_h_idle",
                max_wait_seconds=state["max_wait_seconds"], current_product=PRODUCT_ORDER[0])
        if resume and state.get("active_attempt") is not None:
            recovered = _recover_finished_attempt(
                state=state, plan=plan, queue_path=queue_path, job_dir=output,
                locator=locator, own=own, inventory_fn=inventory_fn, now_fn=now_fn)
            if recovered:
                _update(output, locator, state, own, status=recovered["status"],
                        recovery=recovered)
                return state
        for product in PRODUCT_ORDER:
            product_state = state["products"][product]
            if product_state.get("status") == "completed_diagnostic":
                continue
            if product_state.get("status") == "blocked":
                _update(output, locator, state, own, status="blocked_checkpoint_state",
                        current_product=product)
                return state
            config_baseline = product_state.get("config_baseline_sha256")
            with supervisor_lock(product_locks[product]):
                groups = product_state["groups"]
                for group_index, group in enumerate(groups, 1):
                    if group.get("status") in {"passed_control", "failed_control"}:
                        continue
                    if group.get("status") not in {"pending", "deferred_io"}:
                        _update(output, locator, state, own, status="blocked_checkpoint_group_state",
                                current_product=product, current_variant=group.get("representative"),
                                group_status=group.get("status"))
                        return state
                    attempt_number = len(group.get("attempts", []))
                    while True:
                        remaining = _remaining_seconds(state)
                        if remaining <= 0:
                            _update(output, locator, state, own, status="wait_limit_reached",
                                    current_product=product, current_variant=group["representative"])
                            return state
                        attempt_number += 1
                        # Re-verify all immutable inputs at every fresh launch;
                        # neither code drift nor a queue edit becomes a retry.
                        current_plan, current_queue = _verify_plan(plan_path)
                        if (current_plan["plan_sha256"] != state["fixed_inputs"]["plan_sha256"]
                                or current_queue != queue_path
                                or _fixed_inputs(plan_path, current_plan, current_queue,
                                                 groups_by_product) != fixed):
                            raise HM2SupervisorError("Frozen HM2 inputs changed while waiting")
                        attempt, config_baseline = _spawn_control(
                            plan=plan, product=product, variant_group=group,
                            group_index=group_index, output_index=attempt_number,
                            queue_path=queue_path, job_dir=output, locator=locator,
                            state=state, own=own, inventory_fn=inventory_fn,
                            spawn_fn=spawn_fn, sleep_fn=sleep_fn, now_fn=now_fn,
                            budget_fn=budget_fn, poll_seconds=poll_seconds,
                            deadline_epoch=float(state["started_epoch"])
                            + float(state["max_wait_seconds"]),
                            config_baseline_sha=config_baseline)
                        product_state["config_baseline_sha256"] = config_baseline
                        if attempt.get("status") == "deferred_io":
                            group["status"] = "deferred_io"
                            group.setdefault("attempts", []).append(attempt)
                            _update(output, locator, state, own, status="waiting_for_h_idle",
                                    current_product=product, current_variant=group["representative"],
                                    wait_remaining_seconds=_remaining_seconds(state))
                            group["status"] = "pending"
                            sleep_fn(min(poll_seconds, _remaining_seconds(state)))
                            continue
                        if attempt.get("status") in {"passed_control", "failed_control"}:
                            group["status"] = attempt["status"]
                            group.setdefault("attempts", []).append(attempt)
                            _update(output, locator, state, own, status="control_completed",
                                    current_product=product, current_variant=group["representative"],
                                    last_attempt=attempt)
                            break
                        if attempt.get("status") == "wait_limit_reached":
                            group["status"] = "pending"
                            _update(output, locator, state, own, status="wait_limit_reached",
                                    current_product=product, current_variant=group["representative"],
                                    wait_remaining_seconds=0)
                            return state
                        group["status"] = attempt.get("status", "blocked")
                        group.setdefault("attempts", []).append(attempt)
                        product_state["status"] = "blocked"
                        _update(output, locator, state, own, status="blocked_control_attempt",
                                current_product=product, current_variant=group["representative"],
                                last_attempt=attempt)
                        return state
            product_state["classification"] = classify_product_results(product_state["groups"])
            product_state["status"] = ("completed_diagnostic" if all(
                group.get("status") in {"passed_control", "failed_control"}
                for group in product_state["groups"]) else "incomplete")
            _update(output, locator, state, own,
                    status="product_diagnostic_completed" if product_state["status"] == "completed_diagnostic"
                    else "product_diagnostic_incomplete", current_product=product)
        state["status"] = "completed_diagnostic" if all(
            row["status"] == "completed_diagnostic" for row in state["products"].values()) else "incomplete"
        _update(output, locator, state, own, status=state["status"], completed_at=now_fn())
    return state


def _supervise(*, plan_path: Path, output: Path, locator: Path,
               max_wait_seconds: float, poll_seconds: float, resume: bool = False,
               budget_fn=loading_budget, inventory_fn=process_inventory,
               spawn_fn=subprocess.Popen, sleep_fn=time.sleep,
               now_fn=_now) -> dict:
    locator = Path(locator).resolve(strict=False)
    if locator.parent != RUNTIME_ROOT or locator.name not in LOCATOR_NAMES:
        raise HM2SupervisorError("Use the dedicated runtime HM2 controls locator")
    lock_path = locator.with_suffix(locator.suffix + ".lock")
    with supervisor_lock(lock_path):
        if locator.is_file():
            try:
                previous = json.loads(locator.read_bytes())
            except (OSError, ValueError, json.JSONDecodeError):
                raise HM2SupervisorError("Existing HM2 locator is unreadable; refusing a second run")
            previous_dir = previous.get("job_dir")
            if previous_dir and str(Path(previous_dir).resolve(strict=False)) != str(
                    Path(output).resolve(strict=False)):
                previous_checkpoint = Path(previous_dir) / "checkpoint.json"
                previous_state = None
                if previous_checkpoint.is_file():
                    try:
                        previous_state = json.loads(previous_checkpoint.read_bytes())
                    except (OSError, ValueError, json.JSONDecodeError):
                        previous_state = None
                if not previous_state or previous_state.get("status") not in {
                        "completed_diagnostic", "failed_diagnostic"}:
                    raise HM2SupervisorError("A different nonterminal HM2 supervisor job already owns the locator")
        return _supervise_locked(
            plan_path=plan_path, output=output, locator=locator,
            max_wait_seconds=max_wait_seconds, poll_seconds=poll_seconds, resume=resume,
            budget_fn=budget_fn, inventory_fn=inventory_fn, spawn_fn=spawn_fn,
            sleep_fn=sleep_fn, now_fn=now_fn)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--locator", type=Path, default=ROOT / "runtime/ample-hm2-version-index-controls.local.json")
    parser.add_argument("--max-wait-seconds", type=float, default=MAX_WAIT_SECONDS)
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    parser.add_argument("--resume", action="store_true",
                        help="Resume a waiting checkpoint after verifying all frozen inputs")
    args = parser.parse_args()
    try:
        result = _supervise(plan_path=args.plan, output=args.output, locator=args.locator,
                            max_wait_seconds=args.max_wait_seconds, poll_seconds=args.poll_seconds,
                            resume=args.resume)
    except (OSError, ValueError, HM2SupervisorError, SupervisorError) as error:
        print(json.dumps({"status": "blocked_preflight", "error": f"{type(error).__name__}: {error}"},
                         ensure_ascii=False))
        return 2
    print(json.dumps({"status": result.get("status"), "job_dir": result.get("job_dir"),
                      "products": {key: value.get("status") for key, value in
                                   result.get("products", {}).items()}}, ensure_ascii=False))
    return 0 if result.get("status") == "completed_diagnostic" else 1


if __name__ == "__main__":
    raise SystemExit(main())
