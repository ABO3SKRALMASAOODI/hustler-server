"""Track 4C: the B-roll director — licences, providers, the cinematic
scorer, the ``broll`` cutaway compiler and the source_broll / remove_broll
verbs.

CI-safe: no network (provider responses are recorded shapes fed through a
patched ``net_fetch.get_json``), no models; renders go through the probe
child (tests/w0b_probe.py) with lossless encodes. Pinned:

  * licences: NC / ND / reserved never reach a cutaway (schema and search);
  * providers: Wikimedia Commons, NASA and the Internet Archive parse into
    candidates with complete ledger lines; a photo that is really a web
    page is refused (JRE defect 29);
  * the scorer: a mark welded to a moving picture and a starved encode are
    rejected, a clean clip is kept; the vertical crop aims at the subject;
  * the compiler: a clip cutaway changes only its window (bit-exact
    outside), a still never sits dead (2.5D with depth, Ken Burns without),
    the matched LUT and the archival frame fit render, an OOM omits;
  * the verbs: placed items carry credit and match, place=false writes
    nothing, no live library is an honest-off with a shoot list,
    remove_broll restores the edit, and the verb's op validates exactly
    like apply_edit_batch's.
"""

import copy
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import w0b_probe                                              # noqa: E402
import w4a_support as sup                                     # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg")

CREDIT = {"provider": "wikimedia", "license": "CC BY-SA 4.0",
          "author": "A. Photographer", "title": "Test clip",
          "page_url": "https://commons.wikimedia.org/wiki/File:x.webm",
          "attribution_required": True}


def _br(fid, start, end, key, **kw):
    it = {"id": fid, "type": "broll", "stage": "screen", "start": start,
          "end": end, "source": {"kind": "asset", "asset_key": key},
          "credit": dict(CREDIT), "device": "broll_cutaway"}
    it.update(kw)
    return it


# ── licences and the schema ──────────────────────────────────────────────

@pytest.mark.parametrize("raw,ok", [
    ("CC BY-SA 4.0", True), ("Public domain", True), ("cc0-1.0", True),
    ("by-4.0", True), ("pdm-1.0", True), ("Pexels License", True),
    ("http://creativecommons.org/publicdomain/mark/1.0/", True),
    ("by-nc-4.0", False), ("CC BY-ND 2.0", False), ("GFDL", False),
    ("https://creativecommons.org/licenses/by-nc-nd/4.0/", False),
    ("All rights reserved", False), ("", False)])
def test_licence_policy(raw, ok):
    import broll_source
    assert (broll_source.canon_licence(raw) is not None) is ok, raw


def test_schema_rules():
    from schemas import validate_edl
    base = {"keep": [[0.0, 20.0]]}
    good = dict(base, fx=[_br("b1", 2.0, 4.0, "media/p/x.mp4")])
    m = validate_edl(copy.deepcopy(good), 30.0).model_dump()
    assert m["fx"][0]["credit"]["license"] == "CC BY-SA 4.0"
    bad = [
        dict(base, fx=[_br("b1", 2.0, 4.0, "media/p/x.mp4",
                           credit=dict(CREDIT, license="CC BY-NC 4.0"))]),
        dict(base, fx=[_br("b1", 2.0, 4.0, "media/p/x.mp4",
                           credit=dict(CREDIT, author=None))]),
        dict(base, fx=[dict(_br("b1", 2.0, 4.0, "media/p/x.mp4"),
                            source={"kind": "program"})]),
        dict(base, fx=[_br("b1", 2.0, 4.0, "media/p/x.mp4",
                           move={"path": "push_in"})]),
        dict(base, fx=[_br("b1", 2.0, 2.1, "media/p/x.mp4")]),
        dict(base, fx=[_br("b1", 2.0, 4.0, "media/p/x.mp4",
                           stage="world")]),
    ]
    for e in bad:
        with pytest.raises(Exception):
            validate_edl(copy.deepcopy(e), 30.0)


