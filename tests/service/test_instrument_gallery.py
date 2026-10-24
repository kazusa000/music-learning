"""Admission-derived model coverage and live audition context, without plugin loads."""
from copy import deepcopy

import pytest

from music_learning import instrument_gallery as gallery
from music_learning.instrument_localization import localized, translated_names


def entry(identity="new-model", *, verified=True, backend="vst3-preset"):
    return localized(dict(id=identity + "-voice", name="New model - Natural", variant_name="Natural",
        audio_verified=verified, backend=backend, family="piano", instrument_ids=["acoustic-piano"],
        model={"id": identity, "name": "New model"}, source={"library_name": "Fixture Product"},
        labels_zh={"model": "新型号", "variant": "自然"}, key_range=[21, 108]))


def test_new_admitted_model_is_available_without_a_ten_model_allowlist():
    first, second = entry(), entry("another-model")
    variant = {**first, "id": "a-new-variant", "display_variant_name": "柔和（Soft）"}
    items = [first, variant, second, entry("not-verified", verified=False), entry("unsupported", backend="other")]
    before = deepcopy(items)
    result = gallery.gallery(items, [])
    assert {r["model_id"] for r in result} == {"new-model", "another-model"}
    group = next(r for r in result if r["model_id"] == "new-model")
    assert group["variant_count"] == 2 and group["title"] == "新型号"
    assert {p["id"] for p in group["variants"]} == {first["id"], variant["id"]}
    assert items == before


def test_gallery_audition_retains_admitted_channel_velocity_and_controllers(monkeypatch):
    phrase = dict(notes=[dict(note=84, velocity=.35, channel=3), dict(note=86, velocity=.5, channel=6)],
                  controllers=[dict(sample=0, channel=3, controller=11, value=96)])
    monkeypatch.setattr(gallery, "phrase_spec", lambda _: deepcopy(phrase))
    result = gallery.audition(entry())
    assert result == dict(pitches=[84, 86], velocity=.35, channels=[3, 6], controllers=phrase["controllers"])


@pytest.mark.parametrize("original,instrument,translated", [
    ("1901", "dolceola", "1901 年版"),
    ("16+8+4", "reed-organ", "16+8+4 英尺音栓"),
    ("L8' + U8'", "harpsichord", "下层 8 英尺 + 上层 8 英尺"),
    ("D 282", "acoustic-piano", "D 282 型"),
])
def test_factory_registration_and_model_codes_have_chinese_context(original, instrument, translated):
    value = entry()
    value.update(variant_name=original, instrument_ids=[instrument], labels_zh={"model": "型号"})
    assert translated_names(value)[1] == translated
    assert value["variant_name"] == original
