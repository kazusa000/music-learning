"""Prepare and launch one terminal-only Kontakt failed-subset execution.

This one-shot E maintenance CLI builds a failed-only plan after the preserved
992-item full-h job is terminal, freezes a new complete-coverage run over that
selection, and launches it once after fresh process/profile/storage gates pass.
The frozen runner keeps unselected entries pending and never publishes or
dispatches previews in this recovery path.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid
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
        script_path=full_run / "code/src/music_learning/kontakt_failed_only_retry.py",
    )
    return {"status": "gates_passed_no_launch", "retry_run": str(retry_run), "started": False}


def _atomic_json(path: Path, value: Mapping) -> str:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    temporary = path.with_name(path.name + ".stage-" + uuid.uuid4().hex)
    with temporary.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return hashlib.sha256(raw).hexdigest()


def _copy_exclusive(source: Path, destination: Path) -> str:
    raw = Path(source).read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def _failed_only_release_contract(source_release: Path) -> dict:
    """Check the immutable code release can seed and run a selected-only retry.

    This rejects an otherwise valid but stale application release before the
    shared full-job preparer creates a new run directory.
    """
    runtime = (ROOT / "runtime").resolve(strict=True)
    descriptor_input = Path(source_release).absolute()
    if not descriptor_input.is_relative_to(runtime):
        raise ValueError("Failed-only source release must be an immutable runtime release")
    from music_learning.kontakt_import import _resolve_under

    descriptor = _resolve_under(runtime, descriptor_input.relative_to(runtime).as_posix(), must_exist=True)
    record = _regular_json(descriptor)
    release_runtime_path = ROOT / "scripts/release_runtime.py"
    spec = importlib.util.spec_from_file_location("_kontakt_failed_only_release_runtime", release_runtime_path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load immutable release verification")
    release_runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release_runtime)
    source_root = Path(release_runtime.verify_source(ROOT, record)).resolve(strict=True)
    manifest = _regular_json(source_root / "release-source.json")
    if record.get("commit") != manifest.get("commit") or source_root.name != record.get("commit"):
        raise ValueError("Failed-only source release commit does not match its immutable manifest")

    source_resume_path = source_root / "src/music_learning/kontakt_source_resume.py"
    runner_path = source_root / "scripts/instruments/run_kontakt_full_intake.py"
    metadata_path = source_root / "src/music_learning/kontakt_catalog_metadata.py"
    source_resume_raw = source_resume_path.read_bytes()
    runner_raw = runner_path.read_bytes()
    metadata_raw = metadata_path.read_bytes()
    release_files = manifest.get("files")
    expected_release_files = {
        "src/music_learning/kontakt_source_resume.py": hashlib.sha256(source_resume_raw).hexdigest(),
        "scripts/instruments/run_kontakt_full_intake.py": hashlib.sha256(runner_raw).hexdigest(),
        "src/music_learning/kontakt_catalog_metadata.py": hashlib.sha256(metadata_raw).hexdigest(),
    }
    if not isinstance(release_files, dict) or any(
        release_files.get(relative) != expected
        for relative, expected in expected_release_files.items()
    ):
        raise ValueError("Failed-only runner/seeder/legato rules are not pinned by the immutable source manifest")
    seeder_ast = ast.parse(source_resume_raw, filename=str(source_resume_path))
    seed_function = next(
        (node for node in seeder_ast.body
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "seed_full_job"),
        None,
    )
    if seed_function is None:
        raise ValueError("Immutable source release lacks the shared Kontakt source seeder")
    seed_args = {arg.arg for arg in (*seed_function.args.posonlyargs, *seed_function.args.args,
                                     *seed_function.args.kwonlyargs)}
    runner_text, metadata_text = runner_raw.decode("utf-8"), metadata_raw.decode("utf-8")
    if (not {"failed_only_plan", "failed_only_queue"}.issubset(seed_args)
            or "def _failed_only_selection(" not in runner_text
            or 'save(status="failed_only_retry_finished"' not in runner_text
            or "legato-overlap-v1" not in metadata_text):
        raise ValueError("Immutable source release lacks the failed-only runner/seeder or shared legato rule")
    return {
        "commit": record["commit"],
        "descriptor_sha256": _sha256(descriptor),
        "source_resume_sha256": hashlib.sha256(source_resume_raw).hexdigest(),
        "runner_sha256": hashlib.sha256(runner_raw).hexdigest(),
        "metadata_sha256": hashlib.sha256(metadata_raw).hexdigest(),
    }


def _prepare_execution(full_run: Path, retry_run: Path, output: Path, source_release: Path) -> dict:
    """Prepare, seed, and pin a nonpublishing full-run job over the exact failed IDs."""
    full_run = _fixed_full_run(full_run)
    retry_run = Path(retry_run).absolute()
    output = Path(output).absolute()
    if (retry_run.parent != full_run.parent or retry_run == full_run or output.parent != full_run.parent
            or output == full_run or output == retry_run or output.exists()):
        raise ValueError("Failed-only execution must be a new sibling of the preserved full-h run")
    retry_plan_path = retry_run / "plan.json"
    retry_queue_path = retry_run / "retry-queue.json"
    retry_plan = _regular_json(retry_plan_path)
    retry_queue = _regular_json(retry_queue_path)
    retry_plan_sha, retry_queue_sha = _sha256(retry_plan_path), _sha256(retry_queue_path)
    if (retry_plan.get("retry_run") != str(retry_run)
            or (retry_run / "plan.sha256").read_text(encoding="ascii").strip() != retry_plan_sha
            or (retry_run / "retry-queue.sha256").read_text(encoding="ascii").strip() != retry_queue_sha):
        raise ValueError("Terminal failed-only plan/queue does not match its immutable digest sidecars")

    from music_learning.kontakt_failed_only_retry import validate_failed_only_selection

    selected = validate_failed_only_selection(retry_plan, retry_queue)
    source_plan = _regular_json(full_run / "plan.json")
    source_snapshot = retry_plan.get("source_snapshot", {})
    # Inventory overrides are the already reviewed full-h inventory pins. Verify
    # them before asking the shared preparer to create another complete-coverage plan.
    inventory_overrides = {}
    for library_id, details in source_plan.get("libraries", {}).items():
        path = Path(details.get("inventory_path", ""))
        pin = source_snapshot.get("source_artifact_pins", {}).get(f"{library_id}/inventory")
        if (not isinstance(pin, dict) or pin.get("path") is None
                or _sha256(path) != pin.get("sha256")):
            raise ValueError("Frozen full-h inventory changed after failed-only planning: " + library_id)
        inventory_overrides[library_id] = path
    if set(inventory_overrides) != {"css", "css-solo", "kontakt-factory"}:
        raise ValueError("Failed-only run needs the complete three-library inventory set")
    release_contract = _failed_only_release_contract(source_release)
    prep_dirs = {Path(source_plan["runtime_result"]).parent.resolve(strict=True)}
    prep_dirs.update(Path(row["result"]).parent.resolve(strict=True)
                     for row in source_plan["libraries"].values())
    if len(prep_dirs) != 1:
        raise ValueError("Full-h runtime/library preparation results do not share one frozen audit root")

    helper_path = ROOT / "scripts/instruments/prepare_kontakt_full_job.py"
    spec = importlib.util.spec_from_file_location("_kontakt_retry_full_job_preparer", helper_path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load the shared Kontakt full-job preparer")
    prepare_job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare_job)
    prepared = prepare_job.prepare(output, prep_dirs.pop(), start=False, publish=False,
                                   preview_handoff=False, inventory_overrides=inventory_overrides,
                                   source_release=source_release)
    run = Path(prepared["run_root"]).resolve(strict=True)
    plan_path = run / "plan.json"
    plan = _regular_json(plan_path)
    if plan.get("publish") is not False or plan.get("preview_handoff") is not False:
        raise ValueError("Failed-only execution plan unexpectedly enables publication or previews")
    prepared_release = plan.get("source_release")
    if (not isinstance(prepared_release, dict)
            or prepared_release.get("commit") != release_contract["commit"]
            or prepared_release.get("descriptor_sha256") != release_contract["descriptor_sha256"]):
        raise ValueError("Prepared failed-only job did not freeze the preflighted immutable release")

    retry_dir = run / "failed-only"
    retry_dir.mkdir(exist_ok=False)
    frozen_retry_plan = retry_dir / "retry-plan.json"
    frozen_retry_queue = retry_dir / "retry-queue.json"
    retry_plan_sha = _copy_exclusive(retry_plan_path, frozen_retry_plan)
    retry_queue_sha = _copy_exclusive(retry_queue_path, frozen_retry_queue)
    selection_sha = hashlib.sha256(json.dumps(
        selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    execution = {
        "schema_version": 1,
        "source_run": str(full_run),
        "source_release_contract": release_contract,
        "retry_plan_origin": str(retry_run),
        "retry_plan_path": frozen_retry_plan.relative_to(run).as_posix(),
        "retry_plan_sha256": retry_plan_sha,
        "retry_queue_path": frozen_retry_queue.relative_to(run).as_posix(),
        "retry_queue_sha256": retry_queue_sha,
        "selected_ids": [item["id"] for item in selected],
        "selected_count": len(selected),
        "selection_sha256": selection_sha,
    }
    pins = _regular_json(run / "pins.json")
    if not isinstance(pins.get("files"), dict):
        raise ValueError("Prepared Kontakt execution pin manifest is malformed")
    pins["files"][str(frozen_retry_plan)] = retry_plan_sha
    pins["files"][str(frozen_retry_queue)] = retry_queue_sha
    pins_sha = _atomic_json(run / "pins.json", pins)
    plan.update(failed_only_execution=execution, pins_sha256=pins_sha,
                publish=False, preview_handoff=False)
    plan_sha = _atomic_json(plan_path, plan)

    # Seeding is an E-only copy from already reverified original conversions.
    # It never starts Kontakt; first/reopen identity and audio validation remain
    # the normal frozen runner's responsibility.
    code_root = run / "code"
    source_resume_path = code_root / "src/music_learning/kontakt_source_resume.py"
    frozen_runner_path = code_root / "scripts/instruments/run_kontakt_full_intake.py"
    frozen_metadata_path = code_root / "src/music_learning/kontakt_catalog_metadata.py"
    if ({
            "source_resume_sha256": _sha256(source_resume_path),
            "runner_sha256": _sha256(frozen_runner_path),
            "metadata_sha256": _sha256(frozen_metadata_path),
        } != {key: release_contract[key] for key in
              ("source_resume_sha256", "runner_sha256", "metadata_sha256")}):
        raise ValueError("Frozen failed-only execution code differs from its preflighted source release")
    job_pins = _regular_json(run / "pins.json")
    if job_pins.get("files", {}).get(str(source_resume_path)) != _sha256(source_resume_path):
        raise ValueError("Frozen failed-only source seeder is not included in its immutable release pins")
    for name in tuple(sys.modules):
        if name == "music_learning" or name.startswith("music_learning."):
            del sys.modules[name]
    for entry in (str(code_root / "src"), str(code_root / "scripts")):
        if entry in sys.path:
            sys.path.remove(entry)
    sys.path[:0] = [str(code_root / "src"), str(code_root / "scripts")]
    package = importlib.import_module("music_learning")
    source_resume = importlib.import_module("music_learning.kontakt_source_resume")
    metadata_module = importlib.import_module("music_learning.kontakt_catalog_metadata")
    if (Path(package.__file__).resolve().parent != (code_root / "src/music_learning").resolve()
            or Path(source_resume.__file__).resolve() != source_resume_path.resolve()):
        raise ValueError("Failed-only seeder did not import from the immutable execution release")
    for item in selected:
        metadata = metadata_module.kontakt_preset_metadata(item["library_id"], item["source"]["path"])
        if (re.search(r"(?<![A-Za-z])legato(?![A-Za-z])", str(metadata.get("variant_name", "")), re.IGNORECASE)
                and metadata.get("audition_pattern") != "legato-overlap-v1"):
            raise ValueError("Frozen release lacks the shared legato-overlap audition rule")
    seeded = source_resume.seed_full_job(full_run, run, failed_only_plan=retry_plan, failed_only_queue=retry_queue)
    resume_map_path = run / "source-resume-map.json"
    resume_map = _regular_json(resume_map_path)
    if (resume_map.get("destination_plan_sha256") != plan_sha
            or resume_map.get("retry_selection_sha256") != selection_sha
            or set(resume_map.get("selected_ids", [])) != set(execution["selected_ids"])):
        raise ValueError("E-only conversion seed is not bound to the frozen failed-only execution plan")
    launch_path = run / "launch.json"
    launch = _regular_json(launch_path)
    launch.update(status="prepared_failed_only", plan_sha256=plan_sha,
                  failed_only_seed=seeded, source_resume_map_sha256=_sha256(resume_map_path))
    _atomic_json(launch_path, launch)
    return {"status": "prepared_failed_only", "run_root": str(run), "plan_sha256": plan_sha,
            "source_coverage_count": 992, "selected_count": len(selected),
            "selected_ids": execution["selected_ids"], "reused_conversion_count": seeded["reused_count"],
            "pending_source_attempt_count": seeded["pending_count"], "started": False,
            "publish": False, "preview_handoff": False}


def _start_execution(full_run: Path, retry_run: Path, execution_run: Path, evidence_path: Path) -> dict:
    """Recheck all fresh gates, pin them, then start one frozen selected-only worker."""
    full_run = _fixed_full_run(full_run)
    execution_run = Path(execution_run).absolute()
    retry_run = Path(retry_run).absolute()
    if (execution_run.parent != full_run.parent or retry_run.parent != full_run.parent
            or execution_run == full_run or retry_run == full_run):
        raise ValueError("Failed-only execution paths are outside the fixed intake sibling scope")
    plan_path, pins_path, launch_path = (execution_run / name for name in ("plan.json", "pins.json", "launch.json"))
    plan, pins, launch = _regular_json(plan_path), _regular_json(pins_path), _regular_json(launch_path)
    plan_sha = _sha256(plan_path)
    if (launch.get("status") != "prepared_failed_only" or launch.get("plan_sha256") != plan_sha
            or plan.get("run_root") != str(execution_run) or plan.get("publish") is not False
            or plan.get("preview_handoff") is not False or _sha256(pins_path) != plan.get("pins_sha256")):
        raise ValueError("Failed-only execution is not an unchanged, unstarted prepared run")
    if Path(plan.get("failed_only_execution", {}).get("source_run", "")) != full_run:
        raise ValueError("Prepared execution no longer targets the fixed full-h source")
    if Path(plan.get("failed_only_execution", {}).get("retry_plan_path", "" )).is_absolute():
        # The plan stores a run-relative path; this catches path-shape mutation.
        raise ValueError("Failed-only plan path is not run-relative")
    config = plan["failed_only_execution"]
    if (config.get("retry_plan_origin") != str(retry_run)
            or _sha256(retry_run / "plan.json") != config.get("retry_plan_sha256")
            or _sha256(retry_run / "retry-queue.json") != config.get("retry_queue_sha256")):
        raise ValueError("Failed-only start points at a different terminal retry plan/queue")
    for relative_key, digest_key in (("retry_plan_path", "retry_plan_sha256"),
                                     ("retry_queue_path", "retry_queue_sha256")):
        relative = Path(config.get(relative_key, ""))
        if relative.is_absolute() or _sha256(execution_run / relative) != config.get(digest_key):
            raise ValueError("Copied failed-only plan/queue differs from its prepared execution pin")
    evidence_path = Path(evidence_path).absolute()
    if not evidence_path.is_relative_to(INTAKE_ROOT):
        raise ValueError("Fresh start-gate evidence must remain under instrument-intake maintenance")
    evidence = _regular_json(evidence_path)
    required = {"live_source_processes", "live_kontakt_processes", "profile_evidence", "storage_observations"}
    if set(evidence) != required:
        raise ValueError("Start-gate evidence must contain exactly the process, profile, and storage observations")

    planner, _, _, _ = _load_pinned_callback_modules(full_run)
    retry_plan = _regular_json(retry_run / "plan.json")
    planner.verify_retry_start_gates(
        retry_plan,
        live_source_processes=evidence["live_source_processes"],
        live_kontakt_processes=evidence["live_kontakt_processes"],
        profile_evidence=evidence["profile_evidence"],
        storage_observations=evidence["storage_observations"],
        full_run_root=full_run,
        script_path=full_run / "code/src/music_learning/kontakt_failed_only_retry.py",
    )

    gate_path = execution_run / "failed-only" / "start-gate-evidence.json"
    gate_sha = _copy_exclusive(evidence_path, gate_path)
    pins["files"][str(gate_path)] = gate_sha
    pins_sha = _atomic_json(pins_path, pins)
    config.update(start_gate_evidence_path=gate_path.relative_to(execution_run).as_posix(),
                  start_gate_evidence_sha256=gate_sha)
    plan["pins_sha256"] = pins_sha
    plan_sha = _atomic_json(plan_path, plan)

    # The seeder map is descriptive and contains the previous plan digest; keep
    # it bound to the final gate-pinned plan before any worker can read it.
    map_path = execution_run / "source-resume-map.json"
    resume_map = _regular_json(map_path)
    resume_map["destination_plan_sha256"] = plan_sha
    _atomic_json(map_path, resume_map)
    launch.update(status="launching_failed_only", plan_sha256=plan_sha,
                  source_resume_map_sha256=_sha256(map_path), start_gate_evidence_sha256=gate_sha)
    _atomic_json(launch_path, launch)

    command = [sys.executable, str(execution_run / "code/scripts/instruments/run_kontakt_full_intake.py"),
               "--plan", str(plan_path), "--plan-sha256", plan_sha]
    stdout_path, stderr_path = execution_run / "stdout.log", execution_run / "stderr.log"
    with stdout_path.open("ab", buffering=0) as stdout, stderr_path.open("ab", buffering=0) as stderr:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                   creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
    import psutil
    actual = psutil.Process(process.pid)
    launch.update(status="started", pid=process.pid, creation_time=actual.create_time(), command=command)
    _atomic_json(launch_path, launch)
    return {"status": "started", "run_root": str(execution_run), "pid": process.pid,
            "creation_time": actual.create_time(), "command": command,
            "selected_count": plan["failed_only_execution"]["selected_count"],
            "publish": False, "preview_handoff": False}


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
    execution = subparsers.add_parser("prepare-execution", help="Prepare a frozen, failed-subset full-run job; never launches")
    execution.add_argument("--full-run", type=Path, default=FULL_H_RUN)
    execution.add_argument("--retry-run", type=Path, required=True)
    execution.add_argument("--output", type=Path, required=True)
    execution.add_argument("--source-release", type=Path, required=True,
                           help="Immutable release containing current shared Kontakt/legato rules")
    start = subparsers.add_parser("start-execution", help="Recheck gates and launch exactly one frozen failed-subset job")
    start.add_argument("--full-run", type=Path, default=FULL_H_RUN)
    start.add_argument("--retry-run", type=Path, required=True)
    start.add_argument("--execution-run", type=Path, required=True)
    start.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        full_run = _fixed_full_run(args.full_run)
        if args.command == "prepare":
            output = Path(args.output).absolute()
            if output.parent != full_run.parent or output.exists():
                raise ValueError("Retry output must be a new sibling of the preserved full-h run")
            result = _prepare(full_run, output)
        elif args.command == "check-start-gates":
            retry_run = Path(args.retry_run).absolute()
            evidence_path = Path(args.evidence).absolute()
            if (retry_run.parent != full_run.parent or retry_run == full_run
                    or not evidence_path.is_relative_to(INTAKE_ROOT)):
                raise ValueError("Retry plan/evidence paths are outside the allowed maintenance scope")
            result = _check_start_gates(full_run, retry_run, evidence_path)
        elif args.command == "prepare-execution":
            retry_run, output = Path(args.retry_run).absolute(), Path(args.output).absolute()
            source_release = Path(args.source_release).absolute()
            if (retry_run.parent != full_run.parent or retry_run == full_run
                    or output.parent != full_run.parent or output.exists()):
                raise ValueError("Failed-only execution output/retry plan must use new intake siblings")
            result = _prepare_execution(full_run, retry_run, output, source_release)
        else:
            retry_run = Path(args.retry_run).absolute()
            execution_run = Path(args.execution_run).absolute()
            evidence_path = Path(args.evidence).absolute()
            if (retry_run.parent != full_run.parent or execution_run.parent != full_run.parent
                    or execution_run == full_run or retry_run == full_run
                    or not evidence_path.is_relative_to(INTAKE_ROOT)):
                raise ValueError("Failed-only launch inputs are outside the fixed maintenance scope")
            result = _start_execution(full_run, retry_run, execution_run, evidence_path)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "rejected", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
