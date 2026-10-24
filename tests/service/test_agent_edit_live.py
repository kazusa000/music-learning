"""Opt-in real model/tool loop; isolated music state and synthetic sound fixture."""
import asyncio
import json
import os
import time

import pytest

from conftest import invoke
from music_learning.dialogue_worker import DialogueWorker
from music_learning.settings import ROOT
from test_agent_edit_tools import captured_fixture
from test_composition_notebook import composition


@pytest.mark.skipif(os.environ.get("ML_AI_EDIT_LIVE") != "1", reason="Explicit real model generation")
def test_real_model_creates_eight_bars_with_a_captured_instrument(service, tmp_path):
    project, revision = composition(service)
    capture, _ = captured_fixture(service, project, tmp_path)
    prompt = ("在现有工程的前8小节写一段稀疏电钢琴，保持速度与拍号，"
              "和声按 Cmaj7、Am7、Dm7、G7 循环两遍，每小节2至4音，带少量切分。"
              "音色已经加载成功，capture_job_id=" + capture + "，直接绑定到你使用的轨道，无需重新加载。"
              "请实际写入音符并绑定音色，完成后简短汇报。")
    turn = invoke(service, "dialogue.submit", dict(project_id=project["id"], text=prompt,
        model="gpt-6.1-sol", reasoning_effort="medium", fast_mode=True, login_mode="existing"), revision["id"])
    worker = DialogueWorker(service.database, service=service)
    asyncio.run(worker.execute(worker.claim()))
    result = invoke(service, "dialogue.get", dict(project_id=project["id"], turn_id=turn["id"]))
    snapshot = invoke(service, "revisions.get", dict(project_id=project["id"]))["snapshot"]
    report = dict(status=result["status"], error=result["error"], text=result["text"],
                  tools=[dict(tool=x["tool"], success=x["success"], error=x["result"].get("error"), failed_arguments=x["arguments"] if not x["success"] else None) for x in result["tools"]],
                  note_count=len(snapshot["notes"]), plugin_count=len(snapshot["plugins"]),
                  timing_unchanged=snapshot["timing_map"] == revision["snapshot"]["timing_map"],
                  real_model=True, real_audio=False, formal_project_written=False)
    path = ROOT / "runtime/test-reports" / ("agent-edit-live-" + str(time.time_ns()) + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["status"] == "succeeded", report
    assert 16 <= len(snapshot["notes"]) <= 32, report
    assert len(snapshot["plugins"]) == 1 and snapshot["plugins"][0]["preset_id"] == "fixture-ep", report
    assert {n["track_id"] for n in snapshot["notes"]} == {snapshot["plugins"][0]["track_id"]}, report
    assert report["timing_unchanged"], report
    assert not [x for x in result["tools"] if not x["success"]], report
