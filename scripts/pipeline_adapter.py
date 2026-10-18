"""Pinned P2 stage executor. Runs in the isolated transcription environment.

It consumes controlled CAS references and emits validated files plus a manifest.
It has no database writer and never receives NAS paths or arbitrary commands.
"""

from __future__ import annotations

import base64
import copy
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning import contracts as c  # noqa: E402
from music_learning.musical import assemble_candidates, coverage, stable, raw_note_diagnostics  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from music_learning.storage import Storage, digest  # noqa: E402
from media_runtime import verify_tool  # noqa: E402
from midi_to_native import normalize, to_project, load_drum_map  # noqa: E402
from native_host import NativeHost  # noqa: E402

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())


def audio_metadata(path):
    info = sf.info(path)
    peak, power, count, clipped = 0.0, 0.0, 0, 0
    for block in sf.blocks(path, blocksize=65536, dtype="float32", always_2d=True):
        if not np.isfinite(block).all():
            raise ValueError("non-finite audio samples")
        peak = max(peak, float(np.max(np.abs(block))))
        power += float(np.square(block.astype(np.float64)).sum())
        clipped += int((np.abs(block) > 1).sum())
        count += block.size
    return {
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "duration_samples": info.frames,
        "duration_seconds": info.duration,
        "peak": peak,
        "rms": (power / max(count, 1)) ** 0.5,
        "clipped_samples": clipped,
        "offset_samples": 0,
    }


def waveform(path, output):
    info = sf.info(path)
    buckets = []
    for block in sf.blocks(path, blocksize=256, dtype="float32", always_2d=True):
        if not np.isfinite(block).all():
            raise ValueError("invalid waveform samples")
        buckets.append(np.stack([block.min(axis=0), block.max(axis=0)], axis=-1))
    values = np.asarray(buckets)
    levels = []
    stride = 256
    while True:
        levels.append({"samples_per_bucket": stride, "buckets": np.round(values, 6).tolist()})
        if len(values) <= 128:
            break
        groups = []
        for i in range(0, len(values), 4):
            block = values[i : i + 4]
            groups.append(np.stack([block[:, :, 0].min(axis=0), block[:, :, 1].max(axis=0)], axis=-1))
        values = np.asarray(groups)
        stride *= 4
    write_json(
        output,
        {
            "schema_version": 1,
            "asset_sha256": digest(path),
            "sample_rate": info.samplerate,
            "duration_samples": info.frames,
            "channels": info.channels,
            "levels": levels,
        },
    )


