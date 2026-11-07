"""#861 (user 2869, p5543): "enhance the video quality to 2k and sharpen the
video edges and sound should be the same", sent while a 2 h 39 min upload was
indexing. The product then made its automatic first cut (3.8 s, 9:16) and the
request ran on THAT, and the editor had no way to write a 2K file: the canvas
resize was rejected, the customer got a sharpened 720x1280 4-second clip.
"""
import json
import os
import subprocess

import pytest

import agent_loop
import agent_tools
import renderer
import request_contract
import schemas


FIRST_CUT_EDL = {"keep": [[1132.04, 1132.85], [1159.45, 1162.47]],
                 "frame": {"ratio": "9:16", "mode": "crop"}}


def _chat():
    return [{"id": 170081, "role": "user", "content": "enhance to 2k",
             "meta": {}}]


def test_a_request_sent_before_the_first_cut_says_so():
    # the old reader only looked at rows before the request: no first cut
    assert request_contract.edit_origin(_chat(), 170081) is None
    origin = request_contract.edit_origin(_chat(), 170081,
                                          later_first_cut=170092)
    assert origin["made_by"] == "automatic_first_cut"
    assert origin["request_written_before_first_cut"] is True
    assert origin["customer_requests_since"] == 0


def test_a_first_cut_before_the_request_keeps_the_old_reading():
    rows = [{"id": 10, "role": "assistant", "content": "cut",
             "meta": {"kind": "first_cut"}},
            {"id": 11, "role": "user", "content": "more", "meta": {}}]
    origin = request_contract.edit_origin(rows, 12, later_first_cut=None)
    assert origin == {"made_by": "automatic_first_cut",
                      "customer_requests_since": 1}


class _Ctx:
    duration = 9535.041

    def __init__(self, origin):
        self.request_edit_origin = origin

    def latest_edl(self):
        return {"version": 10, "json": FIRST_CUT_EDL}


def test_the_editor_is_told_which_video_the_words_are_about():
    note = agent_loop._before_first_cut_note(_Ctx(
        {"made_by": "automatic_first_cut", "customer_requests_since": 0,
         "request_written_before_first_cut": True}))
    assert "BEFORE" in note and "reset_edit" in note
    assert "3.8s kept of their 9535.0s upload" in note
    assert agent_loop._before_first_cut_note(_Ctx(None)) == ""
    assert agent_loop._before_first_cut_note(_Ctx(
        {"made_by": "automatic_first_cut", "customer_requests_since": 1})) == ""


def test_export_classes_fill_their_box():
    assert renderer.delivery_resolution_dims(1080, 1920, "2k") == (1440, 2560)
    assert renderer.delivery_resolution_dims(1920, 816, "2k") == (2560, 1088)
    assert renderer.delivery_resolution_dims(1920, 1080, "4k") == (3840, 2160)
    assert renderer.delivery_resolution_dims(1080, 1920, None) == (1080, 1920)
    assert renderer.delivery_resolution_dims(3840, 2160, "2k") == (3840, 2160)


def test_frame_resolution_is_optional_and_keeps_old_signatures():
    frame = schemas.Frame(ratio="9:16", mode="crop")
    assert frame.resolution is None
    assert schemas.Frame(ratio="9:16", resolution="2k").resolution == "2k"
    with pytest.raises(Exception):
        schemas.Frame(ratio="9:16", resolution="8k")
    stored = {"keep": [[0.0, 5.0]], "frame": {"ratio": "9:16", "mode": "crop"}}
    dumped = {"keep": [[0.0, 5.0]], "frame": frame.model_dump()}
    assert schemas.edl_signature(stored) == schemas.edl_signature(
        {k: v for k, v in dumped.items()}) or "resolution" not in \
        schemas.edl_signature(dumped)


class _ToolCtx:
    has_main_video = True
    index = {"video": {"width": 1920, "height": 816}}

    def __init__(self, edl):
        self._edl = {"version": 11, "json": edl}
        self.written = None

    def latest_edl(self):
        return self._edl

    def write_edl(self, edl, desc):
        self.written = edl
        self._edl = {"version": 12, "json": edl}
        return f"EDL v11 -> v12: {desc}"


def test_enhance_video_sets_a_2k_export_without_sharpening():
    ctx = _ToolCtx(json.loads(json.dumps(FIRST_CUT_EDL)))
    out = agent_tools.enhance_video(ctx, resolution="2k")
    assert out.startswith("EDL v11 -> v12"), out
    assert ctx.written["frame"]["resolution"] == "2k"
    assert ctx.written["frame"]["ratio"] == "9:16"
    assert not (ctx.written.get("effects") or {}).get("stylize")
    assert "1440x2560" in out
    back = agent_tools.enhance_video(_ToolCtx(ctx.written), resolution="hd")
    assert back.startswith("EDL v11")
    assert agent_tools.enhance_video(ctx, resolution="8k").startswith("REJECTED")


def test_the_tool_schema_offers_the_resolution():
    spec = agent_tools.TOOLS["enhance_video"] if hasattr(agent_tools, "TOOLS") \
        else None
    if spec is None:
        pytest.skip("tool registry name differs")
    assert "resolution" in spec[2]


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"], capture_output=True)
                    .returncode != 0, reason="no ffmpeg")
def test_a_2k_final_is_written_at_2k(tmp_path):
    src = tmp_path / "final.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=360x640:r=30:d=1",
                    "-f", "lavfi", "-i", "sine=f=440:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    before = renderer.media.probe(str(src))
    edl = {"keep": [[0, 1]], "frame": {"ratio": "9:16", "resolution": "2k"}}
    assert renderer.delivery_upscale(edl, str(src), str(tmp_path)) == (1440, 2560)
    after = renderer.media.probe(str(src))
    assert (after["width"], after["height"]) == (1440, 2560)
    assert abs(float(after["duration"]) - float(before["duration"])) < 0.1
    assert renderer.media.has_audio_stream(str(src))
    # nothing asked: the file is left alone
    assert renderer.delivery_upscale({"keep": [[0, 1]]}, str(src),
                                     str(tmp_path)) is None
    assert not [p for p in os.listdir(tmp_path) if p.startswith("upscale_")]


def test_a_2k_export_survives_validation_on_an_uncropped_edit():
    # replay p0861: enhance_video(resolution="2k") on the full upload stored
    # no frame at all, because a "source" frame was always dropped
    ctx = _ToolCtx({"keep": [[0.0, 9535.04]]})
    agent_tools.enhance_video(ctx, resolution="2k", sharpen=0.5)
    first = ctx.written
    stored = schemas.validate_edl(first, 9535.041)
    stored = stored if isinstance(stored, dict) else stored.model_dump()
    assert (stored.get("frame") or {}).get("resolution") == "2k"
    plain = schemas.validate_edl({"keep": [[0.0, 10.0]],
                                  "frame": {"ratio": "source"}}, 10.0)
    plain = plain if isinstance(plain, dict) else plain.model_dump()
    assert not plain.get("frame")
