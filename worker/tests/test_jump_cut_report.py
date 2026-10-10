"""The JUMP-CUT REPORT (judges, round 4, Oct 2026; worker/jump_cut_report.py).

The judges flagged bare same-shot jump cuts (Thiel 7.22, 10.72, 19.18,
20.52; Elon 0.8 inside the hook; Jobs 8.02, 12.49) and 7.4% steps that
stutter — and proposed, again, an automatic zoom ladder at every jump cut.
The owner forbids it: zooms are optional, never rules. So the engine lists
every same-shot join with how visible it is, what covers it and the
editor's options, flags a cut in the hook and a step under ~10%, and NEVER
writes anything itself.

Run:  python -m pytest tests/test_jump_cut_report.py -q     (from worker/)
"""
import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools  # noqa: E402
import cut_steps  # noqa: E402
import jump_cut_report as jcr  # noqa: E402
import taste  # noqa: E402
from schemas import validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

FPS = 30.0
ONE_SHOT = [{"id": 1, "start": 0.0, "end": 60.0}]


def _index(**kw):
    idx = {"video": {"fps": FPS, "width": 1920, "height": 1080},
           "words": [], "shots": ONE_SHOT}
    idx.update(kw)
    return idx


def _edl(**kw):
    e = {"keep": [[0, 1], [1.5, 4], [5, 8], [9, 12]],   # cuts 1.0, 3.5, 6.5
         "frame": {"ratio": "9:16", "mode": "crop"}}
    e.update(kw)
    return validate_edl(e, 60.0).model_dump(exclude_none=True)


def _rows(edl, index=None, **kw):
    return jcr.report(edl, index or _index(), **kw)["rows"]


def _face(cx, w=0.1, top=0.2, h=0.2):
    return [cx - w / 2, top, cx + w / 2, top + h]


# ── what the report lists ────────────────────────────────────────────────

def test_every_same_shot_join_is_listed_and_camera_changes_are_not():
    rows = _rows(_edl())
    assert [r["t"] for r in rows] == [1.0, 3.5, 6.5]
    assert [r["src"] for r in rows] == [(1.0, 1.5), (4.0, 5.0), (8.0, 9.0)]
    assert all(not r["covered"] and r["visibility"] == "unmeasured" for r in rows)
    two = _index(shots=[{"id": 1, "start": 0.0, "end": 4.5},
                        {"id": 2, "start": 4.5, "end": 60.0}])
    rep = jcr.report(_edl(), two)
    assert [r["t"] for r in rep["rows"]] == [1.0, 6.5]
    assert rep["camera_changes"] == 1
    # a source-continuous split is no cut at all
    rep = jcr.report(_edl(keep=[[0, 4], [4, 8], [9, 12]]), _index())
    assert rep["contiguous"] == 1 and [r["t"] for r in rep["rows"]] == [8.0]


def test_a_cut_inside_the_hook_is_flagged():
    rows = _rows(_edl())
    assert "hook" in rows[0]["flags"] and rows[0]["t"] < jcr.HOOK_S
    assert all("hook" not in r["flags"] for r in rows[1:])
    line = jcr.advisory_line(jcr.report(_edl(), _index()))
    assert "a jump cut at 1s sits inside the hook's first 1.5s" in line


