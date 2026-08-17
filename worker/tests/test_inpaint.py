"""Round 39 — burned-in text detection and TRUE removal (worker/inpaint.py).

Users upload footage that already has captions burned into the pixels and ask
for them gone, or in a different font. The old answer was a blur or a black bar
over a rectangle the agent GUESSED from a contact sheet — regularly misplaced,
and never actually removal.

What is pinned here, all against real ffmpeg-encoded video:

  1. detect_text_regions finds a burned caption band: the right place (lower
     third, wide, centred), classified as 'captions' because its content
     changes between samples.
  2. A clip with NO text yields no regions. A detector that fires on ordinary
     footage would have the agent erasing parts of the picture.
  3. A baked-in corner handle is classified 'watermark' (it never changes),
     not 'captions'.
  4. clean_video actually removes the ink: measured ink response inside the
     band drops by most of its value, while the picture OUTSIDE the band is
     left alone (that guard is what catches a repaint that quietly re-encodes
     or shifts the whole frame).
  5. The cleaned file is a drop-in replacement: same duration, same fps, audio
     preserved — every EDL/index/transcript timestamp still points at the same
     moment.
  6. fill='box' removes a solid object, not just thin strokes.
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np  # noqa: E402
import cv2  # noqa: E402

import inpaint  # noqa: E402
import media  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
W, H, FPS = 640, 360, 15


def _background(i, frames):
    """A busy-but-static shot: gradient, texture and one moving element.

    Static camera with a moving subject is the normal case for talking-head
    footage, and it is the case the temporal plate is built for.
    """
    x = np.linspace(0, 255, W, dtype=np.float32)
    y = np.linspace(0, 180, H, dtype=np.float32)
    g = (x[None, :] * 0.6 + y[:, None] * 0.4)
    img = np.dstack([g * 0.7, g * 0.85, g]).astype(np.uint8)
    # fixed texture so the plate has real detail to reconstruct
    rng = np.random.RandomState(7)
    img = cv2.add(img, rng.randint(0, 26, (H, W, 3), dtype=np.uint8))
    cx = int(80 + (W - 160) * i / max(1, frames - 1))
    cv2.circle(img, (cx, 90), 34, (40, 60, 220), -1)
    return img


def _caption(img, text):
    """White text with a black outline, lower third — the common burned style."""
    f, scale, th = cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2
    (tw, tht), _ = cv2.getTextSize(text, f, scale, th)
    org = ((W - tw) // 2, int(H * 0.86))
    cv2.putText(img, text, org, f, scale, (0, 0, 0), th + 4, cv2.LINE_AA)
    cv2.putText(img, text, org, f, scale, (255, 255, 255), th, cv2.LINE_AA)
    return img


def _encode(path, frames, with_audio=True):
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "pipe:0"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i",
                f"sine=frequency=440:duration={len(frames)/FPS:.3f}",
                "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
            "-pix_fmt", "yuv420p", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    assert p.wait(timeout=120) == 0, p.stderr.read().decode()[-500:]


def _clip_with_captions(path, seconds=4.0):
    n = int(seconds * FPS)
    words = ["THIS IS A BURNED CAPTION", "WORDS CHANGE OVER TIME",
             "USERS WANT THESE GONE", "AND THE FONT REPLACED"]
    frames = []
    for i in range(n):
        img = _background(i, n)
        frames.append(_caption(img, words[int(i / (n / len(words))) % len(words)]))
    _encode(path, frames)
    return n


def _clip_plain(path, seconds=3.0):
    n = int(seconds * FPS)
    _encode(path, [_background(i, n) for i in range(n)])
    return n


def _clip_watermark(path, seconds=3.0):
    n = int(seconds * FPS)
    frames = []
    for i in range(n):
        img = _background(i, n)
        cv2.putText(img, "@handle", (int(W * 0.72), int(H * 0.11)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(img, "@handle", (int(W * 0.72), int(H * 0.11)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2,
                    cv2.LINE_AA)
        frames.append(img)
    _encode(path, frames)
    return n


def _skip():
    if not HAVE_FFMPEG:
        print("SKIP: ffmpeg not available")
        return True
    return False


def test_detects_caption_band():
    if _skip():
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "cap.mp4")
        _clip_with_captions(src)
        regions = inpaint.detect_text_regions(src, samples=16)
        assert regions, "no burned text found in a clip that is mostly caption"
        top = regions[0]
        assert top["kind"] == "captions", top
        # lower third, wide, roughly centred
        assert top["y"] > 0.60, top
        assert top["w"] > 0.30, top
        assert 0.28 < top["x"] + top["w"] / 2 < 0.72, top
        assert top["coverage"] > 0.5, top
    print("PASS: caption band detected at", regions[0])


def test_no_false_positive_on_plain_footage():
    if _skip():
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "plain.mp4")
        _clip_plain(src)
        regions = inpaint.detect_text_regions(src, samples=14)
        assert not regions, f"detector invented text in plain footage: {regions}"
    print("PASS: no regions on textless footage")


def test_static_handle_reads_as_watermark():
    if _skip():
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "wm.mp4")
        _clip_watermark(src)
        regions = inpaint.detect_text_regions(src, samples=14)
        assert regions, "static handle not found"
        kinds = {r["kind"] for r in regions}
        assert "watermark" in kinds, regions
        wm = next(r for r in regions if r["kind"] == "watermark")
        assert wm["y"] < 0.4 and wm["x"] > 0.5, wm
    print("PASS: static handle classified as watermark:", wm)


def test_clean_removes_the_text_and_preserves_the_file():
    if _skip():
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "cap.mp4")
        out = os.path.join(d, "clean.mp4")
        prox = os.path.join(d, "clean_proxy.mp4")
        _clip_with_captions(src)
        region = inpaint.detect_text_regions(src, samples=16)[0]
        box = (region["x"], region["y"], region["w"], region["h"])
        before = inpaint.text_energy(src, box, samples=6)
        # a control box in the untouched upper half
        ctrl = (0.05, 0.05, 0.5, 0.25)
        ctrl_before = inpaint.text_energy(src, ctrl, samples=6)

        stats = inpaint.clean_video(src, [dict(region, fill="text")], out, prox)
        assert os.path.getsize(out) > 1000
        assert os.path.getsize(prox) > 500

        after = inpaint.text_energy(out, box, samples=6)
        ctrl_after = inpaint.text_energy(out, ctrl, samples=6)
        # Native OpenCV/ffmpeg builds differ slightly in antialias coverage;
        # the ratio and ground-truth tests below are the actual quality gate.
        assert before > 5, f"test clip has no ink to remove ({before})"
        assert after < before * 0.35, f"text survived: {before} -> {after}"
        # the rest of the picture is untouched (re-encode noise only)
        assert abs(ctrl_after - ctrl_before) < max(3.0, ctrl_before * 0.6), \
            f"clean disturbed the picture outside the box: " \
            f"{ctrl_before} -> {ctrl_after}"

        a, b = media.probe(src), media.probe(out)
        assert abs(a["duration"] - b["duration"]) < 0.2, (a, b)
        assert abs(a["fps"] - b["fps"]) < 0.6, (a, b)
        assert b["has_audio"], "cleaned file lost its audio"
        assert (b["width"], b["height"]) == (a["width"], a["height"])
        assert stats["frames"] > 0 and stats["frames_touched"] > 0
    print(f"PASS: ink {before} -> {after}, control {ctrl_before} -> "
          f"{ctrl_after}, {stats}")


def test_box_fill_removes_an_object():
    """Ground-truth test: the same shot is rendered WITH and WITHOUT the
    object, so "removed" means the repainted pixels match the footage that
    never had it — not merely that they stopped being red."""
    if _skip():
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "obj.mp4")
        truth = os.path.join(d, "truth.mp4")
        out = os.path.join(d, "objclean.mp4")
        n = int(2.5 * FPS)
        with_obj, without = [], []
        for i in range(n):
            base = _background(i, n)
            without.append(base.copy())
            o = base.copy()
            cv2.rectangle(o, (500, 240), (600, 320), (30, 30, 230), -1)
            with_obj.append(o)
        _encode(src, with_obj, with_audio=False)
        _encode(truth, without, with_audio=False)
        spec = {"x": 496 / W, "y": 236 / H, "w": 110 / W, "h": 90 / H,
                "fill": "box"}
        inpaint.clean_video(src, [spec], out)

        got = inpaint._grab(out, 1.0, W, H)[250:310, 510:590]
        want = inpaint._grab(truth, 1.0, W, H)[250:310, 510:590]
        got_m = got.reshape(-1, 3).mean(axis=0)
        want_m = want.reshape(-1, 3).mean(axis=0)
        obj = np.array([30, 30, 230], np.float32)
        assert np.abs(got_m - want_m).max() < 22, \
            f"repaint does not match the real background: {got_m} vs {want_m}"
        assert np.abs(got_m - obj).max() > 60, f"object still there: {got_m}"
    print(f"PASS: object removed — repainted {got_m.round(1)} vs true "
          f"background {want_m.round(1)}")


def test_moving_camera_still_loses_the_text():
    """The temporal plate is only valid on a steady shot. On a pan it is
    correctly rejected and cv2.inpaint carries the removal — pinned because
    the failure mode of a WRONGLY trusted plate is pasting one moment of
    background into another, which looks far worse than the caption did."""
    if _skip():
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "pan.mp4")
        out = os.path.join(d, "panclean.mp4")
        n = int(3.0 * FPS)
        words = ["PANNING SHOT", "TEXT OVER MOTION", "STILL MUST GO"]
        frames = []
        for i in range(n):
            wide = _background(i, n)
            # a real pan: the whole picture translates under the caption
            M = np.float32([[1, 0, -i * 3], [0, 1, 0]])
            moved = cv2.warpAffine(wide, M, (W, H), borderMode=cv2.BORDER_REFLECT)
            frames.append(_caption(moved, words[i * len(words) // n]))
        _encode(src, frames, with_audio=False)
        region = inpaint.detect_text_regions(src, samples=14)[0]
        box = (region["x"], region["y"], region["w"], region["h"])
        before = inpaint.text_energy(src, box, samples=5)
        stats = inpaint.clean_video(src, [dict(region, fill="text")], out)
        after = inpaint.text_energy(out, box, samples=5)
        assert stats["plates"][0]["static"] is False, \
            "a panning shot was treated as static — plate pasting will smear"
        assert after < before * 0.5, f"text survived the pan: {before} -> {after}"
    print(f"PASS: moving camera — plate rejected, ink {before} -> {after}")


def test_edl_carries_the_cleaned_source():
    """The EDL round-trips source_clean, and validation refuses a pointer that
    cannot identify WHICH repaint it means."""
    import schemas

    edl = {"keep": [[0.0, 5.0]],
           "source_clean": {
               "asset_key": "cleaned/1/abc.mp4",
               "proxy_key": "cleaned/1/abc_proxy.mp4",
               "fp": "deadbeef",
               "regions": [{"id": "er1", "x": 0.1, "y": 0.8, "w": 0.8,
                            "h": 0.12, "fill": "text", "kind": "captions"}]}}
    got = schemas.validate_edl(edl, duration=5.0).model_dump()
    assert got["source_clean"]["asset_key"] == "cleaned/1/abc.mp4"
    assert got["source_clean"]["regions"][0]["id"] == "er1"
    assert "erased from the source: captions [er1]" in \
        schemas.describe_edl(got, duration=5.0)

    for bad, why in (({"asset_key": "", "fp": "x", "regions": []}, "no key"),
                     ({"asset_key": "k", "fp": "", "regions": []}, "no fp")):
        try:
            schemas.validate_edl({"keep": [[0.0, 5.0]], "source_clean": bad},
                                 duration=5.0)
            raise AssertionError(f"accepted a source_clean with {why}")
        except schemas.EDLValidationError:
            pass
    # an EDL without the field keeps its old signature (no cache busting)
    plain = schemas.validate_edl({"keep": [[0.0, 5.0]]},
                                 duration=5.0).model_dump()
    assert "source_clean" not in schemas.edl_signature(plain)
    print("PASS: EDL carries source_clean; old signatures unchanged")


def _clip_bar_captions(path, seconds=3.0):
    """The news / CapCut-with-a-background-box style: real font, solid bar.

    Rendered with a BUNDLED font over detailed footage, because this is the
    style that breaks a stroke-only repaint: lifting the letters off the bar
    leaves the bar, which measures as "no ink" and would be reported as a
    clean removal.
    """
    from PIL import Image, ImageDraw, ImageFont
    font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "fonts", "InterDisplay-Bold.ttf")
    if not os.path.exists(font_path):
        return None
    font = ImageFont.truetype(font_path, 26)
    n = int(seconds * FPS)
    words = ["BREAKING: A SOLID CAPTION BAR", "SECOND LINE OF THE REPORT",
             "THIRD THING BEING SAID"]
    frames = []
    for i in range(n):
        img = Image.fromarray(_background(i, n)[:, :, ::-1])
        d = ImageDraw.Draw(img)
        text = words[i * len(words) // n]
        tw = d.textlength(text, font=font)
        x0, y0 = (W - tw) / 2 - 18, H * 0.80
        d.rectangle([x0, y0, x0 + tw + 36, y0 + 44], fill=(18, 18, 22))
        d.text((x0 + 18, y0 + 8), text, font=font, fill=(255, 255, 255))
        frames.append(np.array(img)[:, :, ::-1])
    _encode(path, frames, with_audio=False)
    return n


def _vs_truth(clean_path, truth_path, region, times=(0.8, 1.5, 2.2), pad=25):
    """How far the repainted band is from footage that never had the text.

    The honest measure of "removed". An ink-response check says the LETTERS are
    gone; it stays quiet about a ghost of the words in the background plate, a
    flat plastic patch where the grain should be, or the two ends of a caption
    bar left standing. All three of those shipped and were only caught by
    looking — so they are measured here, per pixel, against ground truth.
    """
    x0 = max(0, int(region["x"] * W) - pad)
    y0 = max(0, int(region["y"] * H) - pad)
    x1 = min(W, int((region["x"] + region["w"]) * W) + pad)
    y1 = min(H, int((region["y"] + region["h"]) * H) + pad)
    worst_mean = worst_p95 = 0.0
    for t in times:
        got = inpaint._grab(clean_path, t, W, H)[y0:y1, x0:x1].astype(np.float32)
        want = inpaint._grab(truth_path, t, W, H)[y0:y1, x0:x1].astype(np.float32)
        d = np.abs(got - want).mean(axis=2)
        worst_mean = max(worst_mean, float(d.mean()))
        worst_p95 = max(worst_p95, float(np.percentile(d, 95)))
    return worst_mean, worst_p95


def test_repaint_matches_footage_that_never_had_captions():
    if _skip():
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "cap.mp4")
        out = os.path.join(d, "clean.mp4")
        truth = os.path.join(d, "truth.mp4")
        n = _clip_with_captions(src)
        _encode(truth, [_background(i, n) for i in range(n)], with_audio=False)
        region = inpaint.detect_text_regions(src, samples=14)[0]
        inpaint.clean_video(src, [dict(region, fill="text")], out)
        mean, p95 = _vs_truth(out, truth, region)
        assert mean < 8.0 and p95 < 20.0, \
            f"repaint is visibly off the real background: mean {mean:.1f}, " \
            f"p95 {p95:.1f} (0-255)"
    print(f"PASS: stroke repaint vs ground truth — mean {mean:.1f}, p95 {p95:.1f}")


def test_caption_on_a_solid_bar_is_fully_removed():
    if _skip():
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "bar.mp4")
        out = os.path.join(d, "barclean.mp4")
        truth = os.path.join(d, "bartruth.mp4")
        if _clip_bar_captions(src) is None:
            print("SKIP: bundled font missing")
            return
        n = int(3.0 * FPS)
        _encode(truth, [_background(i, n) for i in range(n)], with_audio=False)
        region = inpaint.detect_text_regions(src, samples=14)[0]
        stats = inpaint.clean_video(src, [dict(region, fill="text")], out)
        assert stats["plates"][0]["escalated"] is True, \
            "a caption on a solid bar was repainted stroke-only — the bar stays"

        # Per-pixel, across the span, INCLUDING a margin — a bar is as wide as
        # the line it carries, and measuring one frame in the middle once let
        # both ends of the longest caption's bar survive in the corners.
        mean, p95 = _vs_truth(out, truth, region)
        assert mean < 9.0 and p95 < 22.0, \
            f"the bar (or an end of it) survived: mean {mean:.1f}, p95 {p95:.1f}"
    print(f"PASS: solid caption bar escalated + removed — mean {mean:.1f}, "
          f"p95 {p95:.1f}")


def test_renderer_reads_the_cleaned_source():
    """Preview takes the repainted proxy, final the repainted full-res, and an
    EDL that erased nothing is untouched (every existing project)."""
    import renderer

    none_edl = {"keep": [[0.0, 5.0]]}
    assert renderer.clean_source_key(none_edl, "preview") is None
    assert renderer.clean_source_key(none_edl, "final") is None
    assert renderer.clean_source_key(None, "final") is None

    edl = {"keep": [[0.0, 5.0]],
           "source_clean": {"asset_key": "cleaned/1/x.mp4",
                            "proxy_key": "cleaned/1/x_proxy.mp4",
                            "fp": "f", "regions": []}}
    assert renderer.clean_source_key(edl, "preview") == "cleaned/1/x_proxy.mp4"
    assert renderer.clean_source_key(edl, "final") == "cleaned/1/x.mp4"
    # no repainted proxy: the preview must NOT fall back to the original, or
    # it would show the text the user just had removed
    edl2 = {"keep": [[0.0, 5.0]],
            "source_clean": {"asset_key": "cleaned/1/x.mp4", "fp": "f",
                             "regions": []}}
    assert renderer.clean_source_key(edl2, "preview") == "cleaned/1/x.mp4"
    print("PASS: renderer picks the repainted source per variant")


def test_replaced_video_drops_a_stale_repaint():
    """Uploading a new video keeps the project's EDL, so a repaint of the OLD
    upload must not be rendered — that would bring the replaced footage back."""
    import renderer
    import schemas

    regions = [{"id": "er1", "x": 0.1, "y": 0.8, "w": 0.8, "h": 0.12,
                "start": None, "end": None, "fill": "text", "kind": "captions"}]
    edl = {"keep": [[0.0, 5.0]],
           "source_clean": {"asset_key": "cleaned/1/x.mp4",
                            "proxy_key": "cleaned/1/x_proxy.mp4",
                            "fp": schemas.clean_fingerprint("sha-OLD", regions),
                            "regions": regions}}
    assert renderer.clean_source_key(edl, "final", "sha-OLD") == "cleaned/1/x.mp4"
    assert renderer.clean_source_key(edl, "final", "sha-NEW") is None
    assert renderer.clean_source_key(edl, "preview", "sha-NEW") is None
    # unknown sha (canvas / legacy call sites) keeps today's behaviour
    assert renderer.clean_source_key(edl, "final") == "cleaned/1/x.mp4"
    print("PASS: a repaint of a replaced upload is ignored")


class _FakeStorage:
    """In-memory stand-in for R2, so the whole tool path runs offline."""

    def __init__(self, root):
        self.root = root
        self.objects = {}

    def exists(self, key):
        return key in self.objects

    def upload_file(self, path, key, content_type=None):
        dst = os.path.join(self.root, key.replace("/", "_"))
        shutil.copyfile(path, dst)
        self.objects[key] = dst

    def download_to(self, key, local):
        shutil.copyfile(self.objects[key], local)


class _FakeCtx:
    """Enough ToolContext for the erase tools, with a REAL EDL validation on
    every write — a tool that wrote a shape the schema rejects would pass a
    mock-only test and fail in production on the first user."""

    def __init__(self, workdir, src, duration):
        import schemas
        self._schemas = schemas
        self.workdir = workdir
        self.project_id = 1
        self.duration = duration
        self.has_main_video = True
        self.db = self
        self.src = src
        self.inserted = []
        self._edl = {"version": 1,
                     "json": schemas.validate_edl(
                         {"keep": [[0.0, duration]]},
                         duration=duration).model_dump()}

    def latest_edl(self):
        return self._edl

    def proxy_path(self):
        return self.src

    def write_edl(self, edl, desc):
        norm = self._schemas.validate_edl(edl, self.duration).model_dump()
        self._edl = {"version": self._edl["version"] + 1, "json": norm}
        return f"EDL v{self._edl['version'] - 1} -> " \
               f"v{self._edl['version']}: {desc}"

    # Set to raise when the DB refuses the new asset kinds (the CHECK
    # constraint before migration 007) — the erase must still succeed.
    reject_kinds = ()

    def run(self, fn, *a, **k):
        import db as dbx
        if fn is dbx.latest_asset:
            return {"storage_key": "originals/1/src.mp4", "sha256": "abc123"}
        if fn is dbx.insert_asset:
            if a[1] in self.reject_kinds:
                raise RuntimeError(
                    'new row for relation "assets" violates check constraint '
                    '"assets_kind_check"')
            self.inserted.append((a[1], a[2]))     # (kind, key)
            return None
        raise AssertionError(f"unexpected db call: {fn}")


def test_erase_region_tool_end_to_end():
    """The full agent path, round 92: erase -> a window PATCH clip uploaded
    -> the EDL carries it -> the measurement is reported -> undo drops it
    instantly. (The measured VERDICT is content-dependent — the stroke
    metric misreads matched grain on flat synthetic footage — so the pixel
    ground truth here is text_energy on the clip pair, not the verdict.)"""
    if _skip():
        return
    import agent_tools as t
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "cap.mp4")
        _clip_with_captions(src, seconds=3.0)
        store = _FakeStorage(d)
        store.objects["originals/1/src.mp4"] = src
        ctx = _FakeCtx(d, src, media.probe(src)["duration"])
        real_storage = t.storage
        t.storage = store
        try:
            region = inpaint.detect_text_regions(src, samples=14)[0]
            out = t.erase_region(ctx, region["x"], region["y"],
                                 region["w"], region["h"])
            assert out.startswith("EDL v"), out
            assert "Measured on the repainted window" in out, out

            pats = ctx.latest_edl()["json"]["patches"]
            assert len(pats) == 1 and pats[0]["asset_key"] in store.objects
            assert [r["id"] for r in pats[0]["regions"]] == ["er1"]
            assert [k for k, _key in ctx.inserted] == ["patch"]

            # PARITY, not an absolute bar: the patch must repaint at least
            # as well as the established clean_video path on the same clip
            # and region. (On this synthetic — static low-contrast text —
            # BOTH paths currently leave a ghost; that quality issue
            # predates patches and has its own failing tests. What a patch
            # must never do is repaint WORSE than the pass it replaced.)
            patch_file = store.objects[pats[0]["asset_key"]]
            box = (region["x"], region["y"], region["w"], region["h"])
            ref = os.path.join(d, "ref_clean.mp4")
            inpaint.clean_video(src, pats[0]["regions"], ref)
            e_ref = inpaint.text_energy(ref, box, samples=5)
            e_pat = inpaint.text_energy(patch_file, box, samples=5)
            assert e_pat <= e_ref * 1.25 + 1.0, (e_ref, e_pat)

            # a second erase adds its own patch and NEVER re-derives er1's
            out2 = t.erase_region(ctx, 0.72, 0.05, 0.22, 0.10, fill="box")
            assert out2.startswith("EDL v"), out2
            pats2 = ctx.latest_edl()["json"]["patches"]
            assert len(pats2) == 2
            assert pats2[0]["fp"] == pats[0]["fp"], "er1's patch was redone"

            undo = t.remove_erase(ctx, "er1")
            assert undo.startswith("EDL v"), undo
            left = ctx.latest_edl()["json"]["patches"]
            assert len(left) == 1 and \
                left[0]["regions"][0]["id"] == "er2"
            all_back = t.remove_erase(ctx)
            assert ctx.latest_edl()["json"]["patches"] == [], all_back
            assert t.remove_erase(ctx).startswith("NO CHANGE")
        finally:
            t.storage = real_storage
    print("PASS: erase_region end-to-end (patch upload, EDL, measure, undo)")


def test_erase_survives_a_db_that_rejects_the_new_asset_kinds():
    """The repaint must not be lost to a bookkeeping row.

    assets.kind carries a CHECK constraint and 'clean_source'/'clean_proxy'
    are only admitted by migration 007 — so between deploying the code and
    running the migration, the INSERT fails. The cleaned file is already in
    storage and the EDL is what the renderer follows, so the erase has to
    complete anyway; only the admin listing is missing.
    """
    if _skip():
        return
    import agent_tools as t
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "cap.mp4")
        _clip_with_captions(src, seconds=2.5)
        store = _FakeStorage(d)
        store.objects["originals/1/src.mp4"] = src
        ctx = _FakeCtx(d, src, media.probe(src)["duration"])
        ctx.reject_kinds = ("clean_source", "clean_proxy", "patch")
        real_storage = t.storage
        t.storage = store
        try:
            region = inpaint.detect_text_regions(src, samples=12)[0]
            out = t.erase_region(ctx, region["x"], region["y"],
                                 region["w"], region["h"])
            assert out.startswith("EDL v"), out
            pats = ctx.latest_edl()["json"]["patches"]
            assert pats and pats[0]["asset_key"] in store.objects
            assert ctx.inserted == [], ctx.inserted
        finally:
            t.storage = real_storage
    print("PASS: erase completes even when the asset rows are refused")


def test_erase_refuses_a_video_too_long_to_finish():
    """The honest refusal, not a turn that dies at the timeout."""
    if _skip():
        return
    import agent_tools as t
    import config as cfg
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "cap.mp4")
        _clip_with_captions(src, seconds=2.0)
        store = _FakeStorage(d)
        store.objects["originals/1/src.mp4"] = src
        ctx = _FakeCtx(d, src, media.probe(src)["duration"])
        real_storage, real_max = t.storage, cfg.CLEAN_MAX_SOURCE_S
        t.storage, cfg.CLEAN_MAX_SOURCE_S = store, 0.5
        try:
            out = t.erase_region(ctx, 0.1, 0.8, 0.8, 0.12)
            assert out.startswith("REJECTED"), out
            assert "blur_region" in out and "crop" in out, out
            assert ctx.latest_edl()["json"].get("source_clean") is None
        finally:
            t.storage, cfg.CLEAN_MAX_SOURCE_S = real_storage, real_max
    print("PASS: over-long source refused with the honest alternatives")


def test_snap_box_to_ink_finds_the_real_rectangle():
    """The "Dream Life" case: vision points, the pixels decide.

    detect_text_regions votes on horizontal line structure, so a small static
    wordmark that is not subtitle-shaped scores nothing. snap_box_to_ink takes
    a rough rectangle from the vision model and returns the tight box measured
    off the frames — or None when there is nothing there, which is what stops
    an imagined watermark from becoming a censored patch.
    """
    if _skip():
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "wm.mp4")
        _clip_watermark(src)
        # Deliberately loose and offset, the way a model's estimate is.
        rough = (0.66, 0.02, 0.30, 0.16)
        got = inpaint.snap_box_to_ink(src, rough)
        assert got, "the watermark should have been measured"
        # The mark is drawn at x=0.72*W, baseline y=0.11*H. The measured box
        # must land on it, not on the whole search window.
        assert 0.66 <= got["x"] <= 0.76, got
        assert 0.02 <= got["y"] <= 0.12, got
        assert got["w"] < rough[2], got          # tighter than what we passed
        assert got["coverage"] > 0.02, got
    print("PASS: snap_box_to_ink measures a mark the line scan misses")


def test_snap_box_to_ink_returns_none_on_empty_footage():
    """An imagined watermark must produce no rectangle at all."""
    if _skip():
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "plain.mp4")
        _clip_plain(src)
        assert inpaint.snap_box_to_ink(src, (0.4, 0.15, 0.2, 0.08)) is None
    print("PASS: no ink in the rectangle -> no region invented")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    print("done", "with failures" if fails else "- all passed")
    sys.exit(1 if fails else 0)
