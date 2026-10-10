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
    # the corrupt screen's synthesized hiss is not a library recording
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