def test_flash_times_and_ledger():
    from fx_schemas.broll import BrollFx
    import broll_fx
    short = BrollFx(**_br("b1", 2.0, 2.4, "media/p/x.mp4"))
    assert short.flash_times() == [2.0]
    assert BrollFx(**_br("b2", 2.0, 4.0, "k.mp4")).flash_times() == []
    led = broll_fx.ledger({"fx": [_br("b2", 6.0, 8.0, "k.mp4"),
                                  _br("b1", 2.0, 4.0, "k.mp4")]})
    assert [r["id"] for r in led] == ["b1", "b2"]
    assert "CC BY-SA 4.0" in led[0]["line"] and "A. Photographer" in \
        led[0]["line"]


# ── providers (recorded response shapes, no network) ────────────────────

COMMONS = {"query": {"pages": {
    "1": {"pageid": 1, "index": 1, "title": "File:Rocket.webm",
          "videoinfo": [{
              "url": "https://upload.wikimedia.org/a/Rocket.webm",
              "descriptionurl": "https://commons.wikimedia.org/wiki/File:"
                                "Rocket.webm",
              "thumburl": "https://upload.wikimedia.org/t.jpg",
              "width": 1920, "height": 1080, "duration": 12.0,
              "extmetadata": {
                  "LicenseShortName": {"value": "CC BY-SA 4.0"},
                  "LicenseUrl": {"value": "https://creativecommons.org/"
                                          "licenses/by-sa/4.0"},
                  "Artist": {"value": "<a href='x'>Jane Doe</a>"},
                  "ObjectName": {"value": "Rocket launch"}},
              "derivatives": [
                  {"src": "https://upload.wikimedia.org/a/Rocket.webm",
                   "type": 'video/webm; codecs="vp9"', "width": 1920,
                   "height": 1080},
                  {"src": "https://upload.wikimedia.org/t/Rocket.720p.webm",
                   "type": 'video/webm; codecs="vp9, opus"', "width": 1280,
                   "height": 720, "transcodekey": "720p.vp9.webm"}]}]},
    "2": {"pageid": 2, "index": 2, "title": "File:NC.webm",
          "videoinfo": [{"url": "https://u/x.webm", "width": 1920,
                         "height": 1080, "extmetadata": {
                             "LicenseShortName": {"value": "CC BY-NC 4.0"}},
                         "derivatives": [{"src": "https://u/x.webm",
                                          "type": "video/webm",
                                          "width": 1920}]}]}}}}
NASA = {"collection": {"items": [
    {"href": "http://images-assets.nasa.gov/video/A/collection.json",
     "data": [{"nasa_id": "A", "title": "Apollo 11 launch",
               "center": "KSC", "description": "Saturn V lifts off",
               "keywords": ["Apollo", "launch"], "media_type": "video"}],
     "links": [{"href": "http://images-assets.nasa.gov/video/A/t.jpg",
                "render": "image"}]}]}}
IA = {"response": {"docs": [
    {"identifier": "film1", "title": "Factory film 1952",
     "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
     "creator": "Archive Films"},
    {"identifier": "film2", "title": "Restricted",
     "licenseurl": "https://creativecommons.org/licenses/by-nc/4.0/"}]}}


def test_providers_parse_with_full_ledger_lines(monkeypatch):
    import broll_source
    import net_fetch

    def fake(url, **kw):
        if "wikimedia" in url:
            return COMMONS
        if "images-api.nasa.gov" in url:
            return NASA
        if "advancedsearch" in url:
            return IA
        raise AssertionError(url)
    monkeypatch.setattr(net_fetch, "get_json", fake)
    wm = broll_source._wikimedia("rocket", "video", "portrait", 4)
    assert [c["id"] for c in wm] == ["wikimedia:video:1"]  # NC dropped
    c = wm[0]
    assert c["license"] == "CC BY-SA 4.0" and c["credit"] == "Jane Doe"
    assert c["attribution_required"] is True
    assert {f["width"] for f in c["_files"]} == {1920, 1280}
    nasa = broll_source._nasa("apollo", "video", "portrait", 4)
    assert nasa[0]["license"].startswith("Public domain")
    assert nasa[0]["_collection"].startswith("https://")
    ia = broll_source._archive("factory", "video", "portrait", 4)
    assert [x["_ident"] for x in ia] == ["film1"] and ia[0]["archival"]
    cr = broll_source.credit(c)
    from fx_schemas.broll import BrollCredit
    BrollCredit(**cr)                         # a complete ledger line
    assert "Jane Doe" in cr["line"]


