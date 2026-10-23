"""Capture raw Ample MIDI diagnostics before the pinned probe validates them.

This wrapper does not alter acceptance rules. It imports one SHA-pinned probe,
stores the exact analyzer argument under the probe's new output directory, then
calls the original analyzer with the original argument and duration.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
INTAKE_ROOT = ROOT / "data" / "maintenance" / "instrument-intake"
PINNED_PROBE = ROOT / "scripts" / "probes" / "engine" / "ample_midi_delivery.py"
PINNED_PROBE_SHA256 = "3570c61f62607ce1041e4b1fb4729552491257e4f16f198b5f856721ee869179"
RAW_CAPTURE_DIRECTORY = "raw-processblock-inputs"
_UNSET = object()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pinned_probe() -> ModuleType:
    path = PINNED_PROBE.resolve(strict=True)
    if sha256_file(path) != PINNED_PROBE_SHA256:
        raise ValueError("Frozen Ample MIDI probe SHA changed")

    probe_dir = str(path.parent)
    scripts_dir = str(ROOT / "scripts")
    source_dir = str(ROOT / "src")
    for import_path in (source_dir, scripts_dir, probe_dir):
        if import_path in sys.path:
            sys.path.remove(import_path)
        sys.path.insert(0, import_path)

    spec = importlib.util.spec_from_file_location("pinned_ample_midi_delivery_probe", path)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load the pinned Ample MIDI probe")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate_new_output(output: Path) -> Path:
    intake_root = INTAKE_ROOT.resolve(strict=True)
    resolved = output.resolve(strict=False)
    try:
        relative = resolved.relative_to(intake_root)
    except ValueError as error:
        raise ValueError("Raw capture output must stay under the E-drive instrument-intake root") from error
    if not relative.parts or resolved == intake_root:
        raise ValueError("Raw capture output must be a unique child directory")
    if resolved.exists():
        raise ValueError("Raw capture output must not already exist")
    return resolved


def make_capturing_analyzer(
    validator: Callable[..., Any],
    *,
    output: Path,
    bundle_path: Path,
    probe_path: Path = PINNED_PROBE,
    probe_sha256: str = PINNED_PROBE_SHA256,
    default_duration_samples: int | None = None,
) -> tuple[Callable[..., Any], Path]:
    """Return a validator wrapper that durably stores each raw input first."""
    output = Path(output).resolve(strict=False)
    capture_root = output / RAW_CAPTURE_DIRECTORY
    call_state = {"count": 0, "created": False}
    bundle_path = Path(bundle_path).resolve(strict=False)
    probe_path = Path(probe_path).resolve(strict=False)

    def capturing_analyzer(midi_delivery, *, duration_samples=_UNSET):
        call_state["count"] += 1
        ordinal = call_state["count"]
        if not call_state["created"]:
            capture_root.mkdir(parents=True, exist_ok=False)
            call_state["created"] = True
        elif not capture_root.is_dir():
            raise ValueError("Raw processBlock capture directory disappeared")

        serialized_argument = json.dumps(
            midi_delivery,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=True,
        ).encode("utf-8")
        duration_was_explicit = duration_samples is not _UNSET
        raw = {
            "schema_version": 1,
            "capture_index": ordinal,
            "capture_stage": "before_frozen_validator",
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "probe_path": str(probe_path),
            "probe_sha256": probe_sha256,
            "diagnostic_bundle_path": str(bundle_path),
            "diagnostic_bundle_id": bundle_path.name,
            "duration_argument_supplied": duration_was_explicit,
            "duration_argument_value": duration_samples if duration_was_explicit else None,
            "effective_duration_samples": (
                duration_samples if duration_was_explicit else default_duration_samples
            ),
            "argument_type": type(midi_delivery).__name__,
            "midi_delivery_sha256": hashlib.sha256(serialized_argument).hexdigest(),
            "midi_delivery": midi_delivery,
        }
        raw_bytes = (json.dumps(raw, ensure_ascii=False, indent=2, allow_nan=True) + "\n").encode("utf-8")
        destination = capture_root / f"processblock-input-{ordinal:03d}.json"
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw_bytes)
            stream.flush()
            os.fsync(stream.fileno())

        if not duration_was_explicit:
            return validator(midi_delivery)
        return validator(midi_delivery, duration_samples=duration_samples)

    return capturing_analyzer, capture_root


def main(argv: list[str] | None = None) -> int:
    probe = load_pinned_probe()
    args = probe.parser().parse_args(argv)
    output = validate_new_output(Path(args.output))
    args.output = output
    bundle_path = Path(args.diagnostic_bundle).resolve(strict=False)
    if not bundle_path.is_relative_to((ROOT / "runtime" / "build").resolve(strict=True)):
        raise ValueError("Diagnostic bundle must remain under this E-drive build tree")

    validator, _ = make_capturing_analyzer(
        probe.analyze_processblock_trace,
        output=output,
        bundle_path=bundle_path,
        probe_path=PINNED_PROBE,
        default_duration_samples=probe.PROJECT_SAMPLES,
    )
    probe.analyze_processblock_trace = validator
    return probe.run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
