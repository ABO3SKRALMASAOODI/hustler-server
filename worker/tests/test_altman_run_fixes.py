"""Altman run (w1023-altman-anAmTGcewX8, 23 Oct 2026): the defects its
editors and reviewers hit, pinned.

1. critique_short's late_word lens named a word-timed typeset's LATER
   emphasis word, a slam's exit cut and a gesture through the box as the
   graphic's landing (54 findings on the 26 delivered finals; 11 of the 54
   checked by eye were on time), and its op moved the end with the start,
   cutting the graphic's last word.
2. payoff_hold_short asked to "extend the keep" where the source's next word
   follows the last one at once (s01, s09, s21, s22, s23).
3. Vector graphics stayed at their program time after a re-cut.
4. list_build rows given as text were refused; a long row was cut short in
   silence.
5. add_overlay reused the id of a still animate_still had moved to the
   motion layer.
"""

import numpy as np
import pytest

import motion_critic
import motion_templates
import timeline


# ── 1. late_word: the landing window ───────────────────────────────────────

def test_a_slam_already_on_screen_before_its_word_is_not_late():
    # 4605 conseq: word_slam 11.95-13.2, 'consequences' said at 12.56
    m = {"id": "conseq", "template": "word_slam", "start": 11.95,
         "end": 13.2, "params": {"text": "*CONSEQUENCES*"}}
    assert motion_critic.landing_window(m, 12.56, 30.0) is None
    # 4601 hook on frame 0, 'tricky' said at 0.55
    hook = {"id": "hook", "template": "word_slam", "start": 0.0, "end": 1.5,
            "params": {"text": "*tricky* stuff"}}
    assert motion_critic.landing_window(hook, 0.55, 30.0) is None


def test_the_window_stops_before_the_exit_and_after_the_entrance():
    m = {"id": "payoff", "template": "word_slam", "start": 32.0,
         "end": 33.1, "params": {"text": "never learned / *MORE*"}}
    lo, hi = motion_critic.landing_window(m, 32.19, 30.0)
    assert lo == pytest.approx(32.0 - 1 / 30.0)
    assert hi <= 32.0 + motion_critic.ENTRANCE_S + 1e-9
    assert hi < 33.1 - 1 / 30.0


def test_a_typesets_later_word_is_not_its_first_words_landing(monkeypatch):
    # 4596 greatlife: 'have a *great life*' reveals each word on its onset
    m = {"id": "greatlife", "template": "typeset", "start": 13.51,
         "end": 14.9, "params": {"text": "have a *great life*"}}
    monkeypatch.setattr(motion_critic, "_reveal_times",
                        lambda _m: [13.51, 13.66, 13.82, 13.98])
    lo, hi = motion_critic.landing_window(m, 13.56, 30.0)
    assert lo == pytest.approx(13.51 - 1 / 30.0)
    assert hi == pytest.approx(13.66 - 1 / 30.0)     # before 'a' draws


def test_a_list_row_is_measured_on_the_reveal_that_draws_it(monkeypatch):
    # 4582 list1: 'NO LEADER' drawn at 24.42 on 'no', 'leader' said 24.74
    m = {"id": "list1", "template": "list_build", "start": 24.42,
         "end": 29.35, "params": {}}
    monkeypatch.setattr(motion_critic, "_reveal_times",
                        lambda _m: [24.42, 25.02, 25.62, 29.1])
    lo, _hi = motion_critic.landing_window(m, 24.74, 30.0)
    assert lo == pytest.approx(24.42 - 1 / 30.0)


def _fake_frames(monkeypatch, events, fps=30.0, n=200, w=270, h=480):
    """Frames whose box pixels step at the given (time, level) events."""
    import qa_metrics

    def read_frames(path, *, size=None, gray=False, frames=None, **_k):
        idx = sorted(frames or range(n))
        out = []
        for i in idx:
            t = i / fps
            level = 0.0
            for at, v in events:
                if t >= at - 1e-9:
                    level = v
            out.append(np.full((h, w), level, np.float32))
        return idx, out
    monkeypatch.setattr(qa_metrics, "read_frames", read_frames)


def test_the_exit_cut_is_never_read_as_the_landing(monkeypatch):
    # a faint entrance at 4.30 (hairline type), a hard exit cut at 5.20
    _fake_frames(monkeypatch, [(4.3, 6.0), (5.2, 0.0)])
    info = {"fps": 30.0, "width": 1080, "height": 1920}
    box = [0.1, 0.5, 0.9, 0.8]
    seen = motion_critic.visual_onset("r.mp4", info, box, 4.34,
                                      lo=4.3 - 1 / 30.0, hi=4.6)
    assert seen == pytest.approx(4.3, abs=0.02)
    # without the window the old reading named the exit
    _fake_frames(monkeypatch, [(4.3, 2.0), (5.2, -40.0)])
    old = motion_critic.visual_onset("r.mp4", info, box, 4.34)
    assert old == pytest.approx(5.2, abs=0.02)


