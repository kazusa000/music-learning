"""Instrument-first classification, independent of library brands and plugin formats.

Reviewed assignments live in Git. New catalogs may supply explicit instrument_ids;
unknown presets remain unclassified rather than guessing from a filename or GM label.
"""

VERSION = 1
# Stable ids are API keys; labels and aliases can evolve without breaking callers.
NODES = [
    ("keyboards", "键盘", None, []),
    ("acoustic-piano", "声学钢琴", "keyboards", ["木钢琴", "钢琴", "piano", "grand piano"]),
    ("electric-piano", "电钢琴", "keyboards", ["电钢", "Rhodes", "electric piano"]),
    ("harpsichord", "羽管键琴", "keyboards", ["大键琴", "harpsichord"]),
    ("pipe-organ", "管风琴", "keyboards", ["organ"]),
    ("clavinet", "击弦电钢琴", "keyboards", ["Clavinet", "D6"]),
    ("celesta", "钢片琴", "keyboards", ["celesta", "celeste"]),
    ("toy-piano", "玩具钢琴", "keyboards", ["toy piano"]),
    ("clavichord", "击弦键琴", "keyboards", ["clavichord", "古钢琴"]),
    ("dulcitone", "音叉键琴", "keyboards", ["dulcitone"]),
    ("carillon-keyboard", "钟键琴", "keyboards", ["Chimeatron", "carillon"]),
    ("reed-organ", "簧风琴", "keyboards", ["Harmochord", "harmonium", "reed organ"]),
    ("guitars", "吉他", None, ["guitar"]),
    ("acoustic-guitar", "原声吉他", "guitars", ["木吉他", "acoustic guitar"]),
    ("electric-guitar", "电吉他", "guitars", ["electric guitar"]),
    ("basses", "贝斯", None, ["bass"]),
    ("electric-bass", "电贝斯", "basses", ["指奏贝斯", "electric bass"]),
    ("keyboard-bass", "键盘贝斯", "basses", ["Piano Bass", "keyboard bass", "Basset"]),
    ("strings", "弦乐", None, ["strings"]),
    ("string-ensemble", "弦乐合奏", "strings", ["弦乐群", "string ensemble"]),
    ("violin", "小提琴", "strings", ["violin"]),
    ("cello", "大提琴", "strings", ["cello"]),
    ("woodwinds", "木管", None, ["woodwind"]),
    ("flute", "长笛", "woodwinds", ["flute"]),
    ("clarinet", "单簧管", "woodwinds", ["clarinet"]),
    ("oboe", "双簧管", "woodwinds", ["oboe"]),
    ("bassoon", "巴松", "woodwinds", ["大管", "bassoon"]),
    ("brass", "铜管", None, ["brass"]),
    ("brass-ensemble", "铜管合奏", "brass", ["brass ensemble"]),
    ("voices", "人声", None, ["voice"]),
    ("choir", "合唱", "voices", ["choir"]),
    ("plucked", "拨弦乐器", None, []),
    ("harp", "竖琴", "plucked", ["harp"]),
    ("kalimba", "拇指琴", "plucked", ["kalimba"]),
    ("dolceola", "键盘齐特琴", "plucked", ["Dolceola", "keyboard zither"]),
    ("percussion", "打击乐", None, ["percussion"]),
    ("drum-kit", "鼓组", "percussion", ["drums", "drum kit"]),
    ("timpani", "定音鼓", "percussion", ["timpani"]),
    ("glockenspiel", "钟琴", "percussion", ["glockenspiel"]),
    ("xylophone", "木琴", "percussion", ["xylophone"]),
    ("marimba", "马林巴", "percussion", ["marimba"]),
    ("vibraphone", "颤音琴", "percussion", ["vibraphone"]),
    ("steelpan", "钢鼓与手碟", "percussion", ["steelpan", "hand pan"]),
    ("synths", "合成音色", None, ["synth"]),
    ("synth-keys", "合成键盘", "synths", ["synth keys"]),
    ("synth-bass", "合成贝斯", "synths", ["synth bass"]),
    ("synth-lead", "合成主音", "synths", ["lead"]),
    ("synth-pad", "合成铺底", "synths", ["pad"]),
    ("synth-pluck", "合成拨弦", "synths", ["pluck"]),
    ("synth-brass", "合成铜管", "synths", ["synth brass"]),
    ("synth-percussion", "合成打击乐", "synths", ["synth percussion"]),
    ("synth-drums", "合成鼓", "synths", ["electronic drums"]),
    ("synth-strings", "合成弦乐", "synths", ["synth strings"]),
    ("synth-winds", "合成管乐", "synths", ["synth winds"]),
    ("synth-guitar", "合成吉他", "synths", ["synth guitar"]),
    ("synth-organ", "合成风琴", "synths", ["synth organ"]),
    ("synth-voice", "合成人声", "synths", ["vox", "synth voice"]),
    ("synth-bells", "合成钟铃", "synths", ["bells"]),
    ("synth-mallets", "合成槌击", "synths", ["mallets"]),
    ("synth-poly", "复音合成器", "synths", ["polysynth"]),
    ("synth-sequence", "合成序列", "synths", ["sequence", "arpeggio", "琶音"]),
    ("synth-rhythm", "合成节奏", "synths", ["rhythm"]),
    ("synth-atmosphere", "合成氛围", "synths", ["atmosphere", "soundscape", "drone"]),
    ("synth-fx", "合成特效", "synths", ["fx", "sound effect"]),
    ("unclassified", "待分类", None, ["未分类", "unclassified"]),
]
INDEX = {id: dict(id=id, name=name, parent_id=parent, aliases=aliases) for id, name, parent, aliases in NODES}
CURATED = {
    "salamander-grand": ["acoustic-piano"],
    "rhodes-mark1": ["electric-piano"],
    "shiny-electric": ["electric-guitar"],
    "shiny-acoustic": ["acoustic-guitar"],
    "swag-finger-bass": ["electric-bass"],
    "strings-sustain": ["string-ensemble"],
    "strings-staccato": ["string-ensemble"],
    "strings-pizzicato": ["string-ensemble"],
    "violin-sustain": ["violin"],
    "cello-sustain": ["cello"],
    "choir-mixed": ["choir"],
    "brass-sustain": ["brass-ensemble"],
    "flute-sustain": ["flute"],
    "clarinet-sustain": ["clarinet"],
    "oboe-sustain": ["oboe"],
    "bassoon-sustain": ["bassoon"],
    "harp": ["harp"],
    "timpani": ["timpani"],
    "glockenspiel": ["glockenspiel"],
    "xylophone": ["xylophone"],
    "pipe-organ": ["pipe-organ"],
    "harpsichord": ["harpsichord"],
    "naked-drums-gm": ["drum-kit"],
}
SYNTH_FAMILIES = {
    "synth-keys": "synth-keys",
    "synth-bass": "synth-bass",
    "lead": "synth-lead",
    "pad": "synth-pad",
    "pluck": "synth-pluck",
    "synth-brass": "synth-brass",
    "synth-percussion": "synth-percussion",
}
SOURCE_CATEGORIES = {
    "Basses": "synth-bass",
    "Bass": "synth-bass",
    "Keys": "synth-keys",
    "Leads": "synth-lead",
    "Pads": "synth-pad",
    "Plucks": "synth-pluck",
    "Brass": "synth-brass",
    "Percussion": "synth-percussion",
    "Drums": "synth-drums",
    "Strings": "synth-strings",
    "Winds": "synth-winds",
    "Guitars": "synth-guitar",
    "Organs": "synth-organ",
    "Vox": "synth-voice",
    "Voices": "synth-voice",
    "Bells": "synth-bells",
    "Mallets": "synth-mallets",
    "Polysynths": "synth-poly",
    "Synths": "synth-poly",
    "Chords": "synth-poly",
    "Sequences": "synth-sequence",
    "Arps": "synth-sequence",
    "Rhythms": "synth-rhythm",
    "FX": "synth-fx",
    "Atmospheres": "synth-atmosphere",
    "Ambiance": "synth-atmosphere",
    "Soundscapes": "synth-atmosphere",
    "Drones": "synth-atmosphere",
}


