import os
import sys

import pytest


sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import audit_npm_lock as audit  # noqa: E402


def test_lock_coordinates_are_scoped_deduplicated_and_sorted():
    lock = {"packages": {
        "": {"name": "root"},
        "node_modules/z": {"version": "2.0.0"},
        "node_modules/x/node_modules/z": {"version": "2.0.0"},
        "node_modules/@scope/pkg": {"version": "1.0.0"},
        "node_modules/missing": {},
    }}

    assert audit.coordinates_from_lock(lock) == [
        ("@scope/pkg", "1.0.0"), ("z", "2.0.0")]


def test_audit_reports_active_advisories_but_ignores_withdrawn_ones():
    lock = {"packages": {
        "node_modules/safe": {"version": "1.0.0"},
        "node_modules/vulnerable": {"version": "2.0.0"},
    }}

    def query(coordinates):
        assert coordinates == [("safe", "1.0.0"),
                               ("vulnerable", "2.0.0")]
        return [
            {"vulns": [{"id": "GHSA-withdrawn", "withdrawn": "now"}]},
            {"vulns": [{"id": "GHSA-active"}]},
        ]

    coordinates, findings = audit.audit_lock(lock, query=query)

    assert len(coordinates) == 2
    assert findings == [("vulnerable", "2.0.0", ["GHSA-active"])]


def test_empty_lock_is_not_reported_as_a_clean_audit():
    with pytest.raises(RuntimeError, match="no resolved npm packages"):
        audit.audit_lock({"packages": {}}, query=lambda _: [])
