"""Pixel-grounded caption placement and source-text collision regressions."""

import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools
import captions
import renderer
import spatial
from schemas import CaptionStyle, default_edl, validate_edl
from timeline import Timeline


def test_spatial_plan_is_bounded_and_covers_real_shot_changes():
    shots = [{"start": 0.0, "end": 20.0},
             {"start": 20.0, "end": 40.0},
             {"start": 40.0, "end": 60.0}]
    times = spatial.plan_times(60.0, shots=shots, max_samples=40)
    assert len(times) <= 40
    assert any(abs(t - 20.12) < 0.2 for t in times)
    assert min(times) < 1.0 and max(times) > 55.0

    small = spatial.plan_times(12.0, max_samples=20)
    assert 6 <= len(small) <= 20


def test_shot_supplement_reuses_coarse_track_and_spends_only_remaining_budget(
        monkeypatch, tmp_path):
    clean = np.full((180, 320, 3), 35, np.uint8)
    frame = str(tmp_path / "clean.jpg")
    cv2.imwrite(frame, clean)
    seen = []

    def extract(_path, times, _out, **_kwargs):
        seen.extend(times)
        return {i: frame for i in range(len(times))}

    monkeypatch.setattr(spatial.tilestrip, "extract_frames", extract)
    coarse = {"v": spatial.SPATIAL_VERSION, "samples": [
        {"t": t, "faces": [], "text": [], "dense_ui": False}
        for t in (0.5, 25.0, 50.0, 75.0)
    ]}
    shots = [{"start": i * 10.0, "end": i * 10.0 + 5.0}
             for i in range(10)]
    augmented = spatial.augment_with_shot_frames(
        "video.mp4", 100.0, str(tmp_path), coarse, shots, max_samples=8)
    assert len(seen) <= 4
    assert len(augmented["samples"]) <= 8
    assert len(augmented["samples"]) > len(coarse["samples"])
    assert any(abs((float(s["t"]) % 10.0) - 0.12) < 0.1
               for s in augmented["samples"])


def test_reused_filmstrip_analysis_honors_critical_path_budget(
        monkeypatch, tmp_path):
    frame = str(tmp_path / "frame.jpg")
    cv2.imwrite(frame, np.full((90, 160, 3), 20, np.uint8))
    seen = []

    def analyze(path):
        seen.append(path)
        return {"faces": [], "text": [], "dense_ui": False}

    monkeypatch.setattr(spatial, "analyze_frame", analyze)
    times = [float(i) for i in range(144)]
    sidecar = spatial.analyze_frames(
        times, {i: frame for i in range(144)}, max_samples=64)
    assert len(seen) == 64
    assert len(sidecar["samples"]) == 64
    assert sidecar["samples"][0]["t"] == 0.0
    assert sidecar["samples"][-1]["t"] == 143.0


def test_text_detector_finds_burned_caption_lines_not_plain_background(tmp_path):
    clean = np.full((360, 640, 3), 35, np.uint8)
    clean_path = str(tmp_path / "clean.jpg")
    cv2.imwrite(clean_path, clean)
    clean_analysis = spatial.analyze_frame(clean_path)
    assert clean_analysis["text"] == []
    assert clean_analysis["mean_luma"] > 30
    assert clean_analysis["std_luma"] < 1

    img = clean.copy()
    cv2.putText(img, "THIS IS BURNED TEXT", (90, 285),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2,
                cv2.LINE_AA)
    cv2.putText(img, "SECOND LINE HERE", (135, 325),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2,
                cv2.LINE_AA)
    text_path = str(tmp_path / "text.jpg")
    cv2.imwrite(text_path, img)
    boxes = spatial.analyze_frame(text_path)["text"]
    assert len(boxes) >= 2
    assert any(box[1] > 0.65 for box in boxes)


def test_burned_caption_score_uses_speaking_moments_and_excludes_dense_ui():
    words = [{"w": "hello", "t0": 0.0, "t1": 1.0},
             {"w": "world", "t0": 2.0, "t1": 3.0}]
    sidecar = {"samples": [
        {"t": 0.5, "text": [[0.2, 0.72, 0.8, 0.82]], "dense_ui": False},
        {"t": 2.5, "text": [[0.2, 0.72, 0.8, 0.82]], "dense_ui": False},
        {"t": 8.0, "text": [], "dense_ui": False},
    ]}
    assert spatial.burned_caption_score(sidecar, words) == 1.0
    for sample in sidecar["samples"]:
        sample["dense_ui"] = True
    assert spatial.burned_caption_score(sidecar, words) == 0.0