def descendants(id):
    if id not in INDEX:
        raise ValueError("Unknown instrument category: " + id)
    found = {id}
    for node in INDEX.values():
        parent = node["parent_id"]
        while parent:
            if parent == id:
                found.add(node["id"])
                break
            parent = INDEX[parent]["parent_id"]
    return found


def classify(entry):
    """An additive view; never mutate legacy catalogs, captured jobs or revisions."""
    from .instrument_models import with_model

    ids = entry.get("instrument_ids")
    basis = entry.get("classification_basis", "catalog-assignment")
    if ids is None:
        ids = CURATED.get(entry["id"])
        basis = "curated-registry"
        if ids is None and entry.get("backend") == "surge-fxp":
            parts = entry.get("entrypoint", "").split("/")
            category = parts[-2] if len(parts) > 1 else ""
            id = SOURCE_CATEGORIES.get(category) or SYNTH_FAMILIES.get(entry.get("family"))
            ids = [id] if id else None
            basis = "source-category"
    if not ids:
        ids, basis = ["unclassified"], "unclassified"
    if not isinstance(ids, list) or any(not isinstance(id, str) or id not in INDEX for id in ids):
        raise ValueError("Invalid instrument classification for preset " + entry["id"])
    if len(set(ids)) != len(ids) or ("unclassified" in ids and len(ids) != 1):
        raise ValueError("Ambiguous instrument classification for preset " + entry["id"])
    return with_model({
        **entry,
        "instrument_ids": ids,
        "classification_basis": basis,
        "classification_version": VERSION,
        "source": {
            "library_id": entry.get("library_id", entry.get("bank_root", "unknown")),
            "library_name": entry.get("library_name", entry.get("bank_root", "unknown")),
            "backend": entry["backend"],
        },
    })


def search_terms(entry):
    nodes = set()
    for id in entry["instrument_ids"]:
        while id:
            nodes.add(id)
            id = INDEX[id]["parent_id"]
    return " ".join(INDEX[id]["name"] + " " + " ".join(INDEX[id]["aliases"]) for id in sorted(nodes))
