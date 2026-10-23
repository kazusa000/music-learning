"""Exercise the actual runner dispatch without opening plugins or libraries."""
import ast
from pathlib import Path

import pytest


RUNNER = Path(__file__).resolve().parents[2] / "scripts/instruments/run_kontakt_full_intake.py"


def dispatch_prefix():
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    loop = next(node for node in ast.walk(tree) if isinstance(node, ast.For)
                and isinstance(node.iter, ast.Name) and node.iter.id == "work_items")
    # Execute real dispatch up to the storage gate. Unknown external calls fail
    # rather than silently allowing evidence/audio reads for completed rows.
    gate = next(i for i, node in enumerate(loop.body)
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "budget")
    loop.body = loop.body[:gate + 1]
    return compile(ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[])),
                   str(RUNNER), "exec")


@pytest.mark.parametrize("statuses,expected", [
    (["passed", "failed"], []),
    (["passed", "pending", "failed", "pending"], [1, 3]),
    (["passed"] * 518 + ["failed"] * 15 + ["pending"] * 459, list(range(533, 992))),
])
def test_resume_dispatches_only_pending_without_reading_terminal_evidence(statuses, expected):
    from types import SimpleNamespace

    rows = [dict(id=i, status=status) for i, status in enumerate(statuses)]
    before = [dict(row) for row in rows]
    dispatched = []
    scope = dict(work_items=rows, time=SimpleNamespace(monotonic=lambda: 0), deadline=1)
    scope["budget"] = lambda: dispatched.append(scope["item"]["id"])
    exec(dispatch_prefix(), scope)
    assert dispatched == expected
    assert rows == before


def test_publication_keeps_independent_receipt_validation():
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    names = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert {"record_receipt", "build_publication_plan", "publish_catalog_atomic",
            "validate_captured", "validate_coverage"} <= names
