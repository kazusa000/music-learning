"""Exercise real product login failure, turn interruption and provider disconnect."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from music_learning.sdk import Client  # noqa: E402
from music_learning.settings import Settings, local_fixed  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--actions", default="needs_login,cancel,disconnect")
    args = parser.parse_args()
    output = local_fixed(args.output)
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings.load()
    results = []
    with Client() as client:
        project = client.call("projects.get", {"project_id": args.project})
        for action in args.actions.split(","):
            if action not in ("needs_login", "cancel", "disconnect"):
                raise ValueError("Unknown probe action")
            turn = client.discuss(
                args.project,
                "请查询整曲summary与和弦候选后详细分析配器和节奏的关系，约1500字，给出有效工程引用。",
                expected_revision=project["current_revision"],
                login_mode="isolated" if action == "needs_login" else "existing",
            )
            if action != "needs_login":
                deadline = time.monotonic() + 90
                while not turn["tools"] and turn["status"] not in ("succeeded", "failed", "cancelled"):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Provider did not expose an observable tool result")
                    time.sleep(0.1)
                    turn = client.call("dialogue.get", {"project_id": args.project, "turn_id": turn["id"]})
                assert turn["status"] == "running", turn["status"]
                if action == "cancel":
                    client.call(
                        "dialogue.cancel",
                        {"project_id": args.project, "turn_id": turn["id"]},
                        expected_revision=project["current_revision"],
                    )
                else:
                    directories = list(settings.path("dialogue").glob(turn["id"] + "-*"))
                    assert len(directories) == 1
                    directory = directories[0]
                    owner = json.loads((directory / "owner.json").read_text(encoding="utf-8"))
                    process = psutil.Process(owner["pid"])
                    assert any(
                        argument.startswith("sqlite_home=")
                        and Path(json.loads(argument.partition("=")[2])).parent.resolve()
                        == directory.resolve()
                        for argument in process.cmdline()
                    )
                    subprocess.run(
                        ["taskkill", "/PID", str(owner["pid"]), "/T", "/F"],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=0x08000000,
                    )
            turn = client.wait_dialogue(args.project, turn["id"], timeout=100)
            (output / (action + ".json")).write_text(
                json.dumps(turn, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            assert turn["status"] == ("cancelled" if action == "cancel" else "failed"), turn["status"]
            if action != "cancel":
                assert turn["error"]["code"] == (
                    "needs_login" if action == "needs_login" else "provider_disconnected"
                ), turn["error"]
            directories = list(settings.path("dialogue").glob(turn["id"] + "-*"))
            rpc = [
                json.loads(line)
                for line in (directories[0] / "rpc-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            if action == "cancel":
                assert any(e.get("method") == "turn/interrupt" for e in rpc)
            forbidden = {"commandExecution", "fileChange", "mcpToolCall", "webSearch", "collabAgentToolCall"}
            assert not any(e.get("item_type") in forbidden for e in rpc)
            results.append(
                {
                    "action": action,
                    "turn_id": turn["id"],
                    "status": turn["status"],
                    "tools_retained": len(turn["tools"]),
                    "rpc_checked": True,
                }
            )
            print(json.dumps(results[-1]), flush=True)
    (output / "summary.json").write_text(
        json.dumps({"status": "succeeded", "results": results}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
