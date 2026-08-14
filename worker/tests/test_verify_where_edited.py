"""Round 81 — the self-check looks WHERE THE EDIT HAPPENED.

The render self-check has always reviewed a 3x3 sheet of the WHOLE programme
sampled evenly — nine tiles for any length of video, blind to where the turn
actually worked, asking a generic "anything broken?". A mis-rendered title at
2.6s of a 20-minute edit had a 9-in-1200 chance of appearing in a tile at
all, and no tile carried the sentence that makes a check falsifiable. This
is the gap between the agent and a human editor: the human looks at the
exact pixels they changed, with the intention in mind, and iterates once if
it missed.

verify_plan derives (output_second, claim) pairs from the diff between the
version being rendered and the version the user last saw; the render job
pulls a frame at each; the self-check reviews the CLAIMS first, then the
whole-video sheet — one vision call either way.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                          # noqa: E402
import edl_diff                                             # noqa: E402
import llm                                                  # noqa: E402
import sheets                                               # noqa: E402


BASE = {"keep": [[0.0, 30.0]]}


def _with(**over):
    e = dict(BASE)
    e.update(over)
    return e


# ── the plan: claims derived from the diff ───────────────────────────────

def test_the_launch_video_text_change_yields_exact_claims():
    """The real round-79 edit: two card texts re-worded, tx1 behind the
    person. The claim must carry the exact new words and the occlusion."""
    prev = _with(texts=[{"id": "tx1", "text": "I TRAINED AN AI AGENT",
                         "start": 0.0, "end": 2.0}])
    new = _with(texts=[{"id": "tx1", "text": "I TURNED AN AI AGENT",
                        "start": 0.0, "end": 2.0, "behind": {"fp": "x"}},
                       {"id": "tx2", "text": "INTO A VIDEO EDITOR",
                        "start": 2.0, "end": 3.5}])
    plan = edl_diff.verify_plan(prev, new)
    assert len(plan) == 2
    (t1, c1), (t2, c2) = plan
    assert t1 == 1.0 and "I TURNED AN AI AGENT" in c1 and "BEHIND" in c1
    assert t2 == 2.75 and "INTO A VIDEO EDITOR" in c2 and "BEHIND" not in c2


def test_unchanged_items_make_no_claims():
    e = _with(texts=[{"id": "tx1", "text": "SAME", "start": 1.0, "end": 2.0}])
    assert edl_diff.verify_plan(e, dict(e)) == []


def test_a_removed_text_claims_its_own_absence():
    prev = _with(texts=[{"id": "tx1", "text": "GONE NOW",
                         "start": 4.0, "end": 6.0}])
    plan = edl_diff.verify_plan(prev, _with(texts=[]))
    assert len(plan) == 1
    t, c = plan[0]
    assert t == 4.0 and "GONE NOW" in c and "NOT appear" in c


def test_an_added_insert_is_claimed_by_clip_name_inside_its_window():
    new = _with(inserts=[{"id": "ins1", "kind": "video",
                          "asset_key": "clips/300/f4d5.mov",
                          "at_output_s": 0.0, "duration_s": 4.0}])
    plan = edl_diff.verify_plan(BASE, new)
    assert len(plan) == 1
    t, c = plan[0]
    assert 0.0 < t < 4.0
    assert "f4d5.mov" in c and "fills" in c


def test_zoom_claims_distinguish_aimed_from_plain():
    aimed = _with(effects={"zooms": [{"id": "z1", "start": 2.0, "end": 4.0,
                                      "strength": 2.0, "cx": 0.8}]})
    plain = _with(effects={"zooms": [{"id": "z1", "start": 2.0, "end": 4.0,
                                      "strength": 2.0}]})
    (ta, ca), = edl_diff.verify_plan(BASE, aimed)
    (tp, cp), = edl_diff.verify_plan(BASE, plain)
    assert ta == tp == 3.0
    assert "subject fully in frame" in ca
    assert "push-in" in cp


def test_windowed_stylize_is_claimed_and_global_passes_are_not():
    new = _with(effects={"stylize": [
        {"id": "st1", "kind": "flash", "start": 5.0, "end": 5.5},
        {"id": "st2", "kind": "grain", "start": None, "end": None}]})
    plan = edl_diff.verify_plan(BASE, new)
    assert len(plan) == 1
    assert "flash" in plan[0][1]


def test_the_cap_thins_across_the_whole_span_not_the_head():
    new = _with(texts=[{"id": f"tx{i}", "text": f"T{i}",
                        "start": float(i * 3), "end": float(i * 3) + 1.0}
                       for i in range(10)])
    plan = edl_diff.verify_plan(BASE, new)
    assert len(plan) == edl_diff.VERIFY_MAX_TILES
    times = [t for t, _ in plan]
    assert times == sorted(times)
    assert times[-1] > 20.0, "capping must not discard the tail"


def test_simultaneous_claims_share_a_tile():
    new = _with(texts=[{"id": "tx1", "text": "A", "start": 2.0, "end": 4.0},
                       {"id": "tx2", "text": "B", "start": 2.0, "end": 4.0}])
    plan = edl_diff.verify_plan(BASE, new)
    assert len(plan) == 1
    assert "ALSO" in plan[0][1]


def test_verify_plan_never_raises():
    assert edl_diff.verify_plan({"keep": "garbage"}, {"keep": None}) == []
    assert edl_diff.verify_plan(None, None) == []


# ── the sheet: numbered tiles at explicit times ──────────────────────────

def test_build_frames_sheet_tiles_the_requested_moments(tmp_path):
    src = str(tmp_path / "src.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=10:duration=3",
                    "-pix_fmt", "yuv420p", src],
                   check=True, capture_output=True)
    out = str(tmp_path / "verify.jpg")
    sheets.build_frames_sheet(src, out, [0.5, 1.5, 2.5])
    from PIL import Image
    img = Image.open(out)
    assert img.size == (3 * sheets.TILE_W, sheets.TILE_H + sheets.LABEL_H)
    with pytest.raises(ValueError):
        sheets.build_frames_sheet(src, out, [])


def test_build_frames_sheet_parallel_path_keeps_tile_order(tmp_path):
    src = str(tmp_path / "src-parallel.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=10:duration=3",
                    "-pix_fmt", "yuv420p", src],
                   check=True, capture_output=True)
    out = str(tmp_path / "parallel.jpg")
    sheets.build_frames_sheet(
        src, out, [0.2, 0.7, 1.2, 1.7, 2.2], cols=4,
        max_tiles=5, parallelism=4)
    from PIL import Image
    assert Image.open(out).size == (
        4 * sheets.TILE_W, 2 * (sheets.TILE_H + sheets.LABEL_H))


# ── the check: claims reach the reviewer, degrade without the sheet ──────

class _Ctx:
    def __init__(self, tmp, edl=None, last_preview=None, db=None):
        self.workdir = str(tmp)
        self._edl = {"version": 7, "json": edl or dict(BASE)}
        self.last_preview = last_preview
        self.db = db
        self.project_id = 300

    def latest_edl(self):
        return self._edl


def _capture_vision(monkeypatch, tmp):
    seen = {}
    monkeypatch.setattr(llm, "vision_available", lambda plan=None: True)
    monkeypatch.setattr(agent_tools.storage, "download_to",
                        lambda key, local: open(local, "wb").write(b"jpg"))

    def fake_ask(prompt, image_paths, **kw):
        seen["prompt"], seen["images"] = prompt, list(image_paths)
        return "tile 1 LANDED. all landed"
    monkeypatch.setattr(llm, "ask_vision", fake_ask)
    return seen


def test_self_check_reviews_the_claims_before_the_sheet(monkeypatch, tmp_path):
    seen = _capture_vision(monkeypatch, tmp_path)
    out = agent_tools._self_check(
        _Ctx(tmp_path), {"sheet_key": "s.jpg", "verify_sheet_key": "v.jpg"},
        plan=[(2.75, 'the text "INTO A VIDEO EDITOR" reads EXACTLY that')])
    assert "all landed" in out
    assert len(seen["images"]) == 2
    assert "Tile 1 (2.8s)" in seen["prompt"] or "Tile 1 (2.7s)" in \
        seen["prompt"] or "Tile 1 (2.75" in seen["prompt"]
    assert "INTO A VIDEO EDITOR" in seen["prompt"]
    assert "IMAGE 2" in seen["prompt"] and "IMAGE 1" in seen["prompt"]
    assert "all landed" in seen["prompt"]


def test_self_check_degrades_to_sheet_only_on_a_stale_executor(monkeypatch,
                                                               tmp_path):
    """A stale executor returns no verify_sheet_key: the check must be the
    round-52 one, unchanged — never a crash, never a claim about frames
    nobody pulled."""
    seen = _capture_vision(monkeypatch, tmp_path)
    agent_tools._self_check(
        _Ctx(tmp_path), {"sheet_key": "s.jpg"},
        plan=[(1.0, "a claim the render job never saw")])
    assert len(seen["images"]) == 1
    assert "never saw" not in seen["prompt"]
    assert "This is a 3x3" in seen["prompt"]
    assert "looks clean" in seen["prompt"]


# ── the baseline: diff against what the user last SAW ────────────────────

class _DB:
    def __init__(self, latest_v, prev_json):
        self.latest_v, self.prev_json = latest_v, prev_json

    def run(self, fn, *a):
        name = getattr(fn, "__name__", "")
        if name == "latest_render_version":
            return self.latest_v
        if name == "get_edl_version":
            return {"version": a[-1], "json": self.prev_json}
        raise AssertionError(f"unexpected db call {name}")


def test_verify_plan_for_uses_the_last_previewed_version():
    prev = _with(texts=[])
    new_row = {"version": 7,
               "json": _with(texts=[{"id": "tx1", "text": "NEW",
                                     "start": 1.0, "end": 2.0}])}
    ctx = _Ctx("/tmp", db=_DB(5, prev))
    plan = agent_tools._verify_plan_for(ctx, new_row)
    assert len(plan) == 1 and "NEW" in plan[0][1]


def test_verify_plan_for_is_empty_on_the_first_ever_render():
    ctx = _Ctx("/tmp", db=_DB(None, None))
    assert agent_tools._verify_plan_for(ctx, {"version": 1,
                                              "json": dict(BASE)}) == []


def test_verify_plan_for_is_empty_when_nothing_new_renders():
    ctx = _Ctx("/tmp", db=_DB(7, dict(BASE)))
    assert agent_tools._verify_plan_for(ctx, {"version": 7,
                                              "json": dict(BASE)}) == []