def test_a_step_under_ten_percent_is_a_stutter_and_from_ten_it_covers():
    def zoom(strength):
        return {"id": "cs1", "start": 3.5, "end": 6.5, "strength": strength,
                "ramp_s": 0.0, "cut_step": True}
    small = _rows(_edl(effects={"zooms": [zoom(0.074)]}))
    for r in small[1:]:
        assert "stutter" in r["flags"] and not r["covered"], r
        assert r["options"][0].startswith("the 7% step from zoom cs1 reads as "
                                          "a stutter: remove it")
    line = jcr.advisory_line(jcr.report(_edl(effects={"zooms": [zoom(0.074)]}),
                                        _index()))
    assert "the framing steps only 7% at 3.5s, 6.5s" in line
    big = _rows(_edl(effects={"zooms": [zoom(0.12)]}))
    assert all(r["covered"] and "stutter" not in r["flags"] for r in big[1:])
    assert "a 12% framing step" in big[1]["covers"]
    # the same rule on a picture card's cut step (the judged Jobs card)
    card = {"id": "c", "start": 0, "end": 9.5, "box": [0.06, 0.3, 0.94, 0.68],
            "source": [0.19, 0.077, 0.955, 0.85], "entrance": "none", "exit": "none",
            "cut_steps": [{"t0": 5.0, "t1": 8.0, "scale": 0.926}]}
    rows = _rows(_edl(effects={"picture_cards": [card]}),
                 _index(video={"fps": FPS, "width": 646, "height": 480}))
    stepped = [r for r in rows if r["t"] in (3.5, 6.5)]
    assert all("stutter" in r["flags"] and r["step_of"] == "the card's cut step"
               for r in stepped)


def test_a_graphic_change_on_the_cut_covers_it_and_one_near_it_is_an_option():
    motion = [{"id": "g", "template": "word_slam", "start": 3.48, "end": 5.0,
               "params": {"text": "G"}},
              {"id": "h", "template": "word_slam", "start": 6.62, "end": 8.0,
               "params": {"text": "H"}}]
    edl = dict(_edl(), motion=motion)
    rows = {r["t"]: r for r in _rows(edl)}
    assert rows[3.5]["covered"] and "graphic 'g' (word_slam) enters" in rows[3.5]["covers"]
    assert not rows[6.5]["covered"]
    near = rows[6.5]["near"][0]
    assert (near["id"], near["how"], near["delta"], near["movable"]) == \
        ("h", "enters", 0.12, True)
    assert any(o.startswith("move graphic 'h' (word_slam)'s start from 6.62s onto "
                            "the cut (set_motion_graphic start=6.50)")
               for o in rows[6.5]["options"])
    line = jcr.advisory_line(jcr.report(edl, _index()))
    assert "graphic 'h' (word_slam) enters 0.12s after the 6.5s cut" in line


def test_the_report_offers_only_the_moves_the_write_keeps():
    # one design with the motion write's cut snap (motion_tools._snap_to_cuts):
    # an entrance is cued to its word, so the report never offers moving it
    # further than the write's own snap would; a number's entrance stays on
    # its spoken number; an exit may move the whole NEAR_S
    import motion_tools
    assert jcr.ENTRANCE_MOVE_S == motion_tools.SNAP_CUT_S
    assert jcr.MIN_ITEM_S == motion_tools.SNAP_MIN_ITEM_S
    far = [{"id": "h", "template": "word_slam", "start": 6.75, "end": 8.0,
            "params": {"text": "H"}}]
    rep = jcr.report(dict(_edl(), motion=far), _index())
    r = {r["t"]: r for r in rep["rows"]}[6.5]
    assert r["near"] and r["near"][0]["movable"] is False
    assert "cued to its word" in r["near"][0]["why_not"]
    assert not any(o.startswith("move ") for o in r["options"])
    assert "moving that change onto the cut" not in jcr.advisory_line(rep)
    num = [{"id": "n", "template": "word_slam", "start": 6.6, "end": 8.0,
            "params": {"text": "*40%*"}}]
    r = {r["t"]: r for r in _rows(dict(_edl(), motion=num))}[6.5]
    assert r["near"][0]["movable"] is False and "spoken number" in r["near"][0]["why_not"]
    # an exit 0.3 s before a cut may move onto it
    ex = [{"id": "x", "template": "word_slam", "start": 5.2, "end": 6.2,
           "params": {"text": "X"}}]
    r = {r["t"]: r for r in _rows(dict(_edl(), motion=ex))}[6.5]
    assert r["near"][0]["how"] == "leaves" and r["near"][0]["movable"] is True
    assert any(o.startswith("move graphic 'x' (word_slam)'s end from 6.20s onto the cut")
               for o in r["options"])
    # a picture card entering or leaving on the cut is a layout change
    card = {"id": "c", "start": 3.5, "end": 9.5, "box": [0.06, 0.3, 0.94, 0.68]}
    rows = {r["t"]: r for r in _rows(_edl(effects={"picture_cards": [card]}))}
    assert "picture card 'c' enters" in rows[3.5]["covers"]


