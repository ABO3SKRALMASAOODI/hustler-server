"""Delivery checks for the wave-four editing paths."""
import base64
import shutil
from pathlib import Path

import numpy as np
import pytest

from scene.palette import light
from typeset import shaping


def test_light_devices_keep_their_own_length_in_a_142_frame_beat():
    scene = {"dur_f": 160, "fps": 30, "items": []}
    for device, expected in ((light.filmburn, [130, 150]), (light.leak, [8, 44]),
                             (light.prism, [8, 32])):
        patch = device(scene, None, {"f": [8, 150]}, None)
        assert patch["items"][0]["f"] == expected


def test_picture_change_reuses_verified_audio_without_reencoding(tmp_path, monkeypatch):
    import subprocess
    import mastering
    import media_cache
    monkeypatch.setattr(media_cache, "cache_dir", lambda: str(tmp_path/"cache"))
    paths = [str(tmp_path/(color+".mp4")) for color in ("red","blue")]
    for color, path in zip(("red","blue"), paths):
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",f"color={color}:s=64x48:r=24:d=3",
                        "-f","lavfi","-i","sine=frequency=480:sample_rate=48000:duration=3",
                        "-c:v","libx264","-c:a","aac","-b:a","128k",path],check=True)
    def packets(path, stream):
        return subprocess.check_output(["ffmpeg","-v","error","-i",path,"-map",stream,"-c","copy",
                                        "-f","framehash","-"])
    blue = packets(paths[1], "0:v:0")
    first = mastering.cached_guard(paths[0], str(tmp_path), "128k")
    assert first["held"] and first["passes"] > 0
    second = mastering.cached_guard(paths[1], str(tmp_path), "128k")
    assert second.get("reused") and second["i"] == first["i"]
    assert packets(paths[1], "0:v:0") == blue
    assert packets(paths[1], "0:a:0") == packets(paths[0], "0:a:0")


