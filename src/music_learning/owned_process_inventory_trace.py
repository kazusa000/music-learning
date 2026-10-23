"""Bounded, read-only tracing for one caller-owned process inventory row.

The observer is deliberately separate from process identity validation. It returns
the exact list supplied by ``inventory_fn`` and never changes or filters that list.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, Iterator


_SCHEMA = "music-learning.owned-process-inventory-trace.v1"
_ERROR_RESERVE_BYTES = 4096
_MAX_ERRORS = 4
_MAX_ERROR_TEXT = 512


class OwnedProcessInventoryTrace:
    """Record bounded observations for the PID bound by one ``launch.json``.

    ``host_path``, ``request_path``, and ``response_path`` are caller-declared
    bindings. The matching launch file supplies the PID and launch-time digests;
    those values are retained as evidence metadata, not as an identity verdict.
    """

    def __init__(
        self,
        inventory_fn: Callable[[], list[dict]],
        *,
        launch_path: str | Path,
        host_path: str | Path,
        request_path: str | Path,
        response_path: str | Path,
        trace_path: str | Path,
        target_pid: int | None = None,
        owned_parent_pid: int | None = None,
        expected_host_sha256: str | None = None,
        expected_request_sha256: str | None = None,
        max_samples: int = 128,
        max_total_bytes: int = 262_144,
        max_row_bytes: int = 16_384,
        utc_now: Callable[[], str] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
    ) -> None:
        if not callable(inventory_fn):
            raise TypeError("inventory_fn must be callable")
        if not 1 <= max_samples <= 10_000:
            raise ValueError("max_samples must be between 1 and 10000")
        if max_total_bytes < _ERROR_RESERVE_BYTES + 4096:
            raise ValueError("max_total_bytes must leave room for samples and a bounded error")
        sample_budget = max_total_bytes - _ERROR_RESERVE_BYTES
        if not 1 <= max_row_bytes <= sample_budget - 512:
            raise ValueError("max_row_bytes must fit inside the bounded sample budget")
        if target_pid is not None and (
            isinstance(target_pid, bool) or not isinstance(target_pid, int) or target_pid <= 0
        ):
            raise ValueError("target_pid must be a positive integer")
        if owned_parent_pid is not None and (
            isinstance(owned_parent_pid, bool)
            or not isinstance(owned_parent_pid, int)
            or owned_parent_pid <= 0
        ):
            raise ValueError("owned_parent_pid must be a positive integer")

        self._inventory_fn = inventory_fn
        self._launch_path = Path(os.path.abspath(launch_path))
        self._caller_paths = {
            "host_path": self._resolved_path(host_path),
            "request_path": self._resolved_path(request_path),
            "response_path": self._resolved_path(response_path),
        }
        self._caller_target_pid = target_pid
        self._owned_parent_pid = owned_parent_pid
        self._expected_hashes = {
            "host_sha256": self._validated_digest(expected_host_sha256, "expected_host_sha256"),
            "request_sha256": self._validated_digest(expected_request_sha256, "expected_request_sha256"),
        }
        self._trace_path = Path(os.path.abspath(trace_path))
        if not self._trace_path.parent.is_dir():
            raise ValueError("trace_path parent must already exist")
        if self._trace_path.is_symlink() or self._trace_path.exists():
            raise FileExistsError(f"refusing to overwrite inventory trace: {self._trace_path}")

        self._max_samples = max_samples
        self._max_total_bytes = max_total_bytes
        self._max_row_bytes = max_row_bytes
        self._utc_now = utc_now or self._default_utc_now
        self._monotonic_ns = monotonic_ns or time.monotonic_ns
        self._attempts = 0
        self._target_pid: int | None = None
        self._trusted_identity: dict | None = None
        self._binding_failed = False
        self._samples: list[dict] = []
        self._errors: list[dict] = []
        self._error_objects: set[int] = set()
        self._last_valid_sample_seq: int | None = None
        self._observer_error: str | None = None
        self._document: dict = {
            "schema": _SCHEMA,
            "observer_only": True,
            "identity_verdict": "not_performed",
            "privacy_policy": "raw command_line only for caller-bound same-lifetime owned PID",
            "caller_binding": {
                **self._caller_paths,
                "target_pid": target_pid,
                "target_pid_source": "caller" if target_pid is not None else "launch.json",
                "owned_parent_pid": owned_parent_pid,
                "owned_parent_pid_source": "caller" if owned_parent_pid is not None else "unspecified",
                "launch_path": str(self._launch_path),
            },
            "launch_binding": None,
            "samples": self._samples,
            "last_valid_sample_seq": None,
            "errors": self._errors,
            "observer_error": None,
            "limits": {
                "max_samples": max_samples,
                "max_total_bytes": max_total_bytes,
                "max_row_bytes": max_row_bytes,
                "max_errors": _MAX_ERRORS,
            },
            "metrics": {"sample_count": 0, "serialized_bytes": 0},
        }
        initial = self._document_payload()
        if len(initial) > max_total_bytes:
            raise ValueError("trace metadata exceeds max_total_bytes")
        self._write_new(initial)

    @staticmethod
    def _default_utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    @staticmethod
    def _resolved_path(value: str | Path) -> str:
        path = Path(value)
        if not path.is_absolute():
            path = Path.cwd() / path
        rendered = str(path.resolve(strict=False)).replace("/", "\\")
        if len(rendered) > 4096:
            raise ValueError("caller path exceeds 4096 characters")
        return rendered

    @staticmethod
    def _normalized_path(value: object) -> str:
        return ntpath.normcase(ntpath.normpath(str(value).replace("/", "\\")))

    @staticmethod
    def _validated_digest(value: str | None, label: str) -> str | None:
        if value is None:
            return None
        rendered = value.casefold()
        if len(rendered) != 64 or any(char not in "0123456789abcdef" for char in rendered):
            raise ValueError(f"{label} must be a SHA-256 hex digest")
        return rendered

    @staticmethod
    def _encode(document: dict) -> bytes:
        return json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _document_payload(self) -> bytes:
        self._document["metrics"]["serialized_bytes"] = 0
        for _ in range(4):
            payload = self._encode(self._document)
            size = len(payload)
            if self._document["metrics"]["serialized_bytes"] == size:
                return payload
            self._document["metrics"]["serialized_bytes"] = size
        return self._encode(self._document)

    def _write_new(self, payload: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(self._trace_path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                self._trace_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _write_update(self) -> None:
        self._document["last_valid_sample_seq"] = self._last_valid_sample_seq
        self._document["observer_error"] = self._observer_error
        self._document["metrics"]["sample_count"] = len(self._samples)
        payload = self._document_payload()
        if len(payload) > self._max_total_bytes:
            raise ValueError("serialized trace exceeds max_total_bytes")

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=self._trace_path.name + ".", suffix=".tmp", dir=self._trace_path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._trace_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _clock_sample(self) -> tuple[str, int]:
        try:
            utc_value = str(self._utc_now())[:80]
        except Exception:
            utc_value = self._default_utc_now()
        try:
            monotonic_value = int(self._monotonic_ns())
        except Exception:
            monotonic_value = time.monotonic_ns()
        return utc_value, monotonic_value

    def _set_observer_error(self, code: str, *, sequence: int, origin: str = "observer") -> None:
        if self._observer_error is None:
            self._observer_error = code[:80]
            if len(self._errors) < _MAX_ERRORS:
                utc_value, monotonic_value = self._clock_sample()
                self._errors.append({
                    "sequence": sequence,
                    "utc": utc_value,
                    "monotonic_ns": monotonic_value,
                    "origin": origin[:48],
                    "type": "InventoryTraceObserverError",
                    "message": code[:_MAX_ERROR_TEXT],
                    "message_truncated": False,
                })

    def _save_safely(self) -> None:
        try:
            self._write_update()
        except Exception as error:
            self._set_observer_error(
                f"trace_write_failed:{type(error).__name__}",
                sequence=max(1, self._attempts),
                origin="trace_write",
            )

    def _add_error(self, error: BaseException, *, origin: str, sequence: int) -> None:
        identity = id(error)
        if identity in self._error_objects:
            return
        self._error_objects.add(identity)
        if len(self._errors) >= _MAX_ERRORS:
            self._set_observer_error("error_record_limit_exceeded", sequence=sequence)
            self._save_safely()
            return
        try:
            message = str(error)
        except Exception:
            message = "<exception string conversion failed>"
        utc_value, monotonic_value = self._clock_sample()
        item = {
            "sequence": sequence,
            "utc": utc_value,
            "monotonic_ns": monotonic_value,
            "origin": origin[:48],
            "type": type(error).__name__[:96],
            "message": message[:_MAX_ERROR_TEXT],
            "message_truncated": len(message) > _MAX_ERROR_TEXT,
        }
        self._errors.append(item)
        self._save_safely()

    def record_error(self, error: BaseException, *, origin: str = "caller") -> None:
        """Record an error from the caller without changing or replacing it."""
        self._add_error(error, origin=origin, sequence=max(1, self._attempts))

    @contextmanager
    def capture_call_errors(self) -> Iterator[OwnedProcessInventoryTrace]:
        """Record an exception raised after the callback returns, then re-raise it."""
        try:
            yield self
        except BaseException as error:
            self.record_error(error, origin="wrapped_host_call")
            raise

    def _bind_launch(self, sequence: int) -> bool:
        try:
            if self._launch_path.is_symlink() or not self._launch_path.is_file():
                raise ValueError("launch.json is missing or a symlink")
            with self._launch_path.open("rb") as stream:
                raw = stream.read(32_769)
            if len(raw) > 32_768:
                raise ValueError("launch.json exceeds 32768 bytes")
            launch = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(launch, dict):
                raise ValueError("launch.json root is not an object")
            argv = launch.get("argv")
            if not isinstance(argv, list) or len(argv) != 3 or not all(
                isinstance(value, str) for value in argv
            ):
                raise ValueError("launch.json argv is not the expected three-path list")
            for field, actual in self._caller_paths.items():
                if self._normalized_path(launch.get(field, "")) != self._normalized_path(actual):
                    raise ValueError(f"launch.json {field} differs from caller binding")
            for index, field in enumerate(("host_path", "request_path", "response_path")):
                if self._normalized_path(argv[index]) != self._normalized_path(self._caller_paths[field]):
                    raise ValueError(f"launch.json argv[{index}] differs from caller binding")
            pid = launch.get("pid")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                raise ValueError("launch.json has no positive dispatched PID")
            if launch.get("runner_dispatched") is not True:
                raise ValueError("launch.json does not declare runner_dispatched")
            if self._caller_target_pid is not None and pid != self._caller_target_pid:
                raise ValueError("launch.json PID differs from caller target_pid")
            launch_hashes = {}
            for field in ("host_sha256", "request_sha256"):
                if not isinstance(launch.get(field), str):
                    raise ValueError(f"launch.json {field} is missing")
                digest = self._validated_digest(launch.get(field), f"launch.{field}")
                expected = self._expected_hashes[field]
                if expected is not None and digest != expected:
                    raise ValueError(f"launch.json {field} differs from caller digest")
                launch_hashes[field] = digest
            self._target_pid = pid
            self._document["caller_binding"]["target_pid"] = pid
            self._document["launch_binding"] = {
                "case": str(launch.get("case", ""))[:128],
                "pid": pid,
                "argv": list(argv),
                "host_sha256": launch_hashes["host_sha256"],
                "request_sha256": launch_hashes["request_sha256"],
                "binding_source": "launch.json verified against caller paths",
            }
            native_identity = launch.get("native_process_identity")
            if isinstance(native_identity, dict) and not self._identity_mismatch_reasons(
                native_identity, require_expected_tokens=True
            ):
                self._trusted_identity = self._identity_projection(native_identity)
                self._document["launch_binding"]["native_process_identity_observed"] = True
            else:
                self._document["launch_binding"]["native_process_identity_observed"] = False
            self._document["trusted_first_identity"] = self._trusted_identity
            self._save_safely()
            return True
        except Exception as error:
            self._binding_failed = True
            self._add_error(error, origin="launch_binding", sequence=sequence)
            return False

    def _check_bounded_row(self, value: object, *, budget: int, depth: int = 0) -> int:
        if depth > 16:
            raise ValueError("target row exceeds maximum JSON nesting")
        if value is None or isinstance(value, bool):
            return 4
        if isinstance(value, int):
            if value.bit_length() > budget * 4:
                raise ValueError("target row exceeds max_row_bytes")
            return min(32, len(str(value)) + 1)
        if isinstance(value, float):
            if not (float("-inf") < value < float("inf")):
                raise ValueError("target row contains a non-finite number")
            return 32
        if isinstance(value, str):
            if len(value) > budget:
                raise ValueError("target row exceeds max_row_bytes")
            size = len(value.encode("utf-8", errors="replace")) + 2
            if size > budget:
                raise ValueError("target row exceeds max_row_bytes")
            return size
        if isinstance(value, dict):
            if len(value) > 256:
                raise ValueError("target row has too many fields")
            total = 2
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("target row has a non-string field name")
                if len(key) > budget:
                    raise ValueError("target row exceeds max_row_bytes")
                total += len(key.encode("utf-8", errors="replace")) + 4
                total += self._check_bounded_row(item, budget=max(0, budget - total), depth=depth + 1)
                if total > budget:
                    raise ValueError("target row exceeds max_row_bytes")
            return total
        if isinstance(value, (list, tuple)):
            if len(value) > 256:
                raise ValueError("target row has too many sequence values")
            total = 2
            for item in value:
                total += self._check_bounded_row(item, budget=max(0, budget - total), depth=depth + 1) + 1
                if total > budget:
                    raise ValueError("target row exceeds max_row_bytes")
            return total
        raise TypeError(f"target row contains unsupported value type {type(value).__name__}")

    def _target_rows(self, rows: list[dict]) -> list[dict]:
        matches = []
        for row in rows:
            if row.get("pid") == self._target_pid:
                if len(matches) >= 16:
                    raise ValueError("target PID appears in too many inventory rows")
                matches.append(row)
        return matches

    @staticmethod
    def _safe_scalar(value: object, *, text_limit: int = 512) -> object:
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, str):
            return value[:text_limit]
        return f"<{type(value).__name__}>"

    def _same_host_path(self, value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            observed = Path(value).resolve(strict=False)
        except (OSError, ValueError):
            observed = Path(value)
        return self._normalized_path(observed) == self._normalized_path(self._caller_paths["host_path"])

    def _command_token_presence(self, value: object) -> tuple[str, dict[str, bool]]:
        command = " ".join(str(value or "").replace("/", "\\").casefold().split())
        expected = {
            key.removesuffix("_path"): self._normalized_path(path)
            for key, path in self._caller_paths.items()
        }
        return command, {key: token in command for key, token in expected.items()}

    def _identity_mismatch_reasons(self, row: dict, *, require_expected_tokens: bool) -> list[str]:
        reasons = []
        pid = row.get("pid")
        if isinstance(pid, bool) or pid != self._target_pid:
            reasons.append("pid_mismatch")
        created_ticks = row.get("created_ticks")
        if isinstance(created_ticks, bool) or not isinstance(created_ticks, int):
            reasons.append("created_ticks_missing_or_invalid")
        name = row.get("name")
        expected_name = ntpath.basename(self._caller_paths["host_path"])
        if not isinstance(name, str) or name.casefold() != expected_name.casefold():
            reasons.append("process_name_mismatch")
        if not self._same_host_path(row.get("executable_path")):
            reasons.append("executable_path_mismatch")
        if self._owned_parent_pid is None or row.get("ppid") != self._owned_parent_pid:
            reasons.append("owned_parent_pid_mismatch_or_unspecified")
        if require_expected_tokens:
            _, presence = self._command_token_presence(row.get("command_line"))
            for token, found in presence.items():
                if not found:
                    reasons.append(f"expected_{token}_token_missing")
        return reasons

    @staticmethod
    def _identity_projection(row: dict) -> dict:
        return {
            "pid": row.get("pid"),
            "created_ticks": row.get("created_ticks"),
            "name": row.get("name"),
            "executable_path": row.get("executable_path"),
            "ppid": row.get("ppid"),
        }

    def _redacted_row(self, row: dict, reasons: list[str]) -> dict:
        command = str(row.get("command_line") or "")
        digest = hashlib.sha256()
        utf8_length = 0
        for offset in range(0, len(command), 8192):
            chunk = command[offset:offset + 8192].encode("utf-8", errors="replace")
            digest.update(chunk)
            utf8_length += len(chunk)
        _, expected_tokens = self._command_token_presence(row.get("command_line"))
        ticks = row.get("created_ticks")
        if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks.bit_length() > 63:
            ticks = None
        ppid = row.get("ppid")
        if isinstance(ppid, bool) or not isinstance(ppid, int) or ppid < 0:
            ppid = None
        return {
            "redacted": True,
            "redaction_reasons": reasons or ["identity_not_established"],
            "pid": self._safe_scalar(row.get("pid")),
            "created_ticks": ticks,
            "name": self._safe_scalar(row.get("name"), text_limit=256),
            "ppid": ppid,
            "executable_path": self._safe_scalar(row.get("executable_path"), text_limit=1024),
            "command_line_chars": len(command),
            "command_line_utf8_bytes": utf8_length,
            "command_line_sha256": digest.hexdigest(),
            "expected_token_presence": expected_tokens,
        }

    def _project_target_row(self, row: dict) -> dict:
        if self._trusted_identity is None:
            reasons = self._identity_mismatch_reasons(row, require_expected_tokens=True)
            if not reasons:
                self._trusted_identity = self._identity_projection(row)
                self._document["trusted_first_identity"] = self._trusted_identity
        else:
            reasons = []
            comparisons = (
                ("pid", "pid_reused"),
                ("created_ticks", "pid_lifetime_changed"),
                ("name", "process_name_changed"),
                ("executable_path", "executable_path_changed"),
                ("ppid", "parent_pid_changed"),
            )
            for key, reason in comparisons:
                current = row.get(key)
                expected = self._trusted_identity.get(key)
                if key == "executable_path":
                    same = self._same_host_path(current) and self._same_host_path(expected)
                elif key == "name":
                    same = (
                        isinstance(current, str)
                        and isinstance(expected, str)
                        and current.casefold() == expected.casefold()
                    )
                else:
                    same = current == expected and not isinstance(current, bool)
                if not same:
                    reasons.append(reason)
        if reasons:
            return self._redacted_row(row, reasons)
        return row

    def __call__(self) -> list[dict]:
        self._attempts += 1
        sequence = self._attempts
        try:
            rows = self._inventory_fn()
        except BaseException as error:
            self._add_error(error, origin="inventory_fn", sequence=sequence)
            raise

        if self._target_pid is None and not self._binding_failed:
            if not self._bind_launch(sequence):
                return rows
        if self._target_pid is None or self._observer_error is not None:
            return rows
        if len(self._samples) >= self._max_samples:
            self._set_observer_error("sample_limit_exceeded", sequence=sequence)
            self._save_safely()
            return rows

        try:
            matches = self._target_rows(rows)
            projected_rows = [self._project_target_row(row) for row in matches]
            for projected_row in projected_rows:
                self._check_bounded_row(projected_row, budget=self._max_row_bytes)
            encoded_rows = json.loads(self._encode(projected_rows).decode("utf-8"))
            row_bytes = len(self._encode(encoded_rows))
            if row_bytes > self._max_row_bytes:
                self._set_observer_error("row_bytes_limit_exceeded", sequence=sequence)
                self._save_safely()
                return rows
        except Exception as error:
            self._add_error(error, origin="row_observation", sequence=sequence)
            return rows

        utc_value, monotonic_value = self._clock_sample()
        sample = {
            "sequence": sequence,
            "utc": utc_value,
            "monotonic_ns": monotonic_value,
            "status": "matched" if encoded_rows else "missing",
            "target_rows": encoded_rows,
        }
        self._samples.append(sample)
        self._last_valid_sample_seq = sequence
        self._document["metrics"]["sample_count"] = len(self._samples)
        try:
            payload = self._encode(self._document)
            if len(payload) > self._max_total_bytes - _ERROR_RESERVE_BYTES:
                self._samples.pop()
                self._last_valid_sample_seq = self._samples[-1]["sequence"] if self._samples else None
                self._set_observer_error("total_bytes_limit_exceeded", sequence=sequence)
                self._save_safely()
                return rows
            self._write_update()
        except Exception as error:
            self._samples.pop()
            self._last_valid_sample_seq = self._samples[-1]["sequence"] if self._samples else None
            self._add_error(error, origin="trace_serialization", sequence=sequence)
        return rows