def test_a_caption_change_on_the_cut_softens_but_does_not_cover():
    edl = dict(_edl(), captions=[{"start": 2.0, "end": 3.5, "text": "a"},
                                 {"start": 3.5, "end": 5.0, "text": "b"}])
    r = {r["t"]: r for r in _rows(edl)}[3.5]
    assert not r["covered"] and r["softens"] == ["a caption block changes on the cut"]
    # write time skips the caption plan
    r = {r["t"]: r for r in _rows(edl, captions=False)}[3.5]
    assert r["softens"] == []


def test_visibility_comes_from_the_head_and_the_picture():
    def measure(src_t):
        return [_face(0.3 if src_t < 8.5 else 0.5)]        # jumps at 6.5 only
    rows = {r["t"]: r for r in _rows(_edl(), measure=measure)}
    assert rows[6.5]["visibility"] == "visible" and rows[6.5]["evidence"] == "frames"
    assert rows[3.5]["visibility"] == "steady"

    def pop(e0, s1, t):
        return {"cut": 0.05, "natural": 0.005, "ratio": 10.0} if t == 3.5 else \
            {"cut": 0.004, "natural": 0.005, "ratio": 0.8}
    rows = {r["t"]: r for r in _rows(_edl(), pop=pop)}
    assert rows[3.5]["visibility"] == "visible"
    assert rows[6.5]["visibility"] == "steady"
    line = jcr.advisory_line(jcr.report(_edl(), _index(), pop=pop))
    assert "1 bare jump cut where the speaker visibly jumps: 3.5s" in line
    # the index's face samples answer when no frame can be decoded
    idx = _index(spatial={"samples": [{"t": 3.9, "faces": [_face(0.3)]},
                                      {"t": 5.1, "faces": [_face(0.6)]}]})
    r = {r["t"]: r for r in _rows(_edl(), idx)}[3.5]
    assert r["visibility"] == "visible" and r["evidence"] == "index"


def test_options_are_restrained_and_never_a_default_zoom():
    idx = _index(words=[{"word": "um", "start": 1.1, "end": 1.3}])

    def pop(e0, s1, t):
        return {"cut": 0.05, "natural": 0.005, "ratio": 10.0}
    rows = {r["t"]: r for r in _rows(_edl(), idx, pop=pop)}
    hook = rows[1.0]
    assert hook["options"][0] == "leave it (a bare jump cut is fine)"
    assert any("restore the 0.50s removed between the takes (source 1.00-1.50, "
               "restore_range): one continuous take, +0.50s — it brings back 'um'"
               in o for o in hook["options"])
    assert any("optional and rare" in o and "conceal_jump_cuts at=[1]" in o
               for o in hook["options"])
    # a long removed stretch is re-cut, not restored
    assert any("re-cut the join" in o for o in rows[3.5]["options"])
    # a bare, steady cut outside the hook: no step is offered at all
    steady = {r["t"]: r for r in _rows(_edl())}[3.5]
    assert not any("conceal_jump_cuts" in o for o in steady["options"])
    assert jcr.advisory_line(jcr.report(_edl(keep=[[0, 4], [5, 8]]), _index())) == ""


