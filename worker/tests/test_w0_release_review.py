"""Wave 0 final review: the seams the release review closed.

* The shared clip store sits on a render's critical path. A render asks it
  for every key its local LRU lacks at once, on a few threads, within one
  wall-clock budget: N misses never cost N sequential round trips, a slow
  store never stalls a preview past the budget, and a key asked once is not
  asked again by the per-item lookup.
* Uploads to the store run at most two at a time.
* The clip key names what a deploy can change under an unchanged document
  (bundled font files, the capture code, the Chromium build): the shared
  store outlives deploys, so it must never serve a clip drawn with an old
  font file.
* Owner rule 2 on the write path: with web sounds off, no write (add_sfx or
  an edit batch) places a sound search_sfx fetched earlier; sounds already
  in the edit keep playing and may be moved.
"""

import json
import os
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import clip_store                                            # noqa: E402
import motion_engine                                         # noqa: E402

META = {"x": 0, "y": 0, "w": 4, "h": 4, "frames": 3}


class _SlowStore:
    """clip_store.get stand-in: every key answers after ``delay`` seconds;
    ``have`` keys are hits (a tiny .mov written into dest)."""

    def __init__(self, delay, have=()):
        self.delay, self.have = delay, set(have)
        self.asked, self.lock = [], threading.Lock()
        self.inflight = self.peak = 0
        # Event.wait, not time.sleep: a test elsewhere in the suite that
        # stubs time.sleep must not make this store answer instantly.
        self.never = threading.Event()

    def get(self, key, dest):
        with self.lock:
            self.asked.append(key)
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
        try:
            self.never.wait(self.delay)
            if key not in self.have:
                return None
            os.makedirs(dest, exist_ok=True)
            path = os.path.join(dest, key + ".mov")
            with open(path, "wb") as f:
                f.write(b"MOV")
            return path, dict(META)
        finally:
            with self.lock:
                self.inflight -= 1


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(d))
    monkeypatch.setattr(clip_store, "available", lambda: True)
    return d


def test_misses_are_asked_in_parallel_within_one_budget(cache, monkeypatch):
    keys = [f"{i:040x}" for i in range(16)]
    slow = _SlowStore(0.25, have=keys[:4])
    monkeypatch.setattr(clip_store, "get", slow.get)
    t = time.monotonic()
    asked = motion_engine._prefetch_shared(keys, budget_s=10)
    wall = time.monotonic() - t
    assert asked == frozenset(keys)
    assert sorted(slow.asked) == sorted(keys)
    # 16 lookups of 0.25 s: one by one would take 4 s
    assert wall < 2.0, wall
    assert 1 < slow.peak <= motion_engine.SHARED_GET_THREADS
    for k in keys[:4]:                          # hits are local now
        assert json.load(open(cache / f"{k}.json")) == META
    # keys the prefetch asked are not asked again, one by one
    token = motion_engine._SHARED_ASKED.set(asked)
    try:
        before = len(slow.asked)
        assert motion_engine._cache_get(keys[0])[1] == META
        assert motion_engine._cache_get(keys[9]) is None
        assert len(slow.asked) == before
    finally:
        motion_engine._SHARED_ASKED.reset(token)
    # outside a render a single lookup still reads the store
    assert motion_engine._cache_get(keys[10]) is None
    assert slow.asked[-1] == keys[10]


def test_a_slow_store_never_holds_a_render_past_the_budget(cache,
                                                           monkeypatch):
    slow = _SlowStore(3.0)
    monkeypatch.setattr(clip_store, "get", slow.get)
    keys = [f"{i:040x}" for i in range(40)]
    t = time.monotonic()
    asked = motion_engine._prefetch_shared(keys, budget_s=0.3)
    assert time.monotonic() - t < 1.5
    assert asked == frozenset(keys)
    # only the first wave started; nothing new starts after the budget
    threading.Event().wait(0.2)
    assert len(slow.asked) <= motion_engine.SHARED_GET_THREADS


