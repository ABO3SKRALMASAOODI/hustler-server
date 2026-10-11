"""Track 0B seams in the motion engine (motion_engine.py, motion/lib/).

* motion/lib/*.js joins the runtime in name order, in the documents that use
  it (MG.<name> or the _<name> param); every other document, and the
  runtime with no library file, is exactly runtime.js.
* params._beats: the item's rhythm on its composition clock, normalized and
  bounded; MG.beats reads it in the page (Chromium test).
* clip_store: a local cache miss asks the shared store; a fresh clip is
  handed to it without holding up the render.
* fault ``chromium_crash``: the item's page dies; the layer degrades.
"""

import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import clip_store                                            # noqa: E402
import faultinject                                           # noqa: E402
import motion_engine                                         # noqa: E402
import motion_layer                                          # noqa: E402
import rhythm_types                                          # noqa: E402


def _chromium_ok():
    if not motion_engine.available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(args=motion_engine.CHROME_ARGS).close()
        return True
    except Exception:
        return False


CHROMIUM = _chromium_ok()
needs_browser = pytest.mark.skipif(
    not (CHROMIUM and shutil.which("ffmpeg")),
    reason="headless Chromium + ffmpeg required")


@pytest.fixture
def fresh_runtime():
    motion_engine._RUNTIME_CACHE.clear()
    yield
    motion_engine._RUNTIME_CACHE.clear()


def test_no_library_file_means_the_runtime_is_runtime_js_exactly(
        tmp_path, monkeypatch, fresh_runtime):
    monkeypatch.setattr(motion_engine, "LIB_DIR", str(tmp_path))
    src = open(os.path.join(motion_engine.MOTION_DIR, "runtime.js"),
               encoding="utf-8").read()
    assert motion_engine._runtime_js() == \
        motion_engine.strip_line_comments(src)
    (tmp_path / "b.js").write_text("// note\nwindow.B = 2;\n")
    (tmp_path / "a.js").write_text("window.A = 1;\n")
    (tmp_path / "skip.txt").write_text("not a library")
    out = motion_engine._runtime_js()
    assert out.endswith("\nwindow.A = 1;\n\nwindow.B = 2;\n")
    assert "note" not in out and "not a library" not in out


def test_the_shipped_library_joins_the_documents_that_use_it(fresh_runtime):
    libs = [os.path.basename(p) for p in motion_engine._lib_files()]
    assert "beats.js" in libs
    runtime = motion_engine.strip_line_comments(open(os.path.join(
        motion_engine.MOTION_DIR, "runtime.js"), encoding="utf-8").read())
    # a page that never asks for beats carries runtime.js alone, byte for
    # byte as before Wave 0 (caption pages hold it under the 200 KB cap)
    plain = motion_engine.build_document("<div></div>")
    assert "MG.beats = {" not in plain
    assert f"<script>{runtime}</script>" in plain
    # it joins a page that names MG.beats, or that carries _beats
    used = motion_engine.build_document(
        "<script>MG.frame(t => MG.beats.pulse(t));</script>")
    assert "MG.beats = {" in used
    with_beats = motion_engine.build_document("<div></div>",
                                              beats={"t": [0.5]})
    assert "MG.beats = {" in with_beats
    assert motion_engine.libs_used("<b>MG.beats</b>") == ["beats"]
    assert motion_engine.libs_used("<b></b>", {"_beats": {}}) == ["beats"]
    assert motion_engine.libs_used("<b></b>", {"beats": 1}) == []
    # every library still enters every clip key
    assert "MG.beats = {" in motion_engine._runtime_js()
    # beats.js is comment-stripped like the runtime and stays small
    lib = open(os.path.join(motion_engine.LIB_DIR, "beats.js")).read()
    assert len(motion_engine.strip_line_comments(lib)) < 1500


def test_beats_are_optional_bounded_and_on_the_composition_clock():
    plain = motion_engine.build_document("<b></b>", params={"x": 1},
                                         duration=4.0)
    assert motion_engine.build_document("<b></b>", params={"x": 1},
                                        duration=4.0, beats=None) == plain
    assert motion_engine.build_document("<b></b>", params={"x": 1},
                                        duration=4.0, beats={"t": []}) == \
        plain
    doc = motion_engine.build_document(
        "<b></b>", params={"x": 1}, duration=4.0,
        beats={"t": [3.0, 0.5, 0.5, 9.0, -1.0], "down": [0.5],
               "on": [[1.25, "stress", 1.7], [8.0, "word", 0.5]],
               "src": "speech"})
    assert '"_beats": {"t": [0.5, 3.0], "down": [0.5], ' \
        '"on": [[1.25, "stress", 1.0]], "src": "speech"}' in doc
    many = motion_engine.normalize_beats(
        {"t": [i * 0.001 for i in range(5000)]}, 10.0)
    assert len(many["t"]) == motion_engine.BEATS_MAX


