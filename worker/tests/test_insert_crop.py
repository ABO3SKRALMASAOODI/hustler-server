"""Round 77 — a spliced scene can show ONE REGION of its clip, letterboxed.

"I want the full timeline to be visible in a static window" is impossible
for a zoom: the timeline strip is ~2.6:1 and a 16:9 viewport wide enough to
span it must also span the video player above it. The launch video shipped
a PAN as a workaround and the user called it "scanning". The honest answer
is a crop on the INSERT — the scene becomes the strip, letterboxed to full
width, camera static.

crop None renders byte-identically to the legacy chain (cached renders and
pinned-filtergraph tests hold).

Run:  python -m pytest tests/test_insert_crop.py -q     (from worker/)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
import pytest                                                 # noqa: E402
from renderer import build_filtergraph                        # noqa: E402
from schemas import default_edl, validate_edl                 # noqa: E402
from timeline import (Timeline, describe_program,             # noqa: E402
                      program_blocks)

INDEX = {"words": [], "silences": [], "shots": [], "sentences": []}
SRC = 354.6
REC = "clips/1/rec.mov"
STRIP = [0.27, 0.51, 0.99, 1.0]


def _graph(edl, insert_inputs):
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return build_filtergraph(edl, SRC, True, tl, None, [], INDEX, False,
                             W=1280, H=720, fps=30.0, frame_mode=None,
                             insert_inputs=insert_inputs,
                             src_w=1280, src_h=720)


def _ins(crop=None, rate=None, dur=5.0, off=4.0):
    it = {"id": "ins1", "kind": "video", "motion": None, "asset_key": REC,
          "at_output_s": 10.0, "duration_s": dur, "source_start_s": off}
    if crop is not None:
        it["crop"] = crop
    if rate is not None:
        it["rate"] = rate
    return it


def _edl(ins):
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    e["inserts"] = [ins]
    return validate_edl(e, SRC).model_dump()


def test_cropped_insert_cuts_the_region_and_letterboxes():
    ins = _ins(crop=STRIP)
    g = _graph(_edl(ins), [(1, ins, True)])
    assert ("crop=trunc(iw*0.7200/2)*2:trunc(ih*0.4900/2)*2"
            ":trunc(iw*0.2700/2)*2:trunc(ih*0.5100/2)*2[insvc0]") in g
    # normalize runs in PAD mode on the cropped strip: letterbox, never a
    # cover-crop that would undo the region choice
    assert "[insvc0]" in g and "force_original_aspect_ratio=decrease" in g


def test_uncropped_insert_emits_the_exact_legacy_strings():
    ins = _ins()
    g = _graph(_edl(ins), [(1, ins, True)])
    assert "trim=start=4.000:end=9.000,setpts=PTS-STARTPTS[insv0]" in g
    assert "insvc" not in g and "crop=trunc" not in g


def test_crop_composes_with_rate():
    ins = _ins(crop=STRIP, rate=2.0)
    g = _graph(_edl(ins), [(1, ins, True)])
    assert "trim=start=4.000:end=14.000" in g       # 5s block x 2 = 10s clip
    assert "setpts=(PTS-STARTPTS)/2.0000" in g
    assert "[insvc0]" in g and "atempo=2.0000" in g


def test_schema_validates_and_clamps_crop():
    e = _edl(_ins(crop=[0.27, 0.51, 1.2, 1.0]))     # x1 clamps to 1.0
    assert e["inserts"][0]["crop"] == [0.27, 0.51, 1.0, 1.0]
    with pytest.raises(Exception):
        validate_edl(_edl(_ins(crop=[0.2, 0.5, 0.25, 1.0])), SRC)


def test_program_blocks_and_scene_map_carry_crop():
    e = _edl(_ins(crop=STRIP))
    b = program_blocks(e)[-1]
    assert b["kind"] == "insert" and b["crop"] == STRIP
    txt = describe_program(e)
    assert "showing only region x0.27-0.99 y0.51-1" in txt
    assert "letterboxed" in txt
    e0 = _edl(_ins())
    assert program_blocks(e0)[-1]["crop"] is None
    assert "region" not in describe_program(e0)


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
        self.workdir = tempfile.mkdtemp(prefix="crop_")
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


def _studio():
    e = default_edl(SRC)
    e["keep"] = [[113.7, 117.25]]
    e["inserts"] = [{"id": "ins6", "kind": "video", "asset_key": REC,
                     "at_output_s": 3.55, "duration_s": 4.88,
                     "source_start_s": 5.94, "rate": 1.6}]
    return e


def test_set_crop_as_a_list_and_clear_with_full():
    ctx = _Ctx(_studio())
    res = agent_tools.set_insert_window(ctx, "ins6", crop=STRIP)
    assert res.startswith("EDL v"), res
    it = ctx.latest_edl()["json"]["inserts"][0]
    assert it["crop"] == STRIP
    assert it["rate"] == 1.6 and it["duration_s"] == 4.88   # untouched
    assert "showing ONLY region x0.27-0.99 y0.51-1" in res
    assert "black bars top+bottom" in res
    res2 = agent_tools.set_insert_window(ctx, "ins6", crop="full")
    assert res2.startswith("EDL v"), res2
    assert ctx.latest_edl()["json"]["inserts"][0].get("crop") is None
    assert "back to the full frame" in res2


def test_set_crop_as_a_json_string_survives_a_stale_mcp_schema():
    ctx = _Ctx(_studio())
    res = agent_tools.set_insert_window(ctx, "ins6",
                                        crop="[0.27, 0.51, 0.99, 1.0]")
    assert res.startswith("EDL v"), res
    assert ctx.latest_edl()["json"]["inserts"][0]["crop"] == STRIP


def test_crop_rejections_are_actionable():
    ctx = _Ctx(_studio())
    assert "REJECTED" in agent_tools.set_insert_window(
        ctx, "ins6", crop=[0.5, 0.5, 0.55, 1.0])        # sliver
    assert "REJECTED" in agent_tools.set_insert_window(
        ctx, "ins6", crop="not json")
    assert not ctx.written


def test_cut_output_range_split_carries_the_crop_to_both_halves():
    e = _studio()
    e["inserts"][0]["crop"] = list(STRIP)
    ctx = _Ctx(e)
    res = agent_tools.cut_output_range(ctx, 4.55, 5.55)  # inside the insert
    assert res.startswith("EDL v"), res
    ins = ctx.latest_edl()["json"]["inserts"]
    assert len(ins) == 2
    assert all(i["crop"] == STRIP for i in ins)
