"""The Valmera defects the Elon x Nikhil Kamath podcast run (Oct 16-22,
bf-kamath-Rni7Fz7208c-20261016) recorded, each pinned to its evidence."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timeline import Timeline, remap_program_items      # noqa: E402


def _card(cid, a, b, src=True):
    row = {"id": cid, "start": a, "end": b, "box": [0, 0, 1, 1],
           "fit": "crop", "entrance": "none", "exit": "none"}
    if src:
        row["source"] = [0.3, 0.0, 0.62, 1.0]
    return row


def _br1_edl(keep):
    # br1 3801 v30: six source cards, a payoff phrase_build held over a
    # closing still spliced at the end
    return {"keep": [list(k) for k in keep],
            "inserts": [{"id": "ins1", "kind": "image", "asset_key": "x.png",
                         "duration_s": 1.15,
                         "at_output_s": round(sum(b - a for a, b in keep), 2)}],
            "motion": [{"id": "ts_turn", "template": "typeset",
                        "start": 18.53, "end": 19.38, "params": {}},
                       {"id": "payoff_lock", "template": "phrase_build",
                        "start": 32.8, "end": 36.08, "params": {}}],
            "effects": {"picture_cards": [
                _card("c_a", 0.0, 4.84), _card("c_w1", 4.84, 15.27),
                _card("c_n", 15.27, 15.77), _card("c_d", 15.77, 27.13),
                _card("c_f", 27.13, 30.91), _card("c_g", 30.91, 36.07)]}}


V30 = [[486.21, 491.05], [495.33, 498.0], [499.1, 505.4], [508.54, 510.5],
       [512.44, 515.79], [516.65, 519.67], [520.28, 522.96], [524.47, 526.78],
       [527.64, 529.72], [531.47, 533.17], [539.0, 543.02]]


def _retrim(edl, new_keep):
    old = Timeline(edl["keep"], edl["inserts"], [])
    edl["keep"] = [list(k) for k in new_keep]
    edl["inserts"][0]["at_output_s"] = round(
        sum(b - a for a, b in new_keep), 2)
    new = Timeline(edl["keep"], edl["inserts"], [])
    return remap_program_items(edl, old, new), new


def test_source_cards_and_the_held_payoff_follow_a_trim_before_them():
    edl = _br1_edl(V30)
    v31 = [list(k) for k in V30]
    v31[4][0] = 512.85                 # 0.41 s trimmed off c_d's first shot
    notes, new = _retrim(edl, v31)
    cards = {c["id"]: c for c in edl["effects"]["picture_cards"]}
    assert cards["c_n"]["end"] == pytest.approx(15.77)
    assert cards["c_d"]["start"] == pytest.approx(15.77)
    assert cards["c_d"]["end"] == pytest.approx(26.72, abs=.011)
    assert cards["c_f"]["start"] == pytest.approx(26.72, abs=.011)
    assert cards["c_g"]["start"] == pytest.approx(30.5, abs=.011)
    prog = round(new.out_duration, 2)
    assert cards["c_g"]["end"] == pytest.approx(prog)
    mo = {m["id"]: m for m in edl["motion"]}
    assert mo["ts_turn"]["start"] == pytest.approx(18.12, abs=.011)
    # the payoff starts on its word again and still runs to the end
    assert mo["payoff_lock"]["start"] == pytest.approx(32.39, abs=.011)
    assert mo["payoff_lock"]["end"] == pytest.approx(prog)
    assert any("c_f moved" in n for n in notes)


def test_a_longer_live_tail_carries_the_cards_and_payoff_that_ran_to_the_end():
    keep = [[100.0, 110.0], [120.0, 130.0]]
    edl = {"keep": keep, "inserts": [],
           "motion": [{"id": "payoff", "template": "word_slam",
                       "start": 17.0, "end": 20.0, "params": {}}],
           "effects": {"picture_cards": [_card("c1", 0.0, 10.0),
                                         _card("c2", 10.0, 20.0)]}}
    old = Timeline(keep, [], [])
    edl["keep"] = [[100.0, 110.0], [120.0, 130.9]]     # +0.9 s live tail
    new = Timeline(edl["keep"], [], [])
    remap_program_items(edl, old, new)
    cards = {c["id"]: c for c in edl["effects"]["picture_cards"]}
    assert cards["c2"]["end"] == pytest.approx(20.9)
    assert edl["motion"][0]["end"] == pytest.approx(20.9)
    assert edl["motion"][0]["start"] == pytest.approx(17.0)


def test_a_card_of_the_composed_program_stays_a_span_of_the_edit():
    keep = [[100.0, 110.0], [120.0, 130.0]]
    edl = {"keep": keep, "inserts": [], "effects": {"picture_cards": [
        _card("p", 12.0, 18.0, src=False)]}}
    old = Timeline(keep, [], [])
    edl["keep"] = [[100.0, 108.0], [120.0, 130.0]]
    remap_program_items(edl, old, Timeline(edl["keep"], [], []))
    card = edl["effects"]["picture_cards"][0]
    assert (card["start"], card["end"]) == (12.0, 18.0)


def test_a_card_whose_footage_was_cut_away_is_removed_and_said():
    keep = [[100.0, 110.0], [120.0, 130.0]]
    edl = {"keep": keep, "inserts": [], "effects": {"picture_cards": [
        _card("a", 0.0, 10.0), _card("b", 10.0, 20.0)]}}
    old = Timeline(keep, [], [])
    edl["keep"] = [[120.0, 130.0]]
    notes = remap_program_items(edl, old, Timeline(edl["keep"], [], []))
    cards = edl["effects"]["picture_cards"]
    assert [c["id"] for c in cards] == ["b"]
    assert (cards[0]["start"], cards[0]["end"]) == (0.0, 10.0)
    assert any("picture card a was removed" in n for n in notes)


# ── set_picture_card: a re-time keeps the card the editor built ──────────

import agent_tools                                       # noqa: E402
from schemas import default_edl, validate_edl            # noqa: E402


class _CardCtx:
    index = {"video": {"width": 1920, "height": 1080, "fps": 30.0},
             "words": [], "sentences": []}
    edit_plan = None
    has_main_video = True

    def __init__(self, duration=12.0):
        self.duration = duration
        self.edl = validate_edl(default_edl(duration), duration).model_dump()
        self.n = 1

    def latest_edl(self):
        return {"version": self.n, "json": self.edl}

    def write_edl(self, edl, desc):
        new = validate_edl(edl, self.duration).model_dump()
        if new == self.edl:
            return f"NO CHANGE — the EDL is identical to v{self.n}"
        self.edl, self.n = new, self.n + 1
        return f"EDL v{self.n}: {desc}"

    def card(self, cid="c_wide"):
        return next(c for c in self.edl["effects"]["picture_cards"]
                    if c["id"] == cid)


def test_a_retime_with_only_id_start_end_keeps_rect_motion_and_canvas():
    ctx = _CardCtx()
    first = agent_tools.set_picture_card(
        ctx, "c_wide", 1.0, 8.0, box=[0, 0, 1, 1], fit="crop",
        source=[0.31, 0.0, 0.626, 0.9987], entrance="none", exit="none",
        follow=False, vignette=.35, background="#24221F", radius=0)
    assert first.startswith("EDL v"), first
    built = ctx.card()
    res = agent_tools.set_picture_card(ctx, "c_wide", 1.0, 9.5)
    assert res.startswith("EDL v") and "KEPT from the current card" in res, res
    card = ctx.card()
    assert card["end"] == 9.5
    for key in ("box", "fit", "source", "entrance", "exit", "radius",
                "vignette", "background"):
        assert card[key] == built[key], key
    # naming one motion key changes only it; naming a colour alone is a flat
    # canvas again, as before
    agent_tools.set_picture_card(ctx, "c_wide", 1.0, 9.5, entrance="fade")
    assert ctx.card()["entrance"] == "fade" and ctx.card()["exit"] == "none"
    assert ctx.card()["source"] == built["source"]
    # nulls a client sends for every optional field count as left out
    agent_tools.set_picture_card(ctx, "c_wide", 1.0, 9.0, box=None,
                                 source=None, entrance=None)
    assert ctx.card()["source"] == built["source"]
    assert ctx.card()["entrance"] == "fade"


def test_a_new_card_still_takes_the_defaults():
    ctx = _CardCtx()
    agent_tools.set_picture_card(ctx, "n", 1.0, 4.0, source="full")
    card = ctx.card("n")
    assert card["entrance"] == "lift" and card["exit"] == "fade"


# ── picture check: what the native frames showed ─────────────────────────

import render_qc                                         # noqa: E402

CARD = [0.0, 0.135, 1.0, 0.8]


def _plan(cards):
    return {"cards": cards, "W": 1080, "H": 1920, "fps": 30.0}


def test_a_shoulder_at_the_card_edge_beside_the_whole_face_is_not_a_cut_face():
    face = [0.38, 0.25, 0.62, 0.40]           # the speaker, centred
    shoulder = [-0.08, 0.30, 0.14, 0.45]      # the far shoulder, past the edge
    samples = [(t / 2.0, [(face, 0), (shoulder, -1)]) for t in range(8)]
    plan_ = _plan([(0.0, 10.0, [CARD])])
    assert render_qc.cut_faces(samples, plan_) == []
    assert render_qc.clipped_faces(samples, plan_) == []
    # the speaker's own face past the edge is still a cut face
    alone = [(t / 2.0, [(shoulder, -1)]) for t in range(8)]
    assert render_qc.cut_faces(alone, plan_)
    # and two people who both sit inside the card are both judged
    two = [(t / 2.0, [([0.20, 0.25, 0.40, 0.40], 0),
                      ([0.62, 0.25, 0.99, 0.40], 0)]) for t in range(8)]
    assert render_qc.clipped_faces(two, plan_)


def _type_plan(items):
    return {"items": items, "W": 1080, "H": 1920, "fps": 30.0,
            "hook": None, "payoff": None, "band": None}


def _item(iid, template, a, b, layer, box):
    return {"id": iid, "template": template, "start": a, "end": b,
            "layer": layer, "persistent": False, "box": box, "tier": None}


def test_a_full_bleed_still_is_neither_on_the_mark_nor_under_the_captions():
    full = [0.0, 0.0, 1.0, 1.0]
    items = [_item("ov1", "still_motion", 15.4, 17.5, "below_captions", full),
             _item("turn_flash", "flash_transition", 13.39, 13.69,
                   "above_captions", full),
             _item("slam", "word_slam", 20.0, 22.0, "above_captions",
                   [0.5, 0.02, 0.95, 0.12])]
    tres = {"items": {"ov1": {"cap": .4, "box": full},
                      "turn_flash": {"cap": .5, "box": full},
                      "slam": {"cap": .06, "box": [0.5, 0.02, 0.95, 0.12]}},
            "cues": [{"t": 16.0, "cap": .035, "box": [0.2, 0.7, 0.8, 0.76],
                      "text": "But but with a"},
                     {"t": 13.6, "cap": .035, "box": [0.4, 0.7, 0.6, 0.76],
                      "text": "As"}]}
    got = render_qc.type_findings(tres, _type_plan(items), [])
    assert got["collide"] == []
    assert [m[0] for m in got["mark"]] == ["slam"]       # type on the mark
    assert "ov1" not in dict(got["caps"]["lockups"])
    # a still drawn ABOVE the captions hides them: still a collision
    items[0]["layer"] = "above_captions"
    got = render_qc.type_findings(tres, _type_plan(items), [])
    assert [c[2] for c in got["collide"]] == ["ov1"]


def test_an_opening_blink_whose_clean_start_drops_the_hook_words_is_advice():
    res = {"hook_eyes": [0, 7, 9], "hook_fps": 25.0,
           "hook_advice": {"start": 4124.44, "open": 4124.43, "word": "you",
                           "drops": ["Maybe"]}}
    plan_ = {"fps": 25.0}
    assert not any("CLOSED EYES" in f for f in render_qc.findings(res, plan_))
    adv = render_qc.advice(res)
    assert any("BLINK" in a and "Maybe" in a and "0.00-0.28s" in a
               for a in adv)
    # a clean start that costs nothing stays a finding
    res["hook_advice"]["drops"] = []
    assert any("CLOSED EYES" in f for f in render_qc.findings(res, plan_))
    assert not any("BLINK" in a for a in render_qc.advice(res))


# ── critique_short: ops that would have spoiled correct timing ───────────

import numpy as np                                       # noqa: E402
import motion_critic                                     # noqa: E402

_WORDS = [{"w": "the", "t0": 10.0, "t1": 10.2},
          {"w": "voice", "t0": 10.22, "t1": 10.6},
          {"w": "of", "t0": 10.62, "t1": 10.7},
          {"w": "your", "t0": 10.72, "t1": 10.9},
          {"w": "thoughts.", "t0": 10.92, "t1": 11.6}]


def _db(monkeypatch, level_at):
    """audio_rms of a render: 10 ms frames whose level level_at(t) gives."""
    n = 1300
    db = np.array([level_at(i * .01) for i in range(n)], np.float32)
    monkeypatch.setattr(motion_critic, "audio_rms", lambda p: (db, .01))


def test_an_ending_in_the_pause_after_the_last_word_is_clean(monkeypatch):
    # ASR says 'thoughts.' runs to 11.6; the sound stopped at 11.3 and the
    # editor ended at 11.4 (kd 3805): no fragment, no op cutting the payoff
    _db(monkeypatch, lambda t: -20.0 if t < 1.3 else -60.0)
    edl = {"keep": [[10.0, 11.4]]}
    got = motion_critic.lens_words(edl, {"words": _WORDS}, {"words": []},
                                   1.4, path="render.mp4")
    assert "ends_mid_word" not in {f["code"] for f in got}
    # cut while the word still sounds: finish it, never drop the payoff
    _db(monkeypatch, lambda t: -20.0)
    got = motion_critic.lens_words(edl, {"words": _WORDS}, {"words": []},
                                   1.4, path="render.mp4")
    f = next(f for f in got if f["code"] == "ends_mid_word")
    assert f["op"]["value"] == [[10.0, 11.64]] and "finish" in f["fix"]


def test_a_late_landing_never_asks_for_a_start_before_zero(monkeypatch):
    edl = {"keep": [[4124.15, 4130.0]],
           "motion": [{"id": "hook_slam", "template": "word_slam",
                       "start": 0.0, "end": 2.24,
                       "params": {"text": "maybe you won't regret it"}}]}
    plan = {"words": [{"w": "Maybe", "t0": 0.05, "t1": 0.3}]}
    monkeypatch.setattr(motion_critic, "_item_box", lambda m, w, h: [0, 0, 1, 1])
    monkeypatch.setattr(motion_critic, "visual_onset", lambda *a, **k: 0.69)
    got = motion_critic.lens_late_words("r.mp4", {"fps": 30.0}, edl, plan)
    assert got and got[0]["code"] == "late_word"
    assert "op" not in got[0] and "cannot move earlier" in got[0]["fix"]


def test_a_box_that_changes_on_a_picture_cut_is_not_a_late_landing(monkeypatch):
    edl = {"keep": [[100.0, 110.0], [120.0, 130.0]],
           "motion": [{"id": "rare_slam", "template": "word_slam",
                       "start": 9.8, "end": 11.0, "params": {"text": "rare"}}]}
    plan = {"words": [{"w": "rare", "t0": 9.85, "t1": 10.2}]}
    monkeypatch.setattr(motion_critic, "_item_box", lambda m, w, h: [0, 0, 1, 1])
    monkeypatch.setattr(motion_critic, "visual_onset", lambda *a, **k: 10.0)
    assert motion_critic.lens_late_words("r.mp4", {"fps": 30.0}, edl,
                                         plan) == []
    # off the cut it is still late, keyed to the saying nearest the start
    plan = {"words": [{"w": "rare", "t0": 8.7, "t1": 9.0},
                      {"w": "rare", "t0": 9.85, "t1": 10.2}]}
    monkeypatch.setattr(motion_critic, "visual_onset", lambda *a, **k: 10.3)
    got = motion_critic.lens_late_words("r.mp4", {"fps": 30.0}, edl, plan)
    assert got and "said at 9.85s" in got[0]["message"]


# ── check_speech: each kept span on the program clock ────────────────────

import shutil                                            # noqa: E402
import subprocess                                        # noqa: E402

from tools_ext import rhythm_tools                       # noqa: E402


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_check_speech_across_a_cut_reads_each_span_on_the_program_clock(
        tmp_path):
    wav = str(tmp_path / "src.wav")
    tones = "+".join(f"between(t,{a},{b})*sin(2*PI*220*t)*0.5"
                     for a, b in ((1.0, 1.5), (3.0, 3.5), (5.5, 6.0)))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='{tones}':s=16000:d=8", wav], check=True)

    class Ctx:
        index = {"words": [{"w": "one", "t0": 1.0, "t1": 1.5},
                           {"w": "cutaway", "t0": 3.0, "t1": 3.5},
                           {"w": "two", "t0": 5.5, "t1": 6.0}]}

        def latest_edl(self):
            return {"version": 3, "json": {"keep": [[0.0, 2.0], [5.0, 7.0]]}}

        def proxy_path(self):
            return wav

    out = rhythm_tools.check_speech(Ctx(), 0.0, 4.0)
    assert "one@1.00" in out and "two@2.50" in out, out
    assert "cutaway" not in out and "cut at 2s" in out
    assert "UNTRANSCRIBED" not in out, out
    # a whole short fits in one call; more than a minute is refused
    assert rhythm_tools.check_speech(Ctx(), 0.0, 61.0).startswith("REJECTED")


# ── argument spellings editors paid a refusal for ────────────────────────

def test_one_to_one_argument_spellings_are_read():
    norm = agent_tools._normalize_tool_call
    assert norm("remove_typeset", {"id": "ts_cant"})[1] == {"ids": ["ts_cant"]}
    assert norm("look_at", {"native": True, "rendered": True})[1] == {
        "native_resolution": True, "rendered": True}
    assert norm("apply_look", {"look": "creator_punch"})[1] == {
        "name": "creator_punch"}
    fixes = norm("set_caption_fixes", {"fixes": '[{"from": "Fuck", "to": '
                                       '"F***", "start": 32.2, "end": 32.5}]'})[1]
    assert fixes == {"replacements": [{"from": "Fuck", "to": "F***",
                                       "start": 32.2, "end": 32.5}]}
    got = norm("justify_verification_findings", {"justifications": (
        '[{"id": "vf_b79576df", "reason": "captions over a full-bleed still"},'
        ' {"id": "vf_37e5ff28", "reason": "the still under the mark"}]')})[1]
    assert got["finding_ids"] == ["vf_b79576df", "vf_37e5ff28"]
    assert "vf_b79576df: captions over a full-bleed still" in \
        got["justification"]
    # a real argument is never overwritten by its alias
    assert norm("apply_look", {"look": "x", "name": "y"})[1]["name"] == "y"


# ── a capture box that misses the graphic renders nothing ────────────────

import edit_batch                                        # noqa: E402


def test_a_capture_box_outside_the_graphic_is_refused_not_drawn_blank():
    item = {"id": "ts_eyes", "template": "typeset",
            "box": [0.06, 0.86, 0.94, 0.97], "params": {"text": "x"},
            "footprint": {"ar": 0.5625,
                          "box": [0.0625, 0.5038, 0.7781, 0.797]}}
    with pytest.raises(ValueError) as e:
        edit_batch._check_capture_box(item)
    assert "capture window" in str(e.value) and "set_typeset(region" in \
        str(e.value)
    item["box"] = [0.0, 0.45, 1.0, 0.85]           # it holds the graphic
    edit_batch._check_capture_box(item)
