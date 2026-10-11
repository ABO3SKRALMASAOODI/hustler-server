"""Track 0A: content-addressed persistence for motion-graphic clips.

clip_store is best effort: it never raises and never blocks a render. The
acceptance test AT-0A-2 renders a 6-item preview, empties the container's
local motion cache (a cold interactive container) and renders it again:
with clip_store behind motion_engine's cache (the wiring track 0B owns,
INTERFACES.md §2.7, reproduced here exactly as specified) the second render
starts no Chromium at all.
"""

import json
import os
import shutil
import subprocess
import time

import pytest

import clip_store
import sidecars

KEY = "ab" * 20


@pytest.fixture
def store(tmp_path):
    s = sidecars.LocalStore(str(tmp_path / "bucket"))
    clip_store.use_store(s)
    yield s
    clip_store.use_store(None)


def _mov(path, payload=b"clip-bytes" * 100):
    with open(path, "wb") as f:
        f.write(payload)
    return str(path)


def test_put_then_get_round_trips_bytes_and_meta(store, tmp_path):
    src = _mov(tmp_path / "a.mov")
    meta = {"x": 10, "y": 20, "w": 300, "h": 120, "frames": 45}
    assert clip_store.put(KEY, src, meta) is True
    assert os.path.exists(os.path.join(store.root, "motion-clips", "ab",
                                       KEY + ".mov"))
    got = clip_store.get(KEY, str(tmp_path / "cold"))
    assert got is not None
    path, back = got
    assert open(path, "rb").read() == open(src, "rb").read()
    assert back == meta                       # bookkeeping keys stripped
    assert clip_store.object_key(KEY) == f"motion-clips/ab/{KEY}.mov"


def test_the_meta_is_written_last_and_means_complete(store, tmp_path):
    src = _mov(tmp_path / "a.mov")
    store.put_file(clip_store.object_key(KEY), src)      # .mov only
    assert clip_store.get(KEY, str(tmp_path / "x")) is None
    clip_store.put(KEY, src, {"x": 0, "y": 0, "w": 1, "h": 1, "frames": 1})
    # a truncated object never comes back as a clip
    store.put_bytes(clip_store.object_key(KEY), b"short")
    assert clip_store.get(KEY, str(tmp_path / "y")) is None


def test_it_never_raises(store, tmp_path, monkeypatch):
    src = _mov(tmp_path / "a.mov")
    assert clip_store.put("../../etc", src, {}) is False
    assert clip_store.put(KEY, str(tmp_path / "missing.mov"), {}) is False
    assert clip_store.put(KEY, src, "not a dict") is False
    assert clip_store.get("NOT-HEX", str(tmp_path)) is None
    assert clip_store.get(None, str(tmp_path)) is None

    def boom(*a, **k):
        raise OSError("bucket unreachable")
    monkeypatch.setattr(store, "put_file", boom)
    monkeypatch.setattr(store, "get_bytes", boom)
    assert clip_store.put(KEY, src, {"x": 1}) is False
    assert clip_store.get(KEY, str(tmp_path)) is None


def test_size_cap_and_off_switch(store, tmp_path, monkeypatch):
    import config
    src = _mov(tmp_path / "a.mov")
    monkeypatch.setattr(config, "CLIP_STORE_MAX_BYTES", 10)
    assert clip_store.put(KEY, src, {"x": 1}) is False
    clip_store.use_store(False)
    assert clip_store.get(KEY, str(tmp_path)) is None
    clip_store.use_store(None)
    monkeypatch.setattr(config, "CLIP_STORE_ENABLED", False)
    assert clip_store._store() is None


def test_an_old_hit_refreshes_its_last_use(store, tmp_path):
    src = _mov(tmp_path / "a.mov")
    clip_store.put(KEY, src, {"x": 1})
    meta_path = os.path.join(store.root, *clip_store.meta_key(KEY).split("/"))
    old = json.load(open(meta_path))
    old["stored_at"] = time.time() - 40 * 86400
    json.dump(old, open(meta_path, "w"))
    assert clip_store.get(KEY, str(tmp_path / "z")) is not None
    assert time.time() - json.load(open(meta_path))["stored_at"] < 60


# ── AT-0A-2: a cold container, a warm store, zero Chromium renders ─────

def _chromium_ok():
    try:
        import motion_engine
        return motion_engine.available() and shutil.which("ffmpeg")
    except Exception:  # noqa: BLE001
        return False


