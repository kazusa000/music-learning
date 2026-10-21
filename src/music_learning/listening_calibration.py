"""Source-referenced audition gains, separate from musical mixer faders."""
import json
import math
import subprocess


def measure(path, ffmpeg):
    result = subprocess.run([str(ffmpeg), "-nostdin", "-hide_banner", "-i", str(path),
        "-af", "loudnorm=I=-23:TP=-1:LRA=7:print_format=json", "-f", "null", "-"],
        capture_output=True, check=True, timeout=180)
    log = result.stderr.decode("utf-8", errors="replace")
    data = json.loads(log[log.rfind("{"):log.rfind("}")+1])
    lufs, peak = float(data["input_i"]), float(data["input_tp"])
    if not math.isfinite(lufs) or not math.isfinite(peak):
        raise ValueError("Silent or invalid audio cannot be loudness calibrated")
    return {"integrated_lufs": lufs, "true_peak_dbtp": peak}


def gains(source, mix, tracks):
    target = source["integrated_lufs"]
    def gain(measured):
        value = target-measured["integrated_lufs"]
        if not -60 <= value <= 60:
            raise ValueError("Unexpected loudness offset; inspect instrument output")
        return value
    return {"algorithm": "source-ebu-r128-v1", "target_lufs": target,
            "mix_gain_db": gain(mix), "solo_gain_db": {id:gain(m) for id,m in tracks.items()},
            "source": source, "mix":mix, "tracks":tracks}