def test_a_web_page_saved_as_a_photo_is_refused(tmp_path):
    import broll_source
    p = tmp_path / "photo.jpg"
    p.write_text("<!doctype html><html><body>Login</body></html>")
    with pytest.raises(broll_source.BrollError, match="web page"):
        broll_source.verify_media(str(p), "photo")
    from PIL import Image
    q = tmp_path / "real.jpg"
    Image.new("RGB", (64, 48), (10, 20, 30)).save(q)
    assert broll_source.verify_media(str(q), "photo") == {"width": 64,
                                                           "height": 48}


def test_relevance_and_prerank():
    import broll_source
    cands = [{"id": "a", "provider": "nasa", "kind": "video",
              "title": "Apollo 11 Saturn V launch", "width": 1920,
              "height": 1080, "duration_s": 10},
             {"id": "b", "provider": "wikimedia", "kind": "video",
              "title": "A cat", "width": 1920, "height": 1080,
              "duration_s": 10},
             {"id": "c", "provider": "wikimedia", "kind": "video",
              "title": "Saturn V launch", "width": 320, "height": 240,
              "duration_s": 10}]
    ranked = broll_source.prerank(cands, ["Saturn V launch"], 1080, 1920)
    assert [c["id"] for c in ranked] == ["a", "b"]   # c too small


# ── the scorer ───────────────────────────────────────────────────────────