def test_a_truly_late_graphic_is_still_named_and_only_its_start_moves(
        monkeypatch):
    # 4601 ts_bad: 'any regulation is *bad*' drawn at 5.53, 'any' said 5.20
    edl = {"keep": [[0.0, 20.0]],
           "motion": [{"id": "ts_bad", "template": "word_slam",
                       "start": 5.53, "end": 6.72,
                       "params": {"text": "any regulation"}}]}
    plan = {"words": [{"w": "any", "t0": 5.20, "t1": 5.35}]}
    monkeypatch.setattr(motion_critic, "_item_box", lambda m, w, h: [0, 0, 1, 1])
    monkeypatch.setattr(motion_critic, "visual_onset", lambda *a, **k: 5.54)
    got = motion_critic.lens_late_words("r.mp4", {"fps": 30.0}, edl, plan)
    assert got and got[0]["code"] == "late_word"
    value = got[0]["op"]["value"]
    assert value["start"] == pytest.approx(5.53 - 10 / 30.0, abs=0.02)
    assert "end" not in value


def test_a_hook_that_cannot_move_names_entrances_its_template_has(
        monkeypatch):
    edl = {"keep": [[4124.15, 4130.0]],
           "motion": [{"id": "hook_slam", "template": "word_slam",
                       "start": 0.0, "end": 2.24,
                       "params": {"text": "maybe you won't regret it",
                                  "entrance": "rise"}}]}
    plan = {"words": [{"w": "Maybe", "t0": 0.05, "t1": 0.3}]}
    monkeypatch.setattr(motion_critic, "_item_box", lambda m, w, h: [0, 0, 1, 1])
    monkeypatch.setattr(motion_critic, "visual_onset", lambda *a, **k: 0.29)
    got = motion_critic.lens_late_words("r.mp4", {"fps": 30.0}, edl, plan)
    assert got and "'cut'" not in got[0]["fix"]
    assert "'pop'" in got[0]["fix"] or "'slam'" in got[0]["fix"]


def test_a_59_94_fps_final_is_held_to_the_previews_tolerance(monkeypatch):
    edl = {"keep": [[0.0, 20.0]],
           "motion": [{"id": "rollon", "template": "word_slam",
                       "start": 3.40, "end": 4.2, "params": {"text": "roll"}}]}
    plan = {"words": [{"w": "roll", "t0": 3.40, "t1": 3.6}]}
    monkeypatch.setattr(motion_critic, "_item_box", lambda m, w, h: [0, 0, 1, 1])
    # 3 frames at 59.94 fps = 50 ms: under the 2 frames at 30 fps (67 ms)
    monkeypatch.setattr(motion_critic, "visual_onset", lambda *a, **k: 3.45)
    assert motion_critic.lens_late_words(
        "r.mp4", {"fps": 59.94}, edl, plan) == []


def test_stale_readings_are_recomputed_before_measuring(monkeypatch):
    import caption_carry
    edl = {"keep": [[0.0, 10.0]],
           "motion": [{"id": "ts", "template": "typeset", "start": 1.0,
                       "end": 2.0, "params": {"text": "x"},
                       "reading": {"v": 1, "rows": [[0.0]]}}]}
    called = []

    def attach(e, index, tl):
        called.append(e is not edl)
        e["motion"][0]["reading"] = {"v": 1, "rows": [[0.5]]}
        return e
    monkeypatch.setattr(caption_carry, "attach_readings", attach)
    timed = motion_critic._as_rendered(edl, {"words": []})
    assert called == [True]
    assert timed["motion"][0]["reading"]["rows"] == [[0.5]]
    assert edl["motion"][0]["reading"]["rows"] == [[0.0]]   # stored EDL kept


# ── 2. payoff hold: the source's own tail ─────────────────────────────────

_SRC = [{"w": "the", "t0": 1420.0, "t1": 1420.2},
        {"w": "fields.", "t0": 1425.395, "t1": 1426.035},
        {"w": "You", "t0": 1426.435, "t1": 1426.6}]


def _hold_edl(end):
    return {"keep": [[1400.0, end]]}


def test_no_live_tail_in_the_source_is_an_advisory_ending():
    # s01: the next word starts 0.40 s after 'fields.'
    last = {"w": "fields.", "t0": 25.395, "t1": 26.035, "s0": 1425.395,
            "s1": 1426.035}
    f = motion_critic._payoff_hold_finding(_hold_edl(1426.41),
                                           {"words": _SRC}, last, 0.37, 26.41)
    assert f["severity"] == "advisory" and f["code"] == "payoff_hold_short"
    assert "op" not in f and "freeze" in f["fix"]


