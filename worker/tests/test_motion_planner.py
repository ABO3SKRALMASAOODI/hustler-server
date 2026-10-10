"""suggest_motion_beats: transcript-anchored premium beat sheet.

Owner, Oct 10 2026: zooms and sound effects are OPTIONAL, NEVER RULES. The
sheet suggests graphics; camera candidates (camera=True) and sound
candidates (sounds=True) are opt-in, and even then a short list — never a
landing per cut, a push per hold or a sound per beat.
"""

import os
import re
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import motion_planner  # noqa: E402
import sound_library  # noqa: E402


def _words(text, start=0.0, step=0.3):
    out, t = [], start
    for tok in text.split():
        out.append({"w": tok, "t0": round(t, 3), "t1": round(t + step * 0.8, 3)})
        t += step
    return out


TEXT = ("Here is the thing. We grew 340% in one year. First we hired, second we shipped, "
        "third we sold. It is better than renting. Follow for part two. "
        + "and then we waited " * 6)
EDL = {"keep": [[0.0, 12.0], [12.4, 30.0]],
       "captions": {"mode": "from_transcript", "emphasis_words": ["shipped"]}}


def test_numbers_lists_contrast_and_cta_become_timed_beats():
    res = motion_planner.plan(EDL, {"words": _words(TEXT)})
    kinds = [b["kind"] for b in res["beats"]]
    assert kinds[0] == "hook" and res["beats"][0]["at"] == 0.0
    num = next(b for b in res["beats"] if b["kind"] == "number")
    assert num["template"] == "counter" and num["params"]["value"] == "340%"
    # the counter's own parameters: the beat is placeable as written
    import motion_templates
    motion_templates.check_params("counter", {k: v for k, v in num["params"].items()
                                              if v is not None})
    lst = next(b for b in res["beats"] if b["kind"] == "list")
    assert lst["template"] == "checklist" and len(lst["item_times"]) == 3
    assert any(b["kind"] == "contrast" for b in res["beats"])
    assert any(b["template"] == "follow_cta" for b in res["beats"])
    assert res["long_holds"] == [] or all(b - a > 3.5 for a, b in res["long_holds"])


def test_no_camera_moves_or_sounds_by_default():
    res = motion_planner.plan(EDL, {"words": _words(TEXT)})
    # the jump cut at 12.0 s used to get a landing zoom "to hide it"
    assert res["camera"] == []
    assert not any("sound" in b for b in res["beats"])


def test_camera_candidates_are_opt_in_few_and_never_on_jump_cuts():
    res = motion_planner.plan(EDL, {"words": _words(TEXT)}, camera=True)
    cam = res["camera"]
    assert 1 <= len(cam) <= max(1, int(res["program_s"] / motion_planner.CAMERA_EVERY_S))
    assert all(c["why"].startswith("optional") for c in cam)
    # 12.0 s is a jump cut inside one take: never a candidate
    assert not any(c["mode"] == "landing" for c in cam)
    assert not any(abs(c["at"] - 12.0) < 0.05 and c["mode"] == "landing" for c in cam)


def test_a_real_camera_change_can_be_a_landing_candidate():
    shots = [{"id": 0, "start": 0.0, "end": 12.2}, {"id": 1, "start": 12.2, "end": 60.0}]
    words = _words("we waited and waited " * 14)
    res = motion_planner.plan({"keep": [[0.0, 12.0], [12.4, 40.0]]},
                              {"words": words, "shots": shots}, camera=True)
    assert any(c["mode"] == "landing" and abs(c["at"] - 12.0) < 0.05 for c in res["camera"])


def test_sound_candidates_are_opt_in_and_library_only():
    res = motion_planner.plan(EDL, {"words": _words(TEXT)}, sounds=True)
    ids = {r["id"] for r in sound_library.catalog(None)}
    retired = ("pop_soft", "whoosh_hard", "kick", "kit")
    for b in res["beats"]:
        text = b["sound"]
        assert text.startswith(("optional", "silent")), b
        named = set(re.findall(r"[a-z_]+_\d", text))
        assert named <= ids, (b["kind"], named - ids)
        assert not any(r in text for r in retired), text


def test_number_cluster_becomes_one_stat_stack_and_values_are_parsed():
    words = _words("look at this 32% fewer errors 24% faster and 2.5 million users")
    res = motion_planner.plan({"keep": [[0.0, 10.0]]}, {"words": words})
    stack = next(b for b in res["beats"] if b["kind"] == "number_cluster")
    assert [r["value"] for r in stack["params"]["rows"]] == ["32%", "24%", "2.5M"]


def _ctx(words, edl, calls):
    return types.SimpleNamespace(index={"words": words}, has_main_video=True,
                                 latest_edl=lambda: {"json": edl},
                                 write_edl=lambda *a: calls.append(a))


def test_tool_formats_never_writes_and_offers_camera_and_sound_as_options():
    calls = []
    ctx = _ctx(_words(TEXT), EDL, calls)
    out = motion_planner.suggest_motion_beats(ctx)
    assert 'word_slam [hook] params={"tier": "hook"}' in out and not calls
    assert "add_zoom" not in out and "sound:" not in out
    assert "Zooms and sound effects are optional, never rules" in out
    assert "camera=true" in out and "sounds=true" in out
    both = motion_planner.suggest_motion_beats(ctx, camera=True, sounds="true")
    assert "add_zoom" in both and "| sound:" in both
    assert "camera=true lists" not in both
    spec = motion_planner.TOOL_SPECS["suggest_motion_beats"]
    assert spec[2]["camera"]["type"] == "boolean"
    assert spec[2]["sounds"]["type"] == "boolean"
    assert "OFF by default" in spec[1]
    assert "landing zooms after cuts" not in spec[1]