def test_stock_gif_with_jpeg_filename_plays_for_its_full_window(tmp_path):
    from PIL import Image
    import renderer
    import render_plan
    import subprocess
    path = tmp_path / "stock.jpg"
    Image.new("RGB", (16, 16), "blue").save(path, format="GIF")
    item = {"kind": "image", "duration_s": .8}
    for build in (render_plan.insert_input, renderer._overlay_input):
        args, _ = build(item, str(path), 25)
        rgb = subprocess.check_output(["ffmpeg", "-v", "error", *args, "-an",
                                       "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
        frames = np.frombuffer(rgb, np.uint8).reshape(-1, 16, 16, 3)
        assert len(frames) == 20
        assert frames[:, :, :, 2].min() > 240


def test_rotated_source_and_index_share_coordinates(tmp_path):
    import media
    import w0b_probe
    src = w0b_probe.make_source(str(tmp_path / "main.mp4"), w=96, h=64, dur=1)
    native = media.probe(src)
    shown = media.probe(media.oriented_source(src, 90, str(tmp_path)))
    idx = {"video": native, "spatial": {"samples": [{"text": [[.1, .2, .4, .6]]}]}}
    idx["source_layout"] = {"windows": [{"x": .1}]}
    rotated = media.oriented_index(idx, 90)
    assert "source_layout" not in rotated and idx["source_layout"]["windows"]
    assert (shown["width"], shown["height"]) == (64, 96)
    assert shown["duration"] == native["duration"]
    assert rotated["video"]["width"] == shown["width"]
    assert rotated["spatial"]["samples"][0]["text"] == [[.4, .1, .8, .4]]
    assert idx["video"]["width"] == 96


def test_follow_up_supplies_the_batch_picture_tool():
    import agent_prompt
    import agent_tools
    state = agent_prompt.project_state_block("video","index","edl",[],[],follow_up=True)
    assert "source_broll()" in state
    assert "source_broll" in agent_tools.TOOL_DOMAINS["acquisition"]


def test_broll_keeps_late_pages_when_the_moment_planner_is_unavailable(monkeypatch):
    from tools_ext import broll_tools
    from types import SimpleNamespace
    import llm
    import device_planner
    words = [{"w": f"word{i}", "t0": float(i)} for i in range(365)]
    monkeypatch.setattr(device_planner, "program_words", lambda *args: words)
    monkeypatch.setattr(llm, "ask_text", lambda *args, **kw: None)
    batches = []
    def record(ctx, moments, limit, providers, place):
        batches.append(moments)
        return "placed"
    monkeypatch.setattr(broll_tools, "_source_broll", record)
    ctx = SimpleNamespace(index={}, user_message="Place cutaways on the mentions",
                          latest_edl=lambda: {"version": 1, "json": {"keep": [[0, 365]]}})
    broll_tools.source_broll(ctx)
    assert len(batches) == 2
    assert batches[-1][-1]["at"] == 350
    assert batches[-1][-1]["query"].startswith("word350 ")


def test_source_gains_follow_the_mix_and_the_kept_spans():
    from schemas import source_gain_stretches
    edl = {"keep": [[0, 2], [4, 6]], "stem_mix": {"voice_gain_db": -4},
           "volume": [None, {"start": 1, "end": 5, "gain_db": -56},
                      {"start": 0, "end": 6, "gain_db": -9, "music_id": "off"}]}
    got = source_gain_stretches(edl)
    assert [(r["start"], r["end"], r["gain_db"]) for r in got] == [
        (0, 1, -4), (1, 2, -60), (4, 5, -60), (5, 6, -4)]


def test_matched_stem_level_survives_unrelated_programme():
    import audio_qc
    rng = np.random.default_rng(702)
    bed, programme = rng.normal(size=(2, 24000))
    reading = audio_qc.matched_layer(programme + .3162*bed, bed, 0, 3)
    assert reading["audible_pct"] == 100
    assert abs(reading["relative_db"] + 10) < .6
    assert audio_qc.matched_layer(programme, bed, 0, 3)["audible_pct"] == 0


@pytest.mark.parametrize("text", ["УКЛОН, ПОЙМА", "Ελληνικά"])
def test_greek_and_cyrillic_ink_matches_the_drawn_face(text):
    from playwright.sync_api import sync_playwright
    from PIL import Image
    from io import BytesIO
    spec = shaping.FontSpec("Fraunces").covering(text)
    shaped = shaping.shape(text, spec, 80)
    assert all(gid for gid, *_ in shaped.glyphs)
    encoded = base64.b64encode(Path(spec.face["path"]).read_bytes()).decode()
    with sync_playwright() as playwright:
        local = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        binary = str(local) if local.exists() else shutil.which("chromium") or playwright.chromium.executable_path
        if not Path(binary).exists():
            pytest.skip("Chromium unavailable")
        browser = playwright.chromium.launch(executable_path=binary)
        page = browser.new_page(viewport={"width": 900, "height": 180})
        styles = ";".join(key + ":" + value for key, value in spec.css().items())
        page.set_content(f"<style>@font-face{{font-family:'{spec.family}';src:url(data:font/ttf;base64,{encoded});"
                         "font-weight:100 900}body{margin:0;overflow:hidden}</style>"
                         f'<svg width="900" height="180"><text x="12" y="110" style="{styles};font-size:80px">{text}</text></svg>')
        page.evaluate("document.fonts.ready")
        pixels = Image.open(BytesIO(page.locator("svg").screenshot())).convert("L")
        ink = pixels.point(lambda value: 255 if value < 190 else 0).getbbox()
        browser.close()
    expected = [12 + shaped.ink[0], 110 + shaped.ink[1], 12 + shaped.ink[2], 110 + shaped.ink[3]]
    assert ink == pytest.approx(expected, abs=2.1)


def test_final_with_seven_cutaways_decodes_each_one(tmp_path, monkeypatch):
    import media
    import renderer
    import w0b_probe
    from test_w4c_broll import _br
    main = w0b_probe.make_source(str(tmp_path / "main.mp4"), w=160, h=96, dur=3)
    still = tmp_path / "blue.png"
    from PIL import Image
    Image.new("RGB", (160, 96), (0, 0, 255)).save(still)
    key = "media/7/blue.png"
    items = [_br(f"br{i}", .4*i, .4*i+.28, key, media="still", device="broll_still")
             for i in range(7)]
    edl = {"keep": [[0., 3.]], "fx": items}
    out = str(tmp_path / "final.mp4")
    run = renderer._render_media_run
    decoder_counts, kills = [], []
    def constrained(cmd, **kw):
        count = sum(Path(v).name.startswith("w4b_") and v.endswith(".mkv") for v in cmd)
        if count > 2:
            kills.append(count)
            raise media.MediaOOMError("fixture decoder memory exhausted")
        if "[vout]" in cmd:
            decoder_counts.append(count)
        return run(cmd, **kw)
    monkeypatch.setattr(renderer, "_render_media_run", constrained)
    info = media.probe(main)
    renderer.render_edl(edl, {"video": info, "words": []}, main, out, str(tmp_path),
                        preview=False, want_wm=True, suppress_outro=True, asset_locals={key: str(still)})
    import subprocess
    pixels = subprocess.check_output(["ffmpeg", "-v", "error", "-i", out, "-an", "-vf",
                                      "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    frames = np.frombuffer(pixels, "uint8").reshape(-1, 3).astype(int)
    assert len(frames) == 75
    for it in items:
        r, g, b = frames[round(25*(it["start"]+.12))]
        assert b > max(r, g) + 100
    assert kills and max(kills) > 2
    assert 0 < max(decoder_counts) <= 2
    delivered = media.probe(out)
    assert delivered["has_audio"]
    import render_qc
    import time
    geometry = render_qc.plan(edl, {}, W=delivered["width"], H=delivered["height"],
                              fps=25, want_wm=True)
    assert render_qc.robot_match(out, geometry, time.monotonic()+30) >= render_qc.ROBOT_MIN_NCC


def test_callback_draws_the_planned_take_when_stock_covers_the_opening(tmp_path, monkeypatch):
    from scene.palette import callback, picks
    take = {"id": "op", "pic": "main", "k": "shot", "f": [0, 120], "src": [7., 11.]}
    stock = dict(take, id="cutaway", pic="stock", src=[0., 4.])
    scene = {"fps": 30, "dur_f": 250, "frame": [160, 96], "items": [stock],
             "opening_shots": [take], "assets": {}}
    monkeypatch.setattr(picks, "pic_of_asset", lambda _, __, aid: picks.Pic(aid, "", "video", 160, 96, 20))
    monkeypatch.setattr(picks, "crops", lambda *args: [[0, 0, 160, 96]]*120)
    monkeypatch.setattr(picks, "luminance", lambda *_: .4)
    result = callback.callback(scene, str(tmp_path), {"at": [180, 210]})
    returned = [it for it in result["items"] if it["id"].startswith("cb.")]
    assert returned and all(it["pic"] == "main" and it["src"][0] == 7 for it in returned)


def test_encoded_window_identity_follows_media_and_device_bytes(tmp_path, monkeypatch):
    import renderer
    monkeypatch.setattr(renderer.shutil, "which", lambda _: str(Path(__file__)))
    source, grade = tmp_path/"source", tmp_path/"grade.cube"
    source.write_bytes(b"picture")
    grade.write_text("grade 1")
    def key(start):
        graph = f"[0:v]trim=start={start},lut3d=file='{grade}'[vout]"
        return renderer._encoded_block_key(["ffmpeg","-i",str(source),"-filter_complex",graph,"out.mp4"],str(tmp_path))
    original = key(1)
    assert original and key(1) == original and key(2) != original
    grade.write_text("different grade")
    assert key(1) != original


@pytest.mark.parametrize("extension", ["png", "jpg"])
def test_depth_samples_the_first_frame_of_a_photo_at_the_batch_rate(tmp_path, monkeypatch, extension):
    import cv2
    import analysis_jobs
    import depth_fx
    import depth_track

    photo = tmp_path / f"local.{extension}"
    image = np.zeros((48, 64, 3), np.uint8)
    image[:, 32:] = 255
    assert cv2.imwrite(str(photo), image)
    ref = depth_fx._asset_ref(str(photo), "fetched/31/photo.png", 31)
    assert (ref.storage_key, ref.project_id) == ("fetched/31/photo.png", 31)
    assert analysis_jobs.sidecars.chunks_for([], ref.duration_s, [(0, .04)]) == [(0, .04)]
    monkeypatch.setattr(depth_track, "_net", lambda *args: object())
    monkeypatch.setattr(depth_track, "infer", lambda net, bgr, size: bgr[:, :, 0])
    path, info = depth_track.child_depth(
        {"params": {"fps": 8, "max_side": 64}, "source": {"width": 64, "height": 48},
         "engine": {"path": "test"}, "src": str(photo), "outdir": str(tmp_path)},
        {"t0": 0, "t1": .04, "out_key": "photo"})
    assert info["frames"] == 1
    with np.load(path) as data:
        assert data["depth"].shape == (1, 48, 64)
        assert data["depth"].max() > data["depth"].min()


def test_dissolve_handles_reach_across_the_crop_handoff(tmp_path):
    import renderer
    import subprocess

    # Three blocks of a single outgoing shot. Its last crop lasts one frame.
    planes = [(4, 32), (2, 112), (1, 208)]
    graph = [f"nullsrc=s=8x8:r=25,format=gray,geq=lum={value},trim=end_frame={n}[b{i}]"
             for i, (n, value) in enumerate(planes)]
    label = renderer._junction_edge(graph, [n for n, _ in planes], 2, "a", 5,
                                    lambda i: f"b{i}", "edge")
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-filter_complex", ";".join(graph),
                                   "-map", f"[{label}]", "-pix_fmt", "gray", "-f", "rawvideo", "-"])
    assert np.frombuffer(raw, "uint8").reshape(-1, 64)[:, 0].tolist() == [32, 32, 112, 112, 208]
