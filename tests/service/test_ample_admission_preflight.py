from pathlib import Path

from music_learning.ample_admission import _failure_code, build_retest_plan, merge_latest_items


def test_later_retry_snapshot_overrides_earlier_factory_row():
    original = {"items": [{"id": "same", "status": "failed"},
                          {"id": "untouched", "status": "passed"}]}
    retry = {"items": [{"id": "same", "status": "passed"}]}

    merged = merge_latest_items((("original.json", original), ("retry.json", retry)))

    assert len(merged) == 2
    assert merged["same"] == (Path("retry.json"), {"id": "same", "status": "passed"})
    assert merged["untouched"] == (Path("original.json"), {"id": "untouched", "status": "passed"})


def test_shared_failure_reason_codes_keep_duration_and_factory_state_distinct():
    assert _failure_code("first-load Rendered request duration differs from the source-pinned performance phrase") == \
        "performance_phrase_duration_mismatch"
    assert _failure_code("Loaded Ample component parameters differ from exact source/default state: changed_source_parameters") == \
        "factory_state_identity_mismatch"


def test_retest_plan_keeps_first_reopen_and_exact_source_profile_provenance():
    report = {
        "kind": "ample-strict-publication-preflight",
        "items": [
            {
                "factory_id": "riff-id", "product": "AGLP", "variant_name": "80s Solo",
                "latest_queue": "retry.json", "latest_queue_status": "passed",
                "factory_source": {"expected_sha256": "factory-sha"},
                "preflight_status": "retest_required",
                "profile": {"source_sha256": "riff-source-sha", "profile_sha256": "profile-sha"},
                "phases": {
                    "first": {"status": "failed", "reason_code": "duration"},
                    "reopen": {"status": "failed", "reason_code": "duration"},
                },
                "artifact_refs": {"first": {"probe": {"sha256": "first-sha"}},
                                  "reopen": {"probe": {"sha256": "reopen-sha"}}},
                "source_parameter_differences": {}, "profile_issues": [],
            },
            {
                "factory_id": "resource-wait", "product": "AGSC",
                "preflight_status": "strict_evidence_pass_resource_pending",
            },
            {
                "factory_id": "unverified", "product": "AGM", "variant_name": "Bad",
                "latest_queue": "original.json", "latest_queue_status": "failed",
                "factory_source": {"expected_sha256": "failed-source-sha"},
                "preflight_status": "source_queue_nonpass",
                "source_queue_result": {"reason": "old probe failed"},
            },
        ],
    }

    plan = build_retest_plan(report)

    assert plan["counts"] == {"evidence_retest_items": 1, "source_queue_nonpass_items": 1}
    retest = plan["evidence_retests"][0]
    assert retest["required_phases"] == ["first", "reopen"]
    assert retest["source_profile_sha256"] == "profile-sha"
    assert retest["source_profile_factory_sha256"] == "riff-source-sha"
    assert retest["factory_source"]["expected_sha256"] == "factory-sha"
    assert retest["artifact_refs"]["reopen"]["probe"]["sha256"] == "reopen-sha"
    assert plan["source_queue_nonpasses"][0]["factory_id"] == "unverified"
    assert plan["scope"]["comparison"] == (
        "exact typed state; host parameters and all non-AGM component fields remain exact; "
        "Ample Guitar M 3.1.0 AAG_STATE numeric Value is serialized with shipped "
        "defaultfloat precision 6 then compared as exact float32; no epsilon"
    )

