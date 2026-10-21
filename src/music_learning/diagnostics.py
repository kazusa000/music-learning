"""Local runtime checks, actual native/CUDA probes and read-only Codex login status."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys

from .codex import CodexAppServer
from .settings import ROOT
from .storage import digest


async def codex_status(directory, existing_login):
    client = CodexAppServer(directory, existing_login=existing_login)
    try:
        await client.start()
        account = await client.request("account/read", {"refreshToken": False})
        return {
            "runtime_verified": True,
            "logged_in": bool(account.get("account")),
            "login_mode": "existing" if existing_login else "isolated",
            "inference_requested": False,
        }
    finally:
        await client.close()


def collect(settings, directory, use_existing_login=False):
    sys.path.insert(0, str(ROOT / "scripts"))
    from media_runtime import verify_tool
    from native_host import NativeHost

    checks = {}

    def check(name, function):
        try:
            checks[name] = {"ok": True, "result": function()}
        except Exception as error:
            checks[name] = {"ok": False, "error_type": type(error).__name__}
            (directory / (name + "-error.txt")).write_text(str(error)[:12000], encoding="utf-8")

    check("web", lambda: {"built": (ROOT / "apps/web/dist/index.html").is_file()})
    check(
        "native",
        lambda: NativeHost(settings.native_host, directory / "native").call(
            {"schema_version": 1, "action": "capabilities"}, timeout=20
        ),
    )
    check(
        "media",
        lambda: {
            name: {"verified": True, "sha256": digest(verify_tool(name))} for name in ("ffmpeg", "ffprobe")
        },
    )

    def plugins():
        profile = json.loads((ROOT / "config/pipeline-profile.json").read_text(encoding="utf-8"))
        result = {}
        for name in ("surge", "sfizz"):
            bundle = settings.plugin_path(str(ROOT / profile[name]))
            binaries = list((bundle / "Contents/x86_64-win").glob("*.vst3"))
            if len(binaries) != 1:
                raise ValueError("unexpected plugin layout")
            actual = digest(binaries[0])
            expected = (
                profile["surge_binary_sha256"]
                if name == "surge"
                else json.loads((settings.model_runtime / profile["drum_map"]).read_text())[
                    "plugin_binary_sha256"
                ]
            )
            if actual != expected:
                raise ValueError("pinned plugin checksum mismatch")
            result[name] = {"sha256": actual, "verified": True}
        return result

    check("plugins", plugins)

    def models():
        spec = json.loads((ROOT / "config/model-dependencies.json").read_text(encoding="utf-8"))
        result = {}
        for name, value in spec["models"].items():
            base = settings.model_runtime / "models" / value.get("directory", name + "-" + value["revision"][:12])
            files = value.get("files", [])
            result[name] = {
                "revision": value["revision"],
                "files": len(files),
                "sizes_match": all(
                    (base / item["path"]).is_file() and (base / item["path"]).stat().st_size == item["size"]
                    for item in files
                ),
                "weight_digests_checked": False,
            }
        if not all(v["sizes_match"] for v in result.values()):
            raise ValueError("model file missing or size mismatch")
        return result

    check("models", models)
    for name in ("transcription",):

        def probe(name=name):
            code = "import torch,json; ok=torch.cuda.is_available(); out={'cuda_available':ok,'torch_version':torch.__version__}; out.update(device=torch.cuda.get_device_name(0),tensor_probe=float((torch.ones((32,32),device='cuda')@torch.ones((32,32),device='cuda'))[0,0])==32) if ok else None; print(json.dumps(out))"
            result = subprocess.run(
                [str(settings.model_runtime / "envs" / name / "Scripts/python.exe"), "-c", code],
                capture_output=True,
                text=True,
                check=True,
                timeout=45,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            body = json.loads(result.stdout.strip().splitlines()[-1])
            if not body["cuda_available"] or not body["tensor_probe"]:
                raise ValueError("CUDA operation unavailable; no CPU fallback assumed")
            return body

        check(name, probe)
    check("codex", lambda: asyncio.run(codex_status(directory / "codex", use_existing_login)))
    return {
        "status": "ok"
        if all(v["ok"] for v in checks.values()) and checks.get("web", {}).get("result", {}).get("built")
        else "needs_attention",
        "checks": checks,
        "free_bytes": shutil.disk_usage(settings.data_root).free,
        "nas_accessed": False,
        "model_inference_requested": False,
        "notice": "Model file sizes checked; use pinned model verification for a full weight digest check. Codex login status is not an inference permission test.",
    }
