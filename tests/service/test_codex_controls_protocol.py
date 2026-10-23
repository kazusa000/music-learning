"""Opt-in pinned App Server handshake; never starts a model turn or touches music."""
import asyncio
import json
import os
import time

import pytest

from music_learning.codex import CodexAppServer
from music_learning.model_catalog import capabilities
from music_learning.settings import ROOT


@pytest.mark.skipif(os.environ.get("MUSIC_TEST_CODEX_CONTROLS") != "1", reason="Explicit local Codex handshake only")
def test_pinned_runtime_accepts_independent_speed_and_effort(tmp_path):
    async def probe():
        client = CodexAppServer(tmp_path / "codex", existing_login=True)
        result = {"generation_requests": 0, "checks": []}
        try:
            await client.start()
            catalog = await client.request("model/list", {"limit": 100})
            models = list(catalog["data"])
            while catalog.get("nextCursor"):
                catalog = await client.request("model/list", {"limit": 100, "cursor": catalog["nextCursor"]})
                models.extend(catalog["data"])
            result["catalog"] = [{"id":m["id"], **capabilities(m),
                                  "additional_speed_tiers":m.get("additionalSpeedTiers")} for m in models]
            selected = next(m for m in models if "medium" in (capabilities(m)["reasoning_efforts"] or []))
            result["model"] = selected["id"]
            result["capabilities"] = capabilities(selected)
            fast_tier = "priority" if "priority" in result["capabilities"]["service_tiers"] else "fast"
            for tier in ("default", fast_tier, "default"):
                thread = await client.request("thread/start", {
                    "cwd": str(tmp_path), "model": selected["id"], "ephemeral": True,
                    "serviceTier": tier, "sandbox": "read-only", "approvalPolicy": "on-request",
                    "config": {"model_reasoning_effort": "medium"}})
                result["checks"].append({"requested_tier": tier, "returned_tier": thread.get("serviceTier"),
                                          "returned_effort": thread["reasoningEffort"]})
                assert thread["reasoningEffort"] == "medium"
                assert thread.get("serviceTier") == tier
            result["status"] = "succeeded"
        finally:
            await client.close()
            output = ROOT / ("runtime/test-reports/codex-controls-" + str(time.time_ns()) + ".json")
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    asyncio.run(probe())
