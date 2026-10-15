"""Wave 2 track 2F: the agent layer v2 — packs, the invention-first story
planner, meaning -> verb, reference transfer, voice variants and the B&W
rule (INTERFACES_W2.md §7, §8)."""

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import w2_support as S                                    # noqa: E402
import agent_tools                                        # noqa: E402
import devices                                            # noqa: E402
import formats                                            # noqa: E402
import meaning_map                                        # noqa: E402
import reference_devices                                  # noqa: E402
import story_planner                                      # noqa: E402
import variants_edit                                      # noqa: E402
from tools_ext import format_tools as FT                  # noqa: E402
from typeset import voices as V                           # noqa: E402


@pytest.fixture(autouse=True)
def _canvas(monkeypatch):
    import motion_tools
    monkeypatch.setattr(motion_tools, "_canvas_size",
                        lambda c, e: (1080, 1920))


def _cards():
    cs = devices.load_cards()
    return set(cs) if isinstance(cs, dict) else {c["id"] for c in cs}


def test_every_pack_is_real_cards_real_voices_and_no_mono_look():
    cards = _cards()
    for p in formats.all_packs():
        assert formats.validate(p, cards, set(V.VOICES)) == [], p["id"]
        assert "whole_short_mono" in p["colour_world"]["never"]


def test_the_users_words_pick_the_pack():
    assert formats.select(None, None, "make it a manifesto")[0]["id"] == "A"
    assert formats.select(None, None, "use pack B")[0]["id"] == "B"
    assert formats.select(None, None, "lyric video please")[0]["id"] == "H"
    p, why = formats.select(None, None, "")
    assert p is None and "no words" in why


def _plan(text_words=None, **kw):
    edl = S.edl(keep=((0.0, 14.0),), **kw.pop("edl_extra", {}))
    idx = S.index()
    if text_words:
        idx = dict(idx, words=text_words)
    return story_planner.suggest(edl, idx, base=[], **kw), edl


def test_the_plan_is_bound_to_beats_and_says_what_is_new():
    cands, _e = _plan()
    assert 1 <= len(cands) <= story_planner.MAX
    for c in cands:
        assert c["beat"] and c["reason"] and c["invention"]
        assert c.get("op") or c.get("call")
    voices = [c["op"]["value"]["params"]["voice"] for c in cands
              if c.get("op") and c["op"]["value"].get("template") ==
              "typeset" and c["op"]["value"]["params"]["voice"] != "auto"]
    assert len(voices) == len(set(voices))          # range, not repeats


def test_two_stories_never_get_the_same_plan():
    a, _ = _plan()
    other = [dict(w, w=x) for w, x in zip(S.words(), (
        "Nobody believed the rocket would land on the barge and then it "
        "did land twice in one night while the whole team watched it "
        "happen live on the stream for the first time ever").split())]
    b, _ = _plan(other)
    sig = lambda cs: [(c["device"], c["beat"], (c.get("op") or {}).get(
        "value", {}).get("params", {}).get("voice")) for c in cs]
    assert sig(a) != sig(b)


def test_bw_is_only_ever_a_device_and_music_only_the_users():
    cands, _ = _plan()
    for c in cands:
        v = (c.get("op") or {}).get("value") or {}
        if v.get("type") == "grade_window":
            assert v["saturation"] == 0.0 and v["end"] > v["start"]
            assert v["end"] - v["start"] < 6.0       # a stretch, not a look
        assert (c.get("call") or {}).get("verb") not in ("shape_music",
                                                         "plan_to_music")
    music = [{"id": "m1", "storage_key": "music/u.mp3", "start": 0.0,
              "end": 14.0, "provenance": "user_upload"}]
    cands, _ = _plan(edl_extra={"music": music})
    assert any((c.get("call") or {}).get("verb") == "shape_music"
               for c in cands)


