"""qa_metrics (ROADMAP §4.5, INTERFACES §8.3): every pinned metric on
synthetic inputs whose right answer is known by construction.

CI-safe: numpy arrays, plus tiny lossless clips made with the ffmpeg on PATH
(skipped when there is none). No fixture, no reference, no model.
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np                                            # noqa: E402
import pytest                                                 # noqa: E402

import qa_metrics as q                                        # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and \
    shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg")


def test_pinned_names_registered():
    for name in q.PINNED:
        assert name in q.METRICS, name
    assert len(q.PINNED) == 12


def test_register_refuses_duplicates_and_bad_names():
    with pytest.raises(ValueError):
        q.register("land_err_f")(lambda **k: None)
    with pytest.raises(ValueError):
        q.register("Bad-Name")


def test_parse_verify_expressions():
    assert q.parse_verify("land_err_f<=1") == ("land_err_f", "<=", 1.0)
    assert q.parse_verify("fill_iou >= 0.95") == ("fill_iou", ">=", 0.95)
    assert q.parse_verify("flash_risk") == ("flash_risk", None, None)
    assert q.known("matte_iou>=0.92") and not q.known("nope<=1")
    with pytest.raises(ValueError):
        q.parse_verify("land_err_f <= one")


def test_bars_and_checks():
    assert q.check(1, "<=1") is True and q.check(2, "<=1") is False
    assert q.check(0.93, (">=", 0.92)) is True
    assert q.check(None, "<=1") is False
    assert q.check(5, None) is None
    assert q.bar_text(("<=", 1)) == "<=1"


def test_land_err_f_pairs_targets_with_nearest_events():
    r = q.land_err_f(events=[10, 31, 52], targets=[10, 30, 50])
    assert r["value"] == 2.0 and r["pass"] is False
    assert [p["err_f"] for p in r["detail"]["pairs"]] == [0, 1, 2]
    r = q.land_err_f(events=[10, 31, 51], targets=[10, 30, 50])
    assert r["value"] == 1.0 and r["pass"] is True
    r = q.land_err_f(event_times=[1.0], target_times=[1.034], fps=30)
    assert r["value"] == 1.0
    r = q.land_err_f(events=[], targets=[12])
    assert r["value"] == float("inf") and r["pass"] is False
    assert r["detail"]["missing"] == [12]
    r = q.land_err_f(events=[5, 40], targets=[5])
    assert r["detail"]["stray"] == [40]
    r = q.land_err_f(events=[11], targets=[10], bar="<=2")
    assert r["pass"] is True


def _disc(h, w, cx, cy, r):
    yy, xx = np.mgrid[:h, :w]
    return ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r


def test_matte_iou_exact_and_shifted():
    a = _disc(100, 100, 50, 50, 20)
    assert q.matte_iou(pred={0: a}, truth={0: a})["value"] == 1.0
    b = _disc(100, 100, 54, 50, 20)
    r = q.matte_iou(pred={0: a, 1: a}, truth={0: a, 1: b})
    inter = (a & b).sum()
    union = (a | b).sum()
    assert r["value"] == pytest.approx((1 + inter / union) / 2)
    assert r["detail"]["min"] == pytest.approx(inter / union)
    # object masks get the 0.85 bar by default
    r = q.matte_iou(pred={0: a}, truth={0: b}, kind="object")
    assert r["bar"] == ">=0.85"
    with pytest.raises(ValueError):
        q.matte_iou(pred={0: a}, truth={0: np.zeros((5, 5))})
    # uint8 masks are read as 0..255
    r = q.matte_iou(pred={0: a.astype(np.uint8) * 255}, truth={0: a})
    assert r["value"] == 1.0


def test_matte_flicker_static_vs_shimmering_edge():
    m = _disc(120, 120, 60, 60, 30).astype(np.float32)
    assert q.matte_flicker(masks=[m] * 6)["value"] == 0.0
    rng = np.random.default_rng(3)
    noisy = []
    band = _disc(120, 120, 60, 60, 33) & ~_disc(120, 120, 60, 60, 27)
    for _ in range(6):
        x = m.copy()
        x[band] = np.clip(x[band] + rng.normal(0, 0.3, band.sum()), 0, 1)
        noisy.append(x)
    r = q.matte_flicker(masks=noisy)
    assert r["value"] > 0.035 and r["pass"] is False


def test_edge_chatter_counts_toggling_pixels():
    h, w = 50, 50
    base = np.zeros((h, w), bool)
    base[10:40, 10:40] = True
    masks = []
    for t in range(30):
        m = base.copy()
        if t % 2:
            m[0, 0:25] = True            # 25 px toggle every frame
        masks.append(m)
    r = q.edge_chatter(masks=masks, fps=30)
    assert r["value"] == pytest.approx(25 / (h * w))
    assert r["pass"] is False
    assert q.edge_chatter(masks=[base] * 30, fps=30)["value"] == 0.0


def test_glyph_visible_word_and_letters():
    h, w = 40, 100
    letters = []
    for i in range(4):
        g = np.zeros((h, w), bool)
        g[10:30, 5 + 25 * i:20 + 25 * i] = True
        letters.append(g)
    subject = np.zeros((h, w), bool)
    subject[:, 30:45] = True          # hides letter 1 entirely
    r = q.glyph_visible(glyphs=letters, subject=subject)
    assert r["value"] == pytest.approx(0.75)
    assert r["detail"]["below_letter_bar"] == {"1": 0.0}
    assert r["pass"] is False        # a letter is gone and not ghosted
    r = q.glyph_visible(glyphs=letters, subject=subject, ghosted=True)
    assert r["pass"] is True
    r = q.glyph_visible(glyphs=letters, subject=np.zeros((h, w)))
    assert r["value"] == 1.0 and r["pass"] is True


def test_apca_matches_runtime_constants():
    # white on black / black on white: the published APCA magnitudes
    assert q.apca(1.0, 0.0) == pytest.approx(107.88, abs=0.1)
    assert q.apca(0.0, 1.0) == pytest.approx(106.04, abs=0.1)
    assert q.apca(0.5, 0.5) == 0.0
    assert q.luminance("#ffffff") == pytest.approx(1.0)
    assert q.luminance("#000") == pytest.approx(0.0)


def test_legibility_ink_on_plate_and_from_frame():
    dark = np.full((10, 10, 3), 20, np.uint8)
    r = q.legibility(ink="#ffffff", plate=dark)
    assert r["value"] > 90 and r["pass"] is True
    grey = np.full((10, 10, 3), 200, np.uint8)
    r = q.legibility(ink="#ffffff", plate=grey)
    assert r["value"] < 45 and r["pass"] is False
    r = q.legibility(ink="#ffffff", plate=grey, role="connector")
    assert r["bar"] == ">=45"
    frame = np.full((60, 120, 3), 30, np.uint8)
    glyph = np.zeros((60, 120), bool)
    glyph[20:40, 20:100] = True
    frame[glyph] = 250
    r = q.legibility(frame=frame, glyph_mask=glyph)
    assert r["value"] > 90


def test_reproj_rms_px_scales_to_1080p():
    t = [[0, 0], [100, 0], [100, 100], [0, 100]]
    p = [[1, 0], [101, 0], [101, 100], [1, 100]]
    r = q.reproj_rms_px(pred=p, truth=t, frame_h=540)
    assert r["value"] == pytest.approx(2.0)
    assert r["pass"] is False
    assert q.reproj_rms_px(pred=t, truth=t)["value"] == 0.0


def test_region_locality():
    a = np.zeros((20, 20, 3), np.uint8)
    b = a.copy()
    b[5:10, 5:10] = 200
    region = np.zeros((20, 20), bool)
    region[5:10, 5:10] = True
    assert q.region_locality(with_fx=[b], without_fx=[a],
                             region=region)["value"] == 0.0
    b[0, 0] = 3
    r = q.region_locality(with_fx=[b], without_fx=[a], region=region)
    assert r["value"] == 3.0 and r["pass"] is False


def test_flash_risk_counts_flashes_per_second():
    def frames(pattern, n=60):
        out = []
        for i in range(n):
            v = 230 if pattern(i) else 20
            out.append(np.full((36, 64, 3), v, np.uint8))
        return out
    calm = frames(lambda i: False)
    assert q.flash_risk(frames=calm, fps=30)["value"] == 0.0
    # 1 white frame every 10 frames: 3 flashes per second
    three = frames(lambda i: i % 10 == 5)
    r = q.flash_risk(frames=three, fps=30)
    assert r["value"] == 3.0 and r["pass"] is True
    # every other frame: 15 flashes per second
    strobe = frames(lambda i: i % 2 == 0)
    r = q.flash_risk(frames=strobe, fps=30)
    assert r["value"] >= 14 and r["pass"] is False
    assert r["detail"]["contains_flashing"] is True
    r = q.flash_risk(frames=strobe, fps=30, opted_in=True)
    assert r["pass"] is None and r["detail"]["contains_flashing"] is True


def test_flash_risk_ignores_small_or_bright_only_changes():
    out = []
    for i in range(60):
        f = np.full((36, 64, 3), 20, np.uint8)
        if i % 2:
            f[:6, :6] = 255                # a small blinking corner
        out.append(f)
    assert q.flash_risk(frames=out, fps=30)["value"] == 0.0
    bright = [np.full((36, 64, 3), 230 if i % 2 else 220, np.uint8)
              for i in range(60)]
    assert q.flash_risk(frames=bright, fps=30)["value"] == 0.0


def test_determinism_and_cost_ratio():
    r = q.determinism(md5s=[["a", "b", "c"], ["a", "b", "c"]])
    assert r["value"] == 0.0 and r["pass"] is True
    r = q.determinism(md5s=[["a", "b", "c"], ["a", "x", "c", "d"]])
    assert r["value"] == 2.0 and r["pass"] is False
    with pytest.raises(ValueError):
        q.determinism(md5s=[["a"]])
    r = q.cost_ratio(with_s=13.0, without_s=10.0)
    assert r["value"] == pytest.approx(1.3) and r["pass"] is None
    assert q.cost_ratio(with_s=13.0, without_s=10.0, bar="<=1.25")["pass"] \
        is False


def test_evaluate_dispatch_and_unknown():
    r = q.evaluate("land_err_f", events=[3], targets=[3])
    assert r["pass"] is True
    with pytest.raises(KeyError):
        q.evaluate("nope")


# ── media helpers (lossless synthetic clips) ─────────────────────────────

def _write_clip(path, frames, fps=30, audio=None):
    h, w = frames[0].shape[:2]
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
           "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio, "-shortest"]
    cmd += ["-c:v", "ffv1", "-pix_fmt", "bgr0"]
    if audio:
        cmd += ["-c:a", "pcm_s16le"]
    cmd += [path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f, np.uint8).tobytes())
    p.stdin.close()
    assert p.wait() == 0


@needs_ffmpeg
def test_read_frames_probe_and_md5s(tmp_path):
    frames = [np.full((32, 48, 3), 10 * i, np.uint8) for i in range(12)]
    p = str(tmp_path / "a.mkv")
    _write_clip(p, frames)
    info = q.probe(p)
    assert (info["width"], info["height"]) == (48, 32)
    assert round(info["fps"]) == 30
    idx, fr = q.read_frames(p, frames={3, 7})
    assert idx == [3, 7]
    assert int(fr[0][0, 0, 0]) == 30 and int(fr[1][0, 0, 0]) == 70
    idx, fr = q.read_frames(p, start_frame=5, count=2, seek=True)
    assert idx == [5, 6] and int(fr[0][0, 0, 0]) == 50
    m1 = q.frame_md5s(p)
    assert len(m1) == 12 and len(set(m1)) == 12
    r = q.determinism(paths=[p, p])
    assert r["value"] == 0.0


@needs_ffmpeg
def test_duration_exact_and_audio_identity(tmp_path):
    frames = [np.full((32, 48, 3), 100, np.uint8) for _ in range(30)]
    a, b, c = (str(tmp_path / n) for n in ("a.mkv", "b.mkv", "c.mkv"))
    tone = "sine=frequency=440:sample_rate=48000:duration=1"
    _write_clip(a, frames, audio=tone)
    flashed = [f.copy() for f in frames]
    flashed[10][:] = 255
    _write_clip(b, flashed, audio=tone)
    r = q.duration_exact(with_fx=b, without_fx=a)
    assert r["value"] == 0.0 and r["pass"] is True
    assert r["detail"]["audio_identical"] is True
    _write_clip(c, frames[:25], audio=tone)
    r = q.duration_exact(with_fx=c, without_fx=a)
    assert r["value"] == 5.0 and r["pass"] is False


@needs_ffmpeg
def test_flash_risk_from_video(tmp_path):
    frames = [np.full((36, 64, 3), 230 if i % 2 else 20, np.uint8)
              for i in range(60)]
    p = str(tmp_path / "s.mkv")
    _write_clip(p, frames)
    r = q.flash_risk(video=p)
    assert r["pass"] is False and r["value"] >= 14


@needs_ffmpeg
def test_loudness_of_a_tone(tmp_path):
    p = str(tmp_path / "t.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=1000:sample_rate=48000:duration=4",
                    "-af", "volume=-20dB", p], check=True)
    lufs = q.loudness_lufs(p)
    # lavfi sine peaks at 1/8 full scale (-18.06 dBFS): a 1 kHz sine at
    # peak P reads P - 3.01 + 0.0 ~= -21.1 LUFS, then -20 dB
    assert lufs is not None and abs(lufs - (-41.1)) <= 1.0


def test_ink_mask_separates_type_from_its_scrim():
    control = np.full((60, 120, 3), 120, np.uint8)
    render = control.copy()
    render[10:50, 10:110] = 70                    # a darkening scrim
    glyph = np.zeros((60, 120), bool)
    glyph[22:38, 20:100] = True
    render[glyph] = 250                           # white type on it
    ink, changed = q.ink_mask(render, control)
    assert (ink == glyph).all()
    assert changed.sum() > ink.sum()
    r = q.legibility(frame=render, glyph_mask=ink)
    assert r["value"] > 60                        # white on the dark scrim
    # no scrim: the whole change is the ink
    plain = control.copy()
    plain[glyph] = 250
    ink, changed = q.ink_mask(plain, control)
    assert (ink == glyph).all() and (changed == glyph).all()


def test_legibility_judges_each_ink_colour_on_its_own():
    """A red stat over a white label on black: the red is the weak ink
    (APCA ~40 on black), so legibility reports it, not the white."""
    frame = np.zeros((120, 200, 3), np.uint8)
    red = np.zeros((120, 200), bool)
    red[20:50, 30:170] = True
    white = np.zeros((120, 200), bool)
    white[70:90, 30:170] = True
    frame[red] = (254, 58, 45)
    frame[white] = (245, 243, 239)
    r = q.legibility(frame=frame, glyph_mask=red | white)
    assert r["detail"]["groups"] is not None
    lcs = sorted(g["lc"] for g in r["detail"]["groups"])
    assert r["value"] == pytest.approx(lcs[0])
    assert 30 < lcs[0] < 50 and lcs[1] > 90
    assert r["pass"] is False


def test_legibility_ring_skips_the_glyphs_own_edges():
    """The plate ring never samples the type's anti-aliased edge (passed
    as ``exclude``): a grey halo around white type is not its plate."""
    frame = np.full((80, 160, 3), 10, np.uint8)
    g = np.zeros((80, 160), bool)
    g[30:50, 20:140] = True
    frame[g] = 250
    edge = q._dilate(g, 3) & ~g
    frame[edge] = 200                       # a bright anti-aliased halo
    with_halo = q.legibility(frame=frame, glyph_mask=g, band_px=6)
    clean = q.legibility(frame=frame, glyph_mask=g, band_px=6,
                         exclude=q._dilate(g, 3))
    assert clean["value"] > with_halo["value"]
    assert clean["value"] > 90


def test_letter_height_is_the_median_component():
    m = np.zeros((100, 200), bool)
    m[10:40, 10:30] = True                  # three 30 px letters
    m[10:40, 40:60] = True
    m[10:40, 70:90] = True
    m[60:62, 10:190] = True                 # a 2 px underline
    assert q.letter_height(m) == 30
