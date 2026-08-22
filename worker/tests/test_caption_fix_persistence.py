"""Caption spelling corrections survive later style/treatment passes."""

import agent_tools


class _Ctx:
    duration = 10.0
    has_main_video = True
    enforce_spatial = False
    index = {
        "video": {"duration": 10.0, "width": 1080, "height": 1920,
                  "fps": 30.0},
        "words": [{"w": "cooked", "t0": 1.0, "t1": 1.4},
                  {"w": "david", "t0": 2.0, "t1": 2.4}],
    }

    def __init__(self):
        self.edl = {
            "keep": [[0.0, 10.0]], "effects": {},
            "captions": {
                "mode": "from_transcript",
                "style": {"preset": "clean"},
                "text_fixes": [["cooked", "got"]],
            },
        }

    def latest_edl(self):
        return {"version": 4, "json": self.edl}

    def write_edl(self, edl, _description):
        self.edl = edl
        return "EDL v4 -> v5: captions restyled."


def test_readding_transcript_captions_preserves_existing_text_fixes():
    ctx = _Ctx()

    result = agent_tools.add_captions(
        ctx, mode="from_transcript", style={"preset": "classic"})

    assert result.startswith("EDL v4 -> v5")
    assert ctx.edl["captions"]["text_fixes"] == [["cooked", "got"]]
