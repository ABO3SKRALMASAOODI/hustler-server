"""Round 45 — driven website demos and the follow-zoom that cuts them.

Three things are worth pinning here, and they are the three that would fail
silently rather than loudly:

  1. The follow zoom must render a MOVING centre. A bug that dropped the path
     would still produce a valid filtergraph and a perfectly watchable static
     zoom — nobody would see a stack trace, the demo would just stop
     following the cursor. So the graph is asserted to contain the
     interpolation, and the legacy graphs are asserted unchanged.
  2. The demo script validator must refuse a password field. That refusal is
     the difference between a launch video and a published credential.
  3. Event timestamps are relative to the DELIVERED file. The whole value of
     the track is that a click at 4.2s is at 4.2s in the mp4 — if the load
     lead were left in, every sound would land early by however long the page
     took to paint, which varies per run and would never reproduce.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import webrecord                                              # noqa: E402
from schemas import EDLValidationError, validate_edl, edl_signature  # noqa: E402
import renderer                                               # noqa: E402
from timeline import Timeline                                 # noqa: E402


# ── the step vocabulary ────────────────────────────────────────────────

def test_steps_accepts_a_normal_demo():
    steps = webrecord._norm_steps([
        {"do": "scroll", "to": "Pricing"},
        {"do": "click", "text": "Start free trial"},
        {"do": "type", "selector": "input[type=email]",
         "text": "you@example.com"},
        {"do": "press", "key": "Enter", "seconds": 2},
    ])
    assert [s["do"] for s in steps] == ["scroll", "click", "type", "press"]
    assert steps[3]["seconds"] == 2.0


def test_steps_reject_a_password_field():
    with pytest.raises(webrecord.WebRecordError) as e:
        webrecord._norm_steps([{"do": "type",
                                "selector": "input[type=password]",
                                "text": "hunter2"}])
    assert "password" in str(e.value).lower()


def test_steps_reject_a_click_with_no_target():
    with pytest.raises(webrecord.WebRecordError):
        webrecord._norm_steps([{"do": "click"}])


def test_steps_reject_an_unknown_verb():
    with pytest.raises(webrecord.WebRecordError) as e:
        webrecord._norm_steps([{"do": "swipe", "text": "x"}])
    assert "swipe" in str(e.value)


def test_steps_are_bounded():
    with pytest.raises(webrecord.WebRecordError):
        webrecord._norm_steps([{"do": "wait", "seconds": 1}] * 100)


# ── event timing is relative to the delivered file ─────────────────────

class _FakePage:
    def wait_for_timeout(self, ms):
        pass


def test_event_times_are_measured_from_the_files_zero():
    """A capture whose page took 3s to paint must still report a click that
    happened 1s after the settle as t=1: t_zero is the instant the delivered
    file starts, and the frame list is built from that same instant."""
    import time
    s = webrecord._Session(_FakePage(), time.monotonic() + 60, (1920, 1080))
    s.t_zero = time.time() - 1.0        # the settle was one second ago
    s.note("click", 960, 540, "Buy")
    ev = s.events[0]
    assert 0.8 < ev["t"] < 1.3, ev
    # …and the position is a fraction of the frame, not a pixel.
    assert ev["x"] == pytest.approx(0.5, abs=0.01)
    assert ev["y"] == pytest.approx(0.5, abs=0.01)


def test_event_positions_clamp_into_the_frame():
    import time
    s = webrecord._Session(_FakePage(), time.monotonic() + 60, (1000, 1000))
    s.note("click", -50, 5000)
    assert s.events[0]["x"] == 0.0 and s.events[0]["y"] == 1.0


def test_frame_list_starts_at_zero_even_when_nothing_painted():
    """The capture's zero is the settle, not the first frame that happened to
    arrive after it. Chromium sends frames only on damage, so a settled page
    can be silent for a second — and starting the file at that later frame
    shifted every reported timestamp by the length of the quiet gap."""
    import os
    import tempfile

    class _Cast(webrecord._Screencast):
        def __init__(self):                       # no browser needed
            self.dir = tempfile.mkdtemp()
            self.frames = []
            self.dropped = 0

    cast = _Cast()
    for name, ts in (("pre.jpg", 100.0), ("a.jpg", 105.0), ("b.jpg", 106.0)):
        path = os.path.join(cast.dir, name)
        open(path, "wb").close()
        cast.frames.append((path, ts))
    # Settle at 104: the last frame BEFORE it opens the file and is held
    # across the one-second silence to 105.
    list_path, total = cast.write_concat(104.0, 108.0)
    body = open(list_path).read()
    assert body.splitlines()[0].endswith("pre.jpg'"), body
    assert "duration 1.0000" in body, body
    assert total == pytest.approx(4.0, abs=0.01)


# ── the follow zoom ────────────────────────────────────────────────────

FOLLOW = {
    "keep": [[0.0, 10.0]],
    "effects": {"zooms": [{
        "id": "zm1", "start": 1.0, "end": 5.0, "strength": 0.4,
        "mode": "follow",
        "path": [{"f": 0.0, "cx": 0.2, "cy": 0.3},
                 {"f": 0.5, "cx": 0.8, "cy": 0.3},
                 {"f": 1.0, "cx": 0.8, "cy": 0.75}]}]},
}


def test_follow_validates_and_survives_a_round_trip():
    out = validate_edl(dict(FOLLOW), 10.0).model_dump()
    z = out["effects"]["zooms"][0]
    assert z["mode"] == "follow"
    assert [p["f"] for p in z["path"]] == [0.0, 0.5, 1.0]


def test_follow_needs_two_points():
    bad = {"keep": [[0.0, 10.0]], "effects": {"zooms": [
        {"id": "z", "start": 1.0, "end": 5.0, "mode": "follow",
         "path": [{"f": 0.0, "cx": 0.2, "cy": 0.2}]}]}}
    with pytest.raises(EDLValidationError):
        validate_edl(bad, 10.0)


def test_a_path_on_a_non_follow_zoom_is_refused():
    """Silently ignoring it would leave the agent believing it placed a move
    that never renders."""
    bad = {"keep": [[0.0, 10.0]], "effects": {"zooms": [
        {"id": "z", "start": 1.0, "end": 5.0, "mode": "ease",
         "path": [{"f": 0.0, "cx": 0.2, "cy": 0.2},
                  {"f": 1.0, "cx": 0.8, "cy": 0.8}]}]}}
    with pytest.raises(EDLValidationError):
        validate_edl(bad, 10.0)


def test_path_points_must_ascend():
    bad = {"keep": [[0.0, 10.0]], "effects": {"zooms": [
        {"id": "z", "start": 1.0, "end": 5.0, "mode": "follow",
         "path": [{"f": 0.8, "cx": 0.2, "cy": 0.2},
                  {"f": 0.1, "cx": 0.8, "cy": 0.8}]}]}}
    with pytest.raises(EDLValidationError):
        validate_edl(bad, 10.0)


def _graph(edl_dict, dur=10.0):
    edl = validate_edl(dict(edl_dict), dur).model_dump()
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    index = {"video": {"duration": dur}, "words": [], "sentences": []}
    return renderer.build_filtergraph(edl, dur, True, tl, None, [], index,
                                      preview=False, W=1280, H=720, fps=30.0)


def test_follow_renders_an_interpolated_centre():
    g = _graph(FOLLOW)
    assert "zoompan" in g
    # The x centre must MOVE: a clip() ramp keyed on the first leg's window.
    assert "clip((on/30.000-1.000)/2.000,0,1)" in g, g
    # …and it must be a delta from the opening position, not an absolute set.
    assert "0.6000*clip" in g or "0.600*clip" in g, g
    # The y centre only moves on the second leg, so its ramp starts at 3s.
    assert "clip((on/30.000-3.000)/2.000,0,1)" in g, g


def test_follow_holds_the_centre_outside_its_window():
    """Every other zoom sums onto 0.5; a follow that forgot its between()
    would drag the whole video off-centre for its entire length."""
    g = _graph(FOLLOW)
    assert "between(on/30.000,1.000,5.000)" in g


def test_a_plain_zoom_graph_is_untouched_by_round_45():
    """The follow branch must not have moved a single character of the
    graph any existing EDL produces — those render caches are live."""
    legacy = {"keep": [[0.0, 10.0]],
              "effects": {"zooms": [{"id": "z", "start": 1.0, "end": 4.0,
                                     "strength": 0.3}]}}
    g = _graph(legacy)
    assert "zoompan=z='1+0.30*between(on/30.000,1.000,4.000)'" in g
    assert ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'" in g
    assert "clip(" not in g


def test_follow_is_a_real_signature_change():
    """Adding a path must re-render, never resolve to a cached older look."""
    a = validate_edl({"keep": [[0.0, 10.0]], "effects": {"zooms": [
        {"id": "z", "start": 1.0, "end": 5.0, "strength": 0.4,
         "mode": "ease"}]}}, 10.0).model_dump()
    b = validate_edl(dict(FOLLOW), 10.0).model_dump()
    assert edl_signature(a) != edl_signature(b)


# ── placement maths ────────────────────────────────────────────────────

def test_click_runs_split_on_a_long_gap():
    from agent_tools import _demo_zoom_runs, DEMO_RUN_GAP_S
    clicks = [{"t": 1.0}, {"t": 2.0}, {"t": 2.0 + DEMO_RUN_GAP_S + 1},
              {"t": 2.0 + DEMO_RUN_GAP_S + 2}]
    runs = _demo_zoom_runs(clicks)
    assert [len(r) for r in runs] == [2, 2]


def test_click_runs_stay_together_when_they_are_one_gesture():
    from agent_tools import _demo_zoom_runs
    assert len(_demo_zoom_runs([{"t": 1.0}, {"t": 2.2}, {"t": 3.1}])) == 1


def test_zoom_path_parser_rejects_seconds_pretending_to_be_fractions():
    """f is a fraction of the window. Absolute seconds would clamp to 1.0 and
    collapse the whole move into a step — a wrong render, not an error."""
    from agent_tools import _parse_zoom_path
    pts, err = _parse_zoom_path([{"f": 0.0, "cx": 0.2, "cy": 0.2},
                                 {"f": 3.5, "cx": 0.8, "cy": 0.8}])
    assert pts is None and "FRACTION" in err


def test_zoom_path_parser_pins_the_ends():
    from agent_tools import _parse_zoom_path
    pts, err = _parse_zoom_path([{"f": 0.2, "cx": 0.1, "cy": 0.1},
                                 {"f": 0.7, "cx": 0.9, "cy": 0.9}])
    assert err is None
    assert pts[0]["f"] == 0.0 and pts[-1]["f"] == 1.0


# ── honesty gate ───────────────────────────────────────────────────────

def test_demo_tools_track_browser_availability_exactly():
    """record_website_demo NEEDS the browser; showcase_demo no longer does.

    Round 51 cut showcase_demo loose from this gate on purpose. It used to be
    hidden wherever the recorder was unconfigured, on the reasoning that it
    could only ever act on a capture the recorder made — but the commonest
    input is a screen recording the USER made and uploaded, and on those
    deployments the tool was invisible for exactly the footage it is most
    useful on. It now takes any clip (plus an optional click_times array), so
    the browser's absence has nothing to do with whether it can run.
    """
    import agent_tools
    assert agent_tools._tool_disabled("record_website_demo") is \
        (not webrecord.available())
    assert agent_tools._tool_disabled("showcase_demo") is False
