"""Run the same diagnostic collector without starting project/model jobs."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from model_runtime import runtime_root  # noqa: E402


def main():
    python = runtime_root() / "envs/app/Scripts/python.exe"
    if Path(sys.executable).resolve() != python.resolve():
        return subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    from music_learning.diagnostics import collect
    from music_learning.settings import Settings, local_fixed

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--use-existing-login", action="store_true")
    args = parser.parse_args()
    settings = Settings.load()
    output = (
        local_fixed(args.output)
        if args.output
        else settings.path("diagnostics", str(int(time.time())) + "-" + uuid.uuid4().hex[:8])
    )
    output.mkdir(parents=True, exist_ok=False)
    report = collect(settings, output, args.use_existing_login)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output / "report.json"),
                "checks": {k: v["ok"] for k, v in report["checks"].items()},
            }
        )
    )
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