def test_a_source_pause_gets_the_exact_keep_extension():
    src = _SRC[:2] + [{"w": "You", "t0": 1428.0, "t1": 1428.2}]
    last = {"w": "fields.", "t0": 25.395, "t1": 26.035, "s0": 1425.395,
            "s1": 1426.035}
    f = motion_critic._payoff_hold_finding(_hold_edl(1426.2),
                                           {"words": src}, last, 0.17, 26.2)
    assert f["severity"] == "fix"
    assert f["op"]["value"][-1][1] == pytest.approx(1426.035 + 0.8, abs=0.01)


# ── 3. vectors follow a re-cut ────────────────────────────────────────────

def _vec(vid, start, end):
    return {"id": vid, "kind": "line", "start": start, "end": end}


def test_a_vector_cued_to_words_follows_a_head_trim():
    edl = {"keep": [[100.0, 120.0]], "inserts": [],
           "vectors": [_vec("vec2", 8.0, 9.5), _vec("frame", 0.0, 20.0)]}
    old = timeline.Timeline(edl["keep"], [], [])
    edl["keep"] = [[101.0, 120.0]]
    new = timeline.Timeline(edl["keep"], [], [])
    notes = timeline.remap_program_items(edl, old, new)
    vec = {v["id"]: v for v in edl["vectors"]}
    assert (vec["vec2"]["start"], vec["vec2"]["end"]) == (7.0, 8.5)
    assert any("vector vec2" in n and "with its moment" in n for n in notes)
    # one held over the whole programme is layout: it keeps the programme
    assert vec["frame"]["start"] == 0.0 and vec["frame"]["end"] == 19.0


def test_a_vector_whose_words_were_cut_is_removed():
    edl = {"keep": [[100.0, 105.0], [110.0, 120.0]], "inserts": [],
           "vectors": [_vec("cross", 2.0, 3.0)]}
    old = timeline.Timeline(edl["keep"], [], [])
    edl["keep"] = [[110.0, 120.0]]
    new = timeline.Timeline(edl["keep"], [], [])
    notes = timeline.remap_program_items(edl, old, new)
    assert edl["vectors"] == []
    assert any("vector cross" in n and "removed" in n for n in notes)


# ── 4. rows as text; no silent truncation ─────────────────────────────────

def test_list_rows_given_as_text_are_rows():
    p = motion_templates.check_params(
        "list_build", {"rows": ["NO LEADER", "NO CREDIT SQUABBLES"]})
    assert [r["text"] for r in p["rows"]] == ["NO LEADER",
                                              "NO CREDIT SQUABBLES"]
    with pytest.raises(ValueError, match="rows must be objects"):
        motion_templates.check_params("bar_compare", {"rows": ["GPU"]})


def test_a_long_row_is_refused_not_cut_short():
    with pytest.raises(ValueError, match="42 characters on screen"):
        motion_templates.check_params("list_build", {"rows": [
            {"text": "TO STILL MAKE MISTAKES AND LEARN FROM THEM"}]})
    # an old stored row still renders (lenient path)
    p = motion_templates.normalize_params("list_build", {"rows": [
        {"text": "TO STILL MAKE MISTAKES AND LEARN FROM THEM"}]})
    assert len(p["rows"][0]["text"]) == 32


# ── 5. overlay ids never collide with a moving still ──────────────────────

def test_a_new_overlay_skips_a_moving_stills_id():
    import agent_tools
    overlays = [{"id": "ov2"}]
    motion = [{"id": "ov1", "template": "still_motion"}]
    assert agent_tools._next_item_id(overlays + motion, "ov") == "ov3"


# ── 6. a hook-tier slam with a figure stays on the opening ────────────────

def test_number_landing_never_moves_the_opening_hook():
    import number_reveal
    edl = {"keep": [[1329.34, 1426.38]], "inserts": []}
    index = {"words": [{"w": "the", "t0": 1330.9, "t1": 1331.0},
                       {"w": "last", "t0": 1331.0, "t1": 1331.2},
                       {"w": "10", "t0": 1331.24, "t1": 1331.5},
                       {"w": "days", "t0": 1331.5, "t1": 1331.8}]}
    hook = {"id": "hook", "template": "word_slam", "start": 0.0, "end": 2.6,
            "params": {"tier": "hook", "text": "the last *10 days*"}}
    changed, note = number_reveal.land(edl, index, hook, 97.0)
    assert not changed and (hook["start"], hook["end"]) == (0.0, 2.6)
    assert "stays on frame 0" in note
    # a display slam of the same figure still lands on its word
    beat = dict(hook, id="beat", start=0.6, end=2.6,
                params={"tier": "display", "text": "*10 days*"})
    changed, note = number_reveal.land(edl, index, beat, 97.0)
    assert changed and "NUMBER LANDED" in note and beat["start"] > 1.0