def test_burned_caption_score_rejects_tall_portrait_texture_and_edge_blobs():
    words = [{"w": "hello", "t0": 0.0, "t1": 1.0}]
    clean_closeup = {"samples": [{
        "t": 0.5, "dense_ui": False,
        # Audited clean close-up false positives: a tall microphone/collar
        # region plus jacket/hands merged into the bottom frame edge.
        "text": [[0.37, 0.50, 0.58, 0.65],
                 [0.26, 0.89, 0.81, 1.00]],
    }]}
    assert spatial.burned_caption_score(clean_closeup, words) == 0.0
    assert spatial.BURNED_CAPTION_BLOCK_SCORE > 0.5


def test_safe_position_moves_off_faces_and_source_text():
    bottom_face = [[0.25, 0.55, 0.75, 0.92]]
    pos, _scores = agent_tools._safe_caption_position(
        bottom_face, [], False, preferred="bottom")
    assert pos == "top"

    top_text = [[0.05, 0.05, 0.95, 0.30]]
    pos, _scores = agent_tools._safe_caption_position(
        bottom_face, top_text, False, preferred="bottom")
    assert pos is None  # every band is occupied; omit instead of overlapping


def test_placement_track_changes_by_shot_and_maps_through_output_frame():
    class Ctx:
        index = {"video": {"width": 1920, "height": 1080}}

    edl = default_edl(6.0)
    sidecar = {"samples": [
        {"t": 1.0, "faces": [[0.3, 0.55, 0.7, 0.95]], "text": [],
         "dense_ui": False},
        {"t": 5.0, "faces": [[0.3, 0.02, 0.7, 0.35]], "text": [],
         "dense_ui": False},
    ]}
    track, unsafe, analyzed = agent_tools._caption_placement_track(
        Ctx(), edl, sidecar, preferred="bottom")
    assert unsafe == 0 and analyzed == 2
    assert [x["position"] for x in track] == ["top", "bottom"]
    assert track[0]["t0"] == 0.0 and track[-1]["t1"] == 6.0


def test_renderer_follows_baked_source_time_placement_track(tmp_path):
    edl = default_edl(6.0)
    edl["captions"] = {
        "mode": "from_transcript",
        "style": {"preset": "podcast"},
        "placement_track": [
            {"t0": 0.0, "t1": 3.0, "position": "top"},
            {"t0": 3.0, "t1": 6.0, "position": "bottom"},
        ],
    }
    edl = validate_edl(edl, 6.0).model_dump()
    words = [{"w": f"w{i}", "t0": i * 0.7, "t1": i * 0.7 + 0.4}
             for i in range(8)]
    path = str(tmp_path / "track.ass")
    captions.build_ass(edl, {"words": words}, Timeline(edl["keep"]), path,
                       play_res=(1080, 1920))
    text = open(path, encoding="utf-8").read()
    # Premium captions bake explicit positions. The top and bottom anchors are
    # materially different, proving this is not one global style anymore.
    import re
    ys = [int(y) for _x, y in re.findall(r"\\pos\((\d+),(\d+)\)", text)]
    assert min(ys) < 200 and max(ys) - min(ys) > 1000


def test_placement_track_schema_rejects_overlaps():
    edl = default_edl(6.0)
    edl["captions"] = {
        "mode": "from_transcript",
        "placement_track": [
            {"t0": 0.0, "t1": 4.0, "position": "top"},
            {"t0": 3.0, "t1": 6.0, "position": "bottom"},
        ],
    }
    try:
        validate_edl(edl, 6.0)
    except Exception as exc:
        assert "placement_track spans overlap" in str(exc)
    else:
        raise AssertionError("overlapping placement spans were accepted")


