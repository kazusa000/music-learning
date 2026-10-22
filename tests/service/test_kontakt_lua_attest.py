import hashlib
import struct

import pytest

from music_learning.kontakt_lua_attest import (
    KontaktLuaAttestError,
    _lua_quote,
    parse_kontakt_lua_attestation_ack,
    render_kontakt_load_multi_attestation,
)


def _block(body):
    return struct.pack("<Q", len(body) + 8) + body


def _chunk(kind, payload, *, next_chunk=b""):
    return _block(b"DSIN" + struct.pack("<II", kind, 1) + next_chunk + payload)


def _opaque_kontakt_item():
    # Unsupported FastLZ framing stays opaque. These tests verify that the
    # probe binds bytes and uses Kontakt getters, not that this fixture proves a name.
    packed = b"\xe2\x17\x92\xac"
    subtree = struct.pack("<IBII", 1, 1, 4096, len(packed)) + packed
    chunks = _chunk(
        116,
        b"state",
        next_chunk=_chunk(115, subtree, next_chunk=_chunk(1, b"\x01\0\0\0")),
    )
    body = bytearray(struct.pack("<I4sII", 1, b"hsin", 1, 0))
    body.extend(b"kontakt-test-id!")
    body.extend(chunks)
    body.extend(struct.pack("<II", 1, 0))
    return _block(body)


def _probe(tmp_path, *, expected_candidate_sha256=None, expected_component_sha256=None, acknowledgement_path=None):
    candidate = tmp_path / "Kontakt's Candidate.nkm"
    candidate.write_bytes(_opaque_kontakt_item())
    component = _opaque_kontakt_item()
    ack_path = acknowledgement_path or tmp_path / "ack" / "readback.kv"
    return render_kontakt_load_multi_attestation(
        candidate_path=candidate,
        source_component=component,
        source_sha256="1" * 64,
        preset_id="fixture-preset",
        phase="derived-control",
        plugin_sha256="2" * 64,
        vst3_class_id="5653544e694b386b6f6e74616b742038",
        kontakt_exe_sha256="3" * 64,
        acknowledgement_path=ack_path,
        probe_id="probe-12345678",
        expected_candidate_sha256=expected_candidate_sha256,
        expected_component_sha256=expected_component_sha256,
    )


def test_generated_lua_loads_pinned_candidate_and_reads_actual_kontakt_getters(tmp_path):
    (tmp_path / "ack").mkdir()
    probe = _probe(tmp_path)

    candidate_sha = hashlib.sha256(_opaque_kontakt_item()).hexdigest()
    assert probe.manifest["candidate"]["sha256"] == candidate_sha
    assert probe.manifest["source_component"]["sha256"] == hashlib.sha256(_opaque_kontakt_item()).hexdigest()
    assert probe.manifest["candidate"]["semantic_identity_status"] == "opaque_identity_unavailable"
    assert probe.manifest["runtime_success_claim"] is False

    assert "Kontakt.reset_multi()" in probe.script
    assert "Kontakt.get_num_instruments() ~= 0" in probe.script
    assert "Kontakt.load_multi(candidate_path)" in probe.script
    assert "Kontakt.get_instrument_indices()" in probe.script
    assert "Kontakt.get_instrument_name(index)" in probe.script
    assert "Kontakt.get_instrument_midi_channel(index)" in probe.script
    assert f'candidate_sha256 = "{candidate_sha}"' in probe.script
    assert f'source_component_sha256 = "{probe.manifest["source_component"]["sha256"]}"' in probe.script
    assert "os.exit" not in probe.script
    assert "os.rename(temporary_path, result_path)" in probe.script
    assert "Kontakt's Candidate.nkm" in probe.script
    assert _lua_quote('embedded "quote"') == '"embedded \\"quote\\""'


