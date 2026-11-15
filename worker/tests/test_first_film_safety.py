import copy
import json
import pytest

import source_screen
from score import source_guard


def test_both_editor_fallbacks_start_from_the_persisted_cleared_upload():
    from types import SimpleNamespace
    rows = [{"version": 1, "json": {"keep": [[0., 14.17]], "texts": []}}]
    evidence = {"status": "verified", "version": 1, "safe_end_s": 10.1,
                "source_duration_s": 14.167, "source_sha256": "sha"}
    def save(edl, note):
        rows.append({"version": 2, "json": edl})
        return "saved"
    ctx = SimpleNamespace(duration=10.1, index={"source_screen": evidence},
                          latest_edl=lambda: rows[-1], apply_score=save)
    start = source_screen.clear_start_state(ctx)
    assert start["version"] == 2 and start["json"]["keep"] == [[0., 10.1]]
    assert rows[0]["json"]["keep"] == [[0., 14.17]]
    source_screen.check_render(start["json"], evidence, "original", "sha")
    source_screen.clear_start_state(ctx)
    assert len(rows) == 2


def test_failed_clearance_write_cannot_continue_to_the_unbounded_fallback():
    from types import SimpleNamespace
    ctx = SimpleNamespace(duration=10.1, index={"source_screen": {}},
        latest_edl=lambda: {"json": {"keep": [[0., 14.17]]}},
        apply_score=lambda *a: "REJECTED")
    with pytest.raises(ValueError, match="not saved"):
        source_screen.clear_start_state(ctx)


def test_screen_samples_accept_the_live_scene_detector_shot_type():
    from schemas import Shot
    typed = [Shot(id=1, start=0., end=10.17), Shot(id=2, start=10.17, end=14.17)]
    times = source_screen.sample_times(14.167, 30., typed)
    assert times == source_screen.sample_times(14.167, 30., [s.model_dump() for s in typed])
    assert any(9.9 <= t < 10.1 for t in times) and all(0 <= t < 14.167 for t in times)


def test_final_frame_seek_cannot_round_past_the_last_decodable_frame():
    times = source_screen.sample_times(35.4, 30, [{"start": 0, "end": 35.4}])
    assert times[-1] == 35.366
    assert float(f"{times[-1]:.3f}") <= 1061 / 30


def test_screen_review_samples_the_picture_tail_without_trimming_longer_audio(tmp_path, monkeypatch):
    import media
    from PIL import Image
    from score import boards
    seen = []
    monkeypatch.setattr(media, "probe", lambda _: {"duration": .2, "video_duration": .12, "fps": 25})
    def grab(_path, t, **_):
        seen.append(t)
        return Image.new("RGB", (160, 90), "red") if t <= .08 else None
    monkeypatch.setattr(boards, "grab", grab)
    monkeypatch.setattr(source_screen.llm, "ask_vision", lambda *a, **k:
        '{"reviewed_all_frames":true,"branding_screen_present":false}')
    result = source_screen.build("source.mp4", .2, 25, [], "sha", str(tmp_path))
    assert max(seen) == .08
    assert result["safe_end_s"] == result["source_duration_s"] == .2


def test_real_picture_tail_is_reviewed_when_audio_runs_longer(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from score import boards
    ffmpeg = shutil.which(boards.FFMPEG)
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools unavailable")
    path = tmp_path / "longer-audio.mp4"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=160x90:r=25:d=0.12", "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=mono:d=0.2", "-vf",
        "drawbox=color=red:t=fill:enable='gte(t,0.08)'", "-c:v", "libx264",
        "-threads", "1", "-filter_threads", "1", "-c:a", "aac", str(path)],
        check=True, capture_output=True, timeout=20)
    grab, frames = boards.grab, []
    def observe(*a, **k):
        im = grab(*a, **k)
        frames.append(im)
        return im
    monkeypatch.setattr(boards, "grab", observe)
    monkeypatch.setattr(source_screen.llm, "ask_vision", lambda *a, **k:
        '{"reviewed_all_frames":true,"branding_screen_present":false}')
    result = source_screen.build(str(path), .2, 25, [], "sha", str(tmp_path))
    assert all(im is not None for im in frames)
    red, _green, blue = frames[-1].getpixel((80, 45))
    assert red > 200 and blue < 50  # the final picture, not an earlier substitute
    assert result["safe_end_s"] == .2


