"""Wave 1 (1F): the final-export DEGRADED gate in the backend.

The worker stamps a final's unacknowledged degraded device rows on the asset
(``degraded_hold``: quality_verifier.unacknowledged_degradations at commit,
re-judged on every acknowledgement). The backend reads the stamp: the final
is not downloadable, a new export request is refused with the devices named
(re-encoding the same immutable EDL cannot fix it), and MCP's download says
how to release it."""

import os
import sys
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest                      # noqa: E402
from flask import Flask            # noqa: E402

from routes import mcp as mcpmod   # noqa: E402
from routes import video           # noqa: E402

ROW = {"id": "sx1", "device": "subject_fill_flash", "rung": "substitute",
       "reason": "matte.person pending -> full_frame_flash"}


def test_hold_reads_the_stamp_and_holds_an_unjudged_final():
    assert video.final_degraded_hold({}) == []
    assert video.final_degraded_hold({"degraded_hold": []}) == []
    assert video.final_degraded_hold({"degraded_hold": [ROW, "x"]}) == [ROW]
    # the stamp is the worker's judgement: [] releases even degraded rows
    assert video.final_degraded_hold({"degraded_hold": [],
                                      "devices": [ROW]}) == []
    # before the job commits (or when its stamp failed) the render's own
    # degraded rows hold it: never downloadable unjudged
    assert video.final_degraded_hold({"devices": [ROW]}) == [ROW]
    full = dict(ROW, rung="full")
    assert video.final_degraded_hold({"devices": [full]}) == []
    msg = video.final_hold_message([ROW])
    assert msg.startswith("This export is held: subject_fill_flash "
                          "(substitute)")


class _GateCur:
    def __init__(self):
        self._one, self._rows = None, []

    def execute(self, sql, params=None):
        self._rows, self._one = [], None
        if "bool_or" in sql:
            self._one = {"emits": True}
        elif "FROM users" in sql:
            self._one = {"is_subscribed": 1, "plan": "ai"}
        elif "to_regclass" in sql:
            self._one = {"t": None}

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


def test_a_held_final_is_not_downloadable():
    video._pipeline_probe.clear()
    gate = video._final_gate(_GateCur(), project_id=1, user_id=7)
    ok_meta = {"outro_v": video.OUTRO_VERSION,
               "trans_v": video.TRANSITION_VERSION, "wm_v": 0,
               "delivery_v": 1}
    assert gate(10, ok_meta, 3)
    assert not gate(11, dict(ok_meta, degraded_hold=[ROW]), 3)
    assert gate(12, dict(ok_meta, degraded_hold=[]), 3)


class _ReqCur:
    def __init__(self, asset):
        self.asset, self.row = asset, None

    def execute(self, sql, params=()):
        self.row = None
        if "SELECT version, json FROM edls" in sql:
            self.row = {"version": 4, "json": {}}
        elif "kind='render'" in sql:
            self.row = self.asset

    def fetchone(self):
        return self.row


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(video, "_project_for_user",
                        lambda cur, pid, uid: {"id": pid})
    monkeypatch.setattr(video, "_obsolete_failed_render_version",
                        lambda *a: None)
    monkeypatch.setattr(video, "_active_original",
                        lambda *a: {"duration_s": 20, "meta": {}})
    monkeypatch.setattr(video, "_export_edl_error", lambda *a: None)
    events = []
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: events.append((a, k)))
    monkeypatch.setattr(video, "_enqueue", lambda *a, **k: 777)
    return events


def _request(cur):
    with Flask(__name__).app_context():
        got = video._request_final(cur, 60, 3, 4)
        resp, status = got if isinstance(got, tuple) else (got, 200)
        return resp.get_json(), status


def test_a_held_version_is_not_re_encoded(quiet):
    data, status = _request(_ReqCur({"id": 9, "meta": {
        "degraded_hold": [ROW]}}))
    assert status == 409 and data["code"] == "device_degraded"
    assert data["devices"][0]["device"] == "subject_fill_flash"
    assert quiet[-1][0][2] == "export_blocked"


def test_an_acknowledged_or_clean_version_exports(quiet):
    assert _request(_ReqCur({"id": 9, "meta": {"degraded_hold": []}})) == \
        ({"job_id": 777}, 200)
    assert _request(_ReqCur(None)) == ({"job_id": 777}, 200)


def test_mcp_marks_the_verify_reads_read_only():
    for verb in ("suggest_devices", "proof_device", "audit_motion"):
        assert verb in mcpmod._READ_ONLY_EXACT
    assert "set_intensity" not in mcpmod._READ_ONLY_EXACT
