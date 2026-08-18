"""Manual dictated captions cannot silently compile to zero pixels."""

import copy

import agent_tools
import renderer


class _Ctx:
    has_main_video = True
    duration = 52.8
    edit_plan = None
    index = {"words": []}

    def __init__(self):
        self._row = {"version": 1, "json": {
            "keep": [[13.5, 15.5], [18.5, 20.5],
                     [34.5, 40.5], [47.5, 51.0]],
            "effects": {},
        }}

    def latest_edl(self):
        return self._row

    def write_edl(self, edl, description):
        self._row = {"version": self._row["version"] + 1,
                     "json": copy.deepcopy(edl)}
        return f"EDL v1 -> v2: {description}"

    def clamp(self, value):
        return round(min(max(float(value), 0.0), self.duration), 2)


def test_output_clock_manual_hook_is_repaired_to_surviving_source(tmp_path):
    ctx = _Ctx()

    result = agent_tools.add_captions(
        ctx, items=[{"text": "POV: THAT FRIEND", "start": 0, "end": 2,
                     "style": {"preset": "impact", "position": "top"}}])

    item = ctx.latest_edl()["json"]["captions"][0]
    assert item["start"] == 13.5
    assert item["end"] == 15.5
    assert "AUTO-CORRECTED CAPTION CLOCK" in result
    assert renderer.caption_review_times(
        ctx.latest_edl()["json"], ctx.index, str(tmp_path), 13.5,
        max_times=0)


def test_manual_caption_with_no_source_or_output_mapping_is_rejected():
    ctx = _Ctx()

    result = agent_tools.add_captions(
        ctx, items=[{"text": "INVISIBLE", "start": 52.0, "end": 52.5}])

    assert result.startswith("CORRECTION NEEDED:")
    assert ctx.latest_edl()["version"] == 1
