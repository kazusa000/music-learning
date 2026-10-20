import base64
import json
import struct
import xml.etree.ElementTree as ET

import pytest

from music_learning.keyscape_presets import CLASS_ID, build_preset, verify_state
from music_learning.vst3_state import ALPHABET, memory_block, state_components, vst3_preset


def plugin(component):
    bits = int.from_bytes(component, "little")
    encoded = "".join(ALPHABET[(bits >> i) & 63] for i in range(0, len(component) * 8, 6))
    node = ET.Element("VST3PluginState")
    ET.SubElement(node, "IComponent").text = str(len(component)) + "." + encoded
    xml = ET.tostring(node)
    return dict(name="Keyscape", version="1.3.3d", vst3_class_id=CLASS_ID,
                state_base64=base64.b64encode(b"VC2!" + struct.pack("<I", len(xml)) + xml).decode())


def test_standard_preset_keeps_multi_and_checks_sample_identity():
    multi = b'<SynthMaster><InputMapper mode="retained"/>' + (
        b'<SynthSubEngine><SynthEngine><SYNTHENG><ENTRYDESCR name="default"/>'
        b'</SYNTHENG></SynthEngine></SynthSubEngine>' * 8) + b'</SynthMaster>\0'
    part = b'<KeyscapePart><SynthEngine><SYNTHENG><ENTRYDESCR name="Requested"/>' \
           b'<VOICE><OSC name="Real source" library="Bank"/></VOICE></SYNTHENG>' \
           b'</SynthEngine></KeyscapePart>'
    preset, expected = build_preset(plugin(multi), part)
    assert preset[:4] == b"VST3" and preset[8:40] == CLASS_ID.encode()
    end = struct.unpack_from("<q", preset, 40)[0]
    assert preset[end:end + 4] == b"List"
    assert struct.unpack_from("<4sqq", preset, end + 8) == (b"Comp", 48, end - 48)
    state = preset[48:end]
    root = ET.fromstring(state.rstrip(b"\0"))
    assert root.find("InputMapper").get("mode") == "retained"
    assert len(root.findall("SynthSubEngine")) == 8
    assert verify_state(plugin(state), expected) == expected
    # A renamed preset alone does not prove its sample source loaded.
    source = root.find(".//OSC")
    source.tag = "MS_IM_0"
    assert verify_state(plugin(ET.tostring(root)), expected) == expected
    source.set("name", "Wrong source")
    with pytest.raises(ValueError, match="requested preset"):
        verify_state(plugin(ET.tostring(root)), expected)


def test_state_decoder_limits_and_format():
    assert memory_block("0.") == b""
    assert state_components(plugin(b"abc\0"))["IComponent"] == b"abc\0"
    for encoded in ("-1.", "1.?", "99999999999.", "1.A", "1.++"):
        with pytest.raises(ValueError):
            memory_block(encoded)
    with pytest.raises(ValueError):
        vst3_preset("invalid", b"a")
    with pytest.raises(ValueError):
        state_components(dict(state_base64=base64.b64encode(b"VC2!\xff\xff\xff\xffx").decode()))


def test_normalized_names_preserve_genuine_xml():
    from pathlib import Path
    from copy_keyscape_library import installed_name

    assert installed_name(Path("samples.db.txt")) == Path("samples.db")
    assert installed_name(Path("zmap.index.xml")) == Path("zmap.index")
    assert installed_name(Path("User Settings Factory.xml")) == Path("User Settings Factory.xml")
    assert installed_name(Path("notes.txt")) == Path("notes.txt")


