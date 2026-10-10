"""Zooms and sound effects are OPTIONAL, NEVER RULES (owner, Oct 10 2026).

Used where nothing calls for them they make an edit look childish, so no
default path may add a zoom or a sound, and no advisory may count a missing
one as a defect or prescribe one. The capabilities themselves stay: every
tool that can place a zoom or a sound still does when explicitly asked
(tests/test_music_library_and_looks.py, test_motion_planner.py,
test_cut_hygiene.py pin the opt-in paths).

Run:  python -m pytest tests/test_restraint_defaults.py -q     (from worker/)
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
import motion_planner                                         # noqa: E402
import motion_tools                                           # noqa: E402
import quality_verifier                                       # noqa: E402
import taste                                                  # noqa: E402
from schemas import validate_edl                              # noqa: E402
from timeline import Timeline                                 # noqa: E402


def _default(fn, arg):
    return inspect.signature(fn).parameters[arg].default


def test_no_tool_adds_a_sound_or_a_camera_move_unless_asked():
    # looks: transition sounds are opt-in
    assert _default(agent_tools.apply_look, "transition_sounds") is None
    # motion graphics: silent unless sfx=true
    assert motion_tools.SFX_DEFAULT is False
    assert _default(motion_tools.add_motion_graphic, "sfx") is None
    # the corrupt screen is silent unless asked (and then library-only)
    assert _default(agent_tools.add_corrupt_screen, "sound") is False
    # the beat sheet suggests graphics; camera and sound are opt-in
    assert _default(motion_planner.plan, "camera") is False
    assert _default(motion_planner.plan, "sounds") is False
    assert _default(motion_planner.suggest_motion_beats, "camera") is False
    assert _default(motion_planner.suggest_motion_beats, "sounds") is False
    # product demos: click sounds are opt-in
    assert _default(agent_tools.showcase_demo, "click_sounds") is False


def _reel(keep, **extra):
    edl = {"keep": keep, "frame": {"ratio": "9:16", "mode": "crop"},
           "captions": {"mode": "from_transcript"},
           "motion": [{"id": "hook", "template": "hook_title",
                       "start": 0.2, "end": 2.4,
                       "params": {"text": "This changed *everything*"}}]}
    edl.update(extra)
    return validate_edl(edl, 120.0).model_dump(exclude_none=True)


def _index(n=90):
    return {"video": {"width": 1080, "height": 1920, "fps": 30.0},
            "words": [{"w": f"w{i}", "t0": 0.4 + i * 0.4, "t1": 0.7 + i * 0.4}
                      for i in range(n)],
            "shots": [{"id": 0, "start": 0.0, "end": 120.0}]}


ASKS_FOR_A_DEVICE = ("add_zoom", "add a zoom", "punch in to", "add_sfx",
                     "add a sound", "needs a sound", "with no camera move",
                     "silent mix")


def test_a_restrained_reel_draws_no_zoom_or_sound_advisory():
    # 40 s, eleven bare jump cuts, a hook title, no zoom and no sound at all
    keep = [[i * 4.0, i * 4.0 + 3.5] for i in range(12)]
    edl = _reel(keep)
    tl = Timeline(edl["keep"], [], [])
    index = _index()
    found = taste.critique(edl, index, tl, 1080, 1920, "")
    assert found == [], found
    # a steady head across every cut (measured) changes nothing
    found = taste.critique(edl, index, tl, 1080, 1920, "",
                           measure=lambda t: [[0.42, 0.2, 0.58, 0.4]])
    assert found == [], found
    rows = quality_verifier.deterministic_findings(edl, index, "")
    for r in rows:
        msg = str(r.get("message") or "").lower()
        assert not any(a in msg for a in ASKS_FOR_A_DEVICE), msg


def test_no_advisory_prescribes_a_zoom_or_a_sound():
    # the busiest restrained-looking case: a reel whose head jumps on cuts
    keep = [[i * 4.0, i * 4.0 + 3.5] for i in range(12)]
    edl = _reel(keep)
    tl = Timeline(edl["keep"], [], [])

    def measure(src_t):
        k = int(src_t // 4.0)
        return [[0.3 + 0.2 * (k % 2), 0.2, 0.46 + 0.2 * (k % 2), 0.4]]
    found = taste.critique(edl, _index(), tl, 1080, 1920, "", measure=measure)
    line = next(f for f in found if "jump cut" in f)
    assert "leave it" in line and "B-roll" in line
    for f in found:
        low = f.lower()
        assert "add_zoom" not in low and "add_sfx" not in low, f
    # at most JUMP_CUT_LIST cuts are named
    assert line.count("face-widths") <= taste.JUMP_CUT_LIST


def test_too_many_zooms_or_a_repeated_sound_are_the_findings_instead():
    keep = [[0.0, 40.0]]
    zooms = [{"id": f"z{i}", "start": 1.0 + i * 5.0, "end": 2.5 + i * 5.0,
              "strength": 0.1 + 0.02 * (i % 3), "mode": "ease"}
             for i in range(8)]
    edl = _reel(keep, effects={"zooms": zooms})
    found = taste.critique(edl, _index(), Timeline(keep, [], []),
                           1080, 1920, "")
    line = next(f for f in found if "zooms across" in f)
    assert "Zooms are optional, never a rule" in line
    assert "jump cuts" not in line
    import sound_library
    key = sound_library.asset_key(7, "pop_1")
    sfx = [{"id": f"s{i}", "storage_key": key, "at": 8.7 + 1.86 * i,
            "gain_db": -12.0, "purpose": f"stat {i + 1}"} for i in range(3)]
    edl = _reel(keep, sfx=sfx)
    found = taste.critique(edl, _index(), Timeline(keep, [], []),
                           1080, 1920, "")
    assert any("the same sound plays twice" in f for f in found), found


# ── review fixes (independent review, Oct 10 2026) ───────────────────────

class _GlitchCtx:
    """Just enough context for add_corrupt_screen's EDL bookkeeping."""
    project_id = 7

    def __init__(self):
        self.edl = {"keep": [[0.0, 20.0]], "inserts": []}

    def latest_edl(self):
        import copy
        return {"json": copy.deepcopy(self.edl), "version": 1}


