"""Isolated P3 production child. No database writes, network paths or arbitrary code."""

# ruff: noqa: E402
from __future__ import annotations

import base64
import importlib.metadata
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_learning import contracts as c
from music_learning.graph import compile_project
from music_learning.settings import Settings
from music_learning.storage import Storage, digest
from native_host import NativeHost, NativeHostError
from media_runtime import verify_tool
from pipeline_adapter import audio_metadata, waveform, write_json
import numpy as np
import soundfile as sf


class ProductionError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.public_error = dict(code=code, message=message, details=details)


class Production:
    def __init__(self, directory):
        self.directory = directory
        config = json.loads((directory / "input.json").read_bytes())
        self.settings, self.job = Settings(**config["settings"]), config["job"]
        self.store = Storage(self.settings)
        self.files = []
        self.allowed = set(self.job["allowed_asset_ids"])
        self.code_paths = [
            Path(__file__),
            *sorted((ROOT / "src/music_learning").glob("*.py")),
            *sorted(ROOT.glob("requirements*.lock")),
            ROOT / "scripts/pipeline_adapter.py",
            ROOT / "scripts/native_host.py",
            ROOT / "scripts/media_runtime.py",
            ROOT / "config/media-runtime.json",
            self.settings.native_host,
        ]
        self.environment = dict(
            python=sys.version,
            packages={
                name: importlib.metadata.version(name) for name in ("numpy", "soundfile", "mido", "pydantic")
            },
        )
        self.fingerprint = {
            str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else "native_host": digest(path)
            for path in self.code_paths
        }

    def path(self, sha):
        if sha not in self.allowed:
            raise ValueError("asset is not registered in this project")
        return self.store.verify(sha)

    def add(self, path, metadata=None):
        relative = path.relative_to(self.directory).as_posix()
        item = dict(
            name=relative,
            sha256=digest(path),
            bytes=path.stat().st_size,
            relative_path=path.relative_to(self.settings.data_root).as_posix(),
            media_type={".wav": "audio/wav", ".mid": "audio/midi", ".json": "application/json"}.get(
                path.suffix, "application/octet-stream"
            ),
            metadata=metadata or {},
        )
        self.files.append(item)
        return item["sha256"]

    def render(self):
        s = c.Snapshot.model_validate(self.job["snapshot"])
        payload = self.job["payload"]
        if s.duration_samples / s.sample_rate > self.settings.pipeline_max_duration_seconds:
            raise ValueError("project exceeds the configured offline render duration budget")
        estimated = (
            (len(s.tracks) + 3 + len(s.frozen_tracks) + len({cl.asset_id for cl in s.clips if cl.asset_id}))
            * (s.duration_samples + round(payload["tail_seconds"] * s.sample_rate))
            * 8
        )
        if (
            shutil.disk_usage(self.settings.data_root).free
            < estimated + self.settings.pipeline_min_free_bytes
        ):
            raise ValueError("insufficient local disk space for graph outputs and safety reserve")
        paths = {}
        for index, sha in enumerate(
            sorted({clip.asset_id for clip in s.clips if clip.asset_id} | set(s.frozen_tracks.values()))
        ):
            # JUCE chooses decoders using file extension; CAS hashes have none.
            # The SHA remains the dictionary identity. Short deterministic names
            # leave room for the task path on Windows readers with MAX_PATH limits.
            target = self.directory / "sources" / (str(index) + ".wav")
            target.parent.mkdir(exist_ok=True)
            subprocess.run(
                [
                    str(verify_tool("ffmpeg")),
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(self.path(sha)),
                    "-acodec",
                    "pcm_f32le",
                    str(target),
                ],
                check=True,
                timeout=180,
            )
            paths[sha] = str(target)
        descriptors, resources = {}, {}
        for plugin in s.plugins:
            if plugin.track_id in s.frozen_tracks:
                continue
            if not plugin.descriptor_asset_id:
                raise ValueError("plugin lacks a captured descriptor; inspect before using it")
            raw = json.loads(self.path(plugin.descriptor_asset_id).read_bytes())
            descriptor = raw.get("plugin", raw)
            descriptors[plugin.id] = descriptor
            if plugin.bypass:
                continue
            if not Path(descriptor["path"]).exists():
                raise ProductionError(
                    "plugin_missing",
                    "Required VST3 is unavailable; saved state and frozen versions remain intact",
                    dict(plugin_id=plugin.id, class_id=plugin.class_id),
                )
            self.settings.plugin_path(descriptor["path"])
            resources[plugin.id] = {
                "state_base64": base64.b64encode(self.path(plugin.state_asset_id).read_bytes()).decode(
                    "ascii"
                )
            }
            resource_dir = self.directory / "resources" / plugin.id
            for relative, sha in plugin.resource_paths.items():
                target = resource_dir / relative
                if not target.resolve().is_relative_to(resource_dir.resolve()):
                    raise ValueError("resource escaped local plugin directory")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.path(sha), target)
                self.add(target)
            if plugin.resource_entrypoint:
                target = resource_dir / plugin.resource_entrypoint
                if not target.resolve().is_relative_to(resource_dir.resolve()) or not target.is_file():
                    raise ValueError("invalid plugin resource entrypoint")
                resources[plugin.id]["entrypoint"] = str(target)
        project = compile_project(
            s,
            self.job["revision_id"],
            descriptors,
            paths,
            resources,
            tail_seconds=payload["tail_seconds"],
            block_size=payload["block_size"],
        )
        end = payload["end_sample"]
        if end != s.duration_samples:
            if s.frozen_tracks:
                raise ValueError(
                    "unfreeze before rendering a truncated excerpt; future frozen notes cannot be isolated"
                )
            project["duration_samples"] = end
            project["tempo"] = [x for x in project["tempo"] if x["sample"] < end]
            project["meter"] = [x for x in project["meter"] if x["sample"] < end]
            for track in project["tracks"]:
                track["notes"] = [
                    dict(n, duration_samples=min(n["duration_samples"], end - n["sample"]))
                    for n in track.get("notes", [])
                    if n["sample"] < end
                ]
                track["controllers"] = [e for e in track.get("controllers", []) if e["sample"] < end]
                track["audio_clips"] = [
                    dict(cl, duration_samples=min(cl["duration_samples"], end - cl["sample"]))
                    for cl in track.get("audio_clips", [])
                    if cl["sample"] < end
                ]
                lanes = []
                for lane in track.get("automation", []):
                    points = [p for p in lane["points"] if p["sample"] <= end]
                    if not points:
                        continue
                    if lane["interpolation"] == "linear":
                        after = next((p for p in lane["points"] if p["sample"] > end), None)
                        before = points[-1]
                        if after and before["sample"] < end:
                            points.append(
                                dict(
                                    sample=end,
                                    value=before["value"]
                                    + (after["value"] - before["value"])
                                    * (end - before["sample"])
                                    / (after["sample"] - before["sample"]),
                                )
                            )
                    lanes.append(dict(lane, points=points))
                track["automation"] = lanes
        native = NativeHost(self.settings.native_host, self.directory / "native-calls").call(
            dict(
                schema_version=1,
                action="render",
                project=project,
                output_path=str(self.directory / "mix.wav"),
            ),
            timeout=self.settings.pipeline_stage_timeout_seconds,
        )
        if native["revision_id"] != self.job["revision_id"]:
            raise ValueError("native returned another revision")
        # Full preroll deliberately precedes excerpt slicing, preserving state,
        # sustain and effect history. Every file records its source-clock offset.
        start, end = payload["start_sample"], payload["end_sample"]
        tail = project["tail_samples"]
        rendered = {node["id"]: Path(node["output_path"]) for node in native["node_renders"]}
        rendered["mix"] = self.directory / "mix.wav"
        outputs, waves, measurements = {}, {}, {}
        if s.source_asset_id:
            source_wave = self.directory / "source-reference.wav"
            subprocess.run(
                [
                    str(verify_tool("ffmpeg")),
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(self.path(s.source_asset_id)),
                    "-ar",
                    str(s.sample_rate),
                    "-ac",
                    "2",
                    "-acodec",
                    "pcm_f32le",
                    str(source_wave),
                ],
                check=True,
                timeout=180,
            )
            source_plot = self.directory / "waveforms" / "source.json"
            source_plot.parent.mkdir(exist_ok=True)
            waveform(source_wave, source_plot)
            waves["waveforms/source.json"] = self.add(source_plot)
        for key, source in rendered.items():
            target = source
            if start or end != s.duration_samples:
                target = self.directory / "excerpts" / (key + ".wav")
                target.parent.mkdir(exist_ok=True)
                with (
                    sf.SoundFile(source) as reader,
                    sf.SoundFile(
                        target, "w", samplerate=s.sample_rate, channels=2, subtype="FLOAT"
                    ) as writer,
                ):
                    reader.seek(start)
                    remaining = end - start + tail
                    while remaining:
                        block = reader.read(min(65536, remaining), dtype="float32", always_2d=True)
                        if not len(block):
                            raise ValueError("native file does not cover render interval")
                        writer.write(block)
                        remaining -= len(block)
            metadata = audio_metadata(target)
            metadata["offset_samples"] = start
            if (
                metadata["duration_samples"] != end - start + tail
                or metadata["sample_rate"] != s.sample_rate
                or metadata["channels"] != 2
            ):
                raise ValueError("native output dimensions differ")
            outputs[key] = self.add(target, metadata)
            measurements[key] = metadata
            wave = self.directory / "waveforms" / (key + ".json")
            wave.parent.mkdir(exist_ok=True)
            waveform(target, wave)
            waves["waveforms/" + key + ".json"] = self.add(wave)
        # Capture actual processor/controller state and all read-back parameters.
        captured = {}
        for track in native["snapshot"]["tracks"]:
            for plugin in track.get("plugins", []):
                identifier = plugin["id"]
                path = self.directory / "states" / (identifier + ".bin")
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(base64.b64decode(plugin["state_base64"], validate=True))
                sha = self.add(path)
                if sha != plugin["state_sha256"]:
                    raise ValueError("native state checksum differs")
                desc = path.with_suffix(".json")
                write_json(desc, plugin)
                captured[identifier] = dict(
                    state_asset_id=sha,
                    descriptor_asset_id=self.add(desc),
                    parameters={p["id"]: p["value"] for p in plugin["parameters_readback"]},
                )
        native_path = self.directory / "native-snapshot.json"
        write_json(native_path, native["snapshot"])
        native_sha = self.add(native_path)
        issues = [
            "Model transcription and harmony remain candidates; reconstruction uses substitute timbres."
        ]
        if not s.timing_map:
            issues.append(
                "Plugin transport fallback is explicitly 120 BPM / 4/4; notes retain source sample timing."
            )
        report = dict(
            revision_id=self.job["revision_id"],
            snapshot_sha256=self.job["snapshot_sha256"],
            measurements=measurements,
            captured_plugins=captured,
            issues=issues,
            pre_roll_samples=start,
            implementation_sha256=self.fingerprint,
            environment=self.environment,
        )
        report_path = self.directory / "report.json"
        write_json(report_path, report)
        report_sha = self.add(report_path)
        manifest = self.directory / "manifest.json"
        write_json(
            manifest,
            dict(
                revision_id=self.job["revision_id"],
                files=self.files,
                graph=project["graph"],
                start_sample=start,
                end_sample=end,
                tail_samples=tail,
                implementation_sha256=self.fingerprint,
                environment=self.environment,
            ),
        )
        manifest_sha = self.add(manifest)
        return dict(
            project_id=self.job["project_id"],
            revision_id=self.job["revision_id"],
            job_id=self.job["id"],
            manifest_asset_id=manifest_sha,
            report_asset_id=report_sha,
            mix_asset_id=outputs.pop("mix"),
            native_snapshot_asset_id=native_sha,
            track_renders=outputs,
            waveforms=waves,
            issues=issues,
            captured_plugins=captured,
            measurements=measurements,
            start_sample=start,
            end_sample=end,
            tail_samples=tail,
            created_at=time.time(),
        )

    def measure(self):
        payload = self.job["payload"]
        source = self.path(payload["asset_id"])
        decoded = self.directory / "measurement-input.wav"
        subprocess.run(
            [
                str(verify_tool("ffmpeg")),
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(source),
                "-acodec",
                "pcm_f32le",
                str(decoded),
            ],
            check=True,
            timeout=180,
        )
        source = decoded
        start, end = payload["start_sample"], payload["end_sample"]
        info = sf.info(source)
        end = info.frames if end is None else end
        if not 0 <= start < end <= info.frames:
            raise ValueError("measurement interval exceeds audio")
        target = self.directory / "measurement.wav"
        count = silent = clipped = 0
        peak = power = 0.0
        bands = np.zeros(6)
        with (
            sf.SoundFile(source) as reader,
            sf.SoundFile(
                target, "w", samplerate=info.samplerate, channels=info.channels, subtype="FLOAT"
            ) as writer,
        ):
            reader.seek(start)
            remaining = end - start
            while remaining:
                block = reader.read(min(65536, remaining), dtype="float32", always_2d=True)
                if not len(block) or not np.isfinite(block).all():
                    raise ValueError("invalid audio data")
                writer.write(block)
                remaining -= len(block)
                count += block.size
                peak = max(peak, float(np.abs(block).max()))
                power += float(np.square(block.astype("float64")).sum())
                clipped += int((np.abs(block) >= 1).sum())
                silent += int((np.abs(block) < 1e-5).sum())
                mono = block.mean(axis=1)
                for offset in range(0, len(mono) - 2047, 2048):
                    fft = np.abs(np.fft.rfft(mono[offset : offset + 2048] * np.hanning(2048))) ** 2
                    freq = np.fft.rfftfreq(2048, 1 / info.samplerate)
                    for i, (a, b) in enumerate(
                        zip(
                            [0, 60, 250, 1000, 4000, 12000],
                            [60, 250, 1000, 4000, 12000, info.samplerate / 2 + 1],
                        )
                    ):
                        bands[i] += fft[(freq >= a) & (freq < b)].sum()
        measured = subprocess.run(
            [
                str(verify_tool("ffmpeg")),
                "-nostdin",
                "-hide_banner",
                "-i",
                str(target),
                "-af",
                "loudnorm=I=-23:TP=-1:LRA=7:print_format=json",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=180,
        )
        diagnostic = measured.stderr.decode("utf-8", errors="replace")
        raw = json.loads(diagnostic[diagnostic.rfind("{") : diagnostic.rfind("}") + 1])

        def numeric(key):
            value = float(raw[key])
            return value if math.isfinite(value) else None

        result = dict(
            asset_id=payload["asset_id"],
            start_sample=start,
            end_sample=end,
            sample_rate=info.samplerate,
            peak=peak,
            rms=(power / count) ** 0.5,
            clipped_samples=clipped,
            silence_sample_ratio=silent / count,
            integrated_lufs=numeric("input_i"),
            true_peak_dbtp=numeric("input_tp"),
            loudness_range_lu=numeric("input_lra"),
            spectrum_band_power_fraction=(bands / max(float(bands.sum()), 1e-30)).tolist(),
            algorithms={
                "loudness": "FFmpeg loudnorm input EBU R128",
                "silence": "abs(sample)<1e-5 (-100 dBFS)",
                "spectrum": "2048 Hann FFT, bands 0/60/250/1000/4000/12000/Nyquist Hz",
            },
        )
        path = self.directory / "measurement.json"
        write_json(path, result)
        result["report_asset_id"] = self.add(path)
        return result

    def export(self):
        from music_learning.midi import export_midi

        payload = self.job["payload"]
        if payload["format"] == "hybrid":
            return self.hybrid()
        if payload["format"] == "wav":
            path = self.directory / "export.wav"
            shutil.copyfile(self.path(payload["audio_asset_id"]), path)
        else:
            path = self.directory / "export.mid"
            export_midi(c.Snapshot.model_validate(self.job["snapshot"]), path, self.job["revision_id"])
        return dict(
            format=payload["format"], asset_id=self.add(path), snapshot_sha256=self.job["snapshot_sha256"]
        )

    def hybrid(self):
        from contextlib import ExitStack

        s = c.Snapshot.model_validate(self.job["snapshot"])
        recipe, payload = self.job["hybrid"], self.job["payload"]
        a, b = recipe["start_sample"], recipe["end_sample"]
        tail = recipe["tail_samples"] if b == s.duration_samples else 0
        frames = s.duration_samples + tail
        if (
            shutil.disk_usage(self.settings.data_root).free
            < frames * 8 + self.settings.pipeline_min_free_bytes
        ):
            raise ValueError("insufficient local space for hybrid output")
        target = self.directory / "hybrid.wav"
        fade = min(round(payload["fade_seconds"] * s.sample_rate), (b - a) // 2)
        gain = 10 ** (payload["replacement_gain_db"] / 20)
        peak, power, count, clipped = 0.0, 0.0, 0, 0
        with ExitStack() as stack:
            source = stack.enter_context(sf.SoundFile(self.path(recipe["source_asset_id"])))
            replacement = stack.enter_context(sf.SoundFile(self.path(recipe["replacement_asset_id"])))
            others = [
                (stem, stack.enter_context(sf.SoundFile(self.path(stem["asset_id"]))))
                for stem in recipe["stems"]
                if stem["name"] != "bass"
            ]
            for reader in [source, replacement, *(reader for _, reader in others)]:
                if reader.samplerate != s.sample_rate or reader.channels not in (1, 2):
                    raise ValueError("hybrid input dimensions do not match the source clock")

            def aligned(reader, start, n, offset=0):
                out = np.zeros((n, 2), dtype=np.float32)
                src = max(0, start - offset)
                dest = max(0, offset - start)
                size = min(n - dest, reader.frames - src)
                if size > 0:
                    reader.seek(src)
                    block = reader.read(size, dtype="float32", always_2d=True)
                    out[dest : dest + size] = block if block.shape[1] == 2 else np.repeat(block, 2, axis=1)
                if not np.isfinite(out).all():
                    raise ValueError("nonfinite hybrid input")
                return out

            writer = stack.enter_context(
                sf.SoundFile(target, "w", samplerate=s.sample_rate, channels=2, subtype="FLOAT")
            )
            for start in range(0, frames, 65536):
                n = min(65536, frames - start)
                original = aligned(source, start, n)
                new = aligned(replacement, start, n) * gain
                for stem, reader in others:
                    new += aligned(reader, start, n, stem["offset_samples"])
                clock = np.arange(start, start + n)
                weight = ((clock >= a) & (clock < b + tail)).astype(np.float32)
                if fade:
                    weight *= np.clip((clock - a) / fade, 0, 1)
                    if b < s.duration_samples:
                        weight *= np.clip((b - clock) / fade, 0, 1)
                mixed = original * (1 - weight[:, None]) + new * weight[:, None]
                writer.write(mixed)
                peak = max(peak, float(np.abs(mixed).max()))
                power += float(np.square(mixed.astype("float64")).sum())
                count += mixed.size
                clipped += int((np.abs(mixed) >= 1).sum())
        report = dict(
            revision_id=self.job["revision_id"],
            snapshot_sha256=self.job["snapshot_sha256"],
            source_asset_id=recipe["source_asset_id"],
            included_original_stems=[stem for stem in recipe["stems"] if stem["name"] != "bass"],
            excluded_bass_stem=next(stem for stem in recipe["stems"] if stem["name"] == "bass"),
            replacement_track_id=payload["replacement_track_id"],
            replacement_asset_id=recipe["replacement_asset_id"],
            start_sample=a,
            end_sample=b,
            fade_samples=fade,
            tail_samples=tail,
            replacement_gain_db=payload["replacement_gain_db"],
            peak=peak,
            rms=(power / count) ** 0.5,
            clipped_samples=clipped,
            boundary_policy="Outside local interval original PCM is preserved; linear crossfades stay inside it. Local replacement tails crossfade back to original; full-song replacement retains rendered tail.",
        )
        metadata = audio_metadata(target)
        report_path = self.directory / "hybrid-report.json"
        write_json(report_path, report)
        return dict(
            format="hybrid",
            asset_id=self.add(target, metadata),
            report_asset_id=self.add(report_path),
            snapshot_sha256=self.job["snapshot_sha256"],
            metadata=metadata,
            recipe=report,
        )


def main():
    directory = Path(sys.argv[1])
    try:
        production = Production(directory)
        publication = {
            "project.render": production.render,
            "audio.measure": production.measure,
            "project.export": production.export,
        }[production.job["kind"]]()
        after = {
            str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else "native_host": digest(path)
            for path in production.code_paths
        }
        if after != production.fingerprint:
            raise ProductionError(
                "implementation_changed",
                "Production implementation changed during execution; retry with a stable checkout",
            )
        write_json(directory / "daw-result.json", dict(files=production.files, publication=publication))
        return 0
    except Exception as error:
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        public = getattr(error, "public_error", None)
        if public is None:
            code = (
                "native_" + error.status
                if isinstance(error, NativeHostError)
                else "production_validation_failed"
            )
            public = dict(
                code=code,
                message="Production failed validation or native processing; previous versions and audio remain available",
            )
        write_json(directory / "daw-result.json", dict(error=public))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
