"""export_final refuses a programme that ends on a held picture.

Owner rule (22 Oct 2026): the payoff holds 0.6-1.2 s on LIVE footage, or the
short ends on the last word; never a freeze-frame, a slowed or time-effect
tail, or a still before the end card. On 23 Oct export_final still shipped
JRE #2054 Part 2 p07/p16/p17/p36/p44 (projects 3824, 3833, 3834, 3853, 3861):
each ended on an add_freeze_frame payoff hold, and each version's stored
verification record predated the picture QC that measures the join.

    cd backend && python -m pytest tests/test_tail_gate.py -q
"""
import contextlib
import copy
import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import mcp as mcpmod                        # noqa: E402
from routes import video                                # noqa: E402
from video_services import tail_gate                    # noqa: E402

TL = video.wtimeline

# p07, project 3824, EDL v34 as exported on 23 Oct (keep + the hold insert).
P07 = {
    "keep": [[876.13, 877.7], [880.7, 881.46], [881.7, 883.22],
             [883.62, 885.3], [917.78, 921.5], [921.95, 925.87],
             [926.35, 928.11], [935.88, 936.84], [937.08, 937.88],
             [941.08, 946.12], [946.56, 947.93], [948.05, 949.46],
             [980.76, 985.4], [987.43, 989.99]],
    "inserts": [{"id": "ins1", "fit": "crop", "crop": None,
                 "hold": {"room_tone": [1000.5, 1001.8]}, "kind": "image",
                 "asset_key": "generated/3824/freeze-54aa0be9c9ba.png",
                 "duration_s": 1.3, "at_output_s": 31.71}],
}
# The rendered final's own picture QC row for it (asset 31069).
P07_STALL = [["held", 31.732, 32.733, "hold",
              "the held frame 'ins1' (31.71-33.01s) freezes the picture "
              "before the card — end on live footage (shorten or move it "
              "so moving picture runs into the card)"]]


def _live(edl):
    out = copy.deepcopy(edl)
    out["inserts"] = []
    return out


def test_the_p07_payoff_hold_is_an_edl_hold():
    holds = tail_gate.edl_holds(P07, TL)
    assert [(h["id"], h["t0"], h["t1"]) for h in holds] == [
        ("ins1", 31.71, 33.01)]
    assert "add_freeze_frame" in holds[0]["what"]
    assert holds[0]["fix"] == "remove_insert(id='ins1')"
    assert tail_gate.edl_holds(_live(P07), TL) == []


def test_a_canvas_programme_without_a_main_video_is_not_gated():
    edl = {"keep": [], "inserts": [
        {"id": "photo3", "kind": "image", "duration_s": 3.0,
         "asset_key": "uploads/9/p3.jpg", "at_output_s": 6.0}]}
    assert tail_gate.edl_holds(edl, TL) == []


def test_a_still_that_leaves_live_footage_before_the_card_passes():
    edl = _live(P07)
    end = TL.Timeline(edl["keep"]).out_duration            # 31.71
    # a 1.0 s still at the second-to-last cut: 2.56 s of live footage follow
    edl["inserts"] = [{"id": "still", "kind": "image", "duration_s": 1.0,
                       "asset_key": "uploads/9/x.png",
                       "at_output_s": round(end - 2.56, 2)}]
    assert tail_gate.edl_holds(edl, TL) == []
    # ...but the same still as the last picture is refused
    edl["inserts"][0]["at_output_s"] = round(end, 2)
    holds = tail_gate.edl_holds(edl, TL)
    assert [h["id"] for h in holds] == ["still"]
    assert holds[0]["what"] == "the still image insert"


def test_a_slowed_tail_and_a_frame_repeating_time_effect_are_holds():
    edl = _live(P07)
    edl["speed"] = [{"id": "sp1", "start": 988.5, "end": 989.99,
                     "factor": 0.55}]
    holds = tail_gate.edl_holds(edl, TL)
    assert [h["id"] for h in holds] == ["sp1"]
    assert holds[0]["fix"] == "remove_speed(id='sp1')"
    # 1x and faster tails are live
    edl["speed"][0]["factor"] = 1.25
    assert tail_gate.edl_holds(edl, TL) == []
    edl = _live(P07)
    end = TL.Timeline(edl["keep"]).out_duration
    edl["fx"] = [{"id": "tm_post", "type": "time", "kind": "posterize",
                  "start": end - 1.0, "end": end},
                 {"id": "tm_echo", "type": "time", "kind": "echo",
                  "start": end - 1.0, "end": end}]
    holds = tail_gate.edl_holds(edl, TL)
    assert [h["id"] for h in holds] == ["tm_post"]
    assert holds[0]["fix"] == "remove_depth_time(ids=['tm_post'])"


def test_a_visual_only_freeze_overlay_is_a_hold_and_a_pip_is_not():
    edl = _live(P07)
    end = TL.Timeline(edl["keep"]).out_duration
    edl["overlays"] = [
        {"id": "ov_freeze", "kind": "image", "fit": "cover",
         "asset_key": "generated/3824/freeze-1.png",
         "start": end - 1.0, "duration_s": 1.0},
        {"id": "ov_pip", "kind": "image", "fit": None,
         "asset_key": "uploads/9/logo.png",
         "start": end - 2.0, "duration_s": 2.0}]
    assert [h["id"] for h in tail_gate.edl_holds(edl, TL)] == ["ov_freeze"]