def _patch_glitch(monkeypatch, ctx, built, sounds):
    def asset(_ctx, style, k, dur, sound):
        built.append(sound)
        return "generated_video/7/glitch-x.mp4", None

    def insert(_ctx, key, at, duration_s=None):
        ctx.edl["inserts"].append({"id": "in1", "storage_key": key,
                                   "at_output_s": float(at),
                                   "duration_s": float(duration_s)})
        return "EDL v2 — inserted the clip. Before: x"

    def sfx(_ctx, storage_key, at, gain_db=-6.0, purpose=None,
            offset_s=None, dur_s=None):
        sounds.append({"storage_key": storage_key, "at": at,
                       "gain_db": gain_db, "dur_s": dur_s})
        return "EDL v3 — added sfx"
    monkeypatch.setattr(agent_tools, "_corrupt_glitch_asset", asset)
    monkeypatch.setattr(agent_tools, "insert_media", insert)
    monkeypatch.setattr(agent_tools, "add_sfx", sfx)


def test_corrupt_screen_sound_is_an_approved_library_recording(monkeypatch):
    import sound_library
    ctx, built, sounds = _GlitchCtx(), [], []
    _patch_glitch(monkeypatch, ctx, built, sounds)
    # default: silent clip, no sound placed, and the result says so
    out = agent_tools.add_corrupt_screen(ctx, 5.0)
    assert built == [False] and sounds == [], (built, sounds)
    assert "it is silent" in out and "sound=true" in out
    # asked: still a SILENT clip (no synthesized hiss) plus one library cue
    # starting on the screen's first frame and stopping with it
    ctx2, built, sounds = _GlitchCtx(), [], []
    _patch_glitch(monkeypatch, ctx2, built, sounds)
    out = agent_tools.add_corrupt_screen(ctx2, 5.0, duration_s=0.6,
                                         sound=True)
    assert built == [False], built
    assert [s["storage_key"] for s in sounds] == ["sound:glitch_1"]
    hit = sounds[0]["at"] - sound_library.hit_s("glitch_1")
    assert abs(hit - 5.0) < 1e-6, sounds
    assert sounds[0]["dur_s"] <= 0.6 and sounds[0]["gain_db"] == -14.0
    assert "approved glitch recording" in out and "EDL v3" in out
    # a longer screen takes the longer recording
    ctx3, built, sounds = _GlitchCtx(), [], []
    _patch_glitch(monkeypatch, ctx3, built, sounds)
    agent_tools.add_corrupt_screen(ctx3, 5.0, duration_s=1.0, sound="true")
    assert [s["storage_key"] for s in sounds] == ["sound:glitch_2"]
    assert sounds[0]["dur_s"] <= 1.0
    desc = agent_tools.TOOLS["add_corrupt_screen"][1]
    assert "synthesized" not in desc and "approved library glitch" in desc


def test_jump_cut_measure_never_touches_the_proxy_until_a_frame_is_asked():
    class Ctx:
        has_main_video = True
        workdir = "/nonexistent"
        calls = 0

        def proxy_path(self):
            Ctx.calls += 1
            raise RuntimeError("no proxy available")
    ctx = Ctx()
    measure = motion_tools.jump_cut_measure(ctx)
    assert measure is not None and Ctx.calls == 0
    # a reel with no jump cut never asks: the proxy is never leased
    keep = [[0.0, 40.0]]
    edl = _reel(keep)
    taste.critique(edl, _index(), Timeline(keep, [], []), 1080, 1920, "",
                   measure=measure)
    assert Ctx.calls == 0
    # asked, an undecodable context answers None and the index stands in
    assert measure(3.0) is None and measure(4.0) is None
    assert Ctx.calls == 1
    no_video = type("NoVideo", (), {"has_main_video": False})()
    assert motion_tools.jump_cut_measure(no_video) is None


def test_restraint_worded_directions_are_not_promises_to_add_a_sound():
    import director
    for text in ("sparse library sounds only where a moment earns one; "
                 "zero is fine",
                 "zooms are optional, never a rule",
                 "a punch only if a moment earns it"):
        assert director._direction_mode(text) == "preserve", text
    assert director._direction_mode("no SFX") == "omit"
    assert director._direction_mode(
        "one impact on the payoff, a shutter on the photo") == "author"
    # the look summaries the agent reads never promise a sound
    for name, look in agent_tools.LOOKS.items():
        low = str(look.get("summary") or "").lower()
        assert "whoosh" not in low and "burst" not in low, name