def test_the_report_never_writes_and_the_tool_reports_first():
    class Ctx:
        project_id = 1
        has_main_video = True
        workdir = "/nonexistent"

        def __init__(self):
            self.index = _index()
            self._edl = _edl()
            self.writes = []

        def latest_edl(self):
            return {"version": 1, "json": json.loads(json.dumps(self._edl))}

        def write_edl(self, edl, desc):
            self.writes.append(desc)
            return "EDL v1 -> v2"

        def proxy_path(self):
            return None
    ctx = Ctx()
    out = cut_steps.conceal_jump_cuts(ctx, mode="report")
    assert out.startswith("JUMP-CUT REPORT (advisory: it lists, it never adds a zoom")
    assert "3 same-shot jump cut(s)" in out and "Nothing was written." in out
    assert "  1.00s  src 1.00->1.50" in out and "inside the hook's first 1.5s" in out
    assert not ctx.writes
    # the module has no write path at all
    src = inspect.getsource(jcr)
    assert "write_edl(" not in src and "cut_step=True" not in src
    assert "mode='report'" in agent_tools._COMPACT_CONTRACTS["conceal_jump_cuts"]
    assert agent_tools.TOOLS["conceal_jump_cuts"][1].startswith(
        "OPTIONAL cut hygiene — never a default. mode='report' FIRST")


def test_steps_are_offered_at_twelve_percent_and_the_tool_agrees():
    assert jcr.STEP_OPTION == cut_steps.STEP_DEFAULT == 0.12
    assert jcr.STUTTER_STEP == cut_steps.STEP_MIN == taste.CUT_STEP_MIN
    assert taste.JUMP_CUT_MIN_SCALE == jcr.STUTTER_STEP


# ── where agents see it ──────────────────────────────────────────────────

def test_the_critic_carries_the_report_and_honours_no_zooms():
    edl = _edl()
    tl = Timeline(edl["keep"])
    found = taste.critique(edl, _index(), tl)
    line = next(f for f in found if f.startswith("jump cuts:"))
    assert "inside the hook" in line and "conceal_jump_cuts(mode='report')" in line
    assert "never a rule" in line and "add_zoom" not in line
    quiet = taste.critique(edl, _index(), tl, user_asked="no zooms please")
    assert not any(f.startswith("jump cuts:") for f in quiet)
    src = inspect.getsource(agent_tools.render_preview)
    assert "pop=jump_cut_report.pop_measure(ctx, edl)" in src
    import finishing_review
    assert "pop=jump_cut_report.cached_pop(ctx)" in inspect.getsource(
        finishing_review.advisory_findings)


def test_a_write_notes_only_what_it_introduced():
    base = _edl(keep=[[0, 4], [5, 8]])
    assert jcr.write_note(base, base, _index()) == ""
    hooked = _edl(keep=[[0, 1], [1.5, 4], [5, 8]])
    note = jcr.write_note(base, hooked, _index())
    assert note.startswith("JUMP-CUT NOTE (advisory): a jump cut at 1s now sits "
                           "inside the hook's first 1.5s")
    assert "conceal_jump_cuts(mode='report')" in note
    # the same cut written again is not news
    assert jcr.write_note(hooked, dict(hooked, captions=None), _index()) == ""
    stepped = dict(base, effects={"zooms": [{"id": "cs1", "start": 4.0, "end": 7.0,
                                             "strength": 0.07, "ramp_s": 0.0,
                                             "cut_step": True}]})
    stepped = validate_edl(stepped, 60.0).model_dump(exclude_none=True)
    note = jcr.write_note(base, stepped, _index())
    assert "a framing step under ~10% on the jump cut at 4s (7%)" in note
    assert "write_note(prev[\"json\"], normalized" in inspect.getsource(
        agent_tools.ToolContext.write_edl)


