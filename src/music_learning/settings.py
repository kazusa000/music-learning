from __future__ import annotations

import ctypes
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def local_fixed(path: Path) -> Path:
    resolved = path.resolve()
    if str(resolved).startswith("\\\\"):
        raise ValueError("application storage must not be on a network share")
    if os.name == "nt" and ctypes.windll.kernel32.GetDriveTypeW(str(resolved.anchor)) != 3:
        raise ValueError("application storage must be on a local fixed drive")
    return resolved


@dataclass
class Settings:
    data_root: Path
    host: str = "127.0.0.1"
    port: int = 8765
    max_upload_bytes: int = 1024**3
    lease_seconds: float = 12
    job_timeout_seconds: float = 120
    plugin_roots: list[Path] = field(default_factory=list)
    native_host: Path = ROOT / "runtime/build/msvc/bin/ml_audio_host.exe"
    live_host: Path = ROOT / "runtime/build/live-track/ml_track_host.exe"
    model_runtime: Path | None = None
    pipeline_stage_timeout_seconds: float = 1800
    pipeline_max_duration_seconds: float = 600
    pipeline_min_free_bytes: int = 3 * 1024**3
    instrument_min_free_bytes: int = 256 * 1024**2
    instrument_library: Path | None = None
    instrument_preview_cache_bytes: int = 512 * 1024**2
    instrument_preview_warm: bool = True
    native_audio_enabled: bool = True
    web_root: Path | None = None

    def __post_init__(self):
        self.data_root = local_fixed(Path(self.data_root))
        if self.web_root is not None:
            self.web_root = local_fixed(Path(self.web_root))
        self.plugin_roots = [local_fixed(Path(p)) for p in self.plugin_roots]
        self.native_host = local_fixed(Path(self.native_host))
        self.live_host = local_fixed(Path(self.live_host))
        if self.model_runtime is None:
            profile = ROOT / "runtime/model-runtime.json"
            self.model_runtime = (
                Path(json.loads(profile.read_text(encoding="utf-8"))["runtime_root"])
                if profile.is_file()
                else self.data_root.parent
            )
        self.model_runtime = local_fixed(Path(self.model_runtime))
        if self.instrument_library is not None:
            self.instrument_library = local_fixed(Path(self.instrument_library))
        if self.host != "127.0.0.1":
            raise ValueError("P1 server only binds 127.0.0.1")
        if self.lease_seconds <= 0 or self.job_timeout_seconds <= 0 or self.max_upload_bytes <= 0:
            raise ValueError("limits must be positive")
        self.data_root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls, path: Path | None = None):
        path = path or Path(os.environ.get("MUSIC_APP_CONFIG", ROOT / "runtime/app.local.json"))
        if path.is_file():
            values = json.loads(path.read_text(encoding="utf-8"))
            library = ROOT / "runtime/instrument-library.local.json"
            if "instrument_library" not in values and library.is_file():
                values["instrument_library"] = json.loads(library.read_text())["root"]
            return cls(**values)
        profile = ROOT / "runtime/model-runtime.json"
        base = (
            Path(json.loads(profile.read_text(encoding="utf-8"))["runtime_root"])
            if profile.is_file()
            else ROOT / "data"
        )
        return cls(data_root=base / "app-data", plugin_roots=[ROOT / "plugins"])

    def path(self, *parts: str) -> Path:
        path = local_fixed(self.data_root.joinpath(*parts))
        if not path.is_relative_to(self.data_root):
            raise ValueError("path escaped local application storage")
        return path

    def plugin_path(self, value: str) -> Path:
        path = local_fixed(Path(value))
        if not any(path.is_relative_to(root) for root in self.plugin_roots):
            raise ValueError("plugin path outside configured local allowlist")
        if path.suffix.lower() != ".vst3" or not path.exists():
            raise ValueError("expected an existing VST3 bundle")
        return path
