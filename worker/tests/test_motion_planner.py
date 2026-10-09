"""suggest_motion_beats: transcript-anchored premium beat sheet."""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import motion_planner  # noqa: E402


def _words(text, start=0.0, step=0.3):
    out, t = [], start
    for tok in text.split():
        out.append({"w": tok, "t0": round(t, 3), "t1": round(t + step * 0.8, 3)})
        t += step
    return out


def test_numbers_lists_contrast_cta_and_cuts_become_timed_beats():
    words = _words("Here is the thing. We grew 340% in one year. First we hired, second we shipped, "
                   "third we sold. It is better than renting. Follow for part two. "
                   + "and then we waited " * 6)
    index = {"words": words}
    edl = {"keep": [[0.0, 12.0], [12.4, 30.0]],
           "captions": {"mode": "from_transcript", "emphasis_words": ["shipped"]}}
    res = motion_planner.plan(edl, index)
    kinds = [b["kind"] for b in res["beats"]]
    assert kinds[0] == "hook" and res["beats"][0]["at"] == 0.0
    num = next(b for b in res["beats"] if b["kind"] == "number")
    assert num["template"] == "counter" and num["params"]["to"] == 340 and num["params"]["suffix"] == "%"
    lst = next(b for b in res["beats"] if b["kind"] == "list")
    assert lst["template"] == "checklist" and len(lst["item_times"]) == 3
    assert any(b["kind"] == "contrast" for b in res["beats"])
    assert any(b["template"] == "follow_cta" for b in res["beats"])
    assert any(c["mode"] == "landing" and abs(c["at"] - 12.0) < 0.05 for c in res["camera"])
    assert res["long_holds"] == [] or all(b - a > 3.5 for a, b in res["long_holds"])


def test_number_cluster_becomes_one_stat_stack_and_values_are_parsed():
    words = _words("look at this 32% fewer errors 24% faster and 2.5 million users")
    res = motion_planner.plan({"keep": [[0.0, 10.0]]}, {"words": words})
    stack = next(b for b in res["beats"] if b["kind"] == "number_cluster")
    assert [r["value"] for r in stack["params"]["rows"]] == ["32%", "24%", "2.5M"]


def test_tool_formats_and_never_writes():
    words = _words("This changed everything for us forever")
    edl = {"keep": [[0.0, 3.0]]}
    calls = []
    ctx = types.SimpleNamespace(index={"words": words}, has_main_video=True,
                                latest_edl=lambda: {"json": edl},
                                write_edl=lambda *a: calls.append(a))
    out = motion_planner.suggest_motion_beats(ctx)
    assert "hook_title" in out and not calls
