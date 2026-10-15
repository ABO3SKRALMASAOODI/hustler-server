"""Black and white is a device, never a flat look (owner taste, 15 Oct).

The owner saw agents turn the references' B&W MOMENTS (a section opening to
colour on the drop, one colour kept, a silhouette hold, negatives on hits)
into a constant B&W grade over the whole short. These pin the rule in code:
the noir look pushes colour down instead of removing it, and a write that
makes the whole short flat B&W needs the user's own ask.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import agent_tools  # noqa: E402
import mono_policy  # noqa: E402
from schemas import default_edl  # noqa: E402
from w0c_support import tool_context  # noqa: E402


def test_the_users_words_decide_whether_bw_was_asked():
    assert mono_policy.asked("make it black and white") is True
    assert mono_policy.asked("go B&W for the whole thing") is True
    assert mono_policy.asked("a monochrome noir feel please") is True
    assert mono_policy.asked("make it premium") is False
    assert mono_policy.asked("no black and white this time") is False
    assert mono_policy.asked("bring back colour, not black and white") \
        is False
    assert mono_policy.asked("") is None


def _mono_window(start, end, sat=0.0, **kw):
    return dict({"id": f"gw{int(start)}", "type": "grade_window",
                 "stage": "world", "start": start, "end": end,
                 "saturation": sat}, **kw)


def test_whole_mono_is_the_bw_grade_or_mono_windows_over_most_of_it():
    edl = default_edl(20.0)
    assert mono_policy.whole_mono(edl) is None
    edl["effects"] = dict(edl.get("effects") or {}, grade="bw")
    assert "bw" in mono_policy.whole_mono(edl)
    edl = default_edl(20.0)
    edl["fx"] = [_mono_window(0.0, 4.0)]                  # a device: 20%
    assert mono_policy.whole_mono(edl) is None
    edl["fx"] = [_mono_window(0.0, 8.0), _mono_window(7.0, 15.0)]
    assert "75%" in mono_policy.whole_mono(edl)
    # one colour kept is a device, not a flat look
    edl["fx"] = [_mono_window(0.0, 20.0, isolate={"hue": "#c0392b"})]
    assert mono_policy.whole_mono(edl) is None


def test_the_agent_cannot_turn_the_whole_short_bw_unasked():
    ctx, fake = tool_context(default_edl(20.0),
                             user_message="make this a premium short")
    out = agent_tools.set_color_grade(ctx, "bw")
    assert out.startswith("REJECTED"), out
    assert "B&W is a device" in out and "isolate_colour" in out
    assert len(fake.rows) == 1
    # the user asked: it is theirs
    ctx, fake = tool_context(default_edl(20.0),
                             user_message="can you make it black and white")
    assert agent_tools.set_color_grade(ctx, "bw").startswith("EDL v")
    # a mono SECTION that opens to colour is the device itself
    ctx, fake = tool_context(default_edl(20.0),
                             user_message="make this a premium short")
    edl = default_edl(20.0)
    edl["fx"] = [_mono_window(0.0, 3.4, ramp_out_f=3)]
    assert ctx.write_edl(edl, "mono until the turn").startswith("EDL v")


def test_with_no_user_words_it_passes_with_the_devices_named():
    """An MCP client or a script has no user words on the server: the write
    passes and the reply says how B&W is used here."""
    ctx, _fake = tool_context(default_edl(20.0), user_message="", mcp=True)
    out = agent_tools.set_color_grade(ctx, "bw")
    assert out.startswith("EDL v"), out
    assert "NOTE (taste)" in out and "silhouette hold" in out


def test_the_noir_look_is_never_a_flat_bw_grade():
    look = agent_tools.LOOKS["mono_noir"]
    assert look.get("grade") != "bw"
    assert look["grade_custom"]["saturation"] >= 0.4
    for name, lk in agent_tools.LOOKS.items():
        assert lk.get("grade") != "bw", name


def test_the_other_roads_to_a_flat_bw_short_are_the_same_look():
    """Review (w1r): refused 'bw', a mid-tier agent reaches for the custom
    grade at saturation 0, a 'hue=s=0' filter chain over the program or a
    xerox finish over the whole short — each is the same flat look and is
    judged the same way; partial windows stay devices."""
    edl = default_edl(20.0)
    edl["effects"] = dict(edl.get("effects") or {},
                          grade_custom={"saturation": 0.0, "contrast": 1.2})
    assert "custom grade" in mono_policy.whole_mono(edl)
    edl["effects"]["grade_custom"]["saturation"] = 0.6
    assert mono_policy.whole_mono(edl) is None
    edl = default_edl(20.0)
    edl["effects"] = dict(edl.get("effects") or {}, custom=[
        {"id": "c1", "chain": "hue=s=0,eq=contrast=1.15", "label": "noir"}])
    assert "100%" in mono_policy.whole_mono(edl)
    edl["effects"]["custom"][0].update(start=2.0, end=5.0)
    assert mono_policy.whole_mono(edl) is None          # a mono section
    edl["effects"]["custom"] = [{"id": "c2", "chain": "hue=s=0.4"}]
    assert mono_policy.whole_mono(edl) is None          # muted, not mono
    edl = default_edl(20.0)
    edl["fx"] = [{"id": "fx1", "type": "finish", "kind": "xerox",
                  "stage": "pre_captions", "start": 0.0, "end": 19.0}]
    assert mono_policy.whole_mono(edl)
    edl["fx"][0]["kind"] = "thermal"
    assert mono_policy.whole_mono(edl) is None
    # the verb never reaches saturation 0 (a value under 0.5 is a delta);
    # a batch can write it directly, and the write gate judges it
    ctx, fake = tool_context(default_edl(20.0),
                             user_message="make this a premium short")
    edl = default_edl(20.0)
    edl["effects"] = dict(edl.get("effects") or {},
                          grade_custom={"saturation": 0.0})
    out = ctx.write_edl(edl, "custom grade")
    assert out.startswith("REJECTED"), out
    assert "B&W is a device" in out and len(fake.rows) == 1
