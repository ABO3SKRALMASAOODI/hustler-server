"""Round 78 — a spliced scene can play SILENT.

"Mute all scenes": set_volume reaches only the MAIN footage (source-time
spans), so on a program that is 90% spliced inserts there was no way to
silence anything. InsertItem.mute drops the insert's own track — the block
renders over the shared anullsrc exactly like a clip that never had audio.
None keeps every old signature and render.

Run:  python -m pytest tests/test_insert_mute.py -q     (from worker/)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
from renderer import build_filtergraph                        # noqa: E402
from schemas import default_edl, validate_edl                 # noqa: E402
from timeline import Timeline                                 # noqa: E402

INDEX = {"words": [], "silences": [], "shots": [], "sentences": []}
SRC = 354.6
REC = "clips/1/rec.mov"


def _graph(edl, insert_inputs):
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return build_filtergraph(edl, SRC, True, tl, None, [], INDEX, False,
                             W=1280, H=720, fps=30.0, frame_mode=None,
                             insert_inputs=insert_inputs,
                             src_w=1280, src_h=720, silence_idx=2)


def _ins(mute=None):
    it = {"id": "ins1", "kind": "video", "motion": None, "asset_key": REC,
          "at_output_s": 10.0, "duration_s": 5.0, "source_start_s": 4.0}
    if mute is not None:
        it["mute"] = mute
    return it


def _edl(ins):
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    e["inserts"] = [ins]
    return validate_edl(e, SRC).model_dump()


def test_muted_insert_takes_the_silence_branch():
    """The assembly layer flips has_audio for a muted item; the graph then
    draws the block's audio from anullsrc — no [idx:a] chain at all."""
    ins = _ins(mute=True)
    g = _graph(_edl(ins), [(1, ins, False)])      # assembly says: no audio
    assert "[1:a]" not in g
    assert "[sil0]atrim" in g                     # block audio = silence


def test_unmuted_insert_emits_the_exact_legacy_audio_chain():
    ins = _ins()
    g = _graph(_edl(ins), [(1, ins, True)])
    assert "[1:a]atrim=start=4.000:end=9.000" in g
    assert "sil0" not in g


def test_schema_keeps_mute_and_drops_none():
    e = _edl(_ins(mute=True))
    assert e["inserts"][0]["mute"] is True
    e0 = _edl(_ins())
    assert e0["inserts"][0]["mute"] is None


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
        self.workdir = tempfile.mkdtemp(prefix="mute_")
        self.db = _DB({REC: {"id": 1, "kind": "video_clip",
                             "storage_key": REC, "duration_s": 154.9,
                             "meta": {"filename": "rec.mov"}},
                       "images/1/m.jpg": {"id": 2, "kind": "image_ref",
                                          "storage_key": "images/1/m.jpg",
                                          "meta": {"filename": "m.jpg"}}})
        self.written = []
        self._edl = validate_edl(edl, duration).model_dump()

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


def test_mute_toggles_and_survives_other_edits():
    e = default_edl(SRC)
    e["keep"] = [[113.7, 117.25]]
    e["inserts"] = [{"id": "ins6", "kind": "video", "asset_key": REC,
                     "at_output_s": 3.55, "duration_s": 4.88,
                     "source_start_s": 5.94, "rate": 1.6}]
    ctx = _Ctx(e)
    res = agent_tools.set_insert_window(ctx, "ins6", mute=True)
    assert res.startswith("EDL v"), res
    it = ctx.latest_edl()["json"]["inserts"][0]
    assert it["mute"] is True and it["rate"] == 1.6
    assert "audio MUTED" in res
    # a later rate change keeps the mute
    agent_tools.set_insert_window(ctx, "ins6", rate=2.0)
    assert ctx.latest_edl()["json"]["inserts"][0]["mute"] is True
    res2 = agent_tools.set_insert_window(ctx, "ins6", mute=False)
    assert ctx.latest_edl()["json"]["inserts"][0].get("mute") is None
    assert "audio back ON" in res2


def test_mute_accepts_the_stale_mcp_string_and_rejects_images():
    e = default_edl(SRC)
    e["keep"] = [[113.7, 117.25]]
    e["inserts"] = [{"id": "ins1", "kind": "video", "asset_key": REC,
                     "at_output_s": 3.55, "duration_s": 2.0,
                     "source_start_s": 1.0},
                    {"id": "im1", "kind": "image",
                     "asset_key": "images/1/m.jpg",
                     "at_output_s": 3.55, "duration_s": 1.5}]
    ctx = _Ctx(e)
    res = agent_tools.set_insert_window(ctx, "ins1", mute="true")
    assert res.startswith("EDL v"), res
    assert ctx.latest_edl()["json"]["inserts"][0]["mute"] is True
    assert "REJECTED" in agent_tools.set_insert_window(ctx, "im1", mute=True)
    assert "REJECTED" in agent_tools.set_insert_window(ctx, "ins1",
                                                       mute="maybe")