def test_local_hits_and_an_unconfigured_store_ask_nothing(cache,
                                                          monkeypatch):
    slow = _SlowStore(0.0)
    monkeypatch.setattr(clip_store, "get", slow.get)
    os.makedirs(cache, exist_ok=True)
    (cache / "aa.mov").write_bytes(b"MOV")
    (cache / "aa.json").write_text(json.dumps(META))
    assert motion_engine._prefetch_shared(["aa"]) == frozenset()
    monkeypatch.setattr(clip_store, "available", lambda: False)
    assert motion_engine._prefetch_shared(["bb", "cc"]) == frozenset()
    assert slow.asked == []


def test_render_all_reads_the_store_once_per_key_in_parallel(cache,
                                                             monkeypatch,
                                                             tmp_path):
    jobs = [motion_engine.RenderJob(
        html=f"<div>{i}</div>", out_w=64, out_h=64, fps=10.0, duration=0.3,
        label=f"j{i}") for i in range(12)]
    keys = [j.key() for j in jobs]
    slow = _SlowStore(0.2, have=keys)
    monkeypatch.setattr(clip_store, "get", slow.get)
    import asyncio
    pytest.importorskip("playwright.async_api")   # imported before timing
    out = tmp_path / "out"
    out.mkdir()
    t = time.monotonic()
    got = asyncio.run(motion_engine._render_all(jobs, str(out), 1, 60))
    assert time.monotonic() - t < 1.8          # 12 x 0.2 s one by one: 2.4 s
    assert all(c is not None and c.cached for c in got)
    assert sorted(slow.asked) == sorted(keys)  # each key asked exactly once


def test_uploads_run_at_most_two_at_a_time(cache, monkeypatch, tmp_path):
    lock, state = threading.Lock(), {"now": 0, "peak": 0, "done": 0}
    all_done = threading.Event()

    def put(key, path, meta):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        threading.Event().wait(0.05)
        with lock:
            state["now"] -= 1
            state["done"] += 1
            if state["done"] == 6:
                all_done.set()
        return True
    monkeypatch.setattr(clip_store, "put", put)
    src = tmp_path / "clip.mov"
    src.write_bytes(b"CLIP")
    for i in range(6):
        motion_engine._cache_put(f"{i:040x}", str(src), dict(META))
    assert all_done.wait(5)
    assert state["peak"] <= 2


def test_the_clip_key_names_the_font_files_it_draws_with(tmp_path,
                                                         monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    font = fonts / "Display.ttf"
    font.write_bytes(b"glyphs-v1")
    monkeypatch.setattr(motion_engine, "FONTS_DIR", str(fonts))
    monkeypatch.setattr(motion_engine, "_ENV_DIGEST", None)
    job = motion_engine.RenderJob(html="<div>x</div>", out_w=64, out_h=64,
                                  fps=10.0, duration=0.3)
    k1 = job.key()
    assert job.key() == k1                       # stable within a process
    # a deploy that replaces a font file under the same name (a new process)
    font.write_bytes(b"glyphs-v2")
    os.utime(font, (1_800_000_000, 1_800_000_000))
    monkeypatch.setattr(motion_engine, "_ENV_DIGEST", None)
    assert job.key() != k1
    monkeypatch.setattr(motion_engine, "_ENV_DIGEST", None)


# ── owner rule 2 on every write path ────────────────────────────────────

WEB = "music/7/whoosh.mp3"
MINE = "music/7/mine.wav"


def _sfx(key, sid="s1", at=2.0):
    return {"id": sid, "storage_key": key, "at": at}


@pytest.fixture
def web_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "WEB_SFX_ENABLED", False)


def _ctx(sfx=()):
    from w0c_support import tool_context, upload_asset
    ctx, fake = tool_context({"keep": [[0.0, 10.0]], "sfx": list(sfx)})
    upload_asset(fake, WEB, caption="found online by search_sfx",
                 source="freesound")
    upload_asset(fake, MINE, filename="mine.wav")
    return ctx, fake


def test_an_edit_batch_cannot_place_a_web_sound_while_web_sfx_is_off(
        web_off):
    ctx, fake = _ctx()
    edl = dict(ctx.latest_edl()["json"])
    out = ctx.write_edl(dict(edl, sfx=[_sfx(WEB)]), "batch")
    assert out.startswith("REJECTED") and "found online" in out
    assert fake.inserts == []
    # the user's own sound still places through the same write
    out = ctx.write_edl(dict(edl, sfx=[_sfx(MINE)]), "batch")
    assert out.startswith("EDL v"), out


