"""Create two native VST3 render versions without interacting with any UI."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from native_host import NativeHost, ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / "reports/local/p0" / ("demo-" + uuid.uuid4().hex[:8])
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    host = NativeHost(diagnostics=output / "calls")
    surge = ROOT / "plugins/surge-xt-1.3.4/Surge Synth Team/Surge XT.vst3"
    chow = ROOT / "plugins/chow-tape-2.11.4/code$GetDir$VST3_64/CHOWTapeModel.vst3"
    chords, bass = [], []
    for bar, (voicing, root) in enumerate((([60, 64, 67], 36), ([57, 60, 64, 67], 33),
                                           ([57, 60, 65], 29), ([55, 60, 62, 65], 31))):
        for index, pitch in enumerate(voicing):
            chords.append({"id": f"chord-{bar}-{index}", "sample": bar * 96000,
                           "duration_samples": 84000, "note": pitch, "velocity": 0.55})
        for beat in range(4):
            bass.append({"id": f"bass-{bar}-{beat}", "sample": bar * 96000 + beat * 24000,
                         "duration_samples": 18000, "note": root, "velocity": 0.65})
    project = {"revision_id": "native-demo-original", "sample_rate": 48000, "block_size": 256,
               "duration_samples": 384000, "tail_samples": 48000,
               "tempo": [{"sample": 0, "bpm": 120}],
               "tracks": [
                   {"id": "harmony", "gain_db": -18, "notes": chords,
                    "plugins": [{"id": "instrument", "path": str(surge)}, {"id": "tape", "path": str(chow)}]},
                   {"id": "bass", "gain_db": -15, "notes": bass,
                    "plugins": [{"id": "instrument", "path": str(surge)}]}]}
    baseline = host.call({"schema_version": 1, "action": "render", "project": project,
                          "output_path": str(output / "original.wav")})
    changed = copy.deepcopy(baseline["snapshot"])
    changed["revision_id"] = "native-demo-sparser-bass"
    # Preserve the harmony events; replace only the bass pattern with half notes
    # and add a fifth in the latter two bars. This is a native snapshot experiment,
    # before the P1 edit-transaction service exists.
    changed_bass = []
    for bar, root in enumerate((36, 33, 29, 31)):
        for half in range(2):
            changed_bass.append({"id": f"new-bass-{bar}-{half}", "sample": bar * 96000 + half * 48000,
                                 "duration_samples": 42000, "note": root + (7 if bar >= 2 and half else 0),
                                 "velocity": 0.65})
    changed["tracks"][1]["notes"] = changed_bass
    altered = host.call({"schema_version": 1, "action": "render", "project": changed,
                         "output_path": str(output / "modified.wav")})
    for name, result in (("original", baseline), ("modified", altered)):
        (output / f"{name}-snapshot.json").write_text(json.dumps(result["snapshot"], ensure_ascii=False), encoding="utf-8")
        (output / f"{name}-result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    report = {"created_utc": datetime.now(timezone.utc).isoformat(),
              "change": "Bass quarter notes -> half notes; latter bars add a fifth; harmony events preserved",
              "harmony_preserved": project["tracks"][0]["notes"] == changed["tracks"][0]["notes"],
              "renders": [{key: result[key] for key in ("revision_id", "output_path", "frames", "peak", "rms", "clipped_samples", "elapsed_seconds")}
                          for result in (baseline, altered)]}
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **report}, indent=2))


if __name__ == "__main__":
    main()