class Adapter:
    def __init__(self, directory):
        self.out = directory.resolve()
        data = json.loads((self.out / "input.json").read_text(encoding="utf-8"))
        self.settings = Settings(**data["settings"])
        self.args = data["job"]["payload"]
        self.stage = self.args["stage"]
        self.completed = self.args["completed"]
        self.store = Storage(self.settings)
        self.source = self.args["source"]
        self.rate = self.source["metadata"]["sample_rate"]
        self.frames = self.source["metadata"]["duration_samples"]
        self.duration = self.frames / self.rate
        self.files = []
        self.summary = {}
        for name, expected in self.args["fingerprint"]["code"].items():
            if digest(ROOT / name) != expected:
                raise ValueError(
                    "application code changed during preprocessing; retry against one consistent version"
                )
        if digest(self.settings.native_host) != self.args["fingerprint"]["native_host_sha256"]:
            raise ValueError("native host changed during preprocessing")

    def add(self, path, *, audio=False):
        path = path.resolve()
        if not path.is_relative_to(self.out):
            raise ValueError("stage output escaped its owned local directory")
        if audio:
            metadata, mime = audio_metadata(path), "audio/wav"
        else:
            metadata = {}
            mime = "application/json" if path.suffix in (".json", ".jsonl") else "application/octet-stream"
        self.files.append(
            {
                "name": path.relative_to(self.out).as_posix(),
                "sha256": digest(path),
                "bytes": path.stat().st_size,
                "media_type": mime,
                "metadata": metadata,
            }
        )
        return self.files[-1]

    def add_waveform(self, path, name):
        destination = self.out / "waveforms" / (name + ".json")
        destination.parent.mkdir(exist_ok=True)
        waveform(path, destination)
        self.add(destination)

    def input_file(self, stage, name):
        file = next(f for f in self.completed[stage]["files"] if f["name"] == name)
        return self.store.verify(file["sha256"])

    def input_json(self, stage, name):
        return json.loads(self.input_file(stage, name).read_text(encoding="utf-8"))

    def convert_audio(self, source, destination, rate, *, expected_frames=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                str(verify_tool("ffmpeg")),
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-n",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-ac",
                "2",
                "-ar",
                str(rate),
                "-c:a",
                "pcm_f32le",
                str(destination),
            ],
            check=True,
            capture_output=True,
            timeout=self.settings.pipeline_stage_timeout_seconds,
        )
        info = sf.info(destination)
        if info.samplerate != rate or info.channels != 2:
            raise ValueError("normalized audio format differs")
        if expected_frames is not None and info.frames != expected_frames:
            raise ValueError("derived audio length differs from original sample time base")

    def normalize(self):
        source = self.store.verify(self.source["id"])
        normalized = self.out / "normalized.wav"
        self.convert_audio(source, normalized, self.rate, expected_frames=self.frames)
        self.add(normalized, audio=True)
        self.add_waveform(normalized, "source")
        self.summary = {
            "duration_samples": self.frames,
            "sample_rate": self.rate,
            "offset_samples": 0,
            "gain_applied_db": 0,
            "source_sha256": self.source["id"],
        }

    def run_model(self, model):
        python = (
            self.settings.model_runtime
            / "envs"
            / ("sheetsage" if model == "sheetsage" else "transcription")
            / "Scripts/python.exe"
        )
        output = self.out / "model"
        output.mkdir()
        command = [
            str(python),
            str(ROOT / "scripts/p0_model_worker.py"),
            "--model",
            model,
            "--input",
            str(self.input_file("normalize", "normalized.wav")),
            "--output",
            str(output),
        ]
        env = dict(
            os.environ,
            MUSIC_MODEL_RUNTIME=str(self.settings.model_runtime),
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
        )
        with (
            (self.out / "model.stdout.log").open("x", encoding="utf-8") as log,
            (self.out / "model.stderr.log").open("xb") as err,
        ):
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=err,
                text=True,
                encoding="utf-8",
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            for line in process.stdout:
                log.write(line)
                log.flush()
                try:
                    progress = json.loads(line)
                    if isinstance(progress, dict):
                        temp = self.out / "progress.partial"
                        temp.write_text(json.dumps(progress), encoding="utf-8")
                        os.replace(temp, self.out / "progress.json")
                except (ValueError, UnicodeError):
                    pass
            if process.wait() != 0:
                raise RuntimeError(model + " model failed; inspect this stage's local model.stderr.log")
        report = json.loads((output / "worker-result.json").read_text(encoding="utf-8"))
        if report["status"] != "completed" or report["input"]["sha256"] != digest(
            self.input_file("normalize", "normalized.wav")
        ):
            raise ValueError("model result does not match normalized source")
        for file in sorted(output.rglob("*")):
            if file.is_file() and file.suffix != ".log":
                self.add(file, audio=file.suffix.lower() == ".wav")
        return output, report

    def demucs(self):
        output, report = self.run_model("demucs")
        stems = {s["name"]: s for s in report["result"]["stems"]}
        if set(stems) != {"vocals", "drums", "bass", "other"}:
            raise ValueError("missing or duplicate separated stems")
        for name, item in stems.items():
            if abs(
                Fraction(item["frames"], item["sample_rate"]) - Fraction(self.frames, self.rate)
            ) > Fraction(1, item["sample_rate"]):
                raise ValueError("Demucs did not cover the whole source")
            target = self.out / "stems" / (name + ".wav")
            self.convert_audio(output / (name + ".wav"), target, self.rate, expected_frames=self.frames)
            self.add(target, audio=True)
            self.add_waveform(target, "stem-" + name)
        self.summary = {
            "stems": list(stems),
            "covered_samples": self.frames,
            "offset_samples": 0,
            "settings": report["result"]["settings"],
            "cuda": report["cuda"],
            "elapsed_seconds": report["elapsed_seconds"],
        }

    def muscriptor(self):
        output, report = self.run_model("muscriptor")
        result = report["result"]
        if result["completed_chunks"] != result["total_chunks"] or result["total_chunks"] != int(
            np.ceil(self.duration / 5)
        ):
            raise ValueError("MuScriptor chunks do not cover the full song")
        normalized = normalize(output / "transcription.mid", self.rate)
        if normalized["unhandled_events"] or normalized["issues"]:
            raise ValueError("ambiguous or unsupported MIDI cannot be silently reconstructed")
        notes = [n for t in normalized["tracks"] for n in t["notes"]]
        if not notes or any(n["sample"] + n["duration_samples"] > self.frames for n in notes):
            raise ValueError("empty or out-of-range MIDI events")
        write_json(self.out / "normalized-midi.json", normalized)
        self.add(self.out / "normalized-midi.json")
        with (output / "events.jsonl").open(encoding="utf-8") as stream:
            diagnostics = raw_note_diagnostics((json.loads(line) for line in stream), len(notes))
        self.summary = {
            **result,
            "notes": len(notes),
            **diagnostics,
            "tracks": len(normalized["tracks"]),
            "first_note_sample": min(n["sample"] for n in notes),
            "last_note_end_sample": max(n["sample"] + n["duration_samples"] for n in notes),
            "controllers": sum(len(t["controllers"]) for t in normalized["tracks"]),
            "covered_samples": self.frames,
            "cuda": report["cuda"],
            "elapsed_seconds": report["elapsed_seconds"],
        }

    def sheetsage(self):
        output, report = self.run_model("sheetsage")
        result = report["result"]["model_result"]
        coverage(result["windows"], self.duration)
        events = json.loads((output / "analysis/events.json").read_text(encoding="utf-8"))
        if not events["events"] or not events["has_eos"] or result["warnings"]:
            raise ValueError("SheetSage incomplete or generation limit reached")
        required = ("beat.lab", "chord.lab", "key.lab", "structure.lab")
        if any(not (output / "analysis" / f).read_text(encoding="utf-8").strip() for f in required):
            raise ValueError("missing full-song structure candidates")
        self.summary = {
            "windows": result["windows"],
            "covered_samples": self.frames,
            "events": result["events"],
            "diagnostics": result["diagnostics"],
            "warnings": result["warnings"],
            "cuda": report["cuda"],
            "elapsed_seconds": report["elapsed_seconds"],
        }

    def assemble(self):
        normalized = self.input_json("muscriptor", "normalized-midi.json")
        beats = [
            [float(a), int(b), int(n), int(d)]
            for a, b, n, d in (
                line.split()
                for line in self.input_file("sheetsage", "model/analysis/beat.lab").read_text().splitlines()
                if line.strip()
            )
        ]
        labels = {}
        for kind, name in (("chord", "chord.lab"), ("key", "key.lab"), ("section", "structure.lab")):
            labels[kind] = []
            for line in (
                self.input_file("sheetsage", "model/analysis/" + name)
                .read_text(encoding="utf-8")
                .splitlines()
            ):
                if line.strip():
                    a, b, label = line.split(maxsplit=2)
                    labels[kind].append((float(a), float(b), label))
        stems = {
            name: next(
                f["sha256"]
                for f in self.completed["demucs"]["files"]
                if f["name"] == "stems/" + name + ".wav"
            )
            for name in ("vocals", "drums", "bass", "other")
        }
        snapshot, issues = assemble_candidates(
            normalized,
            stems,
            beats,
            labels,
            namespace=self.args["target_revision"],
            run_ids=self.args["run_ids"],
            source_asset_id=self.source["id"],
            sample_rate=self.rate,
            duration_samples=self.frames,
        )
        profile = self.args["fingerprint"]["profile"]
        mapping = load_drum_map(self.settings.model_runtime / profile["drum_map"])
        # Copy only validated, flat self-created resources to this local stage.
        resource_dir = self.out / "resources"
        resource_dir.mkdir()
        for item in mapping["assets"]:
            if Path(item["path"]).name != item["path"]:
                raise ValueError("default resource adapter only accepts flat resource names")
            target = resource_dir / item["path"]
            shutil.copyfile(mapping["sfz_path"].parent / item["path"], target)
            self.add(target)
        native, assignments = to_project(normalized, self.frames, drum_mapping=mapping)
        native["revision_id"] = self.args["target_revision"]
        for track, candidate in zip(native["tracks"], snapshot.tracks):
            track["id"] = candidate.id
            track["plugins"][0]["path"] = str(self.settings.plugin_path(track["plugins"][0]["path"]))
            if not next(
                v["is_drum"]
                for v in normalized["tracks"]
                if stable(self.args["target_revision"], "track:" + v["id"]) == candidate.id
            ):
                track["plugins"][0]["binary_sha256"] = self.args["fingerprint"]["plugin_sha256"]["surge"]
            else:
                track["plugins"][0]["binary_sha256"] = self.args["fingerprint"]["plugin_sha256"]["sfizz"]
            # Native transport metadata is derived from candidate timing, while
            # performed notes remain anchored at original samples.
        if snapshot.timing_map:
            if snapshot.timing_map.pickup.n:
                issues.append(
                    "Native VST3 transport PPQ starts at audio zero; pickup bar labels are candidate notation. Performed sample timing is preserved."
                )
            anchors = snapshot.timing_map.anchors
            native["tempo"] = [
                {
                    "sample": a.sample,
                    "bpm": float(
                        (b.beat.fraction() - a.beat.fraction()) * 60 * self.rate / (b.sample - a.sample)
                    ),
                }
                for a, b in zip(anchors, anchors[1:])
            ]
            native["meter"] = [
                {
                    "sample": 0,
                    "numerator": snapshot.timing_map.meters[0].numerator,
                    "denominator": snapshot.timing_map.meters[0].denominator,
                }
            ]
            for meter in snapshot.timing_map.meters[1:]:
                sample = next(a.sample for a in anchors if a.beat.fraction() == meter.beat.fraction())
                native["meter"].append(
                    {"sample": sample, "numerator": meter.numerator, "denominator": meter.denominator}
                )
        else:
            issues.append(
                "Native plugin transport uses explicit fallback 120 BPM / 4/4; performed note times unchanged."
            )
        write_json(
            self.out / "musical.json",
            {
                "snapshot": snapshot.model_dump(mode="json"),
                "issues": issues,
                "assignments": assignments,
                "native_timing_fallback": snapshot.timing_map is None,
            },
        )
        write_json(self.out / "native-project.json", native)
        self.add(self.out / "musical.json")
        self.add(self.out / "native-project.json")
        self.summary = {
            "tracks": len(snapshot.tracks),
            "notes": len(snapshot.notes),
            "annotations": len(snapshot.annotations),
            "timing_map_available": snapshot.timing_map is not None,
            "issues": issues,
        }

    def render(self):
        musical = self.input_json("assemble", "musical.json")
        snapshot = c.Snapshot.model_validate(musical["snapshot"])
        project = self.input_json("assemble", "native-project.json")
        resources = {}
        for item in self.completed["assemble"]["files"]:
            if item["name"].startswith("resources/"):
                relative = Path(item["name"])
                target = self.out / relative
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(self.store.verify(item["sha256"]), target)
                resources[relative.name] = item["sha256"]
        for track in project["tracks"]:
            if "sfizz_sfz_path" in track["plugins"][0]:
                track["plugins"][0]["sfizz_sfz_path"] = str(self.out / "resources/kit.sfz")
        host = NativeHost(self.settings.native_host, self.out / "native-calls")
        mix = self.out / "mix.wav"
        result = host.call(
            {"schema_version": 1, "action": "render", "project": project, "output_path": str(mix)},
            timeout=self.settings.pipeline_stage_timeout_seconds,
        )
        if result["snapshot"]["revision_id"] != self.args["target_revision"]:
            raise ValueError("native render belongs to another revision")
        master = stable(self.args["target_revision"], "master")
        nodes, edges = [master], []
        for track, native_track in zip(snapshot.tracks, result["snapshot"]["tracks"]):
            if track.id != native_track["id"]:
                raise ValueError("native track order/identity changed")
            previous = track.id
            nodes.append(previous)
            for index, plugin in enumerate(native_track["plugins"]):
                plugin_id = stable(self.args["target_revision"], f"plugin:{track.id}:{index}")
                state = base64.b64decode(plugin["state_base64"], validate=True)
                state_path = self.out / "states" / (plugin_id + ".bin")
                state_path.parent.mkdir(exist_ok=True)
                state_path.write_bytes(state)
                state_item = self.add(state_path)
                if state_item["sha256"] != plugin["state_sha256"]:
                    raise ValueError("plugin opaque state checksum changed")
                descriptor = self.out / "states" / (plugin_id + ".json")
                write_json(descriptor, plugin)
                descriptor_item = self.add(descriptor)
                source_track = next(t for t in project["tracks"] if t["id"] == track.id)
                uses_resources = "sfizz_sfz_path" in source_track["plugins"][index]
                if uses_resources:
                    plugin["sfizz_sfz_path"] = str(self.out / "resources/kit.sfz")
                snapshot.plugins.append(
                    c.PluginInstance(
                        id=plugin_id,
                        track_id=track.id,
                        class_id=plugin["vst3_class_id"],
                        version=plugin["version"],
                        binary_sha256=plugin["binary_sha256"],
                        state_asset_id=state_item["sha256"],
                        descriptor_asset_id=descriptor_item["sha256"],
                        resource_asset_ids=list(resources.values()) if uses_resources else [],
                        resource_paths=resources if uses_resources else {},
                        resource_entrypoint="kit.sfz" if uses_resources else None,
                        parameters={p["id"]: p["value"] for p in plugin["parameters_readback"]},
                        order=index,
                    )
                )
                nodes.append(plugin_id)
                edges.append(c.RoutingEdge(source=previous, target=plugin_id))
                previous = plugin_id
            edges.append(c.RoutingEdge(source=previous, target=master))
            isolated = copy.deepcopy(result["snapshot"])
            isolated["tracks"] = [native_track]
            target = self.out / "tracks" / (track.id + ".wav")
            target.parent.mkdir(exist_ok=True)
            stem_result = host.call(
                {"schema_version": 1, "action": "render", "project": isolated, "output_path": str(target)},
                timeout=self.settings.pipeline_stage_timeout_seconds,
            )
            if [p["state_sha256"] for p in stem_result["snapshot"]["tracks"][0]["plugins"]] != [
                p["state_sha256"] for p in native_track["plugins"]
            ]:
                raise ValueError("plugin state did not restore for individual track rendering")
            self.add(target, audio=True)
            self.add_waveform(target, "track-" + track.id)
        snapshot.routing = c.RoutingGraph(
            id=stable(self.args["target_revision"], "routing"), nodes=nodes, edges=edges
        )
        c.Snapshot.model_validate(snapshot)
        write_json(self.out / "full-snapshot.json", snapshot.model_dump(mode="json"))
        write_json(self.out / "native-snapshot.json", result["snapshot"])
        write_json(self.out / "native-result.json", result)
        for name in ("full-snapshot.json", "native-snapshot.json", "native-result.json"):
            self.add(self.out / name)
        self.add(mix, audio=True)
        self.add_waveform(mix, "mix")
        self.summary = {
            "revision_id": snapshot and self.args["target_revision"],
            "tracks": len(snapshot.tracks),
            "plugins": len(snapshot.plugins),
            "tail_samples": project["tail_samples"],
            "timbre_note": self.args["fingerprint"]["profile"]["timbre_note"],
        }

    def validate(self):
        snapshot = c.Snapshot.model_validate(self.input_json("render", "full-snapshot.json"))
        issues = list(self.completed["assemble"]["summary"]["issues"])
        issues.extend(self.completed["sheetsage"]["summary"]["diagnostics"])
        mu = self.completed["muscriptor"]["summary"]
        if mu["raw_to_midi_note_difference"]:
            issues.append(
                f"Raw MuScriptor NoteStart count differs from exported MIDI by {mu['raw_to_midi_note_difference']}; raw events retained."
            )
        issues.append(
            "Model processing coverage does not establish note/chord accuracy; separation leakage may remain."
        )
        expected_frames = self.frames + int(self.args["fingerprint"]["profile"]["tail_seconds"] * self.rate)
        renders = [f for f in self.completed["render"]["files"] if f["name"].endswith(".wav")]
        if len(renders) != len(snapshot.tracks) + 1:
            raise ValueError("missing per-track reconstruction or mix")
        for item in renders:
            m = item["metadata"]
            if (
                m["duration_samples"] != expected_frames
                or m["sample_rate"] != self.rate
                or m["channels"] != 2
            ):
                raise ValueError("render length/rate/layout differs from revision")
            if m["clipped_samples"] or m["rms"] <= 1e-8:
                raise ValueError("clipped or silent reconstruction")
        if len(snapshot.stems) != 4 or len(snapshot.tracks) != len(snapshot.plugins) or not snapshot.routing:
            raise ValueError("incomplete editable snapshot")
        kinds = {a.kind for a in snapshot.annotations}
        if not {"beat", "chord", "key", "section"} <= kinds:
            raise ValueError("missing required music candidates")
        for stage, manifest in self.completed.items():
            if not manifest.get("validated"):
                raise ValueError("unvalidated upstream stage")
            for artifact in manifest["files"]:
                self.store.verify(artifact["sha256"])
        report = {
            "schema_version": 1,
            "ready": True,
            "source_sha256": self.source["id"],
            "revision_id": self.args["target_revision"],
            "duration_samples": self.frames,
            "sample_rate": self.rate,
            "tracks": len(snapshot.tracks),
            "notes": len(snapshot.notes),
            "stems": 4,
            "render_frames": expected_frames,
            "timing_map_available": snapshot.timing_map is not None,
            "issues": issues,
            "processed_coverage_samples": [0, self.frames],
            "model_outputs_reviewed": False,
            "ready_meaning": "all required full-song artifacts validated; not a claim of musical accuracy",
        }
        write_json(self.out / "completeness.json", report)
        self.add(self.out / "completeness.json")
        self.summary = report

    def run(self):
        getattr(self, self.stage)()
        write_json(
            self.out / "stage-result.json",
            {
                "schema_version": 1,
                "stage": self.stage,
                "validated": True,
                "files": self.files,
                "summary": self.summary,
            },
        )


if __name__ == "__main__":
    Adapter(Path(sys.argv[1])).run()
