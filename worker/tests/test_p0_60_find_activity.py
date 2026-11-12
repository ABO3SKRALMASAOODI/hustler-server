"""P0 #60: "cut the space between serves" on a long wordless match cuts.

Project 5592, 9-10 Nov 2026: a 45-minute volleyball match (one locked-off
camera). Five requests ended in a question and no cut: the agent's only
picture search sampled one frame every ~19 s and it asked the customer for
timestamps. find_activity measures motion and sound hits over every frame
and returns the active stretches and a keep list, so the agent cuts.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np                                              # noqa: E402
import pytest                                                   # noqa: E402

import activity                                                 # noqa: E402
import agent_tools                                              # noqa: E402

# a match: rallies (play) separated by dead time, in source seconds
RALLIES = [(6.0, 14.0), (26.0, 33.0), (47.0, 58.0)]
DURATION = 66.0


def _match_clip(path):
    """Still court between rallies (a little sensor noise, a walker's worth
    of drift); a moving picture and ball-hit clicks during each rally."""
    still = "color=c=0x8a3b2e:s=320x180:r=25:d=%g" % DURATION
    busy = "testsrc2=s=320x180:r=25:d=%g" % DURATION
    on = "+".join(f"between(t,{a},{b})" for a, b in RALLIES)
    clicks = ("aevalsrc='0.002*random(0)+(" + on + ")*0.8*exp(-60*mod(t,0.9))"
              "*sin(2*PI*900*t)':d=%g:s=16000" % DURATION)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", still,
         "-f", "lavfi", "-i", busy, "-f", "lavfi", "-i", clicks,
         "-filter_complex",
         f"[0][1]overlay=enable='{on}',noise=alls=4:allf=t[v]",
         "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         path], check=True)


@pytest.fixture(scope="module")
def match(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("p60") / "match.mp4")
    _match_clip(path)
    return path


def _covers(stretches, a, b, slack=1.5):
    return any(s <= a + slack and e >= b - slack for s, e, *_ in stretches)


def test_the_rallies_are_measured_and_the_dead_time_is_idle(match):
    track = activity.measure(match)
    assert track["audio"] is not None and len(track["motion"]) > 200
    seg = activity.segment(track)
    assert "motion" in seg["basis"] and "sound" in seg["basis"]
    assert len(seg["active"]) == len(RALLIES)
    for a, b in RALLIES:
        assert _covers(seg["active"], a, b)
    # the space between serves is idle
    for (_, b1), (a2, _) in zip(RALLIES, RALLIES[1:]):
        assert _covers(seg["idle"], b1, a2)


def test_picture_alone_still_finds_the_rallies(match):
    track = activity.measure(match)
    track["audio"] = None
    seg = activity.segment(track)
    assert seg["basis"] == ["motion"]
    assert len(seg["active"]) == len(RALLIES)


def test_a_short_pause_inside_play_does_not_split_it():
    fps = activity.ACTIVITY_FPS
    motion = np.full(int(60 * fps), 0.01, dtype=np.float32)
    motion[int(10 * fps):int(30 * fps)] = 0.3
    motion[int(19 * fps):int(21 * fps)] = 0.01        # a 2 s lull mid-rally
    seg = activity.segment({"fps": fps, "motion": list(motion),
                            "audio": None}, min_idle_s=4.0)
    assert len(seg["active"]) == 1


class _Ctx:
    def __init__(self, proxy, workdir):
        self._proxy, self.workdir = proxy, workdir
        self.job, self.db, self.project_id = {}, None, None
        self.editing_metrics = {}

    def proxy_path(self):
        return self._proxy


def test_the_tool_returns_a_keep_list_over_the_rallies(match, tmp_path):
    out = agent_tools.find_activity(_Ctx(match, str(tmp_path)))
    assert out.startswith("ACTIVITY") and "KEEP LIST" in out
    keep = json.loads(out.split("KEEP LIST", 1)[1].split(": ", 1)[1]
                      .split("\n", 1)[0])
    assert len(keep) == len(RALLIES)
    kept = sum(b - a for a, b in keep)
    assert kept < 0.75 * DURATION          # the dead time is cut
    for a, b in RALLIES:
        assert any(s <= a and e >= b for s, e in keep)


def test_the_agent_can_reach_it():
    assert "find_activity" in agent_tools.TOOLS
    assert "find_activity" in agent_tools.TOOL_DOMAINS["direction"]
    assert "find_activity" in agent_tools.capability_directory()
    import agent_prompt
    assert "find_activity" in agent_prompt.__dict__.get(
        "SYSTEM_PROMPT", "") or "find_activity" in open(
        agent_prompt.__file__).read()


def _rhythm(fps, seconds=300, period=9.25, rally=4.5, seed=7):
    """The measured shape of p5592: a rally burst every ~9 s over a busy
    floor of people repositioning, with short dips between serves, and a
    long side-switch break in the middle."""
    rng = np.random.default_rng(seed)
    n = int(seconds * fps)
    t = np.arange(n) / fps
    motion = 0.04 + 0.01 * rng.standard_normal(n)
    starts = []
    s = 5.0
    while s + rally < seconds:
        if not 140 <= s <= 190:            # the side switch: nobody plays
            motion[(t >= s) & (t < s + rally)] += 0.06
            starts.append(s)
        s += period
    motion[(t >= 140) & (t < 190)] = 0.015 + 0.004 * rng.standard_normal(
        int(((t >= 140) & (t < 190)).sum()))
    return np.clip(motion, 0, 1).astype(np.float32), starts


def test_each_rally_between_short_pauses_is_its_own_stretch():
    fps = activity.ACTIVITY_FPS
    motion, starts = _rhythm(fps)
    seg = activity.segment({"fps": fps, "motion": list(motion),
                            "audio": None})
    # one global split merged runs of rallies (the first p5592 measurement
    # found 123 stretches for ~295 rallies); every rally is found apart
    assert abs(len(seg["active"]) - len(starts)) <= 2
    assert _covers(seg["idle"], 141.0, 189.0)      # the break stays idle


def test_sound_that_does_not_follow_the_picture_is_left_out():
    fps = activity.ACTIVITY_FPS
    motion, _ = _rhythm(fps)
    rng = np.random.default_rng(3)
    gym = list(np.abs(rng.normal(5, 3, motion.size)))  # everyone's noise
    _s, _f, used = activity.score({"fps": fps, "motion": list(motion),
                                   "audio": gym})
    assert used == ["motion"]
