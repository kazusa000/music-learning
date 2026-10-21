import xml.etree.ElementTree as ET

import pytest

from music_learning.kontakt_registration import library_hints, parse_nicnt, profile_settings


def metadata(name="Factory", snpid="KL2"):
    raw = (b"binary header\x00" + ('<ProductHints spec="1.0"><Product version="1">'
           '<RegKey>' + name + '</RegKey><Name>' + name + '</Name><SNPID>' + snpid + '</SNPID>'
           '<Company>Vendor</Company><ProductSpecific><HU>original-hu</HU><JDX>original-jdx</JDX>'
           '</ProductSpecific></Product></ProductHints>').encode() + b"\x00artwork")
    return parse_nicnt(raw)


def test_runtime_hints_use_library_container_and_preserve_original_fields():
    original = metadata()
    result = ET.fromstring(library_hints(None, [original]))
    assert result.tag == "LibraryHints"
    observed = result.find("Library")
    expected = ET.fromstring(original.product_xml)
    assert observed.attrib == expected.attrib
    assert [ET.tostring(x) for x in observed] == [ET.tostring(x) for x in expected]
    assert result.find("Product") is None


def test_runtime_merge_keeps_other_libraries_and_is_repeatable():
    old = metadata("Old", "OLD")
    new = metadata()
    merged = library_hints(library_hints(None, [old]), [new])
    assert library_hints(merged, [new]) == merged
    assert [node.findtext("RegKey") for node in ET.fromstring(merged)] == ["Old", "Factory"]


def test_conflicting_vendor_library_identity_is_rejected_not_regenerated():
    original = library_hints(None, [metadata()])
    with pytest.raises(ValueError, match="identifier"):
        library_hints(original, [metadata("Factory", "BAD")])
    with pytest.raises(ValueError, match="collide"):
        library_hints(original, [metadata("Other", "KL2")])


def test_settings_keeps_device_and_unrelated_fields_and_updates_only_selected_library():
    original = "[Kontakt Application]\nAB2 AudioDevice=sz:valid device\n\n[Factory]\nCustom=dw:9\nContentDir=sz:old\n\n[Other]\nName=sz:keep\n"
    result = profile_settings(original, [(metadata(), "H:\\Sound Library\\Factory\\", "1.4.0")])
    assert "AB2 AudioDevice=sz:valid device" in result and "Custom=dw:9" in result
    assert "[Other]\nName=sz:keep\n" in result
    assert "ContentDir=sz:old" not in result
    assert "HU=sz:original-hu" in result and "JDX=sz:original-jdx" in result
    assert result.count("[Factory]") == 1
    assert profile_settings(result, [(metadata(), "H:\\Sound Library\\Factory\\", "1.4.0")]) == result


def test_registration_rejects_wrong_runtime_container_and_injected_sections():
    with pytest.raises(ValueError, match="LibraryHints/Library"):
        library_hints(metadata().product_xml, [metadata()])
    with pytest.raises(ValueError, match="invalid"):
        parse_nicnt(b'<ProductHints><Product><Name>x</Name><RegKey>x\n[other]</RegKey><SNPID>x</SNPID></Product></ProductHints>')
    with pytest.raises(ValueError, match="ambiguous"):
        profile_settings("[Factory]\nA=1\n[Factory]\nB=2\n", [(metadata(), "H:\\Factory", "1")])


def test_unknown_content_version_is_not_fabricated():
    result = profile_settings("[Kontakt Application]\nA=1\n", [(metadata(), "H:\\Factory", None)])
    assert "ContentVersion" not in result
