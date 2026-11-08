"""Musical tempo/meter editing shared by the UI, SDK and assistant."""
from fractions import Fraction

from . import contracts as c
from .timing import convert


def change(data, operation, revision_id):
    old = c.TimingMap.model_validate(data["timing_map"]) if data.get("timing_map") else None
    if old is None:
        raise ValueError("工程尚无节拍网格，请先识别或建立网格。")
    if operation.reference_sample > data["duration_samples"]:
        raise ValueError("速度参考位置超出工程。")
    provenance = c.Provenance(origin="user", source_id="timing:" + revision_id)
    new = old.model_copy(deep=True)
    new.id, new.provenance = c.new_id(), provenance
    factor = Fraction(1)
    if operation.tempo is not None:
        i = next((i for i in range(len(old.anchors)-1)
                  if old.anchors[i+1].sample > operation.reference_sample), len(old.anchors)-2)
        a, b = old.anchors[i:i+2]
        bpm = (b.beat.fraction()-a.beat.fraction()) * data["sample_rate"] * 60 / (b.sample-a.sample)
        factor = bpm / Fraction(str(operation.tempo))
        for anchor in new.anchors:
            anchor.sample = round(anchor.sample * factor)
        if any(a.sample >= b.sample for a, b in zip(new.anchors, new.anchors[1:])):
            raise ValueError("目标速度使节拍锚点重合，请使用较慢的速度。")
    if operation.numerator is not None:
        new.meters = [c.Meter(beat=c.Beat(n=0), bar=1,
                             numerator=operation.numerator, denominator=operation.denominator)]
        # Keep the anacrusis in musical time; it must fit the new first bar.
        if new.pickup.fraction() >= Fraction(operation.numerator * 4, operation.denominator):
            raise ValueError("弱起长度超过新拍号的小节长度。")
    if data.get("source_asset_id") and data.get("source_duration_samples") is None:
        data["source_duration_samples"] = data["duration_samples"]

    def scaled(sample):
        return round(sample * factor)

    if factor != 1:
        for clip in data["clips"]:
            if clip.get("asset_id"):
                continue  # Recorded audio is not time-stretched by MIDI tempo.
            end = scaled(clip["start_sample"] + clip["duration_samples"])
            clip["start_sample"] = scaled(clip["start_sample"])
            clip["duration_samples"] = max(1, end-clip["start_sample"])
        for note in data["notes"]:
            if note.get("timing_mode") == "beat":
                begin = c.Beat.model_validate(note["notation_beat"])
                end = c.Beat.of(begin.fraction() + c.Beat.model_validate(note["notation_duration"]).fraction())
                note["start_sample"] = convert(data["sample_rate"], new, "beat", begin, "sample")["rounded_sample"]
                note["end_sample"] = convert(data["sample_rate"], new, "beat", end, "sample")["rounded_sample"]
            else:
                note["start_sample"], note["end_sample"] = scaled(note["start_sample"]), scaled(note["end_sample"])
            if note["end_sample"] <= note["start_sample"]:
                raise ValueError("目标速度使短音符不足一个采样点。")
        for control in data["controllers"]:
            control["sample"] = scaled(control["sample"])
        audio_tracks = {t["id"] for t in data["tracks"] if t["kind"] == "audio"}
        owners = {p["id"]: p["track_id"] for p in data["plugins"]}
        for lane in data["automation"]:
            if (lane.get("track_id") or owners.get(lane.get("plugin_id"))) in audio_tracks:
                continue
            for point in lane["points"]:
                point["sample"] = scaled(point["sample"])
        for annotation in data["annotations"]:
            annotation["start_sample"] = scaled(annotation["start_sample"])
            annotation["end_sample"] = max(annotation["start_sample"]+1, scaled(annotation["end_sample"]))
        audio_end = max((x["start_sample"]+x["duration_samples"] for x in data["clips"] if x.get("asset_id")), default=0)
        event_end = max(
            [n["end_sample"] for n in data["notes"]]
            + [clip["start_sample"]+clip["duration_samples"] for clip in data["clips"]]
            + [control["sample"]+1 for control in data["controllers"]]
            + [point["sample"] for lane in data["automation"] for point in lane["points"]]
            + [a["end_sample"] for a in data["annotations"]], default=0)
        data["duration_samples"] = max(scaled(data["duration_samples"]), audio_end, event_end,
                                       data.get("source_duration_samples") or 0, 1)
    # A faster MIDI arrangement may finish before unwarped audio. Extend the
    # last tempo segment so the full project still has a usable beat grid.
    last, previous = new.anchors[-1], new.anchors[-2]
    if last.sample < data["duration_samples"]:
        beat = last.beat.fraction() + (data["duration_samples"]-last.sample) * (
            last.beat.fraction()-previous.beat.fraction()) / (last.sample-previous.sample)
        new.anchors.append(c.TimingAnchor(sample=data["duration_samples"], beat=c.Beat.of(beat)))
    for note in data["notes"]:
        if note.get("timing_map_id"):
            note["timing_map_id"] = new.id
    data["timing_map"] = c.TimingMap.model_validate(new.model_dump()).model_dump(mode="json")
