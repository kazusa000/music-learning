"""Kontakt round-trip audio evidence with independent startup-default leases.

First load may start from a converted factory Multi. Reopen MUST start from an
empty rack, so a failed setState cannot be masked by preloading the same sound.
The caller supplies process-owning host and profile contexts; this module never
publishes a catalog item or claims that audio alone proves instrument identity.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Callable

from .instrument_intake_publication import _audio_measure, _verify_state_blob, artifact_ref
from .kontakt_import import atomic_write_json


@dataclass(frozen=True)
class ProbeProfile:
    pitches: tuple[int, ...] = (48, 60, 72)
    channels: tuple[int, ...] = (1,)
    controllers: tuple[tuple[int, int], ...] = ()

    def project(self, plugin: dict) -> dict:
        if (not self.pitches or len(self.pitches) > 16 or len(set(self.pitches)) != len(self.pitches)
                or any(type(p) is not int or not 0 <= p <= 127 for p in self.pitches)):
            raise ValueError("Probe requires distinct explicitly playable MIDI pitches")
        if (not self.channels or len(self.channels) > 16 or len(set(self.channels)) != len(self.channels)
                or any(type(ch) is not int or not 1 <= ch <= 16 for ch in self.channels)):
            raise ValueError("Probe requires distinct supported MIDI channels")
        if (len({cc for cc, _ in self.controllers}) != len(self.controllers)
                or any(type(cc) is not int or type(value) is not int or not 0 <= cc <= 127
                       or not 0 <= value <= 127 for cc, value in self.controllers)):
            raise ValueError("Invalid probe controller profile")
        if len(self.channels) * len(self.pitches) > 6:
            raise ValueError("Split larger racks into channel probes; keep each note long enough to sound")
        rate = 44100
        spacing = rate // 2
        notes = []
        # Different channels are played sequentially, avoiding a layered rack's
        # accidental gain summation/clipping during a basic load check.
        for channel in self.channels:
            for pitch in self.pitches:
                index = len(notes)
                notes.append(dict(sample=index * spacing, duration_samples=spacing * 2 // 3,
                                  note=pitch, velocity=(0.4, 0.65, 0.85)[index % 3], channel=channel))
        controllers = [dict(sample=0, channel=channel, controller=cc, value=value)
                       for channel in self.channels for cc, value in self.controllers]
        return dict(revision_id="kontakt-instrument-probe", sample_rate=rate, block_size=512,
                    duration_samples=4 * rate, tail_samples=rate,
                    tracks=[dict(id="kontakt-probe", gain_db=-12, plugins=[dict(plugin)],
                                 notes=notes, controllers=controllers)])


def _parameters(plugin):
    result = {}
    for parameter in plugin["parameters_readback"]:
        key, value = str(parameter["id"]), parameter["value"]
        if key in result or type(value) not in (float, int) or not math.isfinite(value):
            raise ValueError("Invalid or duplicate captured native parameter")
        result[key] = value
    if not result:
        raise ValueError("Kontakt did not return its native parameter map")
    return result


def audio_roundtrip(host, plugin_spec: dict, output_root: Path, *,
                    first_startup: Callable[[], AbstractContextManager],
                    empty_startup: Callable[[], AbstractContextManager],
                    profile: ProbeProfile = ProbeProfile(), timeout_seconds=180) -> dict:
    """Produce actual first/reopen evidence; semantic admission remains separate.

    ``empty_startup`` installs the independently verified empty Multi, never the
    original factory Multi. The caller must own all host processes and wait for
    them before each context exits/restores the private default.
    """
    if any(plugin_spec.get(key) for key in ("state_base64", "preset_path")):
        raise ValueError("Initial Kontakt probe must use startup Multi, not a second hidden preset")
    if not all(plugin_spec.get(key) for key in ("identifier", "binary_sha256", "path")):
        raise ValueError("Kontakt native probe must pin plugin binary and class identifier")
    output = Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=False)
    diagnostics = Path(host.diagnostics).resolve()
    if not diagnostics.is_relative_to(output):
        raise ValueError("Native probe diagnostics must stay inside its evidence root")
    project = profile.project(plugin_spec)
    runs, plugins, measurements = {}, {}, {}
    for phase, startup in (("first", first_startup), ("reopen", empty_startup)):
        probe = output / (phase + ".wav")
        before = set(diagnostics.iterdir()) if diagnostics.exists() else set()
        request = dict(schema_version=1, action="render", project=project, output_path=str(probe))
        # Each context exits before the next phase starts. In particular the
        # factory default must be removed before loading the captured state.
        with startup():
            result = host.call(request, timeout=timeout_seconds)
        added = set(diagnostics.iterdir()) - before
        if len(added) != 1:
            raise ValueError("Native host call has no unique persisted execution evidence")
        call = added.pop()
        execution = json.loads((call / "execution.json").read_bytes())
        if execution.get("status") != "completed" or execution.get("returncode") != 0:
            raise ValueError("Kontakt native probe did not finish with a zero exit")
        actual = _audio_measure(probe)
        if actual["rms"] < 1e-6 or actual["peak"] <= 0 or actual["clipped_samples"]:
            raise ValueError("Kontakt " + phase + " audio is silent, invalid or clipped")
        for metric in ("rms", "peak"):
            if not math.isclose(actual[metric], result[metric], rel_tol=1e-4, abs_tol=1e-7):
                raise ValueError("Kontakt native metrics differ from decoded probe audio")
        if result["clipped_samples"] != 0:
            raise ValueError("Kontakt native host reported clipping")
        snapshot = result["snapshot"]
        plugin = snapshot["tracks"][0]["plugins"][0]
        _verify_state_blob(plugin)
        _parameters(plugin)
        if plugin["identifier"] != plugin_spec["identifier"] or plugin["binary_sha256"] != plugin_spec["binary_sha256"]:
            raise ValueError("Kontakt native binary/class changed during the audio probe")
        state_path = output / (phase + "-state.json")
        atomic_write_json(state_path, plugin)
        refs = {key: artifact_ref(call / (key + ".json"), output)
                for key in ("request", "response", "execution")}
        refs.update(state=artifact_ref(state_path, output), probe=artifact_ref(probe, output))
        runs[phase], plugins[phase], measurements[phase] = refs, plugin, actual
        if phase == "first":
            project = snapshot
        elif _parameters(plugin) != _parameters(plugins["first"]):
            raise ValueError("Kontakt parameter map did not survive a clean-rack reopen")
    report = dict(schema_version=1, status="audio_passed_pending_factory_identity",
                  reopen_startup="independently_verified_empty_multi",
                  first=runs["first"], reopen=runs["reopen"], audio=measurements,
                  catalog_written=False)
    atomic_write_json(output / "evidence-index.json", report)
    return report
