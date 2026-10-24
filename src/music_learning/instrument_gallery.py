"""Instrument-model presentation over the admitted catalogue; no resource mutation."""
from functools import lru_cache
import json

from .instrument_localization import translated_names
from .instrument_preview_audio import phrase_spec
from .instrument_taxonomy import INDEX
from .settings import ROOT

ACCENTS = {"键盘": "#c5946e", "吉他": "#b98c70", "贝斯": "#9ba894", "弦乐": "#b08793",
           "木管": "#8ba5a0", "铜管": "#bea36c", "人声": "#ba95a4", "拨弦乐器": "#b7a082",
           "击弦乐器": "#b79b74", "打击乐": "#a0a98b", "合成音色": "#9b94bb", "复合音色": "#a5a0bc"}


@lru_cache(maxsize=12)
def _read(path, size, modified):
    return json.loads(path.read_bytes())


def document(name):
    path = ROOT / "resources/instruments" / name
    stat = path.stat()
    return _read(path, stat.st_size, stat.st_mtime_ns)


def image_record(image_id):
    return next((x for x in document("images.json")["images"] if x["id"] == image_id), None)


def family(instrument_id):
    node = INDEX.get(instrument_id, INDEX["unclassified"])
    while node["parent_id"]:
        node = INDEX[node["parent_id"]]
    return node["name"]


def definition(entry):
    plan = document("gallery-models.json")
    model = entry["model"]
    profile = plan["models"].get(model["id"])
    if profile is None:
        # A newly published ordinary instrument should be visible immediately.
        # Only reviewed model identities claim model-specific artwork.
        leaf = next(iter(entry.get("instrument_ids", [])), "unclassified")
        if leaf.startswith("synth-"):
            leaf = "synths"
        image_id = next((a["image_id"] for a in plan["models"].values()
                         if a["instrument_id"] == leaf and a["image_scope"] == "instrument"), "analog-machines")
        profile = dict(image_id=image_id, image_scope="instrument", instrument_id=leaf,
                       category=family(leaf), eyebrow=INDEX.get(leaf, INDEX["unclassified"])["name"],
                       tagline="", description="")
    category = profile["category"]
    return dict(model_id=model["id"], category=category, instrument_id=profile["instrument_id"],
                image_id=profile["image_id"], image_url="/v1/instrument-images/" + profile["image_id"],
                eyebrow=profile["eyebrow"], tagline=profile["tagline"], description=profile["description"],
                accent=ACCENTS.get(category, "#a49a8a"), media=dict(type="image", model_3d=None,
                image_scope=profile["image_scope"], image_identity=profile["image_id"]))


def audition(entry):
    """Use the admitted source's actual pitches, velocities and MIDI channels."""
    phrase = phrase_spec(entry)
    notes = phrase["notes"]
    return dict(pitches=list(dict.fromkeys(n["note"] for n in notes)),
                velocity=notes[0]["velocity"] if notes else .8,
                channels=sorted({n.get("channel", 1) for n in notes}) or [1],
                controllers=phrase.get("controllers", []))


def gallery(installed, curated, model_id=None):
    groups = {}
    for entry in installed:
        if entry.get("audio_verified") and entry["backend"] == "vst3-preset":
            groups.setdefault(entry["model"]["id"], []).append(entry)
    featured = {e["model_id"]: e for e in curated}
    result = []
    for identity, variants in groups.items():
        if model_id and identity != model_id:
            continue
        review = featured.get(identity, {})
        primary = next((p for p in variants if p["id"] == review.get("default_preset_id")), None)
        primary = primary or next((p for p in variants if p.get("default")), variants[0])
        item = definition(primary)
        photo = image_record(item["image_id"])
        item.update(title=translated_names(primary)[0], english_name=primary["model"]["name"],
                    source=primary["source"], default_preset_id=primary["id"], variant_count=len(variants),
                    variants=[dict(id=p["id"], name=p["display_variant_name"], original_name=p["name"],
                                   default=p["id"] == primary["id"], key_range=p["key_range"], audition=audition(p)) for p in variants],
                    image_credit={k: photo.get(k) for k in ["author", "source_url", "license", "license_url"]} if photo else None)
        if photo:
            item["image_url"] += "?v=" + photo["sha256"]
        result.append(item)
    order = {identity: i for i, identity in enumerate(featured)}
    result.sort(key=lambda x: (order.get(x["model_id"], len(order)), x["category"], x["title"], x["model_id"]))
    return result
