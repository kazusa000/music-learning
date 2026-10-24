"""Local artwork integrity and contact sheets for a full visual review."""
import hashlib
import json
import os
import subprocess
import time

from music_learning.instrument_gallery import document
from music_learning.instruments import resource_path
from music_learning.settings import ROOT


def test_all_gallery_artwork_integrity_and_visual_atlas(tmp_path):
    plan = document("gallery-models.json")
    images = {image["id"]: image for image in document("images.json")["images"]}
    records = []
    for identity, asset in plan["assets"].items():
        image = images[identity]
        file = resource_path(ROOT / "resources/instruments", image["file"])
        raw = file.read_bytes()
        assert raw.startswith(b"\x89PNG\r\n\x1a\n") and raw[25] == 6, identity
        assert hashlib.sha256(raw).hexdigest() == image["sha256"], identity
        assert asset["scope"] in {"model", "instrument"}
        records.append(dict(id=identity, file=str(file)))
    assert {m["image_id"] for m in plan["models"].values()} <= set(images)
    report = ROOT / "runtime/test-reports" / ("instrument-art-" + str(time.time_ns()))
    report.mkdir(parents=True)
    (tmp_path / "atlas.json").write_text(json.dumps(dict(images=records, report=str(report))), encoding="utf-8")
    result = subprocess.run(["node", str(ROOT / "apps/web/tests/e2e/instrument-gallery-art.mjs"), str(tmp_path)],
                            capture_output=True, env=os.environ, timeout=90)
    (report / "atlas.log").write_bytes(result.stdout + result.stderr)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
