import hashlib
import json

import pytest

from music_learning.kontakt_identity_attestation import make_kontakt_identity_verifier


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    return _sha(raw)


def _fixture(root):
    source_sha = _sha(b"one source nki")
    plugin_sha = _sha(b"kontakt vst3 binary")
    class_id = "Kontakt-VST3-class-id"
    kontakt_sha = _sha(b"kontakt executable")
    lua_sha = _sha(b"pinned lua identity probe")
    first_state = _sha(b"captured first state")
    reopen_state = _sha(b"captured reopened state")
    candidate = {
        "id": "kontakt-one-preset",
        "plugin_sha256": plugin_sha,
        "vst3_class_id": class_id,
        "expected_identity": {
            "source_sha256": source_sha,
            "instrument_count": 2,
            "instrument_indices": [0, 3],
            "instruments": [
                {"index": 0, "name": "Kontakt factory instrument", "key_count": 49, "group_count": 3},
                {"index": 3, "name": "Kontakt layer", "key_count": 37, "group_count": 1},
            ],
        },
    }
    phases = {}
    for phase, state_sha in (("first-load", first_state), ("reopen", reopen_state)):
        value = {
            "schema_version": 1,
            "phase": phase,
            "preset_id": candidate["id"],
            "source_sha256": source_sha,
            "plugin_sha256": plugin_sha,
            "vst3_class_id": class_id,
            "kontakt_exe_sha256": kontakt_sha,
            "lua_script_sha256": lua_sha,
            "component_state_sha256": state_sha,
            "getter": {
                "instrument_count": 2,
                "instrument_indices": [0, 3],
                "instruments": [
                    {"index": 0, "name": "Kontakt factory instrument", "key_count": 49, "group_count": 3},
                    {"index": 3, "name": "Kontakt layer", "key_count": 37, "group_count": 1},
                ],
            },
        }
        path = root / f"attestations/{phase}.json"
        phases[phase] = {"path": path.relative_to(root).as_posix(), "sha256": _write_json(path, value)}
    manifest = {
        "schema_version": 1,
        "preset_id": candidate["id"],
        "source_sha256": source_sha,
        "plugin_sha256": plugin_sha,
        "vst3_class_id": class_id,
        "kontakt_exe_sha256": kontakt_sha,
        "lua_script_sha256": lua_sha,
        "phases": phases,
    }
    manifest_path = root / "attestations/manifest.json"
    manifest_sha = _write_json(manifest_path, manifest)
    return candidate, manifest_path, manifest_sha, kontakt_sha, lua_sha, first_state, reopen_state


def test_verifier_reads_separate_real_getter_attestations_for_first_and_reopen(tmp_path):
    candidate, manifest_path, manifest_sha, kontakt_sha, lua_sha, first_sha, reopen_sha = _fixture(tmp_path)
    verifier = make_kontakt_identity_verifier(
        tmp_path,
        {candidate["id"]: {"path": manifest_path.relative_to(tmp_path).as_posix(), "sha256": manifest_sha}},
        kontakt_exe_sha256=kontakt_sha,
        lua_script_sha256=lua_sha,
    )
    assert verifier.name == "kontakt-lua-getter-attestation"
    assert verifier.version.startswith("1:")
    identity = {
        "source_sha256": candidate["expected_identity"]["source_sha256"],
        "instrument_count": 2,
        "instrument_indices": [0, 3],
        "instruments": [
            {"index": 0, "name": "Kontakt factory instrument", "key_count": 49, "group_count": 3},
            {"index": 3, "name": "Kontakt layer", "key_count": 37, "group_count": 1},
        ],
    }
    assert verifier.verify(candidate, {"state_sha256": first_sha}) == identity
    assert verifier.verify(candidate, {"state_sha256": reopen_sha}) == identity


def test_verifier_binds_attestation_to_candidate_runtime_and_component_hash(tmp_path):
    candidate, manifest_path, manifest_sha, kontakt_sha, lua_sha, first_sha, _ = _fixture(tmp_path)
    verifier = make_kontakt_identity_verifier(
        tmp_path,
        {candidate["id"]: {"path": manifest_path.relative_to(tmp_path).as_posix(), "sha256": manifest_sha}},
        kontakt_exe_sha256=kontakt_sha,
        lua_script_sha256=lua_sha,
    )
    with pytest.raises(ValueError, match="component-state SHA-256"):
        verifier.verify(candidate, {"state_sha256": _sha(b"different component state")})
    with pytest.raises(ValueError, match="runtime pins"):
        verifier.verify({**candidate, "plugin_sha256": _sha(b"other VST3")}, {"state_sha256": first_sha})
    with pytest.raises(ValueError, match="runtime pins"):
        make_kontakt_identity_verifier(
            tmp_path,
            {candidate["id"]: {"path": manifest_path.relative_to(tmp_path).as_posix(), "sha256": manifest_sha}},
            kontakt_exe_sha256=_sha(b"wrong Kontakt executable"),
            lua_script_sha256=lua_sha,
        ).verify(candidate, {"state_sha256": first_sha})


def test_verifier_rejects_changed_attestation_and_inconsistent_multi_instrument_count(tmp_path):
    candidate, manifest_path, manifest_sha, kontakt_sha, lua_sha, first_sha, _ = _fixture(tmp_path)
    first_path = tmp_path / "attestations/first-load.json"
    first = json.loads(first_path.read_bytes())
    first["getter"]["instrument_count"] = 3
    _write_json(first_path, first)
    verifier = make_kontakt_identity_verifier(
        tmp_path,
        {candidate["id"]: {"path": manifest_path.relative_to(tmp_path).as_posix(), "sha256": manifest_sha}},
        kontakt_exe_sha256=kontakt_sha,
        lua_script_sha256=lua_sha,
    )
    with pytest.raises(ValueError, match="SHA-256 differs"):
        verifier.verify(candidate, {"state_sha256": first_sha})


def test_verifier_manifest_and_attestations_must_stay_inside_evidence_root(tmp_path):
    candidate, manifest_path, manifest_sha, kontakt_sha, lua_sha, first_sha, _ = _fixture(tmp_path)
    verifier = make_kontakt_identity_verifier(
        tmp_path,
        {candidate["id"]: {"path": manifest_path.relative_to(tmp_path).as_posix(), "sha256": manifest_sha}},
        kontakt_exe_sha256=kontakt_sha,
        lua_script_sha256=lua_sha,
    )
    unsafe = make_kontakt_identity_verifier(
        tmp_path,
        {candidate["id"]: {"path": "../outside.json", "sha256": manifest_sha}},
        kontakt_exe_sha256=kontakt_sha,
        lua_script_sha256=lua_sha,
    )
    with pytest.raises(ValueError, match="Unsafe"):
        unsafe.verify(candidate, {"state_sha256": first_sha})
    assert verifier.verify(candidate, {"state_sha256": first_sha})["instrument_count"] == 2