def test_beats_param_moves_a_program_map_onto_the_item():
    rmap = rhythm_types.empty("music", producer="perception.beat_grid@3")
    rmap.update(beats=[1.0, 2.0, 3.0, 4.0, 5.0], downbeats=[1.0, 5.0],
                onsets=[{"t": 2.5, "cls": "kick", "strength": 0.9}],
                clock="program")
    b = motion_engine.beats_param(rmap, start=2.0, end=4.5, phase=0.5)
    assert b == {"t": [0.5, 1.5, 2.5], "down": [],
                 "on": [[1.0, "kick", 0.9]], "src": "music"}
    assert motion_engine.beats_param(None, start=0, end=1) is None


def test_a_local_miss_reads_the_shared_clip_store(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(cache))
    remote = tmp_path / "remote.mov"
    remote.write_bytes(b"MOV")
    meta = {"x": 1, "y": 2, "w": 3, "h": 4, "frames": 5}
    asked = []

    def get(key, dest):
        asked.append((key, dest))
        return (str(remote), dict(meta)) if key == "k1" else None
    monkeypatch.setattr(clip_store, "get", get)
    path, m = motion_engine._cache_get("k1")
    assert m == meta and open(path, "rb").read() == b"MOV"
    assert path == str(cache / "k1.mov")
    assert json.load(open(cache / "k1.json")) == meta
    assert motion_engine._cache_get("k2") is None
    assert asked == [("k1", str(cache)), ("k2", str(cache))]
    # the copy is now local: no second network read
    assert motion_engine._cache_get("k1")[0] == path and len(asked) == 2
    # a store that misbehaves is a miss, never an error
    monkeypatch.setattr(clip_store, "get",
                        lambda k, d: (_ for _ in ()).throw(OSError("down")))
    assert motion_engine._cache_get("k3") is None
    monkeypatch.setattr(clip_store, "get", lambda k, d: ("/nope", {}))
    assert motion_engine._cache_get("k4") is None


def test_a_fresh_clip_is_handed_to_the_store_without_blocking(
        tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "c"))
    src = tmp_path / "clip.mov"
    src.write_bytes(b"CLIP")
    got, done = [], threading.Event()
    release = threading.Event()

    def put(key, path, meta):
        release.wait(5)
        got.append((key, open(path, "rb").read(), meta))
        done.set()
        return True
    monkeypatch.setattr(clip_store, "put", put)
    motion_engine._cache_put("kk", str(src), {"x": 0, "y": 0, "w": 2,
                                              "h": 2, "frames": 1})
    # returned while the upload is still blocked
    assert not done.is_set()
    release.set()
    assert done.wait(5)
    assert got == [("kk", b"CLIP", {"x": 0, "y": 0, "w": 2, "h": 2,
                                    "frames": 1})]


@needs_browser
def test_a_chromium_crash_degrades_the_item_never_the_render(tmp_path):
    job = motion_engine.RenderJob(
        html=motion_engine.build_document("<div>x</div>", duration=0.2),
        out_w=64, out_h=64, fps=10.0, duration=0.2, box=[0, 0, 64, 64],
        label="motion 'w0b' (html)")
    old = motion_engine.CACHE_DIR
    motion_engine.CACHE_DIR = str(tmp_path / "cache")
    try:
        with faultinject.inject("chromium_crash"):
            with pytest.raises(motion_engine.MotionRenderError,
                               match="Target crashed"):
                motion_engine.render_jobs([job], str(tmp_path / "out"))
        # a crash aimed at ANOTHER item leaves this one alone
        with faultinject.inject("chromium_crash:motion 'other' (html)"):
            clip = motion_engine.render_jobs([job], str(tmp_path / "o2"))[0]
        assert not clip.empty and os.path.exists(clip.path)
    finally:
        motion_engine.CACHE_DIR = old


@needs_browser
def test_the_page_reads_its_beats(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "c"))
    body = ("<div id='b' style='position:absolute;left:0;top:0;width:64px;"
            "height:64px;background:#fff;opacity:0'></div><script>"
            "MG.frame(t => { document.getElementById('b').style.opacity = "
            "MG.beats.pulse(t, 0.05) > 0.5 ? 1 : 0; });</script>")
    fps, dur = 10.0, 1.0
    beats = {"t": [0.3, 0.7]}
    job = motion_engine.RenderJob(
        html=motion_engine.build_document(body, duration=dur, fps=fps,
                                          design_w=1080, design_h=1080,
                                          beats=beats),
        out_w=108, out_h=108, fps=fps, duration=dur, box=[0, 0, 640, 640],
        label="motion 'beats' (html)")
    clip = motion_engine.render_jobs([job], str(tmp_path / "out"))[0]
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", clip.path, "-vf",
         "format=rgba,crop=2:2:0:0", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    alphas = [raw[i * 16 + 3] for i in range(len(raw) // 16)]
    lit = [i for i, a in enumerate(alphas) if a > 128]
    assert lit == [3, 7], alphas
