"""Bind inherited request templates to a new, immutable diagnostic build plan."""
from copy import deepcopy

CASES = ("64-first", "64-reopen", "32-first", "32-reopen")


def bind_execution_contract(candidate, parent, *, builder_path, builder_sha256):
    """Supply missing producer metadata; keep native/request pins unchanged.

    The existing frozen executor must still validate every file, source digest,
    native binary, request field and output destination before execution.
    """
    for plan in (candidate, parent):
        if (plan.get("kind") != "ample-offline-message-pump-cadence-diagnostic"
                or plan.get("status") != "prepared_not_started"
                or tuple(plan.get("request_variants", {})) != CASES):
            raise ValueError("Expected an immutable, unstarted cadence build plan")
    templates = parent.get("request_templates")
    if not isinstance(templates, dict) or set(templates) != {"first", "reopen"}:
        raise ValueError("Parent lacks its original first/reopen source templates")
    if candidate.get("source_positive_control") != parent.get("source_positive_control"):
        raise ValueError("New build changed the original musical source contract")
    for case in CASES:
        row = candidate["request_variants"][case]
        old = parent["request_variants"][case]
        template = templates[case.split("-", 1)[1]]
        if (row.get("source_request") != template.get("path")
                or row.get("source_request_sha256") != template.get("sha256")
                or row.get("musical_request_sha256") != old.get("musical_request_sha256")):
            raise ValueError("Request no longer binds the same exact source template")
    if (not isinstance(builder_path, str) or not builder_path
            or not isinstance(builder_sha256, str) or len(builder_sha256) != 64
            or any(c not in "0123456789abcdef" for c in builder_sha256)):
        raise ValueError("Exact frozen build-script identity is required")
    result = deepcopy(candidate)
    builder = {"path": builder_path, "sha256": builder_sha256}
    if ("builder_script" in result and result["builder_script"] != builder
            or "request_templates" in result and result["request_templates"] != templates):
        raise ValueError("Existing execution bindings conflict with the frozen inputs")
    result["builder_script"] = builder
    result["request_templates"] = deepcopy(templates)
    return result
