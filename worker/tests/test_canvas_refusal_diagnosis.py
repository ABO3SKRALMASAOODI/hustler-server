"""Ticket #948: music on an EMPTY canvas project was refused with a false
diagnosis ("the SAVED edit is itself invalid ... most likely the video was
replaced ... call reset_edit"). A blank canvas starts with no inserts, which the
validator rejects by design; that is the project's first state, not a replaced
video. The refusal must name the real next step and never send the caller to
reset_edit."""
from agent_tools import ToolContext
from schemas import canvas_edl, default_edl


class _Ctx:
    write_edl = ToolContext.write_edl

    def __init__(self, edl, duration, has_main_video):
        self._edl = edl
        self.duration = duration
        self.has_main_video = has_main_video
        self._states_seen = {}

    def latest_edl(self):
        return {"version": 1, "json": self._edl}


def _music(edl):
    out = dict(edl)
    out["music"] = [{"start": 0.0, "end": 10.0,
                     "storage_key": "library/music/x.mp3"}]
    return out


def test_music_on_an_empty_canvas_is_not_called_a_replaced_video():
    ctx = _Ctx(canvas_edl("9:16"), 0.0, has_main_video=False)
    res = ctx.write_edl(_music(canvas_edl("9:16")), "music")
    assert res.startswith("REJECTED")
    assert "replaced" not in res and "reset_edit" not in res
    assert "SAVED edit" not in res
    assert "insert_media" in res and "no video yet" in res


def test_a_main_video_edit_that_no_longer_fits_still_names_reset_edit():
    # The original escape hatch (2026-07-25) must survive: a saved edit cut for
    # a longer source than the current one cannot be built on.
    stale = default_edl(120.0)
    ctx = _Ctx(stale, 30.0, has_main_video=True)
    res = ctx.write_edl(default_edl(120.0), "anything")
    assert res.startswith("REJECTED")
    assert "reset_edit" in res