def _wire_clip_store(monkeypatch, motion_engine):
    """The motion_engine wiring INTERFACES.md §2.7 assigns to track 0B:
    the local LRU first, then the shared store; every put goes to both."""
    real_get, real_put = motion_engine._cache_get, motion_engine._cache_put

    def cache_get(key):
        hit = real_get(key)
        if hit:
            return hit
        got = clip_store.get(key, motion_engine.CACHE_DIR)
        if not got:
            return None
        path, meta = got
        with open(os.path.join(motion_engine.CACHE_DIR, key + ".json"),
                  "w") as f:
            json.dump(meta, f)
        return path, meta

    def cache_put(key, src, meta):
        real_put(key, src, meta)
        clip_store.put(key, src, meta)
    monkeypatch.setattr(motion_engine, "_cache_get", cache_get)
    monkeypatch.setattr(motion_engine, "_cache_put", cache_put)


SIX = [("hook_title", {"text": "Six *items*"}),
       ("word_slam", {"text": "*WARM*"}),
       ("lower_third", {"name": "Track 0A", "role": "clip store"}),
       ("counter", {"value": 140, "label": "renders saved"}),
       ("glow_title", {"text": "No Chromium"}),
       ("marker_text", {"text": "cold container"})]


def _six_item_edl(dur):
    import motion_templates
    items = []
    for i, (name, params) in enumerate(SIX):
        start = round(0.2 + i * (dur - 1.8) / 6.0, 2)
        items.append({"id": f"m{i}", "template": name, "start": start,
                      "end": round(start + 1.5, 2),
                      "params": motion_templates.check_params(name, params)})
    return {"keep": [[0.0, dur]], "motion": items}


@pytest.mark.skipif(not _chromium_ok(), reason="needs Chromium and ffmpeg")
def test_at_0a_2_cold_container_renders_six_items_without_chromium(
        tmp_path, monkeypatch, store):
    import motion_engine
    import renderer
    dur = 6.0
    src = str(tmp_path / "src.mp4")
    subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                           f"testsrc2=s=640x360:r=25:d={dur}", "-f", "lavfi",
                           "-i", f"sine=f=220:d={dur}", "-c:v", "libx264",
                           "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                           src])
    index = {"video": {"duration": dur, "width": 640, "height": 360,
                       "fps": 25.0}, "words": [], "sentences": []}
    edl = _six_item_edl(dur)
    _wire_clip_store(monkeypatch, motion_engine)
    renders = []
    real_one = motion_engine._render_one

    async def counting(browser, job, out_path, deadline):
        renders.append(job.label)
        return await real_one(browser, job, out_path, deadline)
    monkeypatch.setattr(motion_engine, "_render_one", counting)

    def render(tag):
        cache = tmp_path / f"motion-cache-{tag}"    # a fresh container
        monkeypatch.setattr(motion_engine, "CACHE_DIR", str(cache))
        work = tmp_path / f"work-{tag}"
        work.mkdir()
        out = str(tmp_path / f"{tag}.mp4")
        t = time.monotonic()
        got = renderer.render_edl(edl, index, src, out, str(work),
                                  preview=True)
        return got, time.monotonic() - t, out

    d1, wall1, out1 = render("warm-up")
    first = len(renders)
    assert first == 6, renders                  # every item drew once
    stored = [n for n in os.listdir(os.path.join(store.root,
                                                 "motion-clips", "ab"))] \
        if os.path.isdir(os.path.join(store.root, "motion-clips", "ab")) \
        else []
    keys = set()
    for root, _d, files in os.walk(os.path.join(store.root, "motion-clips")):
        keys |= {f[:-9] for f in files if f.endswith(".mov.json")}
    assert len(keys) == 6, (keys, stored)

    hits = []
    real_get = clip_store.get
    monkeypatch.setattr(clip_store, "get", lambda key, dest: (
        hits.append(key), real_get(key, dest))[1])
    d2, wall2, out2 = render("cold")
    assert len(renders) == first                # ZERO Chromium renders
    assert set(hits) == keys                    # all six came from the store
    assert d2 == pytest.approx(d1, abs=0.05)
    assert os.path.getsize(out2) > 0
    print(f"AT-0A-2: 6-item preview, first render {wall1:.1f}s with "
          f"{first} Chromium renders; cold container {wall2:.1f}s with 0")
