import io
import zipfile

import pytest

from music_learning.instrument_taxonomy import INDEX as TAXONOMY
from music_learning.keyscape_library import (
    KeyscapeContainerError,
    classify_preset,
    read_container_index,
    read_container_member,
    read_container_zip_member,
    read_presets,
)


def _write_container(path, entries):
    """Write a tiny FileSystem-indexed container for parser tests."""
    payload = bytearray()
    files = []
    directories = {}
    for relative, data in entries.items():
        pieces = relative.split("/")
        for depth in range(1, len(pieces)):
            directories.setdefault("/".join(pieces[:depth]), [])
        offset = len(payload)
        payload.extend(data)
        files.append((relative, offset, len(data)))

    children = {}
    for directory in directories:
        parent, _, leaf = directory.rpartition("/")
        children.setdefault(parent, []).append(("DIR", leaf, directory))
    for relative, offset, size in files:
        parent, _, leaf = relative.rpartition("/")
        children.setdefault(parent, []).append(("FILE", leaf, (offset, size)))

    def render(parent=""):
        fragments = []
        for kind, name, value in sorted(children.get(parent, []), key=lambda item: (item[0], item[1])):
            if kind == "DIR":
                fragments.append(f'<DIR name="{name}">{render(value)}</DIR>')
            else:
                offset, size = value
                fragments.append(f'<FILE name="{name}" offset="{offset}" size="{size}"/>')
        return "".join(fragments)

    header = f"<FileSystem>{render()}</FileSystem>\n".encode("utf-8")
    path.write_bytes(header + payload)
    return len(header)


def _preset_xml(name, source_names=(), *, model=None, kind="Duo Combination"):
    attrs = f' Model="{model}"' if model else ""
    descriptor = f'<ENTRYDESCR name="{name}" library="Keyscape"{attrs} />'
    voices = "".join(f'<VOICE><OSC name="{source}" library="Keyscape" kind="sample" /></VOICE>' for source in source_names)
    return (
        f'<KeyscapePart><SynthEngine><SYNTHENG>{descriptor}{voices}'
        "</SYNTHENG></SynthEngine></KeyscapePart>"
    ).encode("utf-8")


def _preset_index(entries):
    rows = []
    for name, category, model in entries:
        rows.append(
            f'<ENTRY NAME="{name}" PATH="Keyboards/{category}">'
            f'<ATTR NAME="SubCategory" VALUE="{category}"/>'
            f'<ATTR NAME="Type" VALUE="{category}"/>'
            f'<ATTR NAME="Model" VALUE="{model}"/>'
            "</ENTRY>"
        )
    return ("<INDEX VERSION=\"2\">" + "".join(rows) + "</INDEX>").encode("utf-8")


def test_container_reader_checks_member_bounds_and_path_escape(tmp_path):
    container = tmp_path / "bank.db.txt"
    header_size = _write_container(container, {"folder/preset.xml": b"small xml"})

    index = read_container_index(container)
    assert index.payload_offset == header_size
    assert read_container_member(container, "FOLDER/PRESET.XML") == b"small xml"
    with pytest.raises(KeyscapeContainerError, match="Unsafe relative path"):
        read_container_member(container, "../outside.xml")

    bad = tmp_path / "bad.db.txt"
    bad.write_bytes(
        b'<FileSystem><FILE name="escape" offset="100" size="1"/></FileSystem>\n' + b"x"
    )
    with pytest.raises(KeyscapeContainerError, match="extends past"):
        read_container_index(bad)


def test_embedded_zip_reader_rejects_traversal_and_reads_selected_member(tmp_path):
    safe_buffer = io.BytesIO()
    with zipfile.ZipFile(safe_buffer, "w") as archive:
        archive.writestr("patches/preset.xml", b"safe")
    safe_container = tmp_path / "safe.db.txt"
    _write_container(safe_container, {"Archive.zip": safe_buffer.getvalue()})
    assert read_container_zip_member(safe_container, "Archive.zip", "patches/preset.xml") == b"safe"

    unsafe_buffer = io.BytesIO()
    with zipfile.ZipFile(unsafe_buffer, "w") as archive:
        archive.writestr("../outside.xml", b"unsafe")
    unsafe_container = tmp_path / "unsafe.db.txt"
    _write_container(unsafe_container, {"Archive.zip": unsafe_buffer.getvalue()})
    with pytest.raises(KeyscapeContainerError, match="Unsafe relative path"):
        read_container_zip_member(unsafe_container, "Archive.zip", "allowed.xml")


def test_same_bank_presets_split_by_model_and_composite_parts(tmp_path):
    container = tmp_path / "Keyscape Library.db.txt"
    presets = [
        ("Reference Piano", "Acoustic Pianos", "Mini Student Butterfly Piano"),
        ("Reference Electric", "Electric Pianos", "Rhodes - Classic Mark I"),
        ("Duo", "Belltone Keyboards", "Duo Combination"),
    ]
    entries = {"prt_key.index": _preset_index(presets)}
    entries["Keyboards/Acoustic Pianos/Reference Piano.prt_key"] = _preset_xml(
        "Reference Piano", (), model="Mini Student Butterfly Piano"
    )
    entries["Keyboards/Electric Pianos/Reference Electric.prt_key"] = _preset_xml(
        "Reference Electric", (), model="Rhodes - Classic Mark I"
    )
    entries["Keyboards/Belltone Keyboards/Duo.prt_key"] = _preset_xml(
        "Duo", ("E6 Duo Clavinet ^", "E6 Duo Pianet ^")
    )
    _write_container(container, entries)

    result = read_presets(container)
    assert [preset["classification"]["instrument_ids"] for preset in result] == [
        ["acoustic-piano"],
        ["electric-piano"],
        ["clavinet", "electric-piano"],
    ]
    assert result[2]["classification"]["instrument_ids"].count("electric-piano") == 1
    assert result[0]["entry_description"]["name"] == "Reference Piano"
    assert all(
        instrument_id in TAXONOMY
        for preset in result
        for instrument_id in preset["classification"]["instrument_ids"]
    )


def test_unknown_titles_are_not_guessed_and_unmapped_parts_are_reported():
    classification = classify_preset(
        {
            "name": "Grand Piano, obviously",
            "models": ["Unrecognized Model"],
            "types": ["Unrecognized Type"],
            "parts": [{"source_name": "Future Instrument Layer"}],
        }
    )
    assert classification == {
        "instrument_ids": ["unclassified"],
        "classification_basis": "unclassified",
        "unresolved_parts": ["Future Instrument Layer"],
    }
