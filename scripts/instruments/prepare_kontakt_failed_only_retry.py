"""Prepare a terminal-only Kontakt failed-subset retry or check its launch gates.

This is a one-shot E maintenance entry point, not a supervisor and not a retry
launcher.  ``prepare`` writes a new immutable failed-only plan only after the
preserved 992-item full-h job is terminal.  ``check-start-gates`` validates
fresh externally collected process/profile/storage evidence and never starts a
Kontakt process.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
from typing import Mapping


ROOT_HINT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_HINT / "src"))
from music_learning.ample_paths import workspace_root  # noqa: E402

ROOT = workspace_root(__file__)
INTAKE_ROOT = ROOT / "data/maintenance/instrument-intake"
FULL_H_RUN = ROOT / "data/maintenance/instrument-intake/kf22/full-h"
PINNED_CALLBACK_FILES = (
    "scripts/instruments/run_kontakt_full_intake.py",
    "src/music_learning/ample_paths.py",
    "src/music_learning/instrument_intake_publication.py",
    "src/music_learning/kontakt_import.py",
    "src/music_learning/kontakt_validation.py",
)


def _sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _regular_json(path: Path) -> dict:
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024 * 1024:
        raise ValueError("Kontakt retry input must be a regular file: " + str(path))
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Kontakt retry JSON must be an object: " + str(path))
    return value


def _load_pinned_full_runner(code_root: Path):
    """Load the runner from its exact frozen path, never from ambient sys.path."""
    runner_path = (Path(code_root) / "scripts/instruments/run_kontakt_full_intake.py").resolve(strict=True)
    expected_root = Path(code_root).resolve(strict=True)
    if not runner_path.is_relative_to(expected_root):
        raise ValueError("Pinned full-runner path escaped the frozen code root")
    spec = importlib.util.spec_from_file_location("_kontakt_pinned_full_runner", runner_path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load the pinned Kontakt full-runner module")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def _fixed_full_run(path: Path | None) -> Path:
    selected = FULL_H_RUN if path is None else Path(path).absolute()
    if selected != FULL_H_RUN:
        raise ValueError("Kontakt failed-only recovery is bound to the preserved full-h run")
    return selected


def _load_pinned_callback_modules(full_run: Path) -> tuple[object, object, object, dict]:
    plan_path = full_run / "plan.json"
    plan = _regular_json(plan_path)
    if (Path(plan.get("run_root", "")).absolute() != full_run
            or Path(plan.get("repo_root", "")).resolve() != ROOT.resolve()
            or Path(plan.get("code_root", "")).absolute() != full_run / "code"
            or Path(plan.get("pins_path", "")).absolute() != full_run / "pins.json"):
        raise ValueError("Frozen full-h plan paths do not match the fixed workspace/run roots")
    pins_path = full_run / "pins.json"
    if _sha256(pins_path) != plan.get("pins_sha256"):
        raise ValueError("Frozen full-h code pins changed")
    pins = _regular_json(pins_path)
    pinned_files = pins.get("files")
    if not isinstance(pinned_files, dict):
        raise ValueError("Frozen full-h pin manifest is malformed")

    code_root = full_run / "code"
    callback_hashes = {}
    for relative in PINNED_CALLBACK_FILES:
        callback_path = code_root / Path(relative)
        actual = _sha256(callback_path)
        expected = pinned_files.get(str(callback_path))
        if not isinstance(expected, str) or actual != expected:
            raise ValueError("Frozen Kontakt callback source differs from its full-h pin: " + relative)
        callback_hashes[relative] = actual

    # The verifier modules must resolve from the immutable full-h code snapshot.
    # Clear only imports made above for workspace-root discovery, then load the
    # pinned package first.  The current failed-only planner is loaded explicitly
    # from the checkout so the E-only tool can be upgraded without editing full-h.
    for name in tuple(sys.modules):
        if name == "music_learning" or name.startswith("music_learning."):
            del sys.modules[name]
    for entry in (str(code_root / "src"), str(code_root / "scripts")):
        if entry in sys.path:
            sys.path.remove(entry)
    sys.path[:0] = [str(code_root / "src"), str(code_root / "scripts")]
    package = importlib.import_module("music_learning")

    planner_path = ROOT / "src/music_learning/kontakt_failed_only_retry.py"
    qualified_name = "music_learning.kontakt_failed_only_retry"
    spec = importlib.util.spec_from_file_location(qualified_name, planner_path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load the current E-only Kontakt retry planner")
    planner = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = planner
    spec.loader.exec_module(planner)

    # Loading by name is unsafe here: the workspace's scripts/instruments can
    # already be on sys.path and shadow this frozen release copy.
    runner = _load_pinned_full_runner(code_root)
    publication = importlib.import_module("music_learning.instrument_intake_publication")
    validation = importlib.import_module("music_learning.kontakt_validation")
    if Path(runner.__file__).resolve() != (code_root / "scripts/instruments/run_kontakt_full_intake.py").resolve():
        raise ValueError("Kontakt retry did not import the pinned full-runner callback module")
    if package.__file__ is None or Path(package.__file__).resolve().parent != (code_root / "src/music_learning").resolve():
        raise ValueError("Kontakt retry did not import the pinned music_learning package")
    return planner, runner, (publication, validation), callback_hashes


def build_reverification_callbacks(*, runner, verify_receipt, identity_verifier,
                                   validator_pin, plan: Mapping, load_json, sha256_file):
    """Adapt the full runner's original conversion and receipt verifiers.

    ``verify_receipt`` remains the authority for actual first/reopen audio,
    complete native state, identity, and resources.  This adapter only converts
    that result into the semantic/raw digest proof required by the retry planner.
    """
    pin = validator_pin(
        name="kontakt-full-validator",
        version="1",
        code_path=Path(plan["pins_path"]),
        host_path=Path(plan["probe"]),
    )
    resource_root = Path(plan["resource_root"])

    def verify_conversion(library_id: str, item: Mapping, library_root: Path) -> dict:
        if library_id not in {"css", "css-solo", "kontakt-factory"}:
            raise ValueError("Unsupported Kontakt conversion library")
        return runner.reverify_conversion(item, library_root)

    def verify_passed(library_id: str, item: Mapping, conversion_proof: Mapping,
                      library_root: Path) -> dict:
        if library_id not in {"css", "css-solo", "kontakt-factory"}:
            raise ValueError("Unsupported Kontakt validation library")
        if conversion_proof.get("status") != "converted_pending_validation":
            raise ValueError("Passed Kontakt entry lacks a reverified source conversion")
        owned = runner.resume_evidence(item, library_root)
        candidate_path = library_root / owned["candidate"]
        receipt_path = library_root / owned["receipt"]
        evidence_root = library_root / owned["evidence_root"]
        candidate = load_json(candidate_path)
        receipt = load_json(receipt_path)
        result = verify_receipt(candidate, receipt, evidence_root, identity_verifier, pin,
                                resource_root=resource_root)
        first = result["first"]
        reopened = result["reopen"]
        return {
            "status": "verified",
            "candidate_sha256": result["candidate_sha256"],
            "candidate_file_sha256": sha256_file(candidate_path),
            "receipt_sha256": result["receipt_sha256"],
            "receipt_file_sha256": sha256_file(receipt_path),
            "first_audio_sha256": first["probe_sha256"],
            "reopen_audio_sha256": reopened["probe_sha256"],
            "first_state_sha256": first["state_sha256"],
            "reopen_state_sha256": reopened["state_sha256"],
        }

    return verify_conversion, verify_passed


def _prepare(full_run: Path, output: Path) -> dict:
    progress = _regular_json(full_run / "progress.json")
    if (progress.get("status") not in {
            "validation_finished_pending_publication", "published_pending_preview_handoff",
            "published_and_previews_dispatched"}
            or progress.get("current") is not None):
        raise ValueError("Full-h remains nonterminal; no failed-only plan was written")

    planner, runner, modules, callback_hashes = _load_pinned_callback_modules(full_run)
    publication, validation = modules
    verify_conversion, verify_passed = build_reverification_callbacks(
        runner=runner,
        verify_receipt=publication.verify_receipt,
        identity_verifier=validation.verifier(),
        validator_pin=publication.ValidatorPin,
        plan=_regular_json(full_run / "plan.json"),
        load_json=runner.load,
        sha256_file=publication.sha256_file,
    )
    result = planner.prepare_failed_only_retry_plan(
        full_run, output,
        verify_conversion=verify_conversion,
        verify_passed=verify_passed,
        full_run_root=full_run,
        recovery_toolchain={
            "schema_version": 1,
            "cli_sha256": _sha256(Path(__file__)),
            "planner_sha256": _sha256(ROOT / "src/music_learning/kontakt_failed_only_retry.py"),
            "pinned_callback_sources": callback_hashes,
        },
    )
    return {**result, "callback_sources": callback_hashes}


def _check_start_gates(full_run: Path, retry_run: Path, evidence_path: Path) -> dict:
    planner, _, _, _ = _load_pinned_callback_modules(full_run)
    evidence = _regular_json(evidence_path)
    required = {"live_source_processes", "live_kontakt_processes", "profile_evidence", "storage_observations"}
    if set(evidence) != required:
        raise ValueError("Start-gate evidence must contain exactly the process, profile, and storage observations")
    plan = _regular_json(retry_run / "plan.json")
    planner.verify_retry_start_gates(
        plan,
        live_source_processes=evidence["live_source_processes"],
        live_kontakt_processes=evidence["live_kontakt_processes"],
        profile_evidence=evidence["profile_evidence"],
        storage_observations=evidence["storage_observations"],
        full_run_root=full_run,
    )
    return {"status": "gates_passed_no_launch", "retry_run": str(retry_run), "started": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="Prepare a new terminal-only failed subset plan")
    prepare.add_argument("--full-run", type=Path, default=FULL_H_RUN)
    prepare.add_argument("--output", type=Path, required=True)
    gates = subparsers.add_parser("check-start-gates", help="Validate evidence; never launches the retry")
    gates.add_argument("--full-run", type=Path, default=FULL_H_RUN)
    gates.add_argument("--retry-run", type=Path, required=True)
    gates.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        full_run = _fixed_full_run(args.full_run)
        if args.command == "prepare":
            output = Path(args.output).absolute()
            if output.parent != full_run.parent or output.exists():
                raise ValueError("Retry output must be a new sibling of the preserved full-h run")
            result = _prepare(full_run, output)
        else:
            retry_run = Path(args.retry_run).absolute()
            evidence_path = Path(args.evidence).absolute()
            if (retry_run.parent != full_run.parent or retry_run == full_run
                    or not evidence_path.is_relative_to(INTAKE_ROOT)):
                raise ValueError("Retry plan/evidence paths are outside the allowed maintenance scope")
            result = _check_start_gates(full_run, retry_run, evidence_path)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "rejected", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