def test_the_plan_adds_no_sound_zoom_or_music_items():
    """2F-5 restraint: proposals never write sfx, zooms or music."""
    cands, _ = _plan(pack=formats.load("B"))
    for c in cands:
        for op in ([c["op"]] if c.get("op") else []) + (c.get("ops") or []):
            assert op["layer"] not in ("sfx", "music", "effects")


def test_one_or_two_verbs_a_sentence():
    words = [{"w": w, "t0": i * 0.3, "t1": i * 0.3 + 0.25} for i, w in
             enumerate(("We raised 140 million but never spent it. "
                        "Then it worked.").split())]
    plan = meaning_map.plan(words)
    assert all(len(s["verbs"]) <= meaning_map.MAX_PER_SENTENCE
               for s in plan)
    first = plan[0]
    assert "number" in first["meanings"] and "contrast" in first["meanings"]


def test_reference_sequence_and_transfer_keep_classes_in_order():
    sig = {"fps": 25.0, "duration_s": 10.0,
           "negatives": {"runs": [[50, 51]]},
           "white_flashes": {"f": [125]}, "colour_flashes": {"f": []},
           "bw": {"sections_t": [[6.0, 8.0]], "selective_colour": [True]},
           "strobe_sections": [], "type_on_black": {"f": []},
           "cuts": {"t": [0.5, 1.0, 1.4, 1.8, 2.1]}}
    seq = reference_devices.sequence(sig)
    assert reference_devices.classes_in_order(seq) == [
        "fast_cuts", "negative", "white_flash", "bw_keep_colour"]
    plan = {"program_s": 20.0, "moments": [{"t": 4.3, "kind": "stress"}]}
    moves = reference_devices.transfer(seq, plan)
    neg = next(m for m in moves if m["cls"] == "negative")
    assert neg["at"] == 4.3 and neg["moment"] == "stress"
    assert {m["card"] for m in moves} <= _cards()


def test_three_voices_are_distinct_and_draw_on_the_frame():
    vs = variants_edit.distinct_voices("bigger than the internet", 3)
    assert len(set(vs)) == 3
    edl = S.edl(motion=[{"id": "ts1", "template": "typeset", "start": 4.0,
                         "end": 6.0, "params": {"text": "bigger than the "
                                                "*internet*",
                                                "voice": "magazine"}}])
    br = variants_edit.branches(edl, n=3, item_id="ts1")
    assert [v for v, _e in br] == vs or len({v for v, _e in br}) == 3
    from typeset import layoutdoc
    frame = np.zeros((1920, 1080, 3), np.uint8)
    it = br[0][1]["motion"][0]
    doc = layoutdoc.solve_item(it, 1080, 1920)
    img = variants_edit.raster(doc, frame)
    assert img.shape == (1920, 1080, 3) and img.max() > 100


def test_a_whole_short_bw_look_needs_the_users_ask(monkeypatch):
    class Ctx(S.Ctx):
        job = {"type": "agent_turn"}
    ctx = Ctx(S.edl())
    ctx.user_message = "make this a premium short"
    out = agent_tools.apply_look(ctx, "mono_noir")
    assert out.startswith("REJECTED") and "device" in out
    import creative
    assert creative.mono_allowed("make it black and white")
    assert not creative.mono_allowed("make it moody")


def test_suggest_then_apply_writes_one_batch(monkeypatch):
    ctx = S.Ctx(S.edl(keep=((0.0, 14.0),)))
    ctx.user_message = "a typographic portrait"
    monkeypatch.setattr(story_planner, "suggest",
                        _no_base(story_planner.suggest))
    out = FT.suggest_story(ctx)
    assert out.startswith("Pack C")
    res = FT.apply_format_pack(ctx, picks=[1, 2])
    assert res.startswith("EDL v3 -> v4"), res
    assert len(ctx.lines) == 1


def _no_base(fn):
    def wrapped(*a, **k):
        k.setdefault("base", [])
        return fn(*a, **k)
    return wrapped


def test_direction_department_holds_the_2f_verbs():
    import tools_ext
    assert set(FT.VERBS) <= tools_ext.EXT_DEPARTMENTS["direction"]