def test_semantic_tail_cut_stops_at_last_clear_frame_not_detected_shot_edge():
    verdict = {"reviewed_all_frames": True, "branding_screen_present": True,
               "last_content_frame_id": 1, "first_branding_frame_id": 2}
    result = source_screen.interpret(json.dumps(verdict), [0, 9.9, 10.1, 14], 14.2, 30)
    assert result["safe_end_s"] == 9.9
    assert result["safe_end_s"] < 10.1


@pytest.mark.parametrize("answer", [{}, {"reviewed_all_frames": False},
    {"reviewed_all_frames": True, "branding_screen_present": True,
     "last_content_frame_id": 3, "first_branding_frame_id": 2}])
def test_incomplete_screen_evidence_never_admits_first_film(answer):
    with pytest.raises(ValueError):
        source_screen.interpret(json.dumps(answer), [0, 8, 10, 14], 14.2, 30)


def test_private_view_does_not_edit_original_index_and_render_blocks_fallback_tail():
    evidence = {"status": "verified", "version": 1, "safe_end_s": 9.9, "source_sha256": "sha"}
    index = {"video": {"duration": 14.2}, "source_screen": evidence,
             "shots": [{"start": 0, "end": 10.1}, {"start": 10.1, "end": 14.2}]}
    before = copy.deepcopy(index)
    bounded = source_screen.first_film_index(index)
    assert index == before and bounded["video"]["duration"] == 9.9
    assert bounded["shots"] == [{"start": 0, "end": 9.9}]
    source_screen.check_render({"keep": [[0, 9.9]]}, evidence, "original", "sha")
    with pytest.raises(ValueError):
        source_screen.check_render({"keep": [[0, 10.1]]}, evidence, "original", "sha")
    with pytest.raises(ValueError):
        source_screen.check_render({"overlays": [{"asset_key": "original",
            "source_start_s": 9.5, "duration_s": .5}]}, evidence, "original", "sha")


def _scene():
    return ({"frame": [720, 1280], "items": [{"id": "quay", "pic": "main",
             "src": [6.6, 8.], "crop": [.15, 0, .65, 1]}]},
            {"_first_film_source_guard": True, "video": {"width": 1920,
             "height": 1080, "duration": 20}, "shots": [{"start": 0, "end": 6.52},
             {"start": 6.52, "end": 12.85}]})


def test_frame_clearance_rounds_inward_to_the_edl_clock():
    index = {"video": {"duration": 14.2}, "source_screen": {
        "status": "verified", "version": 1, "safe_end_s": 9.866667}}
    assert source_screen.first_film_index(index)["video"]["duration"] == 9.86


def test_full_source_clearance_preserves_a_last_word_at_fractional_eof():
    index = {"video": {"duration": 3.582}, "words": [{"w": "end", "t0": 3, "t1": 3.582}],
             "source_screen": {"status": "verified", "version": 1,
                               "safe_end_s": 3.582, "source_duration_s": 3.582}}
    bounded = source_screen.first_film_index(index)
    assert bounded["words"] == index["words"] and bounded["video"]["duration"] == 3.582


def test_neighbouring_shot_face_does_not_authorize_quay_crop():
    film, index = _scene()
    rows = source_guard.apply(film, index, {"samples": [{"t": 6.5, "face": [.3, .2, .5, .4]}]})
    c = film["items"][0]["crop"]
    assert c[0] <= 0 and c[1] <= 0 and c[2] >= 1 and c[3] >= 1
    assert rows[0]["reason"] == "full_frame_missing_subject_boxes"


def test_fallback_reframer_holds_whole_source_until_every_kept_shot_has_boxes(monkeypatch):
    import agent_tools
    from types import SimpleNamespace
    _, index = _scene()
    index["spatial"] = {"samples": [{"t": 6.5, "faces": [[.3, .2, .5, .4]]}]}
    ctx = SimpleNamespace(index=index, duration=20,
                          latest_edl=lambda: {"json": {"keep": [[6.6, 8.]]}})
    monkeypatch.setattr(agent_tools, "set_frame", lambda ctx, ratio, mode, **kw: (ratio, mode))
    assert agent_tools._auto_reframe(ctx) == ("9:16", "pad_blur")
    index["spatial"]["samples"].append({"t": 7., "faces": [[.3, .2, .5, .4]]})
    assert not source_guard.missing_shot_boxes(index, [[6.6, 8.]])


