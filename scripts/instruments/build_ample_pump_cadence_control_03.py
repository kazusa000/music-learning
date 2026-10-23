"""Rebuild the Ample cadence diagnostic from its pinned source recipe.

This creates a new E-only build and plan. It preserves control-01 and does not
launch either diagnostic host or read instrument-library paths.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INTAKE = ROOT / "data/maintenance/instrument-intake/20261023-ample-shared-fix"
ORIGINAL_PLAN = INTAKE / "pump-cadence-control-01/prepared-plan.json"
ORIGINAL_PLAN_SHA256 = "63155e71222bf315b96f69acda555cac91f2e22a570f70e8f445a5615d1ecd55"
ORIGINAL_BUILDER = ROOT / "scripts/instruments/build_ample_pump_cadence_diagnostic.py"
ORIGINAL_BUILDER_SHA256 = "0cd0bf184041f081ef0cb7e01aed72c2a4fc91e6de9394ab36d3237a563017e3"
CONTROL_01_MANIFEST_RENDER_SHA256 = "3970631cc6ce3f21b9ba2ef271093d079e523f54c5e0b0415f2ff876bd72705a"
BUILD_ROOT = ROOT / "runtime/build/ample-pump-cadence-03"
EVIDENCE_ROOT = INTAKE / "pump-cadence-control-03"
OUTPUT_ROOT = ROOT / "data/maintenance/instrument-intake/q/pc03"
CASE_ORDER = ("64-first", "64-reopen", "32-first", "32-reopen")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} is not an object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_original_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    if sha256(ORIGINAL_PLAN) != ORIGINAL_PLAN_SHA256:
        raise RuntimeError("Control-01 plan differs from its pinned bytes")
    plan = load_json(ORIGINAL_PLAN, "control-01 plan")
    builder_sha = sha256(ORIGINAL_BUILDER)
    if builder_sha != ORIGINAL_BUILDER_SHA256 or plan.get("builder_script", {}).get("sha256") != builder_sha:
        raise RuntimeError("The immutable control-01 builder pin does not match")
    manifest_ref = plan.get("source_snapshot_manifest", {})
    manifest_path = Path(manifest_ref.get("path", ""))
    if not manifest_path.is_file() or sha256(manifest_path) != manifest_ref.get("sha256"):
        raise RuntimeError("Control-01 original source manifest changed")
    manifest = load_json(manifest_path, "control-01 source manifest")
    if manifest.get("file_count") != 16 or len(manifest.get("files", {})) != 16:
        raise RuntimeError("Control-01 source manifest no longer lists the pinned 16 files")
    original_source_root = Path(plan["source_snapshot"]).resolve(strict=True)
    for relative, record in manifest["files"].items():
        path = original_source_root / relative
        if (not path.is_file() or path.stat().st_size != record.get("bytes")
                or sha256(path) != record.get("sha256")):
            raise RuntimeError(f"Control-01 source snapshot changed: {relative}")
    diagnostic_record = load_json(ROOT / "runtime/build/ample-pump-cadence-01/diagnostic-build.json",
                                  "control-01 build record")
    if (diagnostic_record.get("source_render_sha256") != plan.get("source_render_sha256")
            or diagnostic_record.get("plan_sha256") != ORIGINAL_PLAN_SHA256
            or diagnostic_record.get("diagnostic_executables") != plan.get("diagnostic_executables")):
        raise RuntimeError("Control-01 build record conflicts with its prepared plan")
    for cadence, entry in plan.get("diagnostic_executables", {}).items():
        exe = Path(entry.get("path", ""))
        if not exe.is_file() or sha256(exe) != entry.get("sha256"):
            raise RuntimeError(f"Control-01 {cadence} host changed")
    for relative, record in plan.get("copied_helper_sources", {}).items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != record.get("sha256"):
            raise RuntimeError(f"Pinned diagnostic helper changed: {relative}")
    if (plan.get("source_render_sha256") != "75b8e7828d848b3f4b455a8ffb40bceeb97859ebdc70882d2eedef2f62c192b7"
            or manifest["files"]["native/audio-host/Render.cpp"]["sha256"]
            != CONTROL_01_MANIFEST_RENDER_SHA256):
        raise RuntimeError("Control-01 source conflict evidence changed")
    return plan, manifest


def snapshot_files(source_root: Path, relative_names: list[str], baseline: str) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    root = source_root.resolve(strict=True)
    for relative in relative_names:
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts:
            raise RuntimeError(f"Unsafe source snapshot path: {relative}")
        candidate = root / rel
        if candidate.is_symlink():
            raise RuntimeError(f"Source snapshot entry is a reparse point: {relative}")
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError(f"Source snapshot entry is not a contained regular file: {relative}")
        files[rel.as_posix()] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    if len(files) != len(relative_names):
        raise RuntimeError("Source manifest has duplicate or missing paths")
    return {"schema_version": 1, "baseline_commit": baseline,
            "source_root": str(root), "file_count": len(files), "files": files}


def verify_snapshot(source_root: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("source_root") != str(source_root.resolve(strict=True)):
        raise RuntimeError("Frozen snapshot root changed")
    files = manifest.get("files")
    if not isinstance(files, dict) or len(files) != manifest.get("file_count"):
        raise RuntimeError("Frozen source manifest shape changed")
    for relative, record in files.items():
        path = source_root / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Frozen source file disappeared or became a reparse point: {relative}")
        if path.stat().st_size != record.get("bytes") or sha256(path) != record.get("sha256"):
            raise RuntimeError(f"Frozen compiled source changed: {relative}")


def require_render_manifest_match(manifest: dict[str, Any], render_sha256: str) -> None:
    entry = manifest.get("files", {}).get("native/audio-host/Render.cpp")
    if not isinstance(entry, dict) or entry.get("sha256") != render_sha256:
        raise RuntimeError("Frozen Render.cpp SHA differs from its source-manifest entry")


def render_source_sha256(manifest: dict[str, Any]) -> str:
    entry = manifest.get("files", {}).get("native/audio-host/Render.cpp")
    digest = entry.get("sha256") if isinstance(entry, dict) else None
    if not isinstance(digest, str) or len(digest) != 64:
        raise RuntimeError("Frozen source manifest has no valid Render.cpp SHA")
    return digest


def build_prepared_plan(*, original: dict[str, Any], built: dict[str, Any],
                        manifest: dict[str, Any], manifest_path: Path,
                        wrapper_sha256: str, test_log: Path,
                        test_executable_sha256: str) -> dict[str, Any]:
    plan = deepcopy(original)
    plan.update({
        "status": "prepared_not_started",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_snapshot": built["source_snapshot"],
        "source_render_sha256": render_source_sha256(manifest),
        "build_root": built["build_root"],
        "evidence_root": built["evidence_root"],
        "diagnostic_executables": built["diagnostic_executables"],
        "builder_script": {"path": str(Path(__file__).resolve()), "sha256": wrapper_sha256},
        "source_builder_script": {"path": str(ORIGINAL_BUILDER.resolve()),
                                  "sha256": ORIGINAL_BUILDER_SHA256},
        "source_snapshot_manifest": {
            "path": str(manifest_path.resolve()), "sha256": sha256(manifest_path),
            "file_count": manifest["file_count"],
        },
        "source_manifest_policy": {
            "snapshot_point": "after diagnostic instrumentation, before CMake configure/build",
            "all_source_files_verified_after_compile": True,
            "render_sha_matches_manifest": True,
        },
        "supersedes_attempt": {
            "build_root": str(ROOT / "runtime/build/ample-pump-cadence-02"),
            "state": "source_snapshot_only_prebuild_pin_mismatch",
            "preserved_unchanged": True,
        },
        "control_01_render_pin_assessment": {
            "plan_source_render_sha256": original["source_render_sha256"],
            "manifest_source_render_sha256": CONTROL_01_MANIFEST_RENDER_SHA256,
            "consistent": False,
            "use_for_control_03_compilation": False,
            "reason": "The rebuilt source recipe deterministically yields the manifest hash; control-01 retained conflicting historical pins.",
        },
        "copied_helper_sources": built["copied_helper_sources"],
        "protected_artifacts_before_after_sha256": built["protected_artifacts_before_after_sha256"],
        "tests": {
            "ctest_name": "ample_pump_cadence_contract", "result": "passed",
            "test_executable_sha256": test_executable_sha256,
            "recorded_validation_log_sha256": sha256(test_log),
            "validation_log": str(test_log.resolve()),
        },
        "comparison": {
            **original["comparison"],
            "source_and_runtime_shared": True,
            "only_compile_time_cadence_interval_differs": True,
            "host_executions": 0,
            "audio_render_executions": 0,
        },
    })
    plan["request_output_root"] = {
        "path": str(OUTPUT_ROOT), "absent_before_run": True,
        "windows_max_path_chars": 259,
        "longest_prepared_path_chars": max(
            len(str(OUTPUT_ROOT / f"{case}{suffix}"))
            for case in CASE_ORDER for suffix in (".wav", ".response.json")),
    }
    plan["request_variants"] = {}
    for case in CASE_ORDER:
        source_variant = original["request_variants"][case]
        source_request_path = Path(source_variant["source_request"])
        original_request_path = Path(source_variant["request"])
        if sha256(source_request_path) != source_variant["source_request_sha256"]:
            raise RuntimeError(f"Pinned source request changed: {case}")
        if sha256(original_request_path) != source_variant["request_sha256"]:
            raise RuntimeError(f"Pinned control-01 request changed: {case}")
        source_request = load_json(source_request_path, f"source request {case}")
        request = load_json(original_request_path, f"control-01 request {case}")
        if {k: v for k, v in source_request.items() if k != "output_path"} != {
                k: v for k, v in request.items() if k != "output_path"}:
            raise RuntimeError(f"Control-01 request differs from its source except for output_path: {case}")
        output_path = OUTPUT_ROOT / f"{case}.wav"
        response_path = OUTPUT_ROOT / f"{case}.response.json"
        if output_path.exists() or response_path.exists():
            raise RuntimeError(f"Control-02 output destination already exists: {case}")
        request["output_path"] = str(output_path)
        request_path = EVIDENCE_ROOT / "requests" / f"{case}.request.json"
        write_json(request_path, request)
        clone = deepcopy(source_variant)
        clone.update({
            "request": str(request_path.resolve()), "request_sha256": sha256(request_path),
            "output_wav": str(output_path), "response": str(response_path),
            "started": False, "only_json_field_changed": "output_path",
            "musical_request_sha256": canonical_sha({k: v for k, v in request.items()
                                                       if k != "output_path"}),
        })
        plan["request_variants"][case] = clone
    return plan


def _load_builder():
    if sha256(ORIGINAL_BUILDER) != ORIGINAL_BUILDER_SHA256:
        raise RuntimeError("Original builder bytes changed after validation")
    spec = importlib.util.spec_from_file_location("ample_pump_cadence_frozen_builder", ORIGINAL_BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot import the frozen original builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BUILD_ROOT = BUILD_ROOT
    module.EVIDENCE_ROOT = EVIDENCE_ROOT
    return module


def main() -> None:
    original, old_manifest = _load_original_inputs()
    if BUILD_ROOT.exists() or EVIDENCE_ROOT.exists() or OUTPUT_ROOT.exists():
        raise SystemExit("Refusing to overwrite an existing control-03 build, evidence, or output root")
    builder = _load_builder()
    source_root = BUILD_ROOT / "source"
    relative_names = list(old_manifest["files"])
    frozen_manifest: dict[str, Any] | None = None
    manifest_path = BUILD_ROOT / "source-manifest.json"
    test_log = EVIDENCE_ROOT / "tests" / "ctest.log"
    real_run = subprocess.run
    ctest_result: dict[str, Any] = {}

    def observed_run(args: Any, *positional: Any, **kwargs: Any) -> Any:
        nonlocal frozen_manifest
        argv = [str(value) for value in args]
        executable = Path(argv[0]).name.casefold() if argv else ""
        is_configure = executable == "cmake.exe" and "-S" in argv and "--build" not in argv
        is_build = executable == "cmake.exe" and "--build" in argv
        is_ctest = executable == "ctest.exe"
        if is_configure:
            if frozen_manifest is not None:
                raise RuntimeError("Unexpected second CMake configure during the frozen build")
            frozen_manifest = snapshot_files(source_root, relative_names, original["baseline_commit"])
            render_hash = render_source_sha256(frozen_manifest)
            write_json(manifest_path, frozen_manifest)
            require_render_manifest_match(frozen_manifest, render_hash)
            verify_snapshot(source_root, frozen_manifest)
        elif is_build:
            if frozen_manifest is None:
                raise RuntimeError("Native build started before the final source manifest was frozen")
            verify_snapshot(source_root, frozen_manifest)

        if is_ctest:
            kwargs = dict(kwargs)
            check = kwargs.pop("check", False)
            result = real_run(args, *positional, capture_output=True, text=True,
                              check=False, **kwargs)
            test_log.parent.mkdir(parents=True, exist_ok=True)
            test_log.write_text(
                f"returncode={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}",
                encoding="utf-8")
            ctest_result.update({"returncode": result.returncode,
                                 "stdout": result.stdout, "stderr": result.stderr})
            if check and result.returncode:
                raise subprocess.CalledProcessError(result.returncode, args,
                                                    output=result.stdout, stderr=result.stderr)
            return result

        result = real_run(args, *positional, **kwargs)
        if is_build:
            verify_snapshot(source_root, frozen_manifest or {})
        return result

    builder.subprocess = SimpleNamespace(run=observed_run, PIPE=subprocess.PIPE)
    builder.main()
    if frozen_manifest is None or ctest_result.get("returncode") != 0:
        raise RuntimeError("Build did not freeze the final source or pass its bounded unit test")
    verify_snapshot(source_root, frozen_manifest)
    _load_original_inputs()  # Reconfirm that control-01 remains byte-for-byte preserved.
    if sha256(ORIGINAL_BUILDER) != ORIGINAL_BUILDER_SHA256:
        raise RuntimeError("Original pinned builder changed during the control-03 build")
    original_protected = original.get("protected_artifacts_before_after_sha256", {})
    if original_protected.get("before") != original_protected.get("after"):
        raise RuntimeError("Control-01 recorded protected-file drift; refusing control-03")
    for relative, expected in original_protected.get("before", {}).items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"Protected control-01 artifact changed: {relative}")

    built_path = EVIDENCE_ROOT / "prepared-plan.json"
    built = load_json(built_path, "fresh control-03 build record")
    test_exe = BUILD_ROOT / "cmake/ml_test_pump_cadence.exe"
    if not test_exe.is_file():
        raise RuntimeError("Cadence contract test executable is missing")
    for cadence, entry in built.get("diagnostic_executables", {}).items():
        executable = Path(entry.get("path", ""))
        if (not executable.is_file() or sha256(executable) != entry.get("sha256")
                or entry.get("executed") is not False):
            raise RuntimeError(f"Fresh {cadence} diagnostic host failed its post-build pin")
    wrapper_sha = sha256(Path(__file__))
    plan = build_prepared_plan(original=original, built=built, manifest=frozen_manifest,
                               manifest_path=manifest_path, wrapper_sha256=wrapper_sha,
                               test_log=test_log,
                               test_executable_sha256=sha256(test_exe))
    plan_path = EVIDENCE_ROOT / "prepared-plan.json"
    write_json(plan_path, plan)
    plan_sha = sha256(plan_path)
    verify_snapshot(source_root, frozen_manifest)
    write_json(BUILD_ROOT / "diagnostic-build.json", {
        "schema_version": 1, "status": "built_and_tested_not_executed",
        "source_snapshot_manifest": str(manifest_path),
        "source_snapshot_manifest_sha256": sha256(manifest_path),
        "source_render_sha256": plan["source_render_sha256"],
        "source_render_matches_manifest": (
            plan["source_render_sha256"]
            == frozen_manifest["files"]["native/audio-host/Render.cpp"]["sha256"]),
        "original_builder": {"path": str(ORIGINAL_BUILDER), "sha256": ORIGINAL_BUILDER_SHA256},
        "wrapper_builder": {"path": str(Path(__file__).resolve()), "sha256": wrapper_sha},
        "plan": str(plan_path), "plan_sha256": plan_sha,
        "executables": plan["diagnostic_executables"],
        "unit_test_executable_sha256": sha256(test_exe),
        "unit_test_log_sha256": sha256(test_log),
        "protected_artifacts_unchanged": True,
        "source_manifest_verified_after_compile": True,
        "host_execution_count": 0, "audio_render_execution_count": 0,
        "H_access": False, "NAS_access": False,
    })
    print(json.dumps({"plan": str(plan_path), "plan_sha256": plan_sha,
                      "manifest_sha256": sha256(manifest_path),
                      "source_render_sha256": plan["source_render_sha256"],
                      "hosts": plan["diagnostic_executables"],
                      "source_verified_after_compile": True,
                      "host_executions": 0, "H_access": False}, indent=2))


if __name__ == "__main__":
    main()