def test_caption_direction_varies_by_editorial_format_not_randomly():
    class Ctx:
        edit_plan = None

        def __init__(self, message, duration=30.0, words=80, speakers=1):
            self.user_message = message
            self.index = {
                "video": {"duration": duration, "width": 1920,
                          "height": 1080},
                "speakers": speakers,
                "words": [{"w": "word", "t0": i * duration / words,
                           "t1": i * duration / words + 0.1}
                          for i in range(words)],
            }

    edl = default_edl(30.0)
    edl["frame"] = {"ratio": "9:16", "mode": "pad_blur"}
    sports, _ = agent_tools._direct_caption_style(
        Ctx("high energy gym sports reel"), edl)
    luxury, _ = agent_tools._direct_caption_style(
        Ctx("luxury jewelry product film"), edl)
    podcast, _ = agent_tools._direct_caption_style(
        Ctx("two-person interview", speakers=2), edl)
    assert sports["preset"] == "impact"
    assert luxury["preset"] == "luxe"
    assert podcast["preset"] == "podcast"
    assert len({sports["highlight_color"], luxury["highlight_color"],
                podcast["highlight_color"]}) == 3


def test_long_form_defaults_to_restrained_subtitles():
    class Ctx:
        user_message = "add captions"
        edit_plan = None
        index = {"video": {"duration": 600.0, "width": 1920,
                           "height": 1080},
                 "speakers": 1, "words": []}

    style, why = agent_tools._direct_caption_style(Ctx(), default_edl(600.0))
    assert style == {"preset": "classic", "size": "m"}
    assert "long-form" in why


class _SpatialWriteCtx:
    enforce_spatial = True
    has_main_video = True

    def __init__(self, sidecar, edl=None, words=None):
        self._spatial = sidecar
        self.index = {
            "video": {"duration": 6.0, "width": 1920, "height": 1080},
            "words": list(words or []),
        }
        self._edl = edl or default_edl(6.0)

    def latest_edl(self):
        return {"version": 1, "json": self._edl}

    def write_edl(self, edl, _summary):
        self._edl = edl
        return "EDL v1 -> v2"


def test_designed_text_moves_to_a_measured_clean_band_and_mutes_captions():
    sidecar = {"v": spatial.SPATIAL_VERSION, "samples": [
        {"t": 1.0, "faces": [[0.20, 0.24, 0.80, 0.66]],
         "text": [[0.10, 0.68, 0.90, 0.86]],
         "dense_ui": False},
        {"t": 2.0, "faces": [[0.20, 0.24, 0.80, 0.66]],
         "text": [[0.10, 0.68, 0.90, 0.86]],
         "dense_ui": False},
    ]}
    edl = default_edl(6.0)
    edl["captions"] = {"mode": "from_transcript"}
    ctx = _SpatialWriteCtx(sidecar, edl)
    result = agent_tools.add_text(ctx, "THE POINT", 0.5, 2.5,
                                  template="title")
    assert result.startswith("EDL v1 -> v2")
    assert ctx._edl["texts"][0]["y"] == 0.16
    assert ctx._edl["caption_mutes"] == [[0.5, 2.5]]
    assert "moved the title" in result
    assert "two independent word layers never stack" in result


