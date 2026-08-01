"""cut_output_range — cutting a span of the ASSEMBLED program (round 71e).

Motivated by a real session: the user asked to cut 12-15s and 18-21s of the
edited video — both inside an inserted screen recording — was told three
times it was impossible (cut_range is source-time only), and then watched
set_insert_window mangle the insert instead. This tool cuts whatever plays
under an output span: footage in source time, inserts by splitting.

Run:  python -m pytest tests/test_cut_output_range.py -q     (from worker/)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
from schemas import default_edl, validate_edl                 # noqa: E402
from timeline import Timeline, insert_windows, program_blocks  # noqa: E402

SRC = 354.6


class _Ctx:
    def __init__(self, edl, duration=SRC):
        self.project_id = 1
        self.duration = duration
        self.index = {"video": {"duration": duration, "width": 1920,
                                "height": 1080, "fps": 30},
                      "words": [], "sentences": []}
        self.has_main_video = True
        self.written = []
        self._edl = validate_edl(edl, duration).model_dump()

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        # Validate exactly as the real ToolContext does — a passing test on
        # an EDL the renderer would refuse is worthless.
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


def _ins(id, at, dur, src=None, asset="clips/1/rec.mov"):
    it = {"id": id, "asset_key": asset, "kind": "video",
          "at_output_s": at, "duration_s": dur}
    if src is not None:
        it["source_start_s"] = src
    return it


def _session_edl():
    """The real shape: 3.55s of footage, then two inserts back to back.
    Program: scene1 0-3.55, scene2 3.55-8.02, scene3 8.02-22.64."""
    e = default_edl(SRC)
    e["keep"] = [[113.7, 117.25]]
    e["inserts"] = [_ins("ins1", 3.55, 4.47, src=12.41, asset="clips/1/a.mov"),
                    _ins("ins2", 3.55, 14.62, src=0.0)]
    return e


def test_the_session_cut_12_to_15_splits_scene_3():
    ctx = _Ctx(_session_edl())
    res = agent_tools.cut_output_range(ctx, 12, 15)
    assert res.startswith("EDL v"), res
    edl = ctx.latest_edl()["json"]
    ins = {i["id"]: i for i in edl["inserts"]}
    # ins2's head: clip 0 - 3.98 (output 8.02-12); tail: clip 6.98 onward
    assert abs(ins["ins2"]["duration_s"] - 3.98) < 0.03
    tail = next(i for i in edl["inserts"]
                if i["id"] not in ("ins1", "ins2"))
    assert abs(tail["source_start_s"] - 6.98) < 0.03
    assert abs(tail["duration_s"] - (14.62 - 6.98)) < 0.03
    # the program lost exactly the 3 cut seconds
    tl = Timeline(edl["keep"], edl["inserts"], edl.get("speed"))
    assert abs(tl.out_duration - (22.64 - 3.0)) < 0.05


def test_both_session_cuts_in_sequence():
    """12-15 then 18-21 — the second call's times are in the NEW program
    (after the first cut), exactly how the agent would issue them if the
    user re-stated times, but here we just verify both writes land."""
    ctx = _Ctx(_session_edl())
    assert agent_tools.cut_output_range(ctx, 12, 15).startswith("EDL v")
    assert agent_tools.cut_output_range(ctx, 15, 18).startswith("EDL v")
    edl = ctx.latest_edl()["json"]
    tl = Timeline(edl["keep"], edl["inserts"], edl.get("speed"))
    assert abs(tl.out_duration - (22.64 - 6.0)) < 0.1


def test_cut_footage_portion_maps_to_source():
    ctx = _Ctx(_session_edl())
    res = agent_tools.cut_output_range(ctx, 1.0, 2.0)
    assert res.startswith("EDL v"), res
    edl = ctx.latest_edl()["json"]
    assert edl["keep"] == [[113.7, 114.7], [115.7, 117.25]]
    # inserts still at the (moved) end boundary, program 1s shorter
    tl = Timeline(edl["keep"], edl["inserts"], edl.get("speed"))
    assert abs(tl.out_duration - 21.64) < 0.05


def test_cut_spanning_footage_and_insert():
    ctx = _Ctx(_session_edl())
    res = agent_tools.cut_output_range(ctx, 3.0, 5.0)
    assert res.startswith("EDL v"), res
    edl = ctx.latest_edl()["json"]
    # footage lost 3.0-3.55 (source 116.7-117.25); ins1 lost its head 1.45s
    assert edl["keep"] == [[113.7, 116.7]]
    ins1 = next(i for i in edl["inserts"] if i["id"] == "ins1")
    assert abs(ins1["source_start_s"] - (12.41 + 1.45)) < 0.03
    assert abs(ins1["duration_s"] - (4.47 - 1.45)) < 0.03


def test_cut_swallowing_an_insert_removes_it():
    ctx = _Ctx(_session_edl())
    res = agent_tools.cut_output_range(ctx, 3.55, 8.1)
    assert res.startswith("EDL v"), res
    edl = ctx.latest_edl()["json"]
    ids = [i["id"] for i in edl["inserts"]]
    assert "ins1" not in ids
    # ins2 lost only its first 0.08s -> under the sliver floor, kept whole
    assert "ins2" in ids


def test_rejects_a_span_past_the_program():
    ctx = _Ctx(_session_edl())
    res = agent_tools.cut_output_range(ctx, 40, 50)
    assert res.startswith("REJECTED")


def test_rejects_cutting_everything():
    e = default_edl(SRC)
    e["keep"] = [[113.7, 117.25]]
    ctx = _Ctx(e)
    res = agent_tools.cut_output_range(ctx, 0, 3.55)
    assert res.startswith("REJECTED")


def test_result_is_always_a_valid_edl_with_scene_map():
    ctx = _Ctx(_session_edl())
    agent_tools.cut_output_range(ctx, 12, 15)
    blocks = program_blocks(ctx.latest_edl()["json"])
    kinds = [b["kind"] for b in blocks]
    assert kinds == ["footage", "insert", "insert", "insert"]
    wins = insert_windows(ctx.latest_edl()["json"]["inserts"],
                          Timeline(ctx.latest_edl()["json"]["keep"],
                                   ctx.latest_edl()["json"]["inserts"]))
    assert len(wins) == 3
