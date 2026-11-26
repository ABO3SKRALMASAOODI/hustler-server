"""p6311 "remove the camera shake" ended cannot_tell with no step: the only
stabilizer was a kind of add_stylize that nothing about shake names. #935
"put memes in it" was skipped: no tool name said meme and find_footage's
guidance steers away from meme frames. Both now exist under the names the
asks point to, and both appear in the agent's capability directory.
"""
import shutil
import subprocess

import pytest

import agent_tools
import schemas
import song_find

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg")


class _Ctx:
    def __init__(self, d, src, duration=6.0):
        self.workdir = d
        self.duration = duration
        self.has_main_video = True
        self.project_id = 1
        self.job = {"user_id": 1}
        self._src = src
        self._edl = {"version": 3, "json": schemas.default_edl(duration)}
        self.written = None
        self.pending_images = []

    def proxy_path(self):
        return self._src

    def latest_edl(self):
        return self._edl

    def write_edl(self, edl, desc):
        self.written = schemas.validate_edl(edl, self.duration).model_dump()
        self._edl = {"version": self._edl["version"] + 1,
                     "json": self.written}
        return f"EDL v3 -> v4: {desc}"


def _clip(path, vf, dur=6.0):
    """A still scene (one testsrc2 frame, so nothing in it moves on its own)
    filmed by a camera that does what ``vf`` says."""
    still = path + ".png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=400x400:r=25:d=1", "-frames:v", "1", still],
                   check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-r", "25", "-t",
         f"{dur}", "-i", still, "-vf", vf + ",noise=alls=3:allf=t",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", path],
        check=True)
    return path


def test_the_directory_names_key_shake_and_meme_tools():
    d = agent_tools.capability_directory()
    for name in ("chroma_key", "stabilize_video", "find_meme"):
        assert name in agent_tools.TOOLS
        if name == "find_meme" and agent_tools._tool_disabled(name):
            continue
        assert name in d, name


@needs_ffmpeg
def test_stabilize_video_writes_the_pass_and_measures_less_jitter(tmp_path):
    from tools_ext import steady_tools
    src = _clip(str(tmp_path / "shaky.mp4"),
                "crop=340:340:30+12*sin(n*1.7)+8*sin(n*0.9):"
                "30+10*cos(n*1.3)+6*sin(n*2.3)")
    ctx = _Ctx(str(tmp_path), src)
    out = steady_tools.stabilize_video(ctx, 0.6)
    assert out.startswith("EDL v"), out
    sts = ctx.written["effects"]["stylize"]
    assert [s["kind"] for s in sts] == ["stabilize"]
    import re
    b, a = (float(x) for x in re.search(
        r"jitter ([0-9.]+) px per frame before, ([0-9.]+) px after",
        out).groups())
    assert b > 5 and a < b / 2, out
    again = steady_tools.stabilize_video(ctx, 0.8)
    assert again.startswith("EDL v")
    sts = ctx.written["effects"]["stylize"]
    assert len(sts) == 1 and sts[0]["intensity"] == 0.8


@needs_ffmpeg
def test_stabilize_video_says_when_the_camera_already_holds_still(tmp_path):
    from tools_ext import steady_tools
    src = _clip(str(tmp_path / "still.mp4"), "crop=340:340:30:30")
    ctx = _Ctx(str(tmp_path), src)
    out = steady_tools.stabilize_video(ctx)
    # review of 26 Nov: the pass used to be written anyway, adding render
    # time and mirrored-edge wobble to footage that holds still (p6311)
    assert out.startswith("NO CHANGE"), out
    assert "no measurable camera shake" in out
    assert ctx.written is None


def test_find_meme_ranks_short_clips_and_hands_over_the_insert(monkeypatch):
    from tools_ext import steady_tools
    monkeypatch.setattr(song_find, "footage_available", lambda: True)
    seen = {}

    def search(ctx, q, mode):
        seen["q"], seen["mode"] = q, mode
        return [{"title": "1 hour of memes", "duration_s": 3600,
                 "url": "https://youtu.be/long", "uploader": "x"},
                {"title": "surprised pikachu", "duration_s": 6,
                 "url": "https://youtu.be/short", "uploader": "y"}]
    monkeypatch.setattr(agent_tools, "_search_named_youtube", search)
    monkeypatch.setattr(agent_tools, "_queue_candidate_thumbs",
                        lambda ctx, hits, limit=5: 0)
    out = steady_tools.find_meme(_Ctx("/tmp", None), "surprised pikachu")
    assert seen == {"q": "surprised pikachu meme", "mode": "footage"}
    assert out.index("youtu.be/short") < out.index("youtu.be/long")
    assert "fetch_url" in out and "insert" in out


def test_reaction_clips_are_not_ranked_down_in_a_meme_search():
    # #935 review: rank_footage marks 'reaction' titles down, and the meme
    # ranker kept that order within a length class.
    hits = [{"title": "cat jumps meme", "duration_s": 9, "search_rank": 1},
            {"title": "shocked reaction meme", "duration_s": 7,
             "search_rank": 0},
            {"title": "meme compilation", "duration_s": 900,
             "search_rank": 2}]
    ranked = song_find.rank_footage(hits, "shocked meme")
    assert ranked[0]["title"] == "cat jumps meme"      # the footage rule
    got = song_find.rank_memes(ranked)
    assert [h["search_rank"] for h in got] == [0, 1, 2]


def test_find_meme_is_off_with_the_footage_search(monkeypatch):
    monkeypatch.setattr(song_find, "footage_available", lambda: False)
    assert agent_tools._tool_disabled("find_meme")
