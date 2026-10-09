"""Round 52: the editorial critic, and the four capabilities that were missing.

Pure logic — no ffmpeg, no DB, no network. Run from worker/:
    python tests/test_taste_round52.py

Every assertion here is anchored to something a real customer hit on
2026-07-27, so a regression is a regression against a known complaint, not
against an opinion.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import captions as caplib                                    # noqa: E402
import subject                                               # noqa: E402
import taste                                                 # noqa: E402
from schemas import STYLIZE_KINDS, validate_edl              # noqa: E402
from timeline import Timeline                                # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


def fired(findings, needle):
    return any(needle in f for f in findings)


# A 40s vertical talking-head reel: the exact shape five users uploaded.
VERTICAL = {"width": 1080, "height": 1920}


def _index(words=40, shots=1):
    # Speech from 0.5s: the fixture is a REEL THAT OPENS ON THE HOOK, so any
    # finding below is caused by what the test added, not by the baseline.
    ws = [{"w": f"w{i}", "t0": 0.5 + i * 0.4, "t1": 0.8 + i * 0.4}
          for i in range(words)]
    return {"words": ws, "video": dict(VERTICAL),
            "shots": [{"t0": 0.0, "t1": 40.0}] * shots}


def _crit(edl, index=None, ask=""):
    index = index or _index()
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    return taste.critique(edl, index, tl, VERTICAL["width"],
                          VERTICAL["height"], ask)


print("== the opening ==")
CAPS = {"mode": "from_transcript"}
base = {"keep": [[0.0, 40.0]], "effects": {}, "captions": dict(CAPS)}
# Oct 2026: a captioned plate whose frame never moves is the owner's own
# 2.6/10 short, not a well-made reel. It raises exactly ONE advisory note
# (static picture). A dry mix is NOT flagged: music is never the agent's
# choice and zero SFX is fine when nothing on screen earns one. The fixture
# below adds the minimum design that clears it, so every other finding in
# this file is still caused by what the test added.
f = _crit(base)
check("a captioned plate that never moves raises only the static-picture note",
      len(f) == 1 and fired(f, "static picture"))
designed = dict(base, effects={"zooms": [{"id": "zd", "start": 6.0,
                                          "end": 8.0, "strength": 0.12,
                                          "mode": "ease"}]})
check("a designed reel raises nothing at all, even with a dry mix",
      _crit(designed) == [])
f = _crit(dict(base, music=[{"id": "md", "start": 0.0, "storage_key": "library:x",
                             "gain_db": -18.0, "duck": True}]))
check("...music alone leaves the picture flagged as static",
      fired(f, "static picture"))
f = _crit(dict(base, effects={"frame_shifts": []}),
          ask="keep it static, no music")
check("...and an explicit static ask suppresses it",
      not any(fired(f, n) for n in ("flat static", "static picture",
                                    "silent mix")))
f = _crit(dict(base, keep=[[0.0, 15.0]]))
check("...a 15s reel is too short to judge as flat",
      not fired(f, "flat static delivery"))
wide = {"words": _index()["words"], "video": {"width": 1920, "height": 1080},
        "shots": [{"t0": 0.0, "t1": 40.0}]}
f = taste.critique(base, wide, Timeline([[0.0, 40.0]], [], []), 1920, 1080, "")
check("...and long-form landscape is never asked for reel motion",
      not fired(f, "flat static delivery"))

f = _crit(dict(base, effects={"fade_in_s": 1.0}))
check("fade from black on a vertical reel is flagged",
      fired(f, "fade from BLACK"))
check("...and the fix names the tool", fired(f, "set_fades fade_in_s=0"))

f = _crit(dict(base, effects={"fade_in_s": 1.0}),
          ask="please add a slow fade in from black at the start")
check("...but NOT when the user asked for it",
      not fired(f, "fade from BLACK"))

f = _crit(dict(base, effects={"fade_out_s": 1.2}))
check("fade to black on a reel is flagged (it loops)",
      fired(f, "fade to BLACK"))

# 16:9 long-form: the same fades are correct there and must stay silent.
land = {"words": _index()["words"], "video": {"width": 1920, "height": 1080},
        "shots": [{"t0": 0, "t1": 40}]}
tl = Timeline([[0.0, 40.0]], [], [])
f = taste.critique(dict(base, effects={"fade_in_s": 1.0, "fade_out_s": 1.0}),
                   land, tl, 1920, 1080, "")
check("landscape keeps its fades", not fired(f, "fade from BLACK"))

print("== zoom rhythm ==")
# Oct 2026 reel profile (taste.REEL_*): a vertical/square output of 120s or
# less is measured against the owner's premium reel references, where a
# punched-in hook at 0s IS the pattern interrupt and framing changes every
# few seconds. These used to be flagged — and, while taste findings blocked
# verification, a premium-density reel could not ship. Long-form keeps the
# original limits (checked on the landscape fixture below).
hook = {"zooms": [{"id": "z1", "start": 0.3, "end": 2.0, "strength": 0.4}]}
f = _crit(dict(base, effects=hook))
check("a hook punch in a reel's first second is a device, not a bump",
      not fired(f, "first zoom fires"))
f = taste.critique(dict(base, effects=hook), land, Timeline([[0.0, 40.0]], [], []),
                   1920, 1080, "")
check("...but on long-form a zoom in the first second is still flagged",
      fired(f, "first zoom fires"))

five = [{"id": f"z{i}", "start": 2.0 + i * 7.0, "end": 4.0 + i * 7.0,
         "strength": 0.35, "mode": "punch"} for i in range(5)]
f = _crit(dict(base, effects={"zooms": five}))
check("five zooms in a 40s reel is premium density, not over-zooming",
      not fired(f, "zooms across"))
check("...but identical zooms are still flagged as mechanical",
      fired(f, "identical"))
# Past one camera move every 3s the frame never settles, even on a reel.
sixteen = [{"id": f"z{i}", "start": 0.5 + i * 2.4, "end": 1.9 + i * 2.4,
            "strength": 0.1 + 0.02 * (i % 4), "mode": "ease"}
           for i in range(16)]
f = _crit(dict(base, effects={"zooms": sixteen}))
check("sixteen zooms in a 40s reel is flagged as density",
      fired(f, "zooms across"))

two_close = [{"id": "z1", "start": 5.0, "end": 6.0, "strength": 0.3},
             {"id": "z2", "start": 6.0, "end": 7.0, "strength": 0.5}]
f = _crit(dict(base, effects={"zooms": two_close}))
check("adjacent zooms (1s apart) are flagged", fired(f, "apart"))
two_spaced = [{"id": "z1", "start": 5.0, "end": 6.0, "strength": 0.3},
              {"id": "z2", "start": 7.0, "end": 8.0, "strength": 0.5}]
f = _crit(dict(base, effects={"zooms": two_spaced}))
check("...but two seconds apart is reel rhythm", not fired(f, "apart"))

print("== junction effects ==")
jump_cuts = {"keep": [[0.0, 4.0], [5.0, 9.0], [10.0, 14.0], [15.0, 19.0],
                      [20.0, 24.0], [25.0, 29.0], [30.0, 34.0], [35.0, 40.0]],
             "captions": dict(CAPS),
             "effects": {"transition": {"style": "whip_left", "scope":
                                        "every_cut", "duration_s": 0.2}}}
f = _crit(jump_cuts, _index(words=40, shots=1))
check("a whip on every jump cut of one continuous shot is flagged",
      fired(f, "supposed to be INVISIBLE"))
scene_scoped = dict(jump_cuts)
scene_scoped["effects"] = {"transition": {"style": "whip_left",
                                          "scope": "scene",
                                          "duration_s": 0.2}}
f = _crit(scene_scoped, _index(words=40, shots=1))
check("...and scope='scene' is not", not fired(f, "supposed to be INVISIBLE"))

print("== sound ==")
# Reel profile (owner policy): about one purposeful sound every 4-5 s is
# premium sound design; a sound every 3 s or faster is "whoosh wars".
sfx = [{"id": f"s{i}", "at": 1.0 + i * 5.0} for i in range(8)]
f = _crit(dict(base, sfx=sfx))
check("eight sound events (one every 5 s) in a 40s reel are not carpet",
      not fired(f, "sound events in"))
sfx = [{"id": f"s{i}", "at": 1.0 + i * 3.0} for i in range(12)]
f = _crit(dict(base, sfx=sfx))
check("a sound every 3 s across a 40s reel is flagged",
      fired(f, "sound events in"))
sfx = [{"id": f"s{i}", "at": 0.5 + i * 1.5} for i in range(25)]
f = _crit(dict(base, sfx=sfx))
check("carpeted sound effects are flagged", fired(f, "sound events in"))
f = _crit(dict(base, sfx=[{"id": "s1", "at": 5.0},
                          {"id": "s2", "at": 5.2}]))
check("two same-role sfx 0.2s apart are flagged as a flam", fired(f, "muddy"))
f = _crit(dict(base, sfx=[
    {"id": "s1", "at": 4.8, "storage_key": "sfx/9/kit-whoosh_hard-ab12.wav"},
    {"id": "s2", "at": 5.0, "storage_key": "sfx/9/kit-impact_hard-cd34.wav"}]))
check("a whoosh pre-rolled into an impact is a designed layer",
      not fired(f, "muddy"))
f = _crit(dict(base, sfx=[
    {"id": "s1", "at": 5.0, "storage_key": "sfx/a.mp3", "purpose": "hit"},
    {"id": "s2", "at": 5.02, "storage_key": "sfx/b.mp3", "purpose": "hit"}]))
check("two different files stacked on one instant are one thicker hit",
      not fired(f, "muddy"))
f = _crit(dict(base, sfx=[
    {"id": "mg_mg1_sfx1", "at": 5.0, "purpose": "pop for motion graphic mg1"},
    {"id": "mg_mg1_sfx2", "at": 5.3, "purpose": "pop for motion graphic mg1"}]))
check("a motion graphic's own cue stack is one designed moment",
      not fired(f, "muddy"))
# A layered stack is ONE instant, measured from its first sound: alternating
# whoosh/impact cues every 0.3s used to chain into a single "event" and hid
# exactly the carpet the density note exists to catch.
carpet = [{"id": f"c{i}", "at": 1.0 + i * 0.3,
           "storage_key": ("sfx/9/kit-whoosh_soft-aa.wav" if i % 2 else
                           "sfx/9/kit-impact_soft-bb.wav")}
          for i in range(30)]
check("an alternating whoosh/impact carpet is many events, not one",
      len(taste.sfx_events(carpet)) >= 10)
f = _crit(dict(base, keep=[[0.0, 20.0]], sfx=carpet))
check("...and the reel density note fires on it", fired(f, "sound events in"))
check("a whoosh into an impact is still one event",
      len(taste.sfx_events(carpet[:2])) == 1)
f = _crit(dict(base, sfx=[{"id": "s1", "at": 5.0, "purpose": "accent"},
                          {"id": "s2", "at": 5.2, "purpose": "accent"}]))
check("a flam of role-less sounds says so instead of claiming one role",
      fired(f, "no distinguishable role") and not fired(f, "same role"))
f = _crit(dict(base, sfx=[
    {"id": "s1", "at": 5.0, "storage_key": "sfx/9/kit-impact_hard-aa.wav"},
    {"id": "s2", "at": 5.2, "storage_key": "sfx/9/kit-impact_soft-bb.wav"}]))
check("a flam of two impacts names the shared role",
      fired(f, "same role (hit)"))

f = _crit(dict(base, music=[{"id": "m1", "start": 0.0, "end": 20.0,
                             "storage_key": "library:x"}]))
check("music that stops early is flagged", fired(f, "play dry"))

print("== text and captions ==")
f = _crit(dict(base, texts=[{"id": "t1", "start": 10.0, "end": 14.0,
                             "text": "Ujjwala Doshi"}]))
check("a text card burning under captions is flagged",
      fired(f, "two layers of text"))
f = _crit(dict(base, caption_mutes=[[10.0, 14.0]],
               texts=[{"id": "t1", "start": 10.0, "end": 14.0, "text": "X"}]))
check("...unless the window is muted", not fired(f, "two layers of text"))

# No captions at all on a talking reel — the one case where their absence
# IS the finding.
f = _crit({"keep": [[0.0, 40.0]], "effects": {}}, _index(words=60))
check("an uncaptioned talking reel is flagged", fired(f, "watches muted"))

print("== pacing ==")
f = _crit(dict(base, speed=[{"id": "sp1", "start": 1.0, "end": 3.0,
                             "factor": 0.45}]))
check("sub-0.6x slow motion is flagged as stepping", fired(f, "visibly steps"))

print("== audit line ==")
check("clean edit yields no audit line", taste.audit_line([]) == "")
line = taste.audit_line(["a", "b", "c", "d", "e", "f"])
check("audit line caps and counts the rest", "(+2 more)" in line)
check("audit line tells the agent to re-render", "Re-render" in line)
check("audit line is labelled advisory, never a mandate",
      "advisory: keep if intentional" in line and "never block" in line)

print("== caption safe area (vertical platform chrome) ==")
check("vertical bottom margin clears the UI band",
      caplib.bottom_margin_v("bottom", (1080, 1920)) >= 1920 * 0.13)
check("landscape bottom margin is untouched",
      caplib.bottom_margin_v("bottom", (1280, 720)) == 46)
check("top position is unaffected by the lift",
      caplib.bottom_margin_v("top", (1080, 1920)) < 1920 * 0.13)

print("== caption text fixes ==")
words = [{"w": "dios,", "t0": 0.0, "t1": 0.5},
         {"w": "el", "t0": 0.5, "t1": 0.8},
         {"w": "espiritu", "t0": 0.8, "t1": 1.2},
         {"w": "santo", "t0": 1.2, "t1": 1.6},
         {"w": "Ushula", "t0": 1.6, "t1": 2.0}]
out = caplib.apply_text_fixes(words, [["dios", "Dios"],
                                      ["espiritu santo", "Espíritu Santo"],
                                      ["ushula", "Ujjwala"]])
check("single-word fix keeps punctuation", out[0]["w"] == "Dios,")
check("multi-word fix maps 1:1",
      [w["w"] for w in out[2:4]] == ["Espíritu", "Santo"])
check("case-insensitive match", out[4]["w"] == "Ujjwala")
check("timings never move",
      [(w["t0"], w["t1"]) for w in out] ==
      [(w["t0"], w["t1"]) for w in words])
check("no fixes is a no-op",
      [w["w"] for w in caplib.apply_text_fixes(words, None)] ==
      [w["w"] for w in words])
check("word-count mismatch is ignored, not applied",
      [w["w"] for w in caplib.apply_text_fixes(
          words, [["espiritu santo", "Espiritu"]])] ==
      [w["w"] for w in words])

v = validate_edl({"keep": [[0, 10]],
                  "captions": {"mode": "from_transcript",
                               "text_fixes": [["dios", "Dios"],
                                              ["a", "b c"]]}}, 20).model_dump()
check("schema drops uneven pairs",
      v["captions"]["text_fixes"] == [["dios", "Dios"]])

print("== new finishing effects ==")
for kind in ("sharpen", "denoise", "motion_blur", "stabilize"):
    check(f"'{kind}' is a real stylize kind", kind in STYLIZE_KINDS)
    validate_edl({"keep": [[0, 10]],
                  "effects": {"stylize": [{"id": "s1", "kind": kind,
                                           "intensity": 0.5}]}}, 20)

print("== subject measurement ==")
check("no frames yields no measurement",
      subject.points_from_frames([]) == ([], None))
check("unreadable frames yield no measurement",
      subject.points_from_frames(["/nonexistent.jpg"]) == ([], None))
check("median of measured points",
      subject.median_point([(0.2, 0.4), (0.3, 0.5), (0.9, 0.6)]) ==
      (0.3, 0.5))
check("spread measures travel",
      subject.spread([(0.2, 0.4), (0.9, 0.45)]) == 0.7)

print("== one type system per video (round 62) ==")


def _tx(i, tpl, ent):
    return {"id": f"tx{i}", "text": f"line {i}", "start": 2.0 + i * 6.0,
            "end": 6.0 + i * 6.0, "template": tpl, "entrance": ent}


# The real 26s architecture reel: title + callout + callout + subtitle,
# entrances mixed fade/pop. Four sentences, three templates, two entrances.
deck = dict(base, texts=[_tx(0, "title", "fade"), _tx(1, "callout", "pop"),
                         _tx(2, "callout", "pop"), _tx(3, "subtitle", "fade")])
check("a template-per-sentence text stack is flagged as a slide deck",
      fired(_crit(deck), "slide deck"))
one_sys = dict(base, texts=[_tx(0, "callout", "fade"), _tx(1, "callout", "fade"),
                            _tx(2, "callout", "fade"), _tx(3, "callout", "fade")])
check("one template + one entrance raises nothing",
      not fired(_crit(one_sys), "slide deck"))
two_cards = dict(base, texts=[_tx(0, "title", "fade"), _tx(1, "subtitle", "pop")])
check("two cards are too few to call a pattern",
      not fired(_crit(two_cards), "slide deck"))
check("asking for the mix suppresses it",
      not fired(_crit(deck, ask="use a different template for each line"),
                "slide deck"))

print("== speech-led hook vs gameplay leftover words ==")
# 605-shaped: 26s 9:16, three leftover VOD words + music. Not a talking head.
game_idx = {
    "words": [{"w": "Thanks", "t0": 5.3, "t1": 5.6},
              {"w": "for", "t0": 5.6, "t1": 5.8},
              {"w": "watching!", "t0": 5.8, "t1": 6.2}],
    "video": {"width": 1080, "height": 1920},
    "shots": [{"t0": 0.0, "t1": 26.0}],
}
game_edl = {"keep": [[0.0, 26.0]], "effects": {},
            "music": [{"id": "m1", "start": 0, "end": 26,
                       "storage_key": "x"}]}
check("gameplay leftover words are not dead-air",
      not fired(_crit(game_edl, game_idx), "dead air"))
talk_idx = _index(words=80)
for w in talk_idx["words"]:
    w["t0"] += 4.8
    w["t1"] += 4.8
check("talking-head late hook is still dead-air",
      fired(_crit(base, talk_idx), "dead air"))

print(f"\n{PASS} checks passed")
