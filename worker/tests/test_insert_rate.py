"""Round 76 — a spliced scene can play FASTER (or slower) in place.

"Perhaps dont shorten the editing timeline screens but speed them up": the
launch video carries multi-minute screen recordings whose every second
matters as CONTENT but not as DURATION. Cutting them loses the story;
speed lives on InsertItem.rate — duration_s stays the OUTPUT length of the
block, the clip consumes duration_s*rate of source, video via setpts,
audio pitch-corrected via atempo. rate None renders byte-identically to
the legacy strings, so every cached render and pinned-filtergraph test
still holds.

Run:  python -m pytest tests/test_insert_rate.py -q     (from worker/)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
from renderer import build_filtergraph                        # noqa: E402
from schemas import default_edl, validate_edl                 # noqa: E402
from timeline import Timeline, program_blocks                 # noqa: E402

INDEX = {"words": [], "silences": [], "shots": [], "sentences": []}
SRC = 354.6
REC = "clips/1/rec.mov"


def _graph(edl, insert_inputs):
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return build_filtergraph(edl, SRC, True, tl, None, [], INDEX, False,
                             W=1280, H=720, fps=30.0, frame_mode=None,
                             insert_inputs=insert_inputs,
                             src_w=1280, src_h=720)


def _ins(rate=None, dur=5.0, off=4.0):
    it = {"id": "ins1", "kind": "video", "motion": None, "asset_key": REC,
          "at_output_s": 10.0, "duration_s": dur, "source_start_s": off}
    if rate is not None:
        it["rate"] = rate
    return it


def _edl(ins):
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    e["inserts"] = [ins]
    return validate_edl(e, SRC).model_dump()


def test_rated_insert_consumes_more_clip_per_output_second():
    ins = _ins(rate=2.0)
    g = _graph(_edl(ins), [(1, ins, True)])
    assert "trim=start=4.000:end=14.000" in g       # 5s block x 2 = 10s clip
    assert "setpts=(PTS-STARTPTS)/2.0000" in g
    assert "atempo=2.0000" in g
    assert "apad=whole_dur=5.000" in g              # the block stays 5s OUT


def test_unrated_insert_emits_the_exact_legacy_strings():
    ins = _ins()
    g = _graph(_edl(ins), [(1, ins, True)])
    assert "trim=start=4.000:end=9.000,setpts=PTS-STARTPTS[insv0]" in g
    assert "atempo" not in g


def test_slowmo_chains_two_atempo_stages():
    ins = _ins(rate=0.4, dur=2.0)
    g = _graph(_edl(ins), [(1, ins, True)])
    assert "trim=start=4.000:end=4.800" in g        # 2s block x 0.4
    assert "atempo=0.5,atempo=0.8000" in g


def test_program_blocks_carries_rate():
    b = program_blocks(_edl(_ins(rate=1.6)))[-1]
    assert b["kind"] == "insert" and abs(b["rate"] - 1.6) < 1e-9
    b0 = program_blocks(_edl(_ins()))[-1]
    assert b0["rate"] == 1.0


# ------------------------------------------------ the tool semantics ----

class _DB:
    def __init__(self, assets):
        self.assets = assets

    def run(self, fn, *a):
        name = getattr(fn, "__name__", "")
        if name == "asset_by_key":
            return self.assets.get(a[1])
        if name == "assets_by_kinds":
            return list(self.assets.values())
        return None


class _Ctx:
    def __init__(self, edl, duration=SRC):
        self.project_id = 1
        self.duration = duration
        self.index = {"video": {"duration": duration, "width": 3840,
                                "height": 2160, "fps": 30},
                      "words": [], "sentences": []}
        self.has_main_video = True
        self.workdir = tempfile.mkdtemp(prefix="rate_")
        self.db = _DB({REC: {"id": 1, "kind": "video_clip",
                             "storage_key": REC, "duration_s": 154.9,
                             "meta": {"filename": "rec.mov"}}})
        self.written = []
        self._edl = validate_edl(edl, duration).model_dump()

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


def test_rate_alone_speeds_the_scene_in_place():
    """'Make that 10s screen recording take 5' — same clip window, half the
    block: nothing cut, and the reply says the tempo."""
    e = default_edl(SRC)
    e["keep"] = [[113.7, 117.25]]
    e["inserts"] = [{"id": "ins6", "kind": "video", "asset_key": REC,
                     "at_output_s": 3.55, "duration_s": 10.0,
                     "source_start_s": 5.94}]
    ctx = _Ctx(e)
    res = agent_tools.set_insert_window(ctx, "ins6", rate=2.0)
    assert res.startswith("EDL v"), res
    it = ctx.latest_edl()["json"]["inserts"][0]
    assert it["duration_s"] == 5.0 and it["rate"] == 2.0
    assert it["source_start_s"] == 5.94              # window untouched
    assert "at 2x" in res
    # back to normal speed: the block grows back, the key drops
    agent_tools.set_insert_window(ctx, "ins6", rate=1.0)
    it = ctx.latest_edl()["json"]["inserts"][0]
    assert it["duration_s"] == 10.0 and it.get("rate") is None


def test_rate_with_duration_consumes_rate_times_clip():
    e = default_edl(SRC)
    e["keep"] = [[113.7, 117.25]]
    e["inserts"] = [{"id": "ins6", "kind": "video", "asset_key": REC,
                     "at_output_s": 3.55, "duration_s": 10.0,
                     "source_start_s": 140.0}]
    ctx = _Ctx(e)
    # 140 + 6*2 = 152 <= 154.9 fits; 140 + 10*2 would not — the block caps
    # to the clip: (154.9 - 140) / 2 = 7.45
    res = agent_tools.set_insert_window(ctx, "ins6", duration_s=6.0, rate=2.0)
    assert ctx.latest_edl()["json"]["inserts"][0]["duration_s"] == 6.0
    res2 = agent_tools.set_insert_window(ctx, "ins6", duration_s=10.0,
                                         rate=2.0)
    assert ctx.latest_edl()["json"]["inserts"][0]["duration_s"] == 7.45
    assert res.startswith("EDL v") and res2.startswith("EDL v")


def test_cut_output_range_splits_a_rated_insert_in_clip_time():
    """The tail of a split rated insert starts rate x deeper into the clip —
    scaled wrong, the cut jumps the wrong footage."""
    e = default_edl(SRC)
    e["keep"] = [[113.7, 117.25]]                    # 3.55s of footage
    e["inserts"] = [{"id": "ins6", "kind": "video", "asset_key": REC,
                     "at_output_s": 3.55, "duration_s": 10.0,
                     "source_start_s": 20.0, "rate": 2.0}]
    ctx = _Ctx(e)
    res = agent_tools.cut_output_range(ctx, 5.55, 7.55)
    assert res.startswith("EDL v"), res
    ins = ctx.latest_edl()["json"]["inserts"]
    assert len(ins) == 2
    head, tail = ins
    assert head["duration_s"] == 2.0                 # out 3.55-5.55
    assert tail["duration_s"] == 6.0                 # out 7.55-13.55 (was)
    # tail clip offset: 20 + (7.55-3.55)*2 = 28, NOT 24
    assert abs(tail["source_start_s"] - 28.0) < 0.02
    assert tail.get("rate") == 2.0                   # the tempo survives