def test_short_text_window_gets_exact_frame_when_sidecar_is_sparse(
        monkeypatch, tmp_path):
    sidecar = {"v": spatial.SPATIAL_VERSION, "samples": [
        {"t": 5.5, "faces": [], "text": [], "dense_ui": False},
    ]}
    ctx = _SpatialWriteCtx(sidecar)
    ctx.workdir = str(tmp_path)
    ctx.proxy_path = lambda: "proxy.mp4"
    monkeypatch.setattr(agent_tools.media, "frame_at",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(spatial, "analyze_frame", lambda _path: {
        "faces": [[0.2, 0.55, 0.8, 0.94]],
        "text": [], "dense_ui": False,
    })
    band, analyzed, unsafe, err = agent_tools._fixed_text_band(
        ctx, ctx.latest_edl()["json"], 1.0, 2.0, preferred="middle")
    assert err is None and analyzed == 1 and unsafe == 0
    assert band == "top"


def test_kinetic_text_moves_each_phrase_off_the_speakers_face():
    words = [{"w": "important", "t0": 0.4, "t1": 0.8}]
    sidecar = {"v": spatial.SPATIAL_VERSION, "samples": [
        {"t": 0.6, "faces": [[0.15, 0.02, 0.85, 0.36]], "text": [],
         "dense_ui": False},
    ]}
    ctx = _SpatialWriteCtx(sidecar, words=words)
    result = agent_tools.add_kinetic_text(ctx, start=0.0, end=2.0,
                                          zone="upper")
    assert result.startswith("EDL v1 -> v2")
    assert ctx._edl["texts"][0]["y"] >= 0.66
    assert "moved 1 phrase" in result


def test_mixed_reframe_track_fits_unmeasured_shots_and_maps_geometry():
    class Ctx:
        index = {"video": {"width": 1920, "height": 1080}}

    edl = default_edl(6.0)
    edl["keep"] = [[0.0, 3.0], [3.0, 6.0]]
    edl["frame"] = {
        "ratio": "9:16", "mode": "crop", "focus_x": 0.5,
        "focus_y": 0.5,
        "focus_track": [
            {"t0": 0.0, "t1": 3.0, "x": 0.5, "y": 0.5,
             "mode": "pad_blur"},
            {"t0": 3.0, "t1": 6.0, "x": 0.5, "y": 0.5,
             "mode": "crop"},
        ],
    }
    edl = validate_edl(edl, 6.0).model_dump()
    # The far-left source point survives the fitted first shot, but is truly
    # outside the tight crop on the second shot.
    assert agent_tools._source_point_to_output(
        Ctx(), edl, 1.0, (0.05, 0.5)) is not None
    assert agent_tools._source_point_to_output(
        Ctx(), edl, 4.0, (0.05, 0.5)) is None

    graph = renderer.build_filtergraph(
        edl, 6.0, True, Timeline(edl["keep"]), None, [], {"words": []},
        preview=True, W=270, H=480, fps=30.0, frame_mode="crop",
        src_w=1920, src_h=1080, frame_focus=(0.5, 0.5))
    assert "boxblur=" in graph  # first segment preserves the full picture
    assert "crop=" in graph     # second segment remains a tight crop


def test_reframe_track_marks_no_face_shots_as_safe_fit(tmp_path):
    class Ctx:
        workdir = str(tmp_path)
        duration = 6.0
        index = {
            "video": {"duration": 6.0, "width": 1920, "height": 1080},
            "shots": [{"start": 0.0, "end": 2.0},
                      {"start": 2.0, "end": 4.0},
                      {"start": 4.0, "end": 6.0}],
            "spatial": {"samples": [
                {"t": 1.0, "faces": [[0.15, 0.2, 0.35, 0.5]]},
                {"t": 3.0, "faces": []},
                {"t": 5.0, "faces": [[0.65, 0.2, 0.85, 0.5]]},
            ]},
        }

        def __init__(self):
            self.edl = default_edl(6.0)

        def latest_edl(self):
            return {"version": 1, "json": self.edl}

        def proxy_path(self):
            raise RuntimeError("sidecar-only test")

        def write_edl(self, edl, _summary):
            self.edl = edl
            return "EDL v1 -> v2"

    ctx = Ctx()
    result = agent_tools._reframe_with_track(
        ctx, "9:16", (0.5, 0.35), preserve_unmeasured=True)
    assert result.startswith("EDL v1 -> v2")
    assert [span["mode"] for span in ctx.edl["frame"]["focus_track"]] == [
        "crop", "pad_blur", "crop"]
    assert "had no measured face" in result


def test_reframe_track_handles_one_face_then_unmeasured_broll(tmp_path):
    class Ctx:
        workdir = str(tmp_path)
        duration = 4.0
        index = {
            "video": {"duration": 4.0, "width": 1920, "height": 1080},
            "shots": [{"start": 0.0, "end": 2.0},
                      {"start": 2.0, "end": 4.0}],
            "spatial": {"samples": [
                {"t": 1.0, "faces": [[0.65, 0.2, 0.85, 0.5]]},
                {"t": 3.0, "faces": []},
            ]},
        }

        def __init__(self):
            self.edl = default_edl(4.0)

        def latest_edl(self):
            return {"version": 1, "json": self.edl}

        def proxy_path(self):
            raise RuntimeError("sidecar-only test")

        def write_edl(self, edl, _summary):
            self.edl = edl
            return "EDL v1 -> v2"

    ctx = Ctx()
    result = agent_tools._reframe_with_track(
        ctx, "9:16", (0.75, 0.35), preserve_unmeasured=True)
    assert result.startswith("EDL v1 -> v2")
    assert [span["mode"] for span in ctx.edl["frame"]["focus_track"]] == [
        "crop", "pad_blur"]


def test_auto_reframe_builds_mixed_track_before_global_detail_fit(
        monkeypatch, tmp_path):
    """Wide shot detail must not force the later face close-up to stay tiny."""
    sidecar = {"samples": [
        {"t": 0.5, "faces": []},
        {"t": 1.5, "faces": []},
        {"t": 2.5, "faces": []},
        {"t": 3.5, "faces": []},
        {"t": 4.5, "faces": []},
        {"t": 5.0, "faces": []},
        {"t": 5.5, "faces": [[0.45, 0.08, 0.65, 0.42]],
         "text": [[0.42, 0.62, 0.69, 0.85]], "dense_ui": False},
        {"t": 6.0, "faces": [[0.45, 0.08, 0.65, 0.42]],
         "text": [[0.38, 0.57, 0.54, 0.64],
                  [0.42, 0.71, 0.62, 0.85],
                  [0.26, 0.91, 0.59, 1.0]], "dense_ui": False},
        {"t": 6.5, "faces": [[0.45, 0.08, 0.65, 0.42]],
         "text": [[0.36, 0.49, 0.59, 0.61],
                  [0.42, 0.62, 0.62, 0.85]], "dense_ui": False},
    ]}

    class Ctx:
        has_main_video = True
        enforce_spatial = True
        duration = 6.52
        workdir = str(tmp_path)
        index = {
            "video": {"duration": 6.52, "width": 960, "height": 540},
            "shots": [{"start": 0.0, "end": 5.48},
                      {"start": 5.48, "end": 6.52}],
        }
        _spatial = sidecar

        def __init__(self):
            self.edl = default_edl(self.duration)

        def latest_edl(self):
            return {"version": 1, "json": self.edl}

        def proxy_path(self):
            return "proxy.mp4"

        def write_edl(self, edl, _summary):
            self.edl = edl
            return "EDL v1 -> v2"

    monkeypatch.setattr(agent_tools.media, "frame_at",
                        lambda *_args, **_kwargs: None)

    def faces(paths):
        # Reproduce the canary: the broad quorum calls this non-face/detail,
        # while exact per-shot fallback still sees no face in shot one.
        if len(paths) == 1 and paths[0].endswith("track_0.jpg"):
            return [], "none"
        if len(paths) == 1 and paths[0].endswith("track_1.jpg"):
            return [(0.55, 0.25)], "faces"
        return [(0.5, 0.5)], "detail"

    monkeypatch.setattr(agent_tools.subject, "points_from_frames", faces)
    monkeypatch.setattr(agent_tools.subject, "crop_detail_kept",
                        lambda *_args, **_kwargs: 0.20)
    ctx = Ctx()
    result = agent_tools.auto_reframe(ctx, "9:16", "auto")
    assert result.startswith("EDL v1 -> v2")
    assert [span["mode"] for span in ctx.edl["frame"]["focus_track"]] == [
        "pad_blur", "crop"]
    captions_result = agent_tools.add_captions(
        ctx, mode="from_transcript",
        style={"preset": "podcast", "size": "l", "effect": "none"},
        max_words_per_caption=4)
    assert captions_result.startswith("EDL v1 -> v2")
    assert ctx.edl["frame"]["focus_track"][-1]["mode"] == "crop"
    assert ctx.edl["captions"]["placement_track"][-1]["t1"] == 6.52


def test_caption_text_filter_rejects_tall_texture_but_keeps_real_lines():
    sample = {"dense_ui": False, "text": [
        [0.42, 0.62, 0.69, 0.85],   # merged jacket/microphone texture
        [0.20, 0.72, 0.80, 0.80],   # genuine horizontal subtitle line
    ]}
    assert agent_tools._caption_source_text_boxes(sample) == [
        [0.20, 0.72, 0.80, 0.80]]
    sample["dense_ui"] = True
    assert len(agent_tools._caption_source_text_boxes(sample)) == 2


def test_caption_effect_none_is_a_canonical_no_effect_not_a_retry():
    assert CaptionStyle.model_validate({"effect": "none"}).effect is None
    assert CaptionStyle.model_validate({"effect": "off"}).effect is None
