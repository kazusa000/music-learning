"""P0b local model storage and authentication. No credentials in argv or reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "runtime/model-runtime.json"
MANIFEST = ROOT / "config/model-dependencies.json"


def runtime_root() -> Path:
    override = os.environ.get("MUSIC_MODEL_RUNTIME")
    if override:
        return Path(override).resolve()
    if PROFILE.exists():
        return Path(json.loads(PROFILE.read_text(encoding="utf-8"))["runtime_root"]).resolve()
    raise RuntimeError("Configure storage first: model_runtime.py init --root <directory>")


def environment(root: Path | None = None) -> dict[str, str]:
    root = root or runtime_root()
    env = dict(os.environ)
    env.update({"HF_HOME": str(root / "huggingface"), "TORCH_HOME": str(root / "torch"),
                "UV_CACHE_DIR": str(root / "uv-cache"), "TMP": str(root / "tmp"),
                "TEMP": str(root / "tmp"), "PYTHONUTF8": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1"})
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    return env


def model_path(name: str) -> Path:
    spec = json.loads(MANIFEST.read_text(encoding="utf-8"))["models"][name]
    return runtime_root() / "models" / (("beat-this-" if name == "beat_this" else name + "-") + spec["revision"][:12])


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_file(path: Path, spec: dict) -> str:
    if path.stat().st_size != spec["size"]:
        raise ValueError(f"Size mismatch: {path.name}")
    actual = digest(path)
    if spec["sha256"]:
        if actual != spec["sha256"]:
            raise ValueError(f"SHA-256 mismatch: {path.name}")
    elif spec.get("git_blob"):
        hasher = hashlib.sha1()
        hasher.update(b"blob " + str(spec["size"]).encode() + b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(chunk)
        if hasher.hexdigest() != spec["git_blob"]:
            raise ValueError(f"Git blob mismatch: {path.name}")
    else:
        raise ValueError(f"No upstream digest: {path.name}")
    return actual


def download(name: str) -> None:
    # Executed only in the selected model environment. Exceptions can contain
    # signed URLs; callers keep raw logs local and summaries use error types.
    from huggingface_hub import hf_hub_download
    spec = json.loads(MANIFEST.read_text(encoding="utf-8"))["models"][name]
    destination = model_path(name)
    destination.mkdir(parents=True, exist_ok=True)
    verified = []
    for file in spec["files"]:
        if file.get("url"):
            import urllib.request
            path = destination / file["path"]
            if not path.exists():
                partial = path.with_suffix(".partial")
                urllib.request.urlretrieve(file["url"], partial)
                verify_file(partial, file)
                os.replace(partial, path)
        else:
            path = Path(hf_hub_download(spec["repo"], file["path"], revision=spec["revision"],
                                       local_dir=destination))
        verified.append({"path": file["path"], "sha256": verify_file(path, file)})
        print(json.dumps({"verified": file["path"]}), flush=True)
    (destination / "verified.json").write_text(json.dumps({"revision": spec["revision"],
        "files": verified}, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--root", type=Path, required=True)
    sub.add_parser("login")
    fetch = sub.add_parser("download")
    fetch.add_argument("model", choices=("muscriptor", "beat_this"))
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "init":
        root = args.root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        PROFILE.parent.mkdir(parents=True, exist_ok=True)
        PROFILE.write_text(json.dumps({"runtime_root": str(root)}, indent=2), encoding="utf-8")
        print(f"Local model storage: {root}")
        return
    env = environment()
    if args.command in ("login", "download") and os.environ.get("ML_MODEL_CHILD") != "1":
        python = runtime_root() / "envs/transcription/Scripts/python.exe"
        env["ML_MODEL_CHILD"] = "1"
        raise SystemExit(subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], env=env))
    os.environ.update(env)
    if args.command == "login":
        from huggingface_hub import login
        login(add_to_git_credential=False)
    elif args.command == "download":
        download(args.model)
    else:
        print(json.dumps({"runtime_root": str(runtime_root()),
            "token_present": bool(os.environ.get("HF_TOKEN")) or (runtime_root() / "huggingface/token").is_file(),
            "models": {name: {"path": str(model_path(name)),
                "download_verified": (model_path(name) / "verified.json").is_file()}
                for name in ("muscriptor", "beat_this")}}, indent=2))


if __name__ == "__main__":
    main()