def test_subject_fully_outside_crop_still_counts_and_all_camera_frames_are_safe():
    film, index = _scene()
    film["items"][0]["crop"] = [[.15, 0, .65, 1], [.2, 0, .7, 1]]
    rows = source_guard.apply(film, index, {"samples": [{"t": 7.5, "person": [.8, .1, .95, .9]}]})
    assert all(c[0] <= 0 and c[2] >= 1 for c in film["items"][0]["crop"])
    assert rows[0]["reason"] == "full_frame_subject_outside_crop"


def test_existing_edits_outside_first_film_unchanged():
    film, index = _scene()
    del index["_first_film_source_guard"]
    before = copy.deepcopy(film)
    assert source_guard.apply(film, index, {}) == [] and film == before


@pytest.mark.parametrize("already_fitted", [False, True])
def test_safe_source_cutaway_is_contained_not_recropped_by_overlay_cover(tmp_path, already_fitted):
    from score import lower
    from types import SimpleNamespace
    film, index = _scene()
    film.update(fps=30, spine=[], assets={"main": {"kind": "video", "path": "source.mp4",
                                               "w": 1920, "h": 1080}})
    film["items"][0].update(k="shot", f=[0, 42])
    perception = {}
    if already_fitted:
        # Printed source words made the compiler fit the picture before the
        # subject guard. A visible face must not turn that fit back into cover.
        h = (1920 / 1080) / (720 / 1280)
        film["items"][0]["crop"] = [0, (1-h)/2, 1, (1+h)/2]
        perception = {"samples": [{"t": 7.5, "face": [.3, .2, .5, .4]}]}
    source_guard.apply(film, index, perception)
    bake = SimpleNamespace(locals={}, plate=lambda *a: "backing.png")
    overlays, *_ = lower.lower_pictures(film, str(tmp_path), bake, {})
    foreground = next(o for o in overlays if o["asset_key"] == "main")
    assert not foreground.get("fit") and not foreground.get("source_crop")
    assert foreground["scale"] == 1 and foreground["x"] == foreground["y"] == .5
    assert overlays[0]["asset_key"] == "backing.png"
    # Publication normalizes by (z_index, start, id), not insertion order.
    # The backing must still sit below the source after that real boundary.
    from schemas import default_edl, validate_edl
    edl = default_edl(14.2)
    edl["overlays"] = overlays
    normalized = validate_edl(edl, 14.2).model_dump()
    assert [o["asset_key"] for o in normalized["overlays"]] == ["backing.png", "main"]


def test_fractional_eof_spine_rounding_matches_the_publication_gate():
    film, index = _scene()
    index["video"]["duration"] = 10.026
    index["source_screen"] = {"status": "verified", "source_duration_s": 10.026}
    # The 30 fps spine rounds the fully cleared source's end to 10.0333.
    film["items"][0]["src"] = [8., 10.033333]
    assert source_guard.apply(film, index, {})
    # The identical end is forbidden if this is a boundary BEFORE an outro.
    index["source_screen"]["source_duration_s"] = 14.
    with pytest.raises(ValueError, match="outside cleared"):
        source_guard.apply(film, index, {})


def test_perception_cannot_relabel_a_neighbouring_shots_face_as_local():
    from score.perceive import face_at
    rows = [{"t": 6.5, "faces": [[.2, .1, .5, .4]]}]
    shots = [{"start": 0, "end": 6.52}, {"start": 6.52, "end": 12.85}]
    assert face_at(rows, 6.6, shots) is None
    assert face_at(rows, 6.4, shots) == rows[0]["faces"][0]
    # No inferred shot outside the known intervals; legacy non-first edits
    # retain their existing nearest-sample behavior.
    assert face_at(rows, 6.6, []) is None
    assert face_at(rows, 6.6) == rows[0]["faces"][0]
