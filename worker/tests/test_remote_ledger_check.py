import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import remote_ledger_check as check  # noqa: E402


def test_complete_remote_ledger_schema_passes():
    report = check.evaluate(
        True, check.REQUIRED_COLUMNS, check.REQUIRED_INDEXES)
    assert report == {
        "status": "pass", "ready": True, "table_present": True,
        "missing_columns": [], "missing_indexes": [],
    }


def test_missing_table_or_partial_schema_fails_closed():
    missing = check.evaluate(False, (), ())
    assert missing["ready"] is False
    assert missing["missing_columns"]
    partial = check.evaluate(
        True, check.REQUIRED_COLUMNS - {"call_id"},
        check.REQUIRED_INDEXES - {"idx_remote_executions_active"})
    assert partial["ready"] is False
    assert partial["missing_columns"] == ["call_id"]
    assert partial["missing_indexes"] == ["idx_remote_executions_active"]