def test_incremental_admission_retains_verified_receipts_and_one_pure_default():
    import copy
    from index_keyscape import LIBRARY_ID, reconcile

    reference = dict(manifest_sha256="a" * 64, locator={"path": "configured", "target": "STEAM"})
    def entry(id, name, ids):
        return dict(id=id, name=name, library_id=LIBRARY_ID, instrument_ids=ids, audio_verified=True,
                    default=True, installed_library=dict(manifest_sha256="a" * 64),
                    plugin_sha256="b" * 64, resources={"preset": "c" * 64}, expected_keyscape={"name": name},
                    metrics={"rms": 0.1})
    saved = [entry("old", "Duo", ["toy-piano", "electric-piano"]),
             entry("new", "Toy Piano - Classic", ["toy-piano"]),
             entry("variant", "Toy Piano - Classic Room", ["toy-piano"])]
    inventory = copy.deepcopy(saved)
    for item in inventory:
        item.update(audio_verified=False, default=False)
    reconcile(inventory, saved, reference)
    assert all(item["audio_verified"] for item in inventory)
    assert [item["id"] for item in saved if item["default"]] == ["new"]
    assert all(item["installed_library"] == reference for item in saved)
    inventory[0]["audio_verified"] = False
    inventory[0]["resources"] = {"different": "d" * 64}
    reconcile(inventory, saved, reference)
    assert inventory[0]["audio_verified"] is False


def test_external_dependency_survives_capture_and_blocks_changed_sample(service, project, tmp_path):
    from conftest import invoke
    from music_learning.storage import digest
    from music_learning.worker import Worker
    from test_daw import musical
    from test_instruments import installed

    library, entry = installed(service.settings, tmp_path)
    bank = library / "bank"
    large = bank / "external.bin"
    large.write_bytes(b"original sample bank")
    manifest = bank / "installed.json"
    manifest.write_text(json.dumps(dict(schema_version=1, library_id="external", files=[
        dict(path=large.name, bytes=large.stat().st_size, sha256=digest(large),
             mtime_ns=large.stat().st_mtime_ns)])))
    reference = dict(library_id="external", bank_root="bank", manifest_path=manifest.name,
                     manifest_sha256=digest(manifest))
    entry["installed_library"] = reference
    entry["resources"][manifest.name] = dict(sha256=digest(manifest), bytes=manifest.stat().st_size)
    (library / "catalog.json").write_text(json.dumps(dict(schema_version=1, presets=[entry])))
    revision, track, *_ = musical(service, project)
    job = invoke(service, "jobs.submit", dict(project_id=project["id"], kind="instrument.capture",
                 payload={"preset_id": entry["id"]}), revision["id"])
    worker = Worker(service.database)
    assert worker.run_once()
    captured = invoke(service, "jobs.get", dict(job_id=job["id"]))
    assert captured["status"] == "succeeded", captured
    value = captured["result"]["plugin"]
    descriptor = json.loads(service.storage.verify(value["descriptor_asset_id"]).read_bytes())
    assert descriptor["installed_library"] == reference
    assert large.name not in value["resource_paths"]
    from music_learning import contracts as c
    revision = invoke(service, "edits.apply", dict(project_id=project["id"],
        scope=dict(track_ids=[track], start_sample=0, end_sample=44100, structural=True),
        operations=[dict(op="put", collection="plugins", entity=dict(value, id=c.new_id(),
                         track_id=track, order=0))], description="Installed dependency"), revision["id"])
    manifest_data = manifest.read_bytes()
    manifest.unlink()  # The captured CAS manifest remains authoritative for this revision.
    render = invoke(service, "jobs.submit", dict(project_id=project["id"], kind="project.render",
                    payload={}), revision["id"])
    assert worker.run_once()
    rendered = invoke(service, "jobs.get", dict(job_id=render["id"]))
    assert rendered["status"] == "succeeded", rendered
    refreshed = invoke(service, "revisions.get", dict(project_id=project["id"]))
    updated = refreshed["snapshot"]["plugins"][0]
    saved = json.loads(service.storage.verify(updated["descriptor_asset_id"]).read_bytes())
    assert saved["installed_library"] == reference
    manifest.write_bytes(manifest_data)
    large.write_bytes(b"different samplebank")
    # An explicit new capture must fail, even though its small preset is intact.
    job = invoke(service, "jobs.submit", dict(project_id=project["id"], kind="instrument.capture",
                 payload={"preset_id": entry["id"]}), revision["id"])
    assert worker.run_once()
    assert invoke(service, "jobs.get", dict(job_id=job["id"]))["status"] == "failed"
