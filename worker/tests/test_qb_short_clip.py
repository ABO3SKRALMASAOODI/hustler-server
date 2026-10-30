"""QB (30 Oct 2026): a short silent clip gets a camera that finds its subject.

Both real clips of that kind (a 17 s tripod walk toward the camera from a
speck, a 15 s mirror clip of a physique) scored 5.75, "raw footage with a
title": the judges asked for a crop on the subject, a move that follows the
action and a moving close ending. Engine accounts only (trial and new
subscribers); a grandfathered payer's First cut is unchanged.
"""
from types import SimpleNamespace as NS

import agent_tools
import first_cut
import short_clip

# the model's boxes on the walk (fractions): a speck that grows as he nears
WALK = [(1.4, (0.41, 0.50, 0.52, 0.66)), (4.3, (0.44, 0.50, 0.56, 0.67)),
        (7.2, (0.37, 0.50, 0.51, 0.71)), (10.1, (0.34, 0.49, 0.51, 0.77)),
        (13.0, (0.25, 0.49, 0.48, 0.85)), (15.9, (0.32, 0.37, 0.68, 1.0))]
# the mirror clip: a physique about a quarter of the frame, low and left
MIRROR = [(1.2, (0.02, 0.46, 0.40, 0.71)), (3.7, (0.07, 0.48, 0.43, 0.71)),
          (6.2, (0.13, 0.47, 0.33, 0.71)), (8.7, (0.10, 0.47, 0.46, 0.71)),
          (11.2, (0.06, 0.48, 0.30, 0.71)), (13.7, (0.07, 0.48, 0.39, 0.71))]


def _checks(keys, prog):
    ts = [k["t"] for k in keys]
    assert ts == sorted(ts) and ts[0] == 0.0 and ts[-1] == round(prog, 2)
    assert keys[0]["strength"] == 0.0 and keys[1]["strength"] == 0.0
    for k in keys:
        x0, y0, x1, y1 = k["rect"]
        assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0
        assert 0.0 <= k["strength"] <= short_clip.Z_MAX + 0.15 - 1.0 + 1e-6


def test_a_speck_gets_the_deepest_push_and_a_close_subject_none():
    keys = short_clip.keyframes(WALK, 17.3)
    _checks(keys, 17.3)
    # the camera starts easing in under the title, not before it
    assert keys[1]["t"] == short_clip.START_S
    deep = max(k["strength"] for k in keys[:5])
    assert deep >= 0.8                      # 1.8x on a man who is a speck
    # he fills the frame by the end: the push has let go, then a little more
    assert keys[-2]["strength"] < deep
    assert keys[-1]["strength"] > keys[-2]["strength"]       # live to the end
    # the window follows him from the middle of the frame to the left
    assert keys[3]["rect"][0] >= keys[-2]["rect"][0] - 1e-6