def test_a_web_sound_already_in_the_edit_keeps_playing_and_moves(web_off):
    ctx, fake = _ctx(sfx=[_sfx(WEB)])
    edl = dict(ctx.latest_edl()["json"])
    out = ctx.write_edl(dict(edl, sfx=[_sfx(WEB, at=3.0)]), "move it")
    assert out.startswith("EDL v"), out
    # removing it is always allowed
    edl = dict(ctx.latest_edl()["json"])
    assert ctx.write_edl(dict(edl, sfx=[]), "remove").startswith("EDL v")


def test_with_web_sounds_on_the_write_gate_stands_aside(monkeypatch):
    import config
    import sfx_search
    monkeypatch.setattr(config, "WEB_SFX_ENABLED", True)
    monkeypatch.setattr(sfx_search, "available", lambda: True)
    ctx, _fake = _ctx()
    edl = dict(ctx.latest_edl()["json"])
    assert ctx.write_edl(dict(edl, sfx=[_sfx(WEB)]), "b").startswith("EDL v")


def test_the_shared_rule_is_pure_and_fails_closed():
    import music_gate
    prev = {"sfx": [_sfx("a")]}
    new = {"sfx": [_sfx("a"), _sfx("b", "s2"), _sfx("c", "s3")]}
    assert music_gate.new_sfx_keys(prev, new) == ["b", "c"]
    rows = {"b": {"meta": {"filename": "b.wav"}},
            "c": {"meta": {"caption": "found online by search_sfx"}}}
    assert "found online" in music_gate.gate_web_sounds(prev, new, rows.get)
    assert music_gate.gate_web_sounds(prev, {"sfx": [_sfx("b")]},
                                      rows.get) is None

    def boom(_key):
        raise OSError("db down")
    assert "could not be verified" in music_gate.gate_web_sounds(
        prev, new, boom)


# ── the backend loads the motion modules by path ────────────────────────

def test_the_backend_can_load_the_motion_modules_by_path(tmp_path):
    """Production main's backend (routes/video.py) registers
    worker/motion_engine.py and motion_templates.py under their plain names
    WITHOUT the worker directory on sys.path. A top-level import of another
    worker module there (clip_store, faultinject) fails the backend's import
    — every API route down. Isolated interpreter, empty cwd: the worker dir
    is not importable."""
    import subprocess
    worker = os.path.dirname(HERE)
    code = (
        "import importlib.util, sys\n"
        f"W = {worker!r}\n"
        "for name in ('motion_engine', 'motion_templates'):\n"
        "    spec = importlib.util.spec_from_file_location(\n"
        "        name, W + '/' + name + '.py')\n"
        "    mod = importlib.util.module_from_spec(spec)\n"
        "    sys.modules[name] = mod\n"
        "    spec.loader.exec_module(mod)\n"
        "import motion_engine\n"
        "assert motion_engine._clip_store() is None\n"
        "assert motion_engine._fault('chromium_crash:x') is False\n"
        "assert motion_engine._prefetch_shared(['ab' * 20]) == frozenset()\n"
        "print('loaded')\n")
    out = subprocess.run([sys.executable, "-I", "-c", code],
                         cwd=str(tmp_path), capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0 and "loaded" in out.stdout, out.stderr[-800:]


def test_library_sounds_and_verified_keys_are_never_looked_up(web_off):
    import db as dbx
    import music_gate
    import sound_library
    ctx, fake = _ctx()
    lookups = []
    real_run = fake.run

    def run(fn, *a, **k):
        if fn is dbx.asset_by_key:
            lookups.append(a[1])
        return real_run(fn, *a, **k)
    fake.run = run
    sid = sound_library.catalog()[0]["id"]
    lib_key = sound_library.asset_key(7, sid)
    edl = dict(ctx.latest_edl()["json"])
    assert ctx.write_edl(dict(edl, sfx=[_sfx(lib_key)]), "lib").startswith(
        "EDL v")
    music_gate.grant_sfx(ctx, MINE)            # what add_sfx does
    edl = dict(ctx.latest_edl()["json"])
    out = ctx.write_edl(dict(edl, sfx=edl["sfx"] + [_sfx(MINE, "s2")]), "m")
    assert out.startswith("EDL v"), out
    assert lookups == []