def test_a_rendered_hold_blocks_and_only_a_footage_run_can_be_justified():
    assert [m["cause"] for m in tail_gate.rendered_holds(
        {"stall": P07_STALL})] == ["hold"]
    footage = [["held", 30.9, 31.6, "footage", "the footage itself..."]]
    assert len(tail_gate.rendered_holds({"stall": footage})) == 1
    record = {"findings": [{"finding_id": "vf_1", "message":
                            "picture QC: STALL BEFORE END CARD 30.90-31.60s"}],
              "justifications": [{"finding_id": "vf_1",
                                  "justification": "frames move"}]}
    assert tail_gate.rendered_holds({"stall": footage}, record) == []
    # a justification never clears the edit's own hold
    assert len(tail_gate.rendered_holds({"stall": P07_STALL}, record)) == 1
    # the renderer's join and black runs are not the editor's to repair
    assert tail_gate.rendered_holds({"stall": [
        ["held", 31.6, 31.8, "join", "..."],
        ["black", 31.0, 31.7, "fade_out", "..."]]}) == []
    assert tail_gate.rendered_holds({}) == []


class _Cur:
    def __init__(self, edl, qc=None, record=None):
        self.edl, self.qc, self.record, self._one = edl, qc, record, None
        self.sql = []

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self.sql.append(q)
        if q.startswith("SELECT json FROM edls"):
            self._one = {"json": self.edl}
        elif "meta->'picture_qc' AS qc" in q:
            self._one = {"qc": self.qc} if self.qc is not None else None
        elif "to_regclass" in q:
            self._one = {"t": "verification_records"}
        elif q.startswith("SELECT record FROM verification_records"):
            self._one = {"record": self.record}
        else:
            self._one = None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return [self._one] if self._one else []


def _export(monkeypatch, cur):
    calls = []

    @contextlib.contextmanager
    def connect():
        yield type("C", (), {"cursor": lambda self: cur,
                             "rollback": lambda self: None})()

    monkeypatch.setattr(mcpmod, "vdb", connect)
    monkeypatch.setattr(mcpmod, "_project_for_user",
                        lambda *_: {"id": 3824, "title": "p07"})
    monkeypatch.setattr(mcpmod, "_open_verification_findings",
                        lambda *_: [])
    monkeypatch.setattr(mcpmod, "_request_final",
                        lambda *a, **k: calls.append(a) or pytest.fail(
                            "a held tail reached the export queue"))
    return mcpmod._t_export_final({"user_id": 60},
                                  {"project_id": 3824, "edl_version": 34}), calls


def test_export_final_refuses_the_p07_hold_naming_the_fix(monkeypatch):
    result, calls = _export(monkeypatch, _Cur(P07, qc={"stall": P07_STALL}))
    assert result["isError"] is True and calls == []
    export = result["structuredContent"]["export"]
    assert export["code"] == "held_tail"
    assert [h["id"] for h in export["edl_holds"]] == ["ins1"]
    text = result["content"][0]["text"]
    assert "no export was started" in text
    assert "remove_insert(project_id=3824, id='ins1')" in text
    assert "0.6-1.2 s after the last word" in text
    assert "end on the last word" in text
    assert "source 987.43-989.99s" in text            # the last keep named
    assert "A justification cannot clear a hold the EDL makes" in text


def test_export_final_refuses_a_measured_hold_on_a_live_edl(monkeypatch):
    footage = [["held", 30.9, 31.6, "footage", "the footage itself does not "
                "move there — end on a moving moment"]]
    result, _ = _export(monkeypatch, _Cur(_live(P07), qc={"stall": footage}))
    export = result["structuredContent"]["export"]
    assert export["code"] == "held_tail" and export["edl_holds"] == []
    assert "justify_verification_findings" in result["content"][0]["text"]


def test_export_final_passes_a_live_tail(monkeypatch):
    from flask import Flask, jsonify
    cur = _Cur(_live(P07), qc={"stall": []})
    with Flask(__name__).app_context():
        @contextlib.contextmanager
        def connect():
            yield type("C", (), {"cursor": lambda self: cur,
                                 "rollback": lambda self: None})()
        monkeypatch.setattr(mcpmod, "vdb", connect)
        monkeypatch.setattr(mcpmod, "_project_for_user",
                            lambda *_: {"id": 3824, "title": "p07"})
        monkeypatch.setattr(mcpmod, "_open_verification_findings",
                            lambda *_: [])
        monkeypatch.setattr(mcpmod, "_request_final",
                            lambda *a, **k: jsonify(job_id=777))
        result = mcpmod._t_export_final(
            {"user_id": 60}, {"project_id": 3824, "edl_version": 34})
    assert result["isError"] is False
    assert result["structuredContent"]["export"]["job_id"] == 777
