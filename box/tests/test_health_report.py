"""box/health_report.py: the owner's box health row and the disk guard.

Run locally: python -m pytest box/tests (box/ is not part of the worker
suite; a box-only push releases nothing but the box itself).
"""

import collections
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import health_report as hr  # noqa: E402

Disk = collections.namedtuple("Disk", "total used free")
GB = 1024 ** 3
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
STATE = {
    "status": "ok", "code_version": "8d6d2d73034d",
    "commit": "e7d0c4faf91785066437781d3883cf41d1fe5cf7",
    "slots": {"total": 24, "busy": 3, "by_lane": {
        "mcp": {"total": 8, "ready": 8}, "batch": {"total": 4, "ready": 3}}},
    "queue": {"depth": 2, "oldest_wait_s": 4.5},
    "cpu": {"last_60s": {"steal_pct": 0.4, "busy_pct": 61.0},
            "last_15m": {"steal_pct": 0.1}},
}
MEM = {"MemTotal": 24 * GB, "MemAvailable": 18 * GB}


def _doc(state=STATE, **kw):
    return hr.build({}, state, mem=MEM,
                    disk=kw.pop("disk", Disk(144 * GB, 39 * GB, 105 * GB)),
                    load=(1.5, 1, 1), cpus=8, reboot=False, now=NOW, **kw)


def test_the_document_has_every_field_the_owner_views_read():
    doc = _doc()
    # backend/ops_loop/views.box_health + the task's contract.
    for key in ("name", "version", "load1", "cpus", "steal_pct",
                "mem_used_pct", "disk_used_pct", "running_jobs", "queued",
                "updated_at"):
        assert key in doc, key
    assert doc["name"] == "box1" and doc["version"] == "8d6d2d73034d"
    assert doc["load1"] == 1.5 and doc["cpus"] == 8
    assert doc["mem_used_pct"] == 25.0 and doc["disk_used_pct"] == 27.1
    assert doc["running_jobs"] == 3 and doc["queued"] == 2
    assert doc["slots_ready"] == 11 and doc["slots_total"] == 24
    assert doc["steal_pct"] == 0.4 and doc["status"] == "ok"
    assert doc["updated_at"] == NOW.isoformat()
    assert doc["maintenance_until"] is None


def test_an_unreadable_admission_is_reported_not_hidden():
    doc = _doc(state=None)
    assert doc["status"] == "admission_down"
    assert doc["running_jobs"] is None and doc["queued"] is None
    assert doc["mem_used_pct"] == 25.0      # the host still reports


def test_keys_one_box_then_named_boxes():
    assert hr.key_for({}) == "box_health"
    assert hr.key_for({"name": "box1"}) == "box_health"
    assert hr.key_for({"name": "box2"}) == "box_health:box2"
    assert hr.key_for({"health_key": "x"}) == "x"


def test_disk_guard_starts_a_clean_under_low_water_at_most_every_5_min(
        tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "CLEAN_STAMP", str(tmp_path / "stamp"))
    started = []
    low = _doc(disk=Disk(144 * GB, 110 * GB, 34 * GB))
    assert hr.disk_guard({"disk_low_water_gib": 40}, low, now_s=1000,
                         start=lambda: started.append(1)) is True
    os.utime(tmp_path / "stamp", (1000, 1000))
    assert hr.disk_guard({"disk_low_water_gib": 40}, low, now_s=1100,
                         start=lambda: started.append(1)) is False
    assert hr.disk_guard({"disk_low_water_gib": 40}, low, now_s=1400,
                         start=lambda: started.append(1)) is True
    ok = _doc()
    assert hr.disk_guard({"disk_low_water_gib": 40}, ok, now_s=5000,
                         start=lambda: started.append(1)) is False
    assert started == [1, 1]


def test_a_planned_window_is_carried_until_it_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "MAINT_UNTIL", str(tmp_path / "until"))
    assert hr.maintenance_until(now_s=100) is None
    (tmp_path / "until").write_text("1000")
    assert hr.maintenance_until(now_s=100) == 1000
    assert hr.maintenance_until(now_s=2000) is None
    doc = _doc(maint=1000)
    assert doc["maintenance_until"].startswith("1970-01-01T00:16:40")
