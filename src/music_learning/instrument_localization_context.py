"""Reviewed musical meanings for ambiguous names and old generated labels.

These rules use the shared instrument taxonomy, not a library or preset ID.
Explicit custom translations remain authoritative; only known legacy machine
translations are corrected when reading older candidates and catalogs.
"""

from __future__ import annotations

import re


# Original term -> (Chinese meaning, old generated spellings).
_TERMS = {
    "tuba": ("大号", ("特乌布阿",)),
    "bass drum": ("大鼓", ("贝斯 鼓",)),
    "claves": ("响棒", ("克勒阿维埃斯",)),
    "cymbals": ("镲", ("克伊姆布阿勒斯",)),
    "snare drum": ("小军鼓", ("斯恩阿尔埃 鼓",)),
    "timpani": ("定音鼓", ("特伊姆普阿恩伊",)),
    "grand piano": ("三角钢琴", ("大钢琴 钢琴",)),
    "tenor saxophone": ("次中音萨克斯管", ("男高音 萨克斯管",)),
    "fingered 12 strings": ("十二弦指弹", ("指弹 十二 弦乐",)),
    "picked 12 strings": ("十二弦拨奏", ("拨片 十二 弦乐",)),
    "strummed 12 strings": ("十二弦扫弦", ("扫弦 十二 弦乐",)),
    "car horn": ("汽车喇叭", ("汽车 圆号",)),
    "sitar zest": ("西塔琴活力", ("斯伊特阿尔 活力",)),
    "off track": ("偏离轨道", ("关闭 轨道",)),
    "take off": ("起飞", ("起飞 关闭",)),
    "up in smoke kit": ("烟消云散鼓组", ("上扬 在 烟雾 鼓组",)),
    "out for fame kit": ("追逐声名鼓组", ("外部 献给 名声 鼓组",)),
    "the wagons roll at night": ("夜行马车", ("马车 滚奏 在 夜晚",)),
    "step by step": ("循序渐进", ("步进 伴随 步进",)),
    "drum kit": ("鼓组", ("鼓 鼓组",)),
    "rhythm drum kit": ("节奏鼓组", ("节奏 鼓 鼓组",)),
    "50's sci-fi": ("五十年代科幻", ("五十'斯 科幻-科幻",)),
}
_CHOIR_TERMS = {
    "factory choir": ("原厂合唱音色", ("工厂合唱音源",)),
    "factory bass choir": ("男低音合唱", ("贝斯合唱",)),
    "factory alto choir": ("女低音合唱", ("中音合唱",)),
    "bass": ("男低音", ("贝斯", "低音")),
    "basses": ("男低音声部", ("低音提琴",)),
    "alto": ("女低音", ("中音",)),
    "altos": ("女低音声部", ("中音部",)),
    "bass vowels": ("男低音元音", ("贝斯 元音",)),
    "alto vowels": ("女低音元音", ("中音 元音",)),
    "a": ("元音啊", ("元音原厂",)),
    "i": ("元音伊", ("元音衣",)),
}


def _rule(text: str, instrument_ids=()):
    term = " ".join(text.casefold().split())
    ids = set(instrument_ids)
    if ids == {"choir"} and term in _CHOIR_TERMS:
        return _CHOIR_TERMS[term]
    if ids == {"triangle"} and term == "triangle":
        return "三角铁", ("三角波",)
    if ids == {"recorder"}:
        recorders = {
            "soprano": ("高音竖笛", ("高音域",)),
            "tenor": ("次中音竖笛", ("次中音域",)),
            "piccolo": ("超高音竖笛", ("短笛音域",)),
        }
        if term in recorders:
            return recorders[term]
    return _TERMS.get(term)


def musical_term(text: str, instrument_ids=()) -> str | None:
    rule = _rule(text, instrument_ids)
    return rule[0] if rule else None


def corrected_legacy_label(text: str, explicit: str, instrument_ids=()) -> str:
    rule = _rule(text, instrument_ids)
    if rule and isinstance(explicit, str):
        # Generated labels sometimes inserted spaces between every Chinese
        # word. Compare those spellings without rewriting arbitrary labels.
        def compact(value):
            return re.sub(r"\s+", "", value)
        if compact(explicit) in {compact(alias) for alias in rule[1]}:
            return rule[0]
    return explicit