def _moving_texture(n=4, w=768, h=432, seed=3):
    rng = np.random.default_rng(seed)
    base = (rng.random((h // 8, (w + 400) // 8, 3)) * 255).astype(np.uint8)
    base = np.kron(base, np.ones((8, 8, 1), np.uint8))
    import cv2
    base = cv2.GaussianBlur(base, (0, 0), 2.0)
    return [np.ascontiguousarray(base[:h, 90 * k: 90 * k + w])
            for k in range(n)]


def _with_mark(frames):
    import cv2
    out = []
    for f in frames:
        g = f.copy()
        cv2.putText(g, "@stockclips", (560, 410), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (255, 255, 255), 2, cv2.LINE_AA)
        out.append(g)
    return out


def test_scorer_rejects_a_welded_mark_and_keeps_the_clean_clip():
    import broll_score
    clean = _moving_texture()
    meta = {"kind": "video", "width": 1920, "height": 1080, "fps": 25,
            "duration_s": 6.0, "bytes": int(6 * 25 * 1920 * 1080 * 0.08 / 8),
            "motion_profile": {"mean_frame_change": 4.0}}
    ok = broll_score.score(clean, meta, W=1080, H=1920)
    assert not any(r.startswith("watermark") for r in ok["reject"])
    bad = broll_score.score(_with_mark(clean), meta, W=1080, H=1920)
    assert any(r.startswith("watermark") for r in bad["reject"])
    box = bad["measures"]["watermark_box"]
    assert box[0] > 0.6 and box[1] > 0.8          # where the mark sits


def test_scorer_rejects_a_starved_encode_and_a_tiny_picture():
    import broll_score
    fr = _moving_texture()
    starved = {"kind": "video", "width": 1920, "height": 1080, "fps": 25,
               "duration_s": 6.0, "bytes": int(6 * 300_000 / 8),
               "motion_profile": {"mean_frame_change": 4.0}}
    r = broll_score.score(fr, starved, W=1080, H=1920)
    assert any(x.startswith("low-bitrate") for x in r["reject"])
    tiny = dict(starved, width=480, height=270, bytes=10 ** 7)
    r = broll_score.score(fr, tiny, W=1080, H=1920)
    assert any(x.startswith("too small") for x in r["reject"])
    # a locked shot needs far fewer bits than a busy one
    assert broll_score.bpp_floor({"mean_frame_change": 0.2}) < \
        broll_score.bpp_floor({"mean_frame_change": 6.0})


def test_vertical_crop_aims_at_the_subject():
    import broll_score
    img = np.full((540, 960, 3), 40, np.uint8)
    img[200:420, 760:860] = (230, 210, 190)          # the subject, right
    import cv2
    cv2.circle(img, (810, 180), 40, (220, 200, 180), -1)
    r = broll_score.score([img], {"kind": "still", "width": 960,
                                  "height": 540, "bytes": 400_000},
                          W=1080, H=1920)
    assert r["focus_x"] > 0.6
    assert r["measures"]["crop_survival"] > 0.5


def test_match_lut_pulls_the_cutaway_into_the_hero_world():
    import broll_score
    bright = [np.full((90, 160, 3), (200, 190, 170), np.uint8)]
    rng = np.random.default_rng(1)
    bright[0] = np.clip(bright[0] + rng.normal(0, 20, bright[0].shape),
                        0, 255).astype(np.uint8)
    hero = [np.clip(np.full((90, 160, 3), (40, 60, 70), np.float32)
                    + rng.normal(0, 12, (90, 160, 3)), 0, 255)
            .astype(np.uint8)]
    before, after, lut = broll_score.palette_gap(bright, hero)
    assert lut is not None and before > 15 and after < 0.75 * before


def test_the_world_grade_never_rotates_a_hue():
    """A blue sky stays blue and an orange sun orange: the grade moves
    tone, saturation and the neutrals' warmth, never hue (a full colour
    transfer turned a launch sky mud-brown and the sun cyan — Wave 4
    review)."""
    import broll_score
    import lutlib
    rng = np.random.default_rng(3)
    hero = [np.clip(np.full((90, 160, 3), (70, 52, 40), np.float32)
                    + rng.normal(0, 10, (90, 160, 3)), 0, 255)
            .astype(np.uint8)]
    for colour in ((90, 130, 210), (240, 150, 30), (60, 160, 90)):
        pic = [np.clip(np.full((90, 160, 3), colour, np.float32)
                       + rng.normal(0, 8, (90, 160, 3)), 0, 255)
               .astype(np.uint8)]
        _b, _a, lut = broll_score.palette_gap(pic, hero)
        src = np.asarray(colour, np.float64)[None] / 255.0
        out = lutlib.apply(lut, src)
        la, lb = lutlib.rgb_to_lab(src)[0], lutlib.rgb_to_lab(out)[0]
        ha = np.degrees(np.arctan2(la[2], la[1]))
        hb = np.degrees(np.arctan2(lb[2], lb[1]))
        assert abs((ha - hb + 180) % 360 - 180) < 12, (colour, ha, hb)


# ── the compiler ─────────────────────────────────────────────────────────

def _clip(path, colour="testsrc2", dur=6.0, w=640, h=360):
    if not os.path.exists(path):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"{colour}=s={w}x{h}:r=25", "-t", f"{dur}",
                        "-c:v", "libx264", "-crf", "8", "-pix_fmt",
                        "yuv420p", path], check=True)
    return path


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    import lutlib
    import media
    import sidecars
    d = tmp_path_factory.mktemp("w4c")
    src = sup.make_split_source(str(d / "split.mp4"), dur=12.0)
    info = media.probe(src)
    ref = sidecars.source_from(media.sha256_file(src), src, info, 7, [])
    idx = sup.index_for(ref, info)
    clip = _clip(str(d / "broll.mp4"), "mandelbrot", w=960, h=540)
    still = sup.make_split_still(str(d / "still.png"))
    store = str(d / "store")
    sref = sidecars.source_from(media.sha256_file(still), still,
                                {"duration": 0.04, "fps": 25.0,
                                 "width": 960, "height": 540}, None, [])
    sup.write_depth_chunks(store, sref)
    bare = sup.make_split_still(str(d / "bare.png"), w=900, h=540)
    cube = d / "warm.cube"
    ident = lutlib.identity(9)
    t = ident.table.copy()
    t[..., 0] = np.clip(t[..., 0] * 1.3, 0, 1)
    cube.write_text(lutlib.cube_text(lutlib.Lut(t), title="warm"))
    locals_ = {"media/w4c/clip.mp4": clip, "media/w4c/still.png": still,
               "media/w4c/bare.png": bare, "generated/7/warm.cube": str(cube)}
    base = {"keep": [[0.0, 12.0]]}

    def case(name, fx, faults=None):
        return {"name": name, "src": src, "idx": idx,
                "edl": dict(copy.deepcopy(base), fx=fx), "preview": True,
                "asset_locals": locals_, "faults": faults or []}
    cases = [
        case("control", []),
        case("clip", [_br("b", 2.0, 3.6, "media/w4c/clip.mp4",
                          source={"kind": "asset",
                                  "asset_key": "media/w4c/clip.mp4",
                                  "offset_s": 1.0})]),
        case("graded", [_br("b", 2.0, 3.6, "media/w4c/clip.mp4",
                            source={"kind": "asset",
                                    "asset_key": "media/w4c/clip.mp4",
                                    "offset_s": 1.0},
                            match={"asset_key": "generated/7/warm.cube"},
                            grain=0.3, soften=0.4)]),
        case("frame", [_br("b", 2.0, 3.6, "media/w4c/clip.mp4",
                           fit="frame", device="broll_archival")]),
        case("still", [_br("s", 2.0, 3.6, "media/w4c/still.png",
                           media="still", device="broll_still",
                           move={"path": "push_in", "amount": 0.5})]),
        case("bare", [_br("s", 2.0, 3.6, "media/w4c/bare.png",
                          media="still", device="broll_still")]),
        case("oom", [_br("b", 2.0, 3.6, "media/w4c/clip.mp4")],
             faults=["oom:fx_precompute"]),
    ]
    old = {k: os.environ.get(k) for k in ("VALMERA_SIDECAR_DIR",
                                         "VALMERA_FX_ASSET_LOCALS")}
    os.environ["VALMERA_SIDECAR_DIR"] = store
    os.environ["VALMERA_FX_ASSET_LOCALS"] = json.dumps(locals_)
    try:
        res = w0b_probe.run(cases, str(d / "out"))
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {"res": res, "dir": str(d / "out")}


def _ok(runs, name):
    r = runs["res"][name]
    assert r["error"] is None, r["error"]
    return r


def _pcm(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map",
                          "0:a", "-ac", "1", "-f", "s16le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.int16)


def _rung(r, fid):
    return {row["id"]: row for row in r["report"]}[fid]


def _changed(a, b):
    return [i for i, (x, y) in enumerate(zip(a["frames"], b["frames"]))
            if x != y]


@needs_ffmpeg
@pytest.mark.parametrize("name", ["clip", "graded", "frame", "still",
                                  "bare"])
def test_a_cutaway_changes_only_its_window(runs, name):
    ctl, got = _ok(runs, "control"), _ok(runs, name)
    assert len(got["frames"]) == len(ctl["frames"])
    ch = _changed(ctl, got)
    assert ch and min(ch) >= 50 and max(ch) < 90, (ch[:3], ch[-3:])
    assert len(ch) == 40                     # every frame of the window
    # picture only: the decoded sound is the control's (the AAC encoder
    # may re-pack a frame or two at the start, a few LSBs)
    a = _pcm(os.path.join(runs["dir"], "control.mp4"))
    b = _pcm(os.path.join(runs["dir"], f"{name}.mp4"))
    assert len(a) == len(b) and np.abs(a.astype(int) - b.astype(int))[
        24000:].max() <= 2


@needs_ffmpeg
def test_the_match_grade_and_grain_render(runs):
    a = sup.frames_rgb(os.path.join(runs["dir"], "clip.mp4"))
    b = sup.frames_rgb(os.path.join(runs["dir"], "graded.mp4"))
    da = a[70].astype(float)
    db = b[70].astype(float)
    assert db[..., 0].mean() > da[..., 0].mean() + 5     # the warm cube
    assert _rung(_ok(runs, "graded"), "b")["rung"] == "full"


@needs_ffmpeg
def test_a_still_never_sits_dead(runs):
    for name, rung in (("still", "full"), ("bare", "substitute")):
        r = _ok(runs, name)
        assert _rung(r, "s")["rung"] == rung, (name, _rung(r, "s"))
        fr = sup.frames_rgb(os.path.join(runs["dir"], f"{name}.mp4"))
        moves = [np.abs(fr[k + 1].astype(int) - fr[k].astype(int)).mean()
                 for k in range(52, 88)]
        assert sum(m > 0.05 for m in moves) > 28, name


@needs_ffmpeg
def test_the_archival_frame_shows_the_whole_picture(runs):
    fr = sup.frames_rgb(os.path.join(runs["dir"], "frame.mp4"))
    cl = sup.frames_rgb(os.path.join(runs["dir"], "clip.mp4"))
    g, c = sup.gray(fr[70]), sup.gray(cl[70])
    h, w = g.shape
    # the margin is the darkened soft copy, the cover fit is the picture
    # itself right to the edge; inside, the contained picture differs from
    # the cover crop (it is smaller: the whole frame is shown)
    band = slice(0, max(2, int(h * 0.02)))
    assert g[band].mean() < c[band].mean() - 15
    mid = (slice(h // 3, 2 * h // 3), slice(w // 3, 2 * w // 3))
    assert np.abs(g[mid] - c[mid]).mean() > 5


@needs_ffmpeg
def test_oom_omits_the_cutaway(runs):
    r = _ok(runs, "oom")
    assert r["frames"] == _ok(runs, "control")["frames"]
    assert _rung(r, "b")["rung"] == "omit"


# ── the verbs ────────────────────────────────────────────────────────────

class FakeDB:
    def __init__(self):
        self.keys = {}
        self.assets = []

    def run(self, fn, *a, **kw):
        import db as dbx
        import edit_batch
        if fn is edit_batch.project_media_keys:
            return dict(self.keys)
        if fn is dbx.insert_asset:
            _pid, kind, key = a[:3]
            self.assets.append({"kind": kind, "key": key, **kw})
            self.keys[key] = {"storage_key": key, "duration_s": None}
            return len(self.assets)
        if fn is dbx.latest_asset:
            return None
        raise AssertionError(f"unexpected db call {fn}")


class Ctx:
    def __init__(self, tmp, src, idx, edl):
        self.db = FakeDB()
        self.project_id = 7
        self.workdir = str(tmp)
        self.duration = 12.0
        self.has_main_video = True
        self._edl = edl
        self.version = 1
        self.writes = []
        self.index = idx
        self._proxy = src
        self.pending_images = []
        self.job = {"payload": {"execution_policy": "legacy"}}

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self._edl)}

    def write_edl(self, edl, desc):
        self.writes.append((edl, desc))
        self._edl = edl
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"

    def proxy_path(self):
        return self._proxy


@pytest.fixture
def project(tmp_path, monkeypatch):
    import media
    import remote
    import storage
    from schemas import validate_edl
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: True)
    store = {}

    def upload_file(path, key, ctype=None):
        dst = tmp_path / ("store_" + key.replace("/", "_"))
        shutil.copy(path, dst)
        store[key] = str(dst)
    monkeypatch.setattr(storage, "upload_file", upload_file)
    monkeypatch.setattr(storage, "download_to",
                        lambda k, local: shutil.copy(store[k], local))
    monkeypatch.setattr(storage, "object_bytes", lambda k: 10)
    src = sup.make_split_source(str(tmp_path / "split.mp4"), dur=12.0)
    info = media.probe(src)
    words = [{"w": w, "t0": t, "t1": t + 0.3} for w, t in (
        ("we", 1.0), ("built", 1.4), ("the", 1.8), ("rocket", 2.1),
        ("and", 4.0), ("then", 4.4), ("the", 4.8), ("factory", 5.2))]
    idx = {"words": words, "silences": [], "sentences": [],
           "video": {"width": info["width"], "height": info["height"],
                     "fps": info["fps"], "duration": info["duration"]}}
    edl = validate_edl({"keep": [[0.0, 12.0]],
                        "frame": {"ratio": "9:16", "mode": "crop"}},
                       12.0).model_dump()
    clip = _clip(str(tmp_path / "cand.mp4"), "mandelbrot", w=1920, h=1080,
                 dur=6.0)
    return Ctx(tmp_path, src, idx, edl), clip, store


def _cands(clip):
    return [{"provider": "nasa", "kind": "video", "id": "nasa:video:R1",
             "title": "Rocket launch", "description": "a rocket launch",
             "credit": "NASA/KSC", "license": "Public domain (NASA media "
             "guidelines)", "license_url": "https://www.nasa.gov/",
             "attribution_required": False,
             "page_url": "https://images.nasa.gov/details/R1",
             "width": 1920, "height": 1080, "duration_s": 6.0,
             "_local_path": clip, "_files": [{"link": "x", "width": 1920,
                                              "height": 1080}]}]


@needs_ffmpeg
def test_source_broll_places_licensed_matched_cutaways(project, monkeypatch):
    import broll_source
    from tools_ext import broll_tools
    ctx, clip, _store = project
    monkeypatch.setenv("VALMERA_BROLL_LOCAL", "1")
    # the synthetic clip (a fractal) cannot be graded into the synthetic
    # camera's world; the colour gate is pinned in its own test below
    monkeypatch.setattr(broll_tools, "UNMATCHABLE_DE", 99.0)
    monkeypatch.setattr(broll_source, "status",
                        lambda: {p: "on" for p in broll_source.PROVIDERS})
    monkeypatch.setattr(broll_source, "search",
                        lambda q, k, o, count=4, providers=None,
                        archival=False: (_cands(clip) if k == "video"
                                         else [], [], ["nasa"]))
    out = broll_tools.source_broll(ctx, moments=[
        {"id": "rk", "word": "rocket", "query": "rocket launch",
         "purpose": "the rocket"}])
    assert "EDL v1 -> v2" in out, out
    items = [i for i in ctx._edl["fx"] if i["type"] == "broll"]
    assert len(items) == 1
    it = items[0]
    assert abs(it["start"] - 2.1) < 0.02 and it["credit"]["provider"] == \
        "nasa"
    assert it["bind"]["on"] == "word"
    assert it["source"]["asset_key"] in ctx.db.keys
    assert "LICENCE LEDGER" in out and "NASA" in out
    assert ctx.pending_images                      # the director's board


@needs_ffmpeg
def test_research_only_writes_nothing(project, monkeypatch):
    import broll_source
    from tools_ext import broll_tools
    ctx, clip, _store = project
    monkeypatch.setenv("VALMERA_BROLL_LOCAL", "1")
    monkeypatch.setattr(broll_source, "status",
                        lambda: {p: "on" for p in broll_source.PROVIDERS})
    monkeypatch.setattr(broll_source, "search",
                        lambda *a, **k: (_cands(clip), [], ["nasa"]))
    out = broll_tools.source_broll(ctx, moments=[
        {"word": "factory", "query": "rocket launch"}], place=False)
    assert out.startswith("B-ROLL RESEARCH") and not ctx.writes


def test_no_live_library_is_an_honest_off_with_a_shoot_list(project,
                                                            monkeypatch):
    import broll_source
    from tools_ext import broll_tools
    ctx, _clip_path, _store = project
    monkeypatch.setattr(broll_source, "status", lambda: {
        p: "off: no key" for p in broll_source.PROVIDERS})
    out = broll_tools.source_broll(ctx, moments=[
        {"id": "rk", "word": "rocket", "query": "rocket launch"}])
    assert out.startswith("B-ROLL OFF") and "SHOOT LIST" in out
    assert "rk: film rocket launch" in out and not ctx.writes


def test_moments_are_validated(project):
    from tools_ext import broll_tools
    ctx, _c, _s = project
    assert broll_tools.source_broll(ctx, moments=[]).startswith("REJECTED")
    out = broll_tools.source_broll(ctx, moments=[
        {"word": "zebra", "query": "x"}])
    assert out.startswith("REJECTED") and "zebra" in out


def test_remove_broll_and_the_batch_path(project):
    import edit_batch
    from tools_ext import broll_tools, _write
    ctx, _c, _s = project
    ctx.db.keys["media/p/x.mp4"] = {"storage_key": "media/p/x.mp4",
                                    "duration_s": 6.0}
    item = _br("br1", 2.0, 4.0, "media/p/x.mp4")
    via_batch = edit_batch.apply_batch(copy.deepcopy(ctx._edl),
                                       [_write.upsert_fx(item)], 12.0,
                                       ctx.db.keys)
    out = _write.commit(ctx, [_write.upsert_fx(item)], "x")
    assert out.startswith("EDL") and ctx._edl["fx"] == via_batch["fx"]
    assert broll_tools.remove_broll(ctx, ["nope"]).startswith("REJECTED")
    out = broll_tools.remove_broll(ctx, ["br1"])
    assert out.startswith("EDL") and not [
        i for i in ctx._edl.get("fx") or [] if i.get("type") == "broll"]


def test_registration_and_budgets():
    import agent_tools
    import tools_ext
    from tools_ext import broll_tools
    for v in broll_tools.VERBS:
        assert v in agent_tools.TOOLS and v in tools_ext.EXT_TOOL_NAMES
        assert v in agent_tools.TOOL_DOMAINS["world"]
    assert broll_tools.WRITE_TOOLS <= agent_tools.WRITE_TOOLS
    for name, (_fn, desc, _p) in broll_tools.TOOL_SPECS.items():
        assert len(desc) <= 900, name
    core = tools_ext.core_names(agent_tools.TOOLS)
    cat = agent_tools.openai_tools("any-model", compact=True, names=core)
    assert len(json.dumps(cat, separators=(",", ":"))) <= 82_700


def test_cards_compile_and_gate():
    import devices
    for cid in ("broll_cutaway", "broll_still", "broll_archival"):
        c = devices.card(cid)
        assert c["fx_type"] == "broll" and c["skill"] == "broll-director"
        assert not c["owner_flags"]["adds_zoom"]
        assert not c["owner_flags"]["adds_sound"]


def test_stock_download_refuses_a_page_served_as_a_photo(tmp_path,
                                                         monkeypatch):
    import net_fetch
    import stock

    def fake_download(url, out, **kw):
        with open(out, "w") as f:
            f.write("<html><body>consent wall</body></html>")
        return out, url
    monkeypatch.setattr(net_fetch, "download", fake_download)
    with pytest.raises(stock.StockError, match="web page"):
        stock.download({"kind": "photo", "_url": "https://x/y.jpg"},
                       str(tmp_path / "p.jpg"), 1080, 1920)


@needs_ffmpeg
def test_a_cutaway_the_grade_cannot_bring_home_is_not_placed(project,
                                                             monkeypatch):
    import broll_source
    from tools_ext import broll_tools
    ctx, clip, _store = project
    monkeypatch.setenv("VALMERA_BROLL_LOCAL", "1")
    monkeypatch.setattr(broll_tools, "UNMATCHABLE_DE", 2.0)
    monkeypatch.setattr(broll_source, "status",
                        lambda: {p: "on" for p in broll_source.PROVIDERS})
    monkeypatch.setattr(broll_source, "search",
                        lambda *a, **k: (_cands(clip), [], ["nasa"]))
    out = broll_tools.source_broll(ctx, moments=[
        {"id": "rk", "word": "rocket", "query": "rocket launch"}])
    assert "camera's world" in out and "SHOOT LIST" in out
    assert not ctx.writes