def test_the_mirror_clip_is_framed_on_the_physique():
    keys = short_clip.keyframes(MIRROR, 15.1)
    _checks(keys, 15.1)
    mid = keys[len(keys) // 2]
    x0, y0, x1, y1 = mid["rect"]
    assert mid["strength"] >= 0.55
    # the subject (y 0.47-0.71, x up to 0.46) is inside the window
    assert y0 <= 0.47 and y1 >= 0.71 and x0 <= 0.07 and x1 >= 0.40


def test_no_looks_or_too_few_means_no_camera():
    assert short_clip.keyframes([], 15.0) == []
    assert short_clip.keyframes(WALK[:2], 15.0) == []
    assert short_clip.keyframes(WALK, 2.0) == []
    # a subject that already fills the frame needs no push
    big = [(t, (0.05, 0.05, 0.95, 0.95)) for t in (1, 4, 7, 10, 13)]
    assert short_clip.keyframes(big, 15.0) == [] or all(
        k["strength"] <= 0.25 for k in short_clip.keyframes(big, 15.0))


def test_the_models_pixel_boxes_become_fractions_and_nonsense_goes():
    sizes = {1: (512, 910), 2: (512, 910), 3: (512, 910)}
    text = ('x {"boxes": [{"n": 1, "box": [212, 455, 264, 602]}, '
            '{"n": 2, "box": [0, 0, 512, 910]}, {"n": 3, "box": "none"}, '
            '{"n": 9, "box": [1, 2, 3, 4]}]}')
    got = short_clip.parse_boxes(text, sizes)
    assert list(got) == [1]
    assert abs(got[1][0] - 212 / 512) < 1e-6 and abs(got[1][3] - 602 / 910) < 1e-6
    assert short_clip.parse_boxes("no json", sizes) == {}


def test_no_lane_no_camera(monkeypatch):
    import claude_lane
    monkeypatch.setattr(claude_lane, "available", lambda: False)
    ctx = NS(first_cut_model="claude-haiku-5-5", proxy_path=lambda: "x.mp4")
    assert short_clip.look(ctx, 0.0, 15.0, {"usd": 0.0}) == []
    ctx = NS(first_cut_model=None)
    assert short_clip.look(ctx, 0.0, 15.0, {"usd": 0.0}) == []


# ---- the First cut's gate ------------------------------------------------

class _Ctx:
    def __init__(self, q1, width=1080, height=1920, duration=15.0):
        self.index = {"video": {"duration": duration, "width": width,
                                "height": height},
                      "words": [], "sentences": []}
        self.duration = duration
        self.first_cut_q1 = q1
        self.versions_written = []
        self.web_recordings = []
        self.last_preview = None


def _run(monkeypatch, ctx):
    calls = []

    def execute(c, name, args):
        calls.append(name)
        c.versions_written.append(len(c.versions_written) + 2)
        return f"EDL v{len(c.versions_written) + 1} ok"
    monkeypatch.setattr(agent_tools, "execute", execute)
    monkeypatch.setattr(first_cut, "montage_scout",
                        lambda c, w, s, title_only=False: (w, "Mirror *flex*"))
    seen = []
    monkeypatch.setattr(short_clip, "camera",
                        lambda c, st, started, run, spend, samples=None:
                        seen.append(1) or "EDL v9 -> v10: add_zoom_path")
    monkeypatch.setattr(short_clip, "whole_looks",
                        lambda c, spend: [(1.0, (0.1, 0.5, 0.4, 0.7))] * 3)
    import time
    _s, applied, _p = first_cut.video_chain(ctx, None, time.monotonic(),
                                            {"usd": 0.0})
    return seen, applied


def test_an_engine_account_gets_the_camera_on_a_short_silent_vertical_clip(
        monkeypatch):
    seen, applied = _run(monkeypatch, _Ctx(q1=True))
    assert seen and "a camera that follows the subject" in applied
    assert "a hook title" in applied


def test_a_grandfathered_payer_and_a_landscape_clip_do_not(monkeypatch):
    seen, applied = _run(monkeypatch, _Ctx(q1=False))
    assert not seen and "a camera that follows the subject" not in applied
    seen, _a = _run(monkeypatch, _Ctx(q1=True, width=1920, height=1080))
    assert not seen


def test_the_title_never_sits_across_the_subject():
    low = [(1.2, (0.02, 0.46, 0.40, 0.71)), (3.0, (0.07, 0.48, 0.43, 0.71))]
    high = [(1.0, (0.3, 0.05, 0.7, 0.30)), (3.0, (0.3, 0.06, 0.7, 0.32))]
    assert short_clip.title_y(low) == short_clip.TITLE_ABOVE_Y
    assert short_clip.title_y(high) == short_clip.TITLE_BELOW_Y
    assert short_clip.title_y([]) is None
    # a subject across most of the height leaves no band: the default
    tall = [(1.0, (0.2, 0.18, 0.8, 0.95))]
    assert short_clip.title_y(tall) is None
    # never in the brand mark's band (y 0.115-0.192)
    assert short_clip.TITLE_ABOVE_Y > 0.2


def test_the_last_frame_is_looked_at_so_the_camera_follows_him_out(monkeypatch):
    import claude_lane
    import subprocess
    seen = []

    class R:
        def __init__(self):
            self.stdout = b"\xff\xd8\xff\xc0\x00\x11\x08\x03\xc0\x02\x00\x03" + b"\x00" * 40
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: (
        seen.append(float(cmd[cmd.index("-ss") + 1])) or R()))

    def create(model, messages, **k):
        raise RuntimeError("stop after the frames")
    monkeypatch.setattr(claude_lane, "create", create)
    ctx = NS(first_cut_model="m", proxy_path=lambda: "x.mp4")
    assert short_clip.look(ctx, 0.0, 17.3, {"usd": 0.0}) == []
    assert len(seen) == short_clip.LOOKS + 1
    assert seen[-1] >= 17.0 and seen[-1] < 17.3        # the very end