@pytest.mark.parametrize("pin", ["expected_candidate_sha256", "expected_component_sha256"])
def test_generator_rejects_mismatched_prevalidated_hashes(tmp_path, pin):
    (tmp_path / "ack").mkdir()
    with pytest.raises(KontaktLuaAttestError, match="SHA-256 differs from its pin"):
        _probe(tmp_path, **{pin: "f" * 64})


def test_generator_rejects_invalid_source_component(tmp_path):
    (tmp_path / "ack").mkdir()
    candidate = tmp_path / "candidate.nkm"
    candidate.write_bytes(_opaque_kontakt_item())
    with pytest.raises(KontaktLuaAttestError, match="captured IComponent"):
        render_kontakt_load_multi_attestation(
            candidate_path=candidate,
            source_component=b"not a Kontakt component",
            source_sha256="1" * 64,
            preset_id="fixture-preset",
            phase="first-load",
            plugin_sha256="2" * 64,
            vst3_class_id="5653544e694b386b6f6e74616b742038",
            kontakt_exe_sha256="3" * 64,
            acknowledgement_path=tmp_path / "ack" / "ack.kv",
            probe_id="probe-12345678",
        )


def test_generator_uses_a_short_unique_temporary_ack_name(tmp_path):
    ack_dir = tmp_path / ("long-" * 18)
    ack_dir.mkdir()
    probe = _probe(tmp_path, acknowledgement_path=ack_dir / "readback.kv")
    assert '"' in probe.script
    assert "kt-probe-123456.tmp" in probe.script
    assert ".kv.tmp" not in probe.script


def _write_ack(path, manifest, *, candidate_sha=None, count=1):
    rows = {
        "schema_version": "1",
        "probe_id": manifest["probe_id"],
        "preset_id": manifest["preset_id"],
        "phase": manifest["phase"],
        "source_sha256": manifest["source_sha256"],
        "candidate_sha256": candidate_sha or manifest["candidate"]["sha256"],
        "source_component_sha256": manifest["source_component"]["sha256"],
        "plugin_sha256": manifest["plugin_sha256"],
        "vst3_class_id": manifest["vst3_class_id"],
        "kontakt_exe_sha256": manifest["kontakt_exe_sha256"],
        "status": "completed",
        "instrument_count": str(count),
        "instrument_indices": "0",
        "instrument_1_index": "0",
        "instrument_1_name_utf8_hex": "4772616e64205069616e6f",
        "instrument_1_midi_channel": "1",
        "error_utf8_hex": "",
    }
    path.write_text("".join(f"{key}={value}\n" for key, value in rows.items()), encoding="ascii")


def test_ack_parser_requires_hash_bindings_and_returns_contact_getters(tmp_path):
    (tmp_path / "ack").mkdir()
    probe = _probe(tmp_path)
    ack_path = tmp_path / "ack" / "readback.kv"
    _write_ack(ack_path, probe.manifest)

    parsed = parse_kontakt_lua_attestation_ack(ack_path, probe.manifest)

    assert parsed["candidate_sha256"] == probe.manifest["candidate"]["sha256"]
    assert parsed["source_component_sha256"] == probe.manifest["source_component"]["sha256"]
    assert parsed["getter"] == {
        "instrument_count": 1,
        "instrument_indices": [0],
        "instruments": [{"index": 0, "name": "Grand Piano", "midi_channel": 1}],
    }


def test_ack_parser_rejects_wrong_candidate_sha_and_inconsistent_count(tmp_path):
    (tmp_path / "ack").mkdir()
    probe = _probe(tmp_path)
    ack_path = tmp_path / "ack" / "readback.kv"

    _write_ack(ack_path, probe.manifest, candidate_sha="f" * 64)
    with pytest.raises(KontaktLuaAttestError, match="differs from its Python pin"):
        parse_kontakt_lua_attestation_ack(ack_path, probe.manifest)

    _write_ack(ack_path, probe.manifest, count=2)
    with pytest.raises(KontaktLuaAttestError, match="count and indices differ"):
        parse_kontakt_lua_attestation_ack(ack_path, probe.manifest)
