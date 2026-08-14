"""Round 79 — a spliced scene can FIT the frame instead of filling it.

The program's cover-crop default beheads any insert whose aspect fights the
canvas: a 9:16 logo card on a 16:9 program showed only its middle band and
the user called the image "corrupted". InsertItem.fit overrides the mapping
for ONE scene — 'pad' letterboxes the whole picture on black, 'pad_blur'
over a blurred backdrop, 'crop' forces the fill. None keeps every old
signature and render byte-identical.

Also here: the wordmark font. "Plus Jakarta Sans ExtraBold" joins
TEXT_FONTS (it was already bundled for the watermark), and graphics.py maps
the API name to the TTF's real family ("Plus Jakarta Sans") so libass finds
the face instead of falling back.

Run:  python -m pytest tests/test_insert_fit.py -q     (from worker/)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
import graphics                                               # noqa: E402
from renderer import build_filtergraph                        # noqa: E402
from schemas import default_edl, validate_edl, TEXT_FONTS     # noqa: E402
from timeline import Timeline, describe_program, program_blocks  # noqa: E402

INDEX = {"words": [], "silences": [], "shots": [], "sentences": []}
SRC = 354.6
REC = "clips/1/rec.mov"
IMG = "images/1/logo.jpg"


def _graph(edl, insert_inputs):
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return build_filtergraph(edl, SRC, True, tl, None, [], INDEX, False,
                             W=1280, H=720, fps=30.0, frame_mode=None,
                             insert_inputs=insert_inputs,
                             src_w=1280, src_h=720, silence_idx=2)


def _edl(ins):
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    e["inserts"] = [ins]
    return validate_edl(e, SRC).model_dump()


def _img_ins(**kw):
    it = {"id": "ins13", "kind": "image", "asset_key": IMG,
          "at_output_s": 10.0, "duration_s": 4.0}
    it.update(kw)
    return it


def test_fit_pad_letterboxes_the_insert():
    ins = _img_ins(fit="pad")
    g = _graph(_edl(ins), [(1, ins, False)])
    assert "force_original_aspect_ratio=decrease" in g
    assert "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black" in g


def test_fit_none_is_byte_identical_to_legacy():
    ins = _img_ins()
    base = _graph(_edl(ins), [(1, ins, False)])
    withnone = dict(ins)
    withnone["fit"] = None
    assert _graph(_edl(withnone), [(1, withnone, False)]) == base
    assert "force_original_aspect_ratio=increase,crop=1280:720" in base


def test_schema_keeps_fit_and_rejects_garbage():
    e = _edl(_img_ins(fit="pad"))
    assert e["inserts"][0]["fit"] == "pad"
    assert _edl(_img_ins())["inserts"][0]["fit"] is None
    try:
        _edl(_img_ins(fit="stretch"))
        raise AssertionError("fit='stretch' must be rejected")
    except ValueError:
        pass


def test_program_blocks_and_describe_carry_fit():
    e = _edl(_img_ins(fit="pad"))
    blocks = program_blocks(e)
    ins_blk = next(b for b in blocks if b["kind"] == "insert")
    assert ins_blk["fit"] == "pad"
    assert "fitted whole into the frame (black bars)" in describe_program(e)


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
        self.workdir = tempfile.mkdtemp(prefix="fit_")
        self.db = _DB({REC: {"id": 1, "kind": "video_clip",
                             "storage_key": REC, "duration_s": 154.9,
                             "meta": {"filename": "rec.mov"}},
                       IMG: {"id": 2, "kind": "image_ref",
                             "storage_key": IMG,
                             "meta": {"filename": "logo.jpg"}}})
        self.written = []
        self._edl = validate_edl(edl, duration).model_dump()

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


def test_fit_tool_sets_clears_and_rejects():
    e = default_edl(SRC)
    e["keep"] = [[113.7, 117.25]]
    e["inserts"] = [_img_ins(at_output_s=3.55)]
    ctx = _Ctx(e)
    res = agent_tools.set_insert_window(ctx, "ins13", fit="pad")
    assert res.startswith("EDL v"), res
    assert ctx.latest_edl()["json"]["inserts"][0]["fit"] == "pad"
    assert "fitted WHOLE into the frame" in res
    # aliases and the stale-MCP string path are the same argument
    res2 = agent_tools.set_insert_window(ctx, "ins13", fit="letterbox")
    assert "already plays" in res2                 # pad again = no-op
    # survives an unrelated change
    agent_tools.set_insert_window(ctx, "ins13", duration_s=3.0)
    assert ctx.latest_edl()["json"]["inserts"][0]["fit"] == "pad"
    res3 = agent_tools.set_insert_window(ctx, "ins13", fit="auto")
    assert ctx.latest_edl()["json"]["inserts"][0].get("fit") is None
    assert "back to the program's default framing" in res3
    assert "REJECTED" in agent_tools.set_insert_window(ctx, "ins13",
                                                       fit="stretch")


def test_image_window_ignores_neutral_seek_and_can_rotate_one_scene():
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    e["inserts"] = [_img_ins(at_output_s=10.0)]
    ctx = _Ctx(e)
    res = agent_tools.set_insert_window(
        ctx, "ins13", duration_s=3.0, clip_start_s=0, rotation="cw")
    assert res.startswith("EDL v"), res
    assert ctx.latest_edl()["json"]["inserts"][0]["rotation"] == 90
    cleared = agent_tools.set_insert_window(ctx, "ins13", rotation="clear")
    assert cleared.startswith("EDL v")
    assert ctx.latest_edl()["json"]["inserts"][0].get("rotation") is None


def test_insert_rotation_is_local_in_the_filtergraph():
    ins = _img_ins(rotation=90)
    g = _graph(_edl(ins), [(1, ins, False)])
    assert "[insv0]transpose=clock[insvr0]" in g
    assert "[insvr0]scale=" in g


def test_video_insert_motion_is_rendered_locally_without_timing_changes():
    ins = {"id": "ins-video", "kind": "video", "asset_key": REC,
           "at_output_s": 10.0, "duration_s": 4.0,
           "source_start_s": 2.0, "motion": "pan_left"}
    normalized = _edl(ins)
    assert normalized["inserts"][0]["motion"] == "pan_left"
    g = _graph(normalized, [(1, normalized["inserts"][0], True)])
    assert "[v_insn0]zoompan=z='1.15'" in g
    assert "(iw-iw/zoom)*(1-(on/120))" in g
    # The same four-second source/audio windows remain authoritative.
    assert "trim=start=2.000:end=6.000" in g
    assert "atrim=start=2.000:end=6.000" in g


def test_insert_media_keeps_requested_motion_on_video_broll():
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    ctx = _Ctx(e)
    res = agent_tools.insert_media(ctx, REC, 10.0, duration_s=3.0,
                                   clip_start_s=1.0, motion="zoom_in")
    assert res.startswith("EDL v"), res
    item = ctx.latest_edl()["json"]["inserts"][0]
    assert item["kind"] == "video" and item["motion"] == "zoom_in"
    assert "local zoom_in camera move" in res


def test_visual_only_freeze_keeps_program_clock_and_audio(monkeypatch):
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    ctx = _Ctx(e)
    placed = {}
    monkeypatch.setattr(
        agent_tools, "_freeze_frame_asset",
        lambda *_args: ("generated/1/freeze.png", None))

    def fake_overlay(_ctx, key, at, **kwargs):
        placed.update(key=key, at=at, **kwargs)
        return "EDL v2: visual freeze"

    monkeypatch.setattr(agent_tools, "add_overlay", fake_overlay)
    result = agent_tools.add_freeze_frame(
        ctx, 3.0, duration_s=2.0, audio_mode="continue", motion=None)
    assert result.startswith("EDL v2"), result
    assert placed == {"key": "generated/1/freeze.png", "at": 3.0,
                      "duration_s": 2.0, "x": 0.5, "y": 0.5,
                      "scale": 1.0, "fit": "cover"}
    assert "speech, source audio, music" in result
    assert "Nothing after it shifts" in result


def test_new_inserts_preserve_the_whole_asset_by_default():
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    ctx = _Ctx(e)
    res = agent_tools.insert_media(
        ctx, REC, 10.0, duration_s=2.0, clip_start_s=1.0)
    assert res.startswith("EDL v"), res
    assert ctx.latest_edl()["json"]["inserts"][0]["fit"] == "pad_blur"


def test_auto_fit_follows_a_black_pad_frame():
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    e["frame"] = {"ratio": "16:9", "mode": "pad",
                  "focus_x": 0.5, "focus_y": 0.5, "focus_track": None}
    ctx = _Ctx(e)
    res = agent_tools.insert_media(
        ctx, REC, 10.0, duration_s=2.0, clip_start_s=1.0)
    assert res.startswith("EDL v"), res
    assert ctx.latest_edl()["json"]["inserts"][0]["fit"] == "pad"


def test_insert_and_overlay_clamp_windows_past_the_clip():
    e = default_edl(SRC)
    e["keep"] = [[0.0, 20.0]]
    ctx = _Ctx(e)
    ctx.db.assets[REC]["duration_s"] = 3.6
    res = agent_tools.insert_media(
        ctx, REC, 0.0, duration_s=8.0, clip_start_s=0.0)
    assert res.startswith("EDL v"), res
    assert "asked 8" in res and "used 3.6s" in res
    assert ctx.latest_edl()["json"]["inserts"][0]["duration_s"] == 3.6
    ov = agent_tools.add_overlay(
        ctx, REC, 1.0, duration_s=8.0, entrance="whoosh")
    assert ov.startswith("EDL v"), ov
    assert ctx.latest_edl()["json"]["overlays"][0]["duration_s"] == 3.6
    assert ctx.latest_edl()["json"]["overlays"][0].get("entrance") is None


def test_insert_crop_does_not_require_prior_inspection_permission():
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    e["frame"] = {"ratio": "9:16", "mode": "pad_blur"}
    ctx = _Ctx(e)
    ctx.enforce_spatial = True
    ctx.db.assets[REC].update(width=1920, height=1080)
    res = agent_tools.insert_media(
        ctx, REC, 10.0, duration_s=2.0, clip_start_s=4.0, fit="crop")
    assert res.startswith("EDL v")
    assert ctx.latest_edl()["json"]["inserts"][0]["fit"] == "crop"


def test_full_frame_broll_allows_an_intentional_aspect_crop():
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    e["frame"] = {"ratio": "9:16", "mode": "pad_blur"}
    ctx = _Ctx(e)
    ctx.db.assets[REC].update(width=1920, height=1080)
    res = agent_tools.add_overlay(
        ctx, REC, 1.0, duration_s=2.0, fit="cover")
    assert res.startswith("EDL v"), res
    assert ctx.latest_edl()["json"]["overlays"][0]["fit"] == "cover"


def test_indexed_flat_black_clip_window_remains_an_editorial_choice():
    class BlankDb(_DB):
        def run(self, fn, *a):
            if getattr(fn, "__name__", "") == "get_index_by_sha":
                return {"json": {"spatial": {"samples": [
                    {"t": 1.0, "faces": [], "text": [],
                     "dense_ui": False, "mean_luma": 0.2,
                     "std_luma": 0.3, "edge_density": 0.0},
                    {"t": 2.0, "faces": [], "text": [],
                     "dense_ui": False, "mean_luma": 0.1,
                     "std_luma": 0.2, "edge_density": 0.0},
                ]}}}
            return super().run(fn, *a)

    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    ctx = _Ctx(e)
    ctx.db.assets[REC]["sha256"] = "blanksha"
    ctx.db = BlankDb(ctx.db.assets)
    res = agent_tools.insert_media(
        ctx, REC, 10.0, duration_s=2.0, clip_start_s=0.5)
    assert res.startswith("EDL v"), res
    assert ctx.written


# ------------------------------------------------ the wordmark font ----

def test_jakarta_is_a_text_font_and_maps_to_its_real_family():
    assert "Plus Jakarta Sans ExtraBold" in TEXT_FONTS
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    e["texts"] = [{"id": "tx9", "text": "Valmera", "start": 1.0, "end": 3.0,
                   "template": "title", "font": "Plus Jakarta Sans ExtraBold"}]
    validate_edl(e, SRC)                           # must not raise
    ttf = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "fonts", "PlusJakartaSans-ExtraBold.ttf")
    assert os.path.exists(ttf)


def test_gfx_ass_uses_the_ttf_family_name():
    wd = tempfile.mkdtemp(prefix="gfx_")
    e = default_edl(SRC)
    e["keep"] = [[0.0, 10.0]]
    e["texts"] = [{"id": "tx9", "text": "Valmera", "start": 1.0, "end": 3.0,
                   "template": "title",
                   "font": "Plus Jakarta Sans ExtraBold"}]
    path = graphics.build_gfx_ass(validate_edl(e, SRC).model_dump(), 10.0,
                                  os.path.join(wd, "gfx.ass"), (1280, 720))
    with open(path, encoding="utf-8") as fh:
        ass = fh.read()
    # nameID 1 of the TTF — what libass actually matches — is the FULL
    # "Plus Jakarta Sans ExtraBold" (verified with fontTools; the bare
    # family is only nameID 16, and requesting it renders the fallback).
    assert "Plus Jakarta Sans ExtraBold," in ass