def test_the_tool_output_fits_the_real_showcase_shape():
    # Thiel's shape: six joins of one shot, a punch on 24.1-26.9 and a slam
    # leaving on 26.9 — the two covered, the rest bare
    keep = [[845.78, 853.0], [853.33, 856.83], [857.11, 865.57], [865.86, 867.2],
            [867.71, 871.29], [871.59, 874.39], [874.62, 880.56]]
    edl = validate_edl({"keep": keep, "frame": {"ratio": "9:16", "mode": "crop"},
                        "effects": {"zooms": [{"id": "z5", "start": 24.1,
                                               "end": 26.9, "strength": 0.12}]},
                        "motion": [{"id": "enough", "template": "word_slam",
                                    "start": 25.25, "end": 26.9,
                                    "params": {"text": "ENOUGH"}}]},
                       3000.0).model_dump(exclude_none=True)
    rep = jcr.report(edl, _index(shots=[{"id": 1, "start": 0.0, "end": 3000.0}]))
    assert [r["t"] for r in rep["rows"]] == [7.22, 10.72, 19.18, 20.52, 24.1, 26.9]
    assert [r["covered"] for r in rep["rows"]] == [False] * 4 + [True, True]
    text = jcr.format_report(rep)
    assert "6 same-shot jump cut(s) in 32.84s: 2 covered, 4 bare." in text
    assert text.count("options:") == 4


# ── review fixes ─────────────────────────────────────────────────────────

def test_the_critic_decodes_only_bare_joins_and_bare_joins_go_first():
    # 3.5 is covered by a graphic entering on it; 1.0 and 6.5 are bare
    edl = dict(_edl(), motion=[{"id": "g", "template": "word_slam",
                                "start": 3.5, "end": 5.0,
                                "params": {"text": "G"}}])
    seen = []

    def measure(src_t):
        seen.append(round(src_t, 2))
        return None

    def pop(e0, s1, t):
        seen.append(("pop", t))
        return None
    jcr.report(edl, _index(), measure=measure, pop=pop, bare_only=True)
    assert ("pop", 3.5) not in seen and 3.98 not in seen      # covered: no decode
    assert ("pop", 1.0) in seen and ("pop", 6.5) in seen
    # the full report measures every join, bare ones first
    seen.clear()
    jcr.report(edl, _index(), pop=pop)
    assert [s for s in seen] == [("pop", 6.5), ("pop", 1.0), ("pop", 3.5)]
    # and the critic asks for the bare-only report
    assert "bare_only=True" in inspect.getsource(taste.critique)


def test_a_moved_stutter_step_is_not_news_at_write_time():
    # cuts at 2, 3 and 6; a 7% step held over the 3-6 block
    step = {"id": "cs1", "start": 3.0, "end": 6.0, "strength": 0.07,
            "ramp_s": 0.0, "cut_step": True}
    before = _edl(keep=[[0, 2], [3, 4], [5, 8], [9, 12]],
                  effects={"zooms": [step]})
    assert [r["t"] for r in _rows(before) if "stutter" in r["flags"]] == [3.0, 6.0]
    # restoring the 2-3 s gap moves the stepped joins (and the step) by 1 s
    after = _edl(keep=[[0, 4], [5, 8], [9, 12]],
                 effects={"zooms": [dict(step, start=4.0, end=7.0)]})
    assert [r["t"] for r in _rows(after) if "stutter" in r["flags"]] == [4.0, 7.0]
    assert jcr.write_note(before, after, _index()) == ""
    # a step on a join that had none is still news
    later = _edl(keep=[[0, 4], [5, 8], [9, 12]],
                 effects={"zooms": [dict(step, start=7.0, end=10.0)]})
    assert "(7%) reads as a stutter" in jcr.write_note(
        _edl(keep=[[0, 4], [5, 8], [9, 12]]), later, _index())


def test_a_card_step_names_what_made_it():
    box = [0.06, 0.3, 0.94, 0.68]
    framed = {"id": "c", "start": 0, "end": 9.5, "box": box,
              "source": [0.2, 0.1, 0.9, 0.85], "entrance": "none",
              "exit": "none", "source_track": [
                  {"t0": 0.0, "t1": 4.5, "source": [0.2, 0.1, 0.9, 0.85]},
                  {"t0": 4.5, "t1": 60.0, "source": [0.21, 0.11, 0.86, 0.81]}]}
    idx = _index(video={"fps": FPS, "width": 646, "height": 480})
    r = {r["t"]: r for r in _rows(_edl(effects={"picture_cards": [framed]}),
                                  idx)}[3.5]
    assert "stutter" in r["flags"] and r["step_of"] == "the card's framing"
