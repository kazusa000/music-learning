"""Compile a revision into the native host's acyclic offline production graph."""

from __future__ import annotations

from fractions import Fraction
from . import contracts as c
from .timing import interpolate
from .midi_input import instrument_channel

MASTER = "0" * 32


def compile_project(snapshot, revision_id, descriptors, paths, resources, *, tail_seconds=2, block_size=512):
    s = c.Snapshot.model_validate(snapshot)
    if not s.tracks:
        raise ValueError("render requires tracks")
    owners = {p.id: p.track_id for p in s.plugins}
    owners.update({t.id: t.id for t in s.tracks})
    master = MASTER
    if s.routing:
        unknown = set(s.routing.nodes) - set(owners)
        if len(unknown) != 1:
            raise ValueError("routing must identify exactly one master node")
        master = unknown.pop()
        if master in {e.source for e in s.routing.edges}:
            raise ValueError("master cannot send audio upstream")
    project = dict(
        revision_id=revision_id,
        sample_rate=s.sample_rate,
        duration_samples=s.duration_samples,
        tail_samples=round(s.sample_rate * tail_seconds),
        block_size=block_size,
        tracks=[],
        graph={"master": master, "edges": []},
    )
    if s.timing_map:
        timing = s.timing_map
        anchors = timing.anchors
        project["ppq_offset"] = -float(timing.pickup.fraction())
        project["tempo"] = [
            {
                "sample": a.sample,
                "bpm": float(
                    (b.beat.fraction() - a.beat.fraction()) * 60 * s.sample_rate / (b.sample - a.sample)
                ),
            }
            for a, b in zip(anchors, anchors[1:])
        ]
        beats, samples = [a.beat.fraction() for a in anchors], [Fraction(a.sample) for a in anchors]
        project["meter"] = [
            {
                "sample": 0 if i == 0 else round(interpolate(m.beat.fraction(), beats, samples)),
                "numerator": m.numerator,
                "denominator": m.denominator,
                "ppq": float(m.beat.fraction()),
                "bar": m.bar,
            }
            for i, m in enumerate(timing.meters)
        ]
    else:
        project.update(
            ppq_offset=0,
            tempo=[{"sample": 0, "bpm": 120}],
            meter=[{"sample": 0, "numerator": 4, "denominator": 4}],
        )
    edges = project["graph"]["edges"]
    if s.routing:
        for edge in s.routing.edges:
            source, target = owners.get(edge.source, edge.source), owners.get(edge.target, edge.target)
            if source == target:
                if edge.kind != "main" or edge.gain_db:
                    raise ValueError("intra-chain routing must use unmodified main connections")
                continue
            value = dict(source=source, target=target, kind=edge.kind, gain_db=edge.gain_db)
            if edge.kind == "sidechain":
                if edge.target not in {p.id for p in s.plugins}:
                    raise ValueError("sidechain target must be a plugin ID")
                value["plugin_id"] = edge.target
            if target not in s.frozen_tracks:
                edges.append(value)
    else:
        edges.extend(dict(source=t.id, target=master, kind="main", gain_db=0) for t in s.tracks)
    if len({(e["source"], e["target"], e["kind"], e.get("plugin_id")) for e in edges}) != len(edges):
        raise ValueError("duplicate audio routes")
    # Validate the collapsed graph too: collapsing chains can expose a cycle.
    c.RoutingGraph(
        id=master,
        nodes=[t.id for t in s.tracks] + [master],
        edges=[c.RoutingEdge(**{k: v for k, v in e.items() if k != "plugin_id"}) for e in edges],
    )
    soloed = {t.id for t in s.tracks if t.solo}
    audible = set(soloed)
    if soloed:
        # A bus solo includes its inputs; signal paths downstream remain audible.
        previous = None
        while previous != audible:
            previous = set(audible)
            audible.update(e["source"] for e in edges if e["target"] in audible and e["target"] != master)
        previous = None
        while previous != audible:
            previous = set(audible)
            audible.update(e["target"] for e in edges if e["source"] in audible)
    for track in s.tracks:
        node = dict(
            id=track.id,
            gain_db=track.gain_db,
            pan=track.pan,
            plugins=[],
            notes=[],
            controllers=[],
            audio_clips=[],
            automation=[],
        )
        if track.mute or (soloed and track.id not in audible):
            node["muted"] = True
        if track.id in s.frozen_tracks:
            asset = s.frozen_tracks[track.id]
            node.update(
                gain_db=0,
                pan=0,
                audio_clips=[
                    dict(
                        path=paths[asset],
                        sample=0,
                        duration_samples=s.duration_samples + project["tail_samples"],
                    )
                ],
            )
            project["tracks"].append(node)
            continue
        node["mix_automation"] = [
            dict(
                parameter_id=a.parameter_id,
                interpolation=a.interpolation,
                points=[p.model_dump() for p in a.points],
            )
            for a in s.automation
            if a.track_id == track.id
        ]
        for plugin in sorted((p for p in s.plugins if p.track_id == track.id), key=lambda p: p.order):
            if plugin.bypass:
                continue
            descriptor = descriptors[plugin.id]
            if (
                descriptor.get("binary_sha256") != plugin.binary_sha256
                or descriptor.get("vst3_class_id") != plugin.class_id
            ):
                raise ValueError("plugin identity differs from descriptor")
            spec = dict(
                id=plugin.id,
                path=descriptor["path"],
                identifier=descriptor["identifier"],
                binary_sha256=plugin.binary_sha256,
                version=plugin.version,
                state_base64=resources[plugin.id]["state_base64"],
                state_sha256=plugin.state_asset_id,
                parameters=plugin.parameters,
            )
            if plugin.resource_entrypoint:
                spec["sfizz_sfz_path"] = resources[plugin.id]["entrypoint"]
            if any(e.get("plugin_id") == plugin.id for e in edges):
                spec["sidechain"] = True
            node["plugins"].append(spec)
            node["automation"].extend(
                dict(
                    plugin_id=a.plugin_id,
                    parameter_id=a.parameter_id,
                    interpolation=a.interpolation,
                    points=[p.model_dump() for p in a.points],
                )
                for a in s.automation
                if a.plugin_id == plugin.id
            )
        for clip in (x for x in s.clips if x.track_id == track.id and x.asset_id):
            available = clip.content_samples or clip.duration_samples
            position, remaining = clip.start_sample, clip.duration_samples
            if clip.loop and not clip.content_samples:
                raise ValueError("looped audio clips require explicit content_samples")
            while remaining:
                count = min(remaining, available) if clip.loop else remaining
                node["audio_clips"].append(
                    dict(
                        id=clip.id,
                        path=paths[clip.asset_id],
                        sample=position,
                        source_offset_samples=clip.source_offset_samples,
                        duration_samples=count,
                        gain=10 ** (clip.gain_db / 20),
                    )
                )
                position += count
                remaining -= count
        clips = {cl.id: cl for cl in s.clips if cl.track_id == track.id}
        for note in (n for n in s.notes if n.track_id == track.id):
            clip = clips.get(note.clip_id)
            offsets = [0]
            if clip and clip.loop:
                if not clip.content_samples:
                    raise ValueError("looped MIDI clips require content_samples")
                if (
                    note.end_sample > clip.start_sample + clip.content_samples
                    or note.start_sample < clip.start_sample
                ):
                    raise ValueError("loop MIDI notes must be inside the base content interval")
                offsets = range(0, clip.duration_samples, clip.content_samples)
            for offset in offsets:
                start, end = note.start_sample + offset, note.end_sample + offset
                if clip and end > clip.start_sample + clip.duration_samples:
                    raise ValueError("loop boundary crosses a note; shorten the final repetition explicitly")
                node["notes"].append(
                    dict(
                        id=note.id,
                        sample=start,
                        duration_samples=end - start,
                        note=note.pitch,
                        channel=note.channel,
                        velocity=note.velocity,
                    )
                )
        node["controllers"] = [
            dict(sample=e.sample, channel=e.channel, controller=e.controller, value=e.value)
            for e in s.controllers
            if e.track_id == track.id
        ]
        channel = instrument_channel(
            [p for p in s.plugins if p.track_id == track.id], descriptors, resources,
            [e["channel"] for e in node["notes"] + node["controllers"]],
        )
        if channel is not None:
            for event in node["notes"] + node["controllers"]:
                event["channel"] = channel
        instrument_bypassed = any(
            p.bypass and descriptors.get(p.id, {}).get("is_instrument")
            for p in s.plugins
            if p.track_id == track.id
        )
        if instrument_bypassed or (not node["plugins"] and any(p.bypass for p in s.plugins if p.track_id==track.id)):
            node["notes"] = []
            node["controllers"] = []
        for clip in (cl for cl in s.clips if cl.track_id == track.id and not cl.asset_id):
            if clip.gain_db:
                raise ValueError(
                    "MIDI clip gain is unsupported; edit note velocities or mixer automation explicitly"
                )
        project["tracks"].append(node)
    project["tracks"].append(dict(id=master, plugins=[], notes=[], audio_clips=[]))
    return project
