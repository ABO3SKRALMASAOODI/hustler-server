"""Round 47b — the three ways this product failed one trial user on 2026-07-26.

He asked for cuts on the beat and lyrics. What he got, three messages running,
was "I only reviewed the video — the edit was not changed."

  1. **The turn said nothing at all.** Every failing step came back with
     completion_tokens EXACTLY at the 2000 ceiling, empty content and no tool
     calls: a reasoning model spends the budget deliberating and never reaches
     `content`. The loop read "no tool calls + no text" as "the model chose to
     say nothing" and posted a canned line — at a user who had just typed
     "Well change it". 0 of 703 grok-4.5 calls ever hit that ceiling; 3 of 113
     deepseek-v4-pro calls did, and all 3 killed the turn.
  2. **One click destroyed the beat grid.** His song analysed as "87.7 BPM,
     confidence 0.01, beats: none detected" because a single burst 67 dB above
     the music carried more variance than every real beat, and _acf divides by
     exactly that variance.
  3. **"The beat" was measured off the wrong audio.** beat_align_cuts read the
     FOOTAGE's own transients (a drinks video: 80.7 BPM, confidence 0.15) while
     the song he wanted to cut to sat in the same EDL, never looked at. And
     when he told us the tempo himself — "a beat every 1 second" — there was no
     way to use it.

Run from the worker/ directory.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools                                             # noqa: E402
import config                                                  # noqa: E402
import perception                                              # noqa: E402
from schemas import default_edl                                # noqa: E402

PASS = 0
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


# ── 1. a single outlier must not erase the pulse ───────────────────────

print("== 1. one click must not destroy the beat grid ==")

if not HAVE_FFMPEG:
    print("  -- skipped (no ffmpeg on this machine)")
else:
    _d = tempfile.mkdtemp(prefix="beat_")
    _music = os.path.join(_d, "music.wav")
    _spiked = os.path.join(_d, "spiked.wav")
    # 60 BPM kick + bass, deliberately QUIET — his track sat ~67dB under the
    # burst, so the burst has to dominate by a realistic margin.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "aevalsrc='0.02*exp(-30*mod(t,1))*sin(2*PI*70*t)"
               "+0.004*sin(2*PI*220*t)':d=40:s=44100",
         "-c:a", "pcm_s16le", _music], check=True)
    # the same track with ONE full-scale 60ms burst at 2.2s
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", _music,
         "-f", "lavfi", "-i", "aevalsrc='0.99*sin(2*PI*1000*t)':d=40:s=44100",
         "-filter_complex",
         "[1]atrim=0:0.06,adelay=2200|2200,apad=whole_dur=40[b];"
         "[0][b]amix=inputs=2:duration=first:normalize=0",
         "-c:a", "pcm_s16le", _spiked], check=True)

    _clean = perception.analyze_audio(_music)
    check("a clean 60 BPM track is measured confidently",
          _clean["bpm"] == 60.0 and _clean["bpm_conf"] >= 0.8
          and len(_clean["beats"]) > 30)

    _hit = perception.analyze_audio(_spiked)
    # The regression this pins: pre-fix this was confidence 0.13 / 0 beats.
    check("the SAME track with one huge click still yields the beat grid",
          _hit["bpm"] == 60.0 and _hit["bpm_conf"] >= 0.5
          and len(_hit["beats"]) > 30)
    check("the click does not shift the measured tempo",
          abs(_hit["bpm"] - _clean["bpm"]) < 0.5)

    shutil.rmtree(_d, ignore_errors=True)

check("the perception version was bumped, so stale 'no beats' sidecars "
      "recompute instead of being read back",
      perception.PERCEPTION_VERSION >= 2)


# ── 2. a broken file is NAMED, not silently worked around ──────────────

print("== 2. a flat-lined file is called out ==")

# His actual envelope, from the DB: one burst, then 117s within 1.7dB of a
# level 67dB down. Every fact reported about it was true and useless.
_broken = {"energy_bin_s": 0.5,
           "energy": [-77.1, -77.2, -74.4, -11.8, 0.0] + [-67.2] * 200}
_note = agent_tools._flatline_note(_broken)
check("a one-burst-then-flatline file is reported as BROKEN",
      "BROKEN" in _note and "re-upload" in _note)
check("the agent is told not to beat-match against it",
      "do NOT try to beat-match" in _note)

# Real music must never trip it — dynamics are not a fault.
_real = {"energy_bin_s": 0.5,
         "energy": [-12.0, -3.0, -18.0, -6.0, -25.0, -1.0, -9.0, -30.0,
                    -4.0, -14.0] * 20}
check("ordinary music with real dynamics is NOT called broken",
      agent_tools._flatline_note(_real) == "")
# ...nor is a quiet-but-alive recording.
_quiet = {"energy_bin_s": 0.5,
          "energy": [-46.0, -38.0, -52.0, -41.0, -35.0, -49.0] * 30}
check("a quiet recording with dynamics is NOT called broken",
      agent_tools._flatline_note(_quiet) == "")


# ── 3. "cut to the beat" means the beat of the MUSIC ───────────────────

print("== 3. beat_align_cuts reads the song, and takes a stated tempo ==")


class FakeCtx:
    def __init__(self, edl, duration=43.8, words=None):
        self.duration = duration
        self.has_main_video = True
        self.index = {"video": {"duration": duration}, "words": words or [],
                      "sentences": []}
        self._edl = edl
        self._asset_perception = {}
        self.written = []
        self.versions_written = []
        self.db = None
        self.project_id = 1

    def latest_edl(self):
        return {"version": 1 + len(self.written), "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = edl
        self.written.append(desc)
        return f"EDL v1 -> v2: {desc}"

    def clamp(self, t):
        return max(0.0, min(float(t), self.duration))


def _edl_with_cuts():
    """Five kept spans whose junctions land 0.1-0.15s OFF a 1-second grid in
    PROGRAM time — 4.85, 9.9, 14.85, 19.9. The gaps are deliberately NOT round
    numbers, so a program-time fix and a source-time fix give different answers
    and the test can tell them apart."""
    e = default_edl(43.8)
    e["keep"] = [[0.0, 4.85], [6.3, 11.2], [12.15, 17.0], [18.4, 23.3],
                 [24.7, 43.3]]
    e["music"] = [{"id": "mus1", "storage_key": "music/1/song.mp3",
                   "start": 0.0, "end": 43.3, "gain_db": -4.0,
                   "duck": False, "loop": True}]
    return e


# The user's own words: "there is a beat every 1 second".
ctx = FakeCtx(_edl_with_cuts())
res = agent_tools.beat_align_cuts(ctx, tolerance_s=0.35, every_s=1.0)
check("a tempo the USER stated is accepted, not refused",
      res.startswith("EDL v"))
check("all four internal cuts moved", "beat-aligned 4 cuts" in ctx.written[0])

# The real check: PROGRAM time, not source time. A junction is heard where the
# footage before it ends, so moving one shifts every later one — walk them
# through the same Timeline the renderer uses.
from timeline import Timeline                                  # noqa: E402
_tl = Timeline(ctx._edl["keep"], [], [])
_offsets = [_tl.src_to_out(s[1]) for s in ctx._edl["keep"][:-1]]
check("each junction is HEARD on the 1-second grid",
      _offsets and all(p is not None and abs(p - round(p)) < 0.03
                       for p in _offsets))
check("aligning in program time is not the same as aligning source times "
      "(the source ends are NOT round numbers)",
      any(abs(s[1] - round(s[1])) > 0.05 for s in ctx._edl["keep"][:-1]))

# bpm and every_s are the same statement.
ctx2 = FakeCtx(_edl_with_cuts())
res2 = agent_tools.beat_align_cuts(ctx2, bpm=60)
check("bpm=60 is accepted the same as every_s=1",
      res2.startswith("EDL v")
      and [s[1] for s in ctx2._edl["keep"]] == [s[1] for s in ctx._edl["keep"]])

# An absurd tempo is still refused — accepting the user's number is not the
# same as accepting any number.
check("a nonsense tempo is rejected with the units spelled out",
      agent_tools.beat_align_cuts(FakeCtx(_edl_with_cuts()),
                                  every_s=0.001).startswith("REJECTED"))

# No music, no stated tempo, no pulse in the footage: still an honest no —
# but one that names both real routes instead of dead-ending.
_no_music = _edl_with_cuts()
_no_music["music"] = []


class BlindCtx(FakeCtx):
    pass


def _no_perception(_ctx):
    raise perception.PerceptionError("no index row for this video")


_real_get = agent_tools._get_perception
agent_tools._get_perception = _no_perception
try:
    r = agent_tools.beat_align_cuts(BlindCtx(_no_music))
    check("with nothing to cut to it refuses rather than inventing a pulse",
          "unavailable" in r or r.startswith("REJECTED"))
finally:
    agent_tools._get_perception = _real_get

# A cut may never be dragged over the gap after it: that would silently
# restore footage the user removed.
_tight = _edl_with_cuts()
_tight["keep"] = [[0.0, 4.95], [5.0, 43.3]]     # only 0.05s of gap
ctx3 = FakeCtx(_tight)
agent_tools.beat_align_cuts(ctx3, tolerance_s=1.0, every_s=1.0)
check("a move that would close a cut gap is skipped",
      ctx3._edl["keep"][0][1] <= ctx3._edl["keep"][1][0] - 0.1
      or not ctx3.written)


# ── 4. the truncation guard is wired ───────────────────────────────────

print("== 4. an empty completion at the ceiling is not an answer ==")

check("the agent's completion ceiling is no longer the hardcoded 2000",
      config.AGENT_MAX_TOKENS >= 8000
      and config.AGENT_MAX_TOKENS_CEILING > config.AGENT_MAX_TOKENS)
check("the honesty redraft gets room to think too",
      config.AGENT_REPLY_MAX_TOKENS >= 2000)

import agent_loop                                              # noqa: E402

check("the retry nudge tells the model to act, not to plan again",
      "ACT NOW" in agent_loop._TRUNCATED_NUDGE)
_src = open(os.path.join(os.path.dirname(__file__), "..",
                         "agent_loop.py")).read()
check("truncation is distinguished from a deliberate empty reply",
      "finish == \"length\"" in _src and "truncated_out" in _src)
check("a truncated turn is marked unbillable",
      '"billable"' in _src)
_completion = open(os.path.join(os.path.dirname(__file__), "..",
                                "job_completion.py")).read()
check("and the charge site honours that",
      'result.get("billable", True)' in _completion)

print(f"\n{PASS} checks passed.")
