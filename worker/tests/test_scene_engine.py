"""The engine's golden tests (worker/scene), promoted from the Director slice with the engine.
Synthetic sources only: nothing here depends on a session pack.

BUILD S4's four tests come first. The rest pin what the owner named: exact frames, centred cards,
clean type, and sound that lands on its event. Everything that draws needs a GL context, so the
module skips where there is none (moderngl missing, or no EGL); test_scene_plan.py covers the rest.
"""
import json
import math
import os
import shutil
import subprocess
import types

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

pytest.importorskip("moderngl")
pytest.importorskip("uharfbuzz")

import scene                                    # noqa: E402
import scene.audio as AU                        # noqa: E402
import scene.compile as SC                      # noqa: E402
import scene.reader as SR_                      # noqa: E402
import scene.run as RUN                         # noqa: E402
import scene.type as ST                         # noqa: E402
from scene.faults import SceneFault            # noqa: E402
from scene.type import Face, find_font          # noqa: E402

FF = SR_.FF
if not shutil.which(FF):
    pytest.skip("no ffmpeg on this machine", allow_module_level=True)
try:
    from scene.runtime import make_context
    make_context().release()
except SceneFault as _e:
    pytest.skip(f"no GL context here: {_e}", allow_module_level=True)


class Engine:
    """The slice tests' handle on the engine: compile, then draw in this process."""

    def __init__(self, l1, work, drop=()):
        self.plan = SC.compile(l1, work, segment=False)
        self._e = RUN.Engine(self.plan, drop=drop)
        self.rt = self._e.rt

    def stills(self, frames):
        return self._e.stills(frames)

    def close(self):
        self._e.close()


def _render(l1, work, out, frames=None, preset="medium", audio=True, frames_log=False):
    return RUN.render_plan(SC.compile(l1, work), out, tier="master", frames=frames, audio=audio, frames_log=frames_log,
                           cache=os.path.join(os.path.dirname(out), "_segcache"))


def _check_film(l1, work, film):
    return RUN.check_film(lambda drop_id=None: SC.compile(l1 if drop_id is None else dict(l1, items=[i for i in l1["items"] if i["id"] != drop_id]), work), film)


engine = types.SimpleNamespace(render=_render, probe=SR_.probe, mix_audio=AU.mix_audio, SR=AU.SR, SOUNDS=AU.SOUNDS,
                               Plan=lambda l1, work: SC.compile(l1, work, segment=False), Decoder=SR_.Decoder,
                               voice_layer=AU.voice_layer, check_film=_check_film, compiler_advance=ST.compiler_advance, FP=SR_.FP)

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD = os.path.join(HERE, "scene_golden")
FPS = 30
BARS = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0),
        (128, 128, 128), (235, 64, 16)]
def _font_or_none(name):
    try:
        return find_font(name)
    except FileNotFoundError:
        return None


# Arabic-script type needs the images' fonts-noto-core (or VALMERA_SCRIPT_FONTS pointing at a copy)
ARABIC = _font_or_none("NotoSansArabic-Bold.ttf")
needs_arabic = pytest.mark.skipif(ARABIC is None, reason="no Arabic-script font on this machine")
VOICE = {"a": {"font": "InterDisplay-Bold.ttf", "px": 58, "track": -3}, "b": {"font": "InstrumentSerif-Italic.ttf", "px": 62, "track": 0}}


def film(items, dur=10, assets=None, film_=None, voices=None, sound=(), spine=(), frame=(1080, 1920), seed=7):
    return {"l1": 1, "sid": "t", "seed": seed, "fps": FPS, "frame": list(frame), "dur_f": dur, "assets": assets or {},
            "spine": list(spine), "voices": voices or VOICE, "film": film_ if film_ is not None else {"dither": 0.0},
            "items": items, "sound": list(sound)}


def plate(dur=10, c="#102030"):
    return {"id": "p", "k": "shot", "f": [0, dur], "pic": "plate", "plate": {"c0": c, "c1": c, "grain": 0}}


def bars_img(w=640, h=360):
    a = np.zeros((h, w, 3), np.uint8)
    bw = w // len(BARS)
    for i, c in enumerate(BARS):
        a[:, i * bw:(i + 1) * bw] = c
    return a


def encode(path, frames, w, h, matrix="bt709", audio=None, crf=10, fps=FPS, full=False):
    """Encode RGB frames with the tags and range a phone would write."""
    rng = "pc" if full else "tv"
    cmd = [FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio, "-shortest", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-vf", f"scale=in_range=pc:out_color_matrix={matrix}:out_range={rng}:flags=accurate_rnd+full_chroma_int+lanczos,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-color_range", rng, "-colorspace", matrix,
            "-color_primaries", "bt709" if matrix == "bt709" else "bt470bg", "-color_trc", "bt709" if matrix == "bt709" else "smpte170m", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    assert p.wait() == 0


def barcode(n, w=360, h=640):
    """A frame that carries its own number, in 10 blocks across the top."""
    a = np.full((h, w, 3), 40, np.uint8)
    bw = w // 10
    for b in range(10):
        if (n >> b) & 1:
            a[0:bw, b * bw:(b + 1) * bw] = 255
    a[-60:, :60] = (220, 30, 30)       # a red corner, bottom left: any flip moves it
    return a


def read_code(frame):
    H, W = frame.shape[:2]
    n = 0
    for b in range(10):
        y, x = int(0.5 * W / 10), int((b + 0.5) * W / 10)
        if frame[y, x, 0] > 128:
            n |= 1 << b
    return n


@pytest.fixture(scope="session")
def src(tmp_path_factory):
    d = tmp_path_factory.mktemp("src")
    p = str(d / "code.mp4")
    encode(p, (barcode(n) for n in range(150)), 360, 640)
    return p


@pytest.fixture(scope="session")
def tex(tmp_path_factory):
    """A still with no self-similarity, for registration."""
    d = tmp_path_factory.mktemp("tex")
    rng = np.random.RandomState(3)
    a = cv2.resize(rng.randint(0, 255, (48, 27, 3), dtype=np.uint8), (1080, 1920), interpolation=cv2.INTER_CUBIC)
    a = cv2.GaussianBlur(a, (0, 0), 6)
    a = np.clip((a.astype(float) - 128) * 2.2 + 128, 0, 255).astype(np.uint8)
    p = str(d / "tex.png")
    Image.fromarray(a).save(p)
    return p


def stills(l1, frames, work="/", drop=()):
    e = Engine(l1, work, drop=drop)
    try:
        return e.stills(frames)
    finally:
        e.close()


def one(l1, f=0, **kw):
    return stills(l1, [f], **kw)[f][..., :3].astype(np.int16)


# ---- BUILD S4, test 1: golden frame for orientation

def test_golden_orientation():
    voices = {"big": {"font": "InterDisplay-Bold.ttf", "px": 300, "track": 0}}
    l1 = film([plate(), {"id": "w", "k": "word", "f": [0, 10], "t": "Fg", "v": "big", "x": 0.12, "y": 0.30, "ink": "#FFFFFF"}], voices=voices)
    a = one(l1)
    assert a.shape == (1920, 1080, 3)
    for y, x in ((0, 0), (0, 1079), (1919, 0), (1919, 1079), (1000, 540)):
        assert np.abs(a[y, x] - (16, 32, 48)).max() <= 1
    ink = a[..., 0] > 128
    ys, xs = np.where(ink)
    assert ys.min() < 0.30 * 1920 < ys.max(), "the descender of g must fall below the baseline"
    assert xs.min() >= 0.12 * 1080 - 1
    # Independent raster of the same word, by FreeType through Pillow: only the true orientation matches.
    f = ImageFont.truetype(find_font("InterDisplay-Bold.ttf"), 300)
    im = Image.new("L", (1080, 1920), 0)
    ImageDraw.Draw(im).text((0.12 * 1080, 0.30 * 1920), "Fg", font=f, fill=255, anchor="ls")
    b = np.asarray(im, float)
    m = (a[..., 0].astype(float) - 16) / 239 * 255

    def corr(p, q):
        return float(np.corrcoef(p.ravel(), q.ravel())[0, 1])
    assert corr(m, b) > 0.985
    assert corr(m[::-1], b) < 0.7 and corr(m[:, ::-1], b) < 0.7
    # The stored golden frame, looked at by eye once.
    os.makedirs(GOLD, exist_ok=True)
    g = os.path.join(GOLD, "orientation.png")
    if not os.path.exists(g):
        Image.fromarray(a.astype(np.uint8)).save(g)
    gold = np.asarray(Image.open(g).convert("RGB"), np.int16)
    assert np.abs(gold - a).max() <= 1


# ---- BUILD S4, test 2: a known colour bar through the reader

@pytest.mark.parametrize("matrix,full", [("bt709", False), ("smpte170m", False), ("bt470bg", False), ("bt709", True)])
def test_colour_bars_through_reader(tmp_path, matrix, full):
    p = str(tmp_path / "bars.mp4")
    w, h = 640, 360
    encode(p, [bars_img(w, h)] * 8, w, h, matrix=matrix, crf=4, full=full)
    a = dict(engine.probe(p), abs=p, rate=FPS)
    d = engine.Decoder(a, 2)
    b = np.frombuffer(d.next(), np.uint8).reshape(h, w, 4)
    d.close()
    bw = w // len(BARS)
    worst = 0
    for i, c in enumerate(BARS):
        got = b[h // 2 - 20:h // 2 + 20, i * bw + bw // 2 - 12:i * bw + bw // 2 + 12, :3].reshape(-1, 3).mean(0)
        worst = max(worst, float(np.abs(got - c).max()))
    assert worst <= 2.0, (matrix, worst)


def test_colour_through_the_whole_path(tmp_path):
    """Reader, GL, encoder, decode: the picture out equals the picture in."""
    src_ = str(tmp_path / "bars.mp4")
    w, h = 1080, 1920
    encode(src_, [bars_img(w, h)] * 8, w, h, crf=4)
    l1 = film([{"id": "s", "k": "shot", "f": [0, 6], "pic": "main", "src": [0.0, 0.2], "crop": [0, 0, 1, 1]}], dur=6,
              assets={"main": {"kind": "video", "path": src_}})
    out = str(tmp_path / "o.mp4")
    engine.render(l1, "/", out, audio=False, frames_log=False)
    a = dict(engine.probe(out), abs=out, rate=FPS)
    assert a["matrix"] == "bt709" and a["range"] == "tv"
    d = engine.Decoder(a, 3)
    b = np.frombuffer(d.next(), np.uint8).reshape(h, w, 4)
    d.close()
    bw = w // len(BARS)
    for i, c in enumerate(BARS):
        got = b[900:1000, i * bw + 30:i * bw + 70, :3].reshape(-1, 3).mean(0)
        assert np.abs(got - c).max() <= 2.5, (c, got)


# ---- BUILD S4, test 3: picture and audio lengths agree within a frame

def test_lengths_agree(tmp_path, src):
    p = str(tmp_path / "with_audio.mp4")
    encode(p, (barcode(n) for n in range(150)), 360, 640, audio="sine=frequency=1000:duration=5")
    m = {x["id"]: x for x in json.load(open(engine.SOUNDS + "/manifest.json"))}["impact_1"]
    snd = [{"id": "a", "s": "impact_1", "file": m["file"], "event_f": 60, "start_s": 2 - m["peak_s"], "trim_s": 0, "db": -6, "peak_s": m["peak_s"]}]
    l1 = film([{"id": "s", "k": "shot", "f": [0, 97], "pic": "main", "src": [0, 97 / FPS], "crop": [0, 0, 1, 1]}], dur=97,
              assets={"main": {"kind": "video", "path": p}}, sound=snd, spine=[{"f": [0, 97], "src": [0.0, 97 / FPS]}], film_={"grain": 0.04})
    out = str(tmp_path / "f.mp4")
    log = engine.render(l1, "/", out)
    r = subprocess.run([engine.FP, "-v", "error", "-count_frames", "-show_entries", "stream=codec_type,nb_read_frames,duration",
                        "-of", "json", out], capture_output=True, text=True)
    st = {s["codec_type"]: s for s in json.loads(r.stdout)["streams"]}
    assert int(st["video"]["nb_read_frames"]) == 97
    v, a = float(st["video"]["duration"]), float(st["audio"]["duration"])
    assert abs(v - a) <= 1 / FPS, (v, a)
    assert abs(v - 97 / FPS) <= 1 / FPS
    assert abs(log["audio"]["lufs"] + 14) <= 1.0, log["audio"]


# ---- BUILD S4, test 4: a negative rect registers to its frame

def test_negative_registers(tex):
    rect = [0.2, 0.3, 0.8, 0.6]
    n = 5
    crops = [[0.1 + 0.002 * i, 0.1 + 0.002 * i, 0.9 - 0.002 * i, 0.9 - 0.002 * i] for i in range(n)]
    items = [{"id": "s", "k": "shot", "f": [0, n], "pic": "t", "crop": crops}, {"id": "n", "k": "flash", "f": [2, 4], "mode": "neg", "rect": rect}]
    l1 = film(items, dur=n, assets={"t": {"kind": "image", "path": tex}})
    base = stills(l1, [1, 2, 3, 4], drop=("flash",))
    full = stills(l1, [1, 2, 3, 4])
    W, H = 1080, 1920
    x0, y0, x1, y1 = int(rect[0] * W), int(rect[1] * H), int(rect[2] * W), int(rect[3] * H)
    for f in (2, 3):
        b, g = base[f][..., :3].astype(np.int16), full[f][..., :3].astype(np.int16)
        assert np.abs((255 - b[y0:y1, x0:x1]) - g[y0:y1, x0:x1]).max() <= 2, "inside: the exact inverse, pixel for pixel"
        out = np.ones((H, W), bool)
        out[y0:y1, x0:x1] = False
        assert np.abs(b - g)[out].max() <= 1, "outside: untouched"
        # Registration, measured: the shift that best aligns the negative to the picture is zero.
        p, q = b[y0:y1, x0:x1, 1].astype(np.float32), (255 - g[y0:y1, x0:x1, 1]).astype(np.float32)
        (dx, dy), _ = cv2.phaseCorrelate(p, q)
        assert abs(dx) < 0.1 and abs(dy) < 0.1
    for f in (1, 4):
        assert np.array_equal(base[f], full[f]), "no flash outside its planned frames"
    # hard edge: full strength on the first pixel inside, none on the last pixel outside
    g, b = full[2][..., :3].astype(np.int16), base[2][..., :3].astype(np.int16)
    assert np.abs((255 - b[900, x0]) - g[900, x0]).max() <= 2
    assert np.abs(g[900, x0 - 1] - b[900, x0 - 1]).max() <= 1


# ---- the gate: 2-frame cards on the exact planned frames, by frame diff on the encoded file

def test_two_frame_cards_on_exact_frames(tmp_path, tex, src):
    items = [{"id": "s", "k": "shot", "f": [0, 60], "pic": "t", "crop": [[0.1, 0.1, 0.9, 0.9]] * 60}]
    plan = [(7, "neg"), (19, "neg"), (20 + 11, "threshold"), (44, "neg")]
    for n, (f, m) in enumerate(plan):
        items.append({"id": f"c{n}", "k": "flash", "f": [f, f + 2], "mode": m, "rect": [0.1 + 0.05 * n, 0.2, 0.9, 0.7], "th": 0.5})
    l1 = film(items, dur=60, assets={"t": {"kind": "image", "path": tex}}, film_={"grain": 0.05, "vignette": 0.3})
    out = str(tmp_path / "g.mp4")
    engine.render(l1, "/", out, audio=False)
    r = engine.check_film(l1, "/", out)
    assert r["frames_in_file"] == 60
    assert r["short_flashes"] == 4 and r["on_exact_frames"] == 4, r
    assert r["decode_errors"] == "" and r["sources_newer_than_film"] == []


# ---- frames: every programme frame shows the source frame the plan says

def test_frame_exact(src):
    items = [
        {"id": "a", "k": "shot", "f": [0, 20], "pic": "main", "src": [1.0, 1.0 + 20 / FPS], "crop": [0, 0, 1, 1]},
        {"id": "b", "k": "shot", "f": [20, 40], "pic": "main", "src": [0.2, 0.2 + 20 / FPS], "crop": [0, 0, 1, 1], "step": 3},   # jump back, step print
        {"id": "c", "k": "shot", "f": [40, 50], "pic": "main", "src": [3.0, 3.0], "crop": [0, 0, 1, 1]},                         # freeze
        {"id": "d", "k": "shot", "f": [50, 60], "pic": "main", "src": [2.0, 2.0 + 20 / FPS], "crop": [0, 0, 1, 1]},              # double speed
    ]
    l1 = film(items, dur=60, assets={"main": {"kind": "video", "path": src}})
    fr = stills(l1, list(range(60)))
    want = {}
    for f in range(20):
        want[f] = 30 + f
    for f in range(20, 40):
        want[f] = 6 + (f - 20) // 3 * 3
    for f in range(40, 50):
        want[f] = 90
    for f in range(50, 60):
        want[f] = 60 + 2 * (f - 50)
    got = {f: read_code(a) for f, a in fr.items()}
    assert got == want
    a = fr[0]
    assert a[-30, 30, 0] > 180 and a[-30, -30, 0] < 90, "the red corner stays bottom left"


def test_sparse_frames_never_stall_the_reader(src):
    """Regression: the read-ahead window counted programme frames, so a gap wider than it deadlocked."""
    items = [{"id": "s", "k": "shot", "f": [0, 148], "pic": "main", "src": [0.0, 148 / FPS], "crop": [0, 0, 1, 1]}]
    l1 = film(items, dur=148, assets={"main": {"kind": "video", "path": src}})
    fr = stills(l1, [0, 60, 120, 147])
    assert {f: read_code(a) for f, a in fr.items()} == {0: 0, 60: 60, 120: 120, 147: 147}


def test_a_frame_used_twice_far_apart_is_still_there(src):
    """Regression: on a sparse request the reader had passed the second use when the first released it."""
    items = [{"id": "a", "k": "shot", "f": [0, 10], "pic": "main", "src": [1.0, 1.0], "crop": [0, 0, 1, 1]},
             {"id": "b", "k": "shot", "f": [10, 140], "pic": "main", "src": [0.0, 130 / FPS], "crop": [0, 0, 1, 1]},
             {"id": "c", "k": "shot", "f": [140, 148], "pic": "main", "src": [1.0, 1.0], "crop": [0, 0, 1, 1]}]
    fr = stills(film(items, dur=148, assets={"main": {"kind": "video", "path": src}}), [0, 30, 60, 90, 120, 141])
    assert read_code(fr[0]) == 30 and read_code(fr[141]) == 30 and read_code(fr[60]) == 50


def test_slow_motion_reads_the_sources_own_frames(tmp_path):
    """Half speed on a 60 fps upload shows a new source frame every output frame, no repeats."""
    p = str(tmp_path / "s60.mp4")
    encode(p, (barcode(n) for n in range(120)), 360, 640, fps=60)
    n = 20
    items = [{"id": "s", "k": "shot", "f": [0, n], "pic": "main", "src": [0.5, 0.5 + n / FPS / 2], "crop": [0, 0, 1, 1]}]
    fr = stills(film(items, dur=n, assets={"main": {"kind": "video", "path": p}}), list(range(n)))
    got = [read_code(fr[f]) for f in range(n)]
    assert got == [30 + f for f in range(n)]


def test_edge_directed_upscale_stays_true_to_the_source(tmp_path):
    """The upscale is clamped to its 4 nearest texels: no specks, no ringing, close to Catmull-Rom."""
    rng = np.random.RandomState(5)
    a = rng.randint(0, 2, (106, 60, 3), dtype=np.uint8) * 255      # the worst case: hard binary noise
    p = str(tmp_path / "noise.png")
    Image.fromarray(a).save(p)
    shot = {"id": "s", "k": "shot", "f": [0, 2], "pic": "t", "crop": [0, 0, 1, 1]}
    assets = {"t": {"kind": "image", "path": p}}
    e = one(film([shot], dur=2, assets=assets, film_={"dither": 0.0}))
    c = one(film([shot], dur=2, assets=assets, film_={"dither": 0.0, "no_easu": True}))
    assert e.min() >= 0 and e.max() <= 255
    flat = np.full((106, 60, 3), 90, np.uint8)
    Image.fromarray(flat).save(p)
    f = one(film([shot], dur=2, assets=assets, film_={"dither": 0.0}))
    assert f.min() >= 89 and f.max() <= 91, "a flat picture stays flat: no invalid pixels"
    assert np.abs(e.astype(float) - c.astype(float)).mean() < 40


def test_same_source_twice_on_one_frame(src):
    """A shot and a card can show two different moments of one upload at once."""
    items = [{"id": "s", "k": "shot", "f": [0, 4], "pic": "main", "src": [1.0, 1.0 + 4 / FPS], "crop": [0, 0, 1, 1]},
             {"id": "c", "k": "card", "f": [0, 4], "pic": "main", "src": [0.0, 4 / FPS], "crop": [0, 0, 1, 0.4], "x": 0.5, "y": 0.7, "w": 1.0, "h": 0.40 * (640 / 360) * (1080 / 1920)}]
    l1 = film(items, dur=4, assets={"main": {"kind": "video", "path": src}})
    a = stills(l1, [2])[2]
    assert read_code(a) == 32
    H = int(0.7 * 1920 - 0.5 * items[1]["h"] * 1920)
    assert read_code(a[H:]) == 2


def test_past_the_end_holds_the_last_picture(src):
    items = [{"id": "s", "k": "shot", "f": [0, 6], "pic": "main", "src": [4.8, 4.8 + 6 / FPS], "crop": [0, 0, 1, 1]}]
    l1 = film(items, dur=6, assets={"main": {"kind": "video", "path": src}})
    fr = stills(l1, list(range(6)))
    assert read_code(fr[5]) >= 148, "never a black frame while sound runs"


def test_pure_function_of_the_plan(tex):
    """Any frame can be rendered alone, with grain, and it is the same frame."""
    items = [{"id": "s", "k": "shot", "f": [0, 30], "pic": "t", "crop": [[0.1, 0.1, 0.9, 0.9]] * 30},
             {"id": "n", "k": "flash", "f": [10, 12], "mode": "neg", "rect": [0.2, 0.2, 0.8, 0.8]},
             {"id": "w", "k": "word", "f": [5, 25], "t": "agora", "v": "a", "x": 0.3, "y": 0.5, "ink": "#FFFFFF"}]
    l1 = film(items, dur=30, assets={"t": {"kind": "image", "path": tex}}, film_={"grain": 0.05, "vignette": 0.3})
    seq = stills(l1, [9, 10, 11, 12, 20])
    alone = stills(l1, [11])
    assert np.array_equal(seq[11], alone[11])
    again = stills(l1, [20])
    assert np.array_equal(seq[20], again[20])
    assert not np.array_equal(seq[9], seq[12]), "grain moves"


# ---- cards

def card_mask(l1, f=0):
    a = one(l1, f)
    return np.abs(a - (16, 32, 48)).sum(2) > 60


def test_card_is_centred():
    c = {"id": "c", "k": "card", "f": [0, 2], "pic": "plate", "plate": {"c0": "#F0F0F0", "c1": "#F0F0F0"}, "x": 0.5, "y": 0.5, "w": 0.7, "h": 0.4, "s": 1, "op": 1, "radius": 0.02}
    m = card_mask(film([plate(2), c], dur=2))
    ys, xs = np.where(m)
    assert abs(xs.mean() - 539.5) < 0.05 and abs(ys.mean() - 959.5) < 0.05
    assert xs.max() - xs.min() + 1 == round(0.7 * 1080) and ys.max() - ys.min() + 1 == round(0.4 * 1920)


def test_card_settle_follows_the_baked_curve():
    n = 20
    s = [0.84 + 0.16 * math.exp(-i / 6.9) for i in range(n)]
    c = {"id": "c", "k": "card", "f": [0, n], "pic": "plate", "plate": {"c0": "#F0F0F0", "c1": "#F0F0F0"}, "x": 0.5, "y": 0.5, "w": 0.6, "h": 0.3, "s": s, "op": 1}
    fr = stills(film([plate(n), c], dur=n), [0, 6, 19])
    for f, a in fr.items():
        m = np.abs(a[..., :3].astype(np.int16) - (16, 32, 48)).sum(2) > 60
        xs = np.where(m.any(0))[0]
        assert abs((xs.max() - xs.min() + 1) - 0.6 * 1080 * s[f]) <= 1.5


def test_card_rotates_clockwise():
    c = {"id": "c", "k": "card", "f": [0, 2], "pic": "plate", "plate": {"c0": "#F0F0F0", "c1": "#F0F0F0"}, "x": 0.5, "y": 0.5, "w": 0.7, "h": 0.2, "s": 1, "rot": 10, "op": 1}
    m = card_mask(film([plate(2), c], dur=2))
    left, right = np.where(m[:, 300])[0].mean(), np.where(m[:, 780])[0].mean()
    assert right > left + 50, "clockwise: the right end goes down"


def test_card_edge_is_antialiased():
    c = {"id": "c", "k": "card", "f": [0, 2], "pic": "plate", "plate": {"c0": "#F0F0F0", "c1": "#F0F0F0"}, "x": 0.5, "y": 0.5, "w": 0.7, "h": 0.2, "s": 1, "rot": 7, "op": 1}
    a = one(film([plate(2), c], dur=2))[..., 0]
    mid = ((a > 40) & (a < 220)).sum()
    edge = ((a > 16) & (a < 240)).sum()
    assert mid > 500 and mid >= 0.4 * edge


def test_transparent_logo_on_a_card_has_no_box(tmp_path):
    a = np.zeros((200, 200, 4), np.uint8)
    cv2.circle(a, (100, 100), 70, (255, 255, 255, 255), -1, cv2.LINE_AA)
    p = str(tmp_path / "logo.png")
    Image.fromarray(a).save(p)
    c = {"id": "c", "k": "card", "f": [0, 2], "pic": "bin:0", "crop": [0, 0, 1, 1], "x": 0.5, "y": 0.5, "w": 0.4, "h": 0.4 * 1080 / 1920, "s": 1, "op": 1}
    b = one(film([plate(2, "#303030"), c], dur=2, assets={"bin:0": {"kind": "image", "path": p}}))
    assert np.abs(b[int(960 - 0.19 * 1080), int(540 - 0.19 * 1080)] - (48, 48, 48)).max() <= 1, "outside the circle: the plate, no box"
    assert b[960, 540].min() >= 250


def test_shadow_falls_where_asked():
    c = {"id": "c", "k": "card", "f": [0, 2], "pic": "plate", "plate": {"c0": "#F0F0F0", "c1": "#F0F0F0"}, "x": 0.5, "y": 0.5, "w": 0.5, "h": 0.25, "s": 1, "op": 1,
         "shadow": {"dx": 0.0, "dy": 0.02, "blur": 0.012, "a": 0.8}}
    a = one(film([plate(2, "#A0A0A0"), c], dur=2))[..., 0]
    below, above = a[int(0.625 * 1920) + 14, 540], a[int(0.375 * 1920) - 14, 540]
    assert below < 120 and above > 150, (below, above)


# ---- type

def test_type_is_rastered_at_twice_target_size():
    l1 = film([plate(2), {"id": "w", "k": "word", "f": [0, 2], "t": "chegando", "v": "a", "x": 0.3, "y": 0.5, "ink": "#FFFFFF"}], dur=2)
    plan = SC.compile(l1, "/", segment=False)
    (spr,) = plan.sprites.values()
    assert spr["ss"] == 2 and spr["aw"] == spr["w"] * 2
    assert spr["ax"] % 8 == 0 and spr["ay"] % 8 == 0, "sprites sit on the atlas grid, so mips stay registered"


def test_stem_edge_is_crisp():
    """A vertical stem goes from plate to ink in under 2 pixels: no blur, no blocks."""
    voices = {"t": {"font": "InterDisplay-Bold.ttf", "px": 120, "track": 0}}
    l1 = film([plate(2, "#000000"), {"id": "w", "k": "word", "f": [0, 2], "t": "IIII", "v": "t", "x": 0.3, "y": 0.5, "ink": "#FFFFFF"}], dur=2, voices=voices)
    a = one(l1)[..., 0]
    row = a[int(0.5 * 1920) - 40].astype(float)
    rise = np.where((row[1:] > 40) & (row[:-1] <= 40))[0]
    assert len(rise) >= 4
    for x in rise:
        seg = row[x:x + 5]
        n = np.argmax(seg > 215)
        assert seg.max() > 240 and n <= 2


def test_held_word_never_moves():
    l1 = film([plate(8), {"id": "w", "k": "word", "f": [0, 8], "t": "parado", "v": "a", "x": 0.31234, "y": 0.5123, "ink": "#FFFFFF"}], dur=8)
    fr = stills(l1, [0, 3, 7])
    assert np.array_equal(fr[0], fr[3]) and np.array_equal(fr[3], fr[7])


def test_word_at_rest_after_settle_is_as_crisp_as_a_plain_word():
    """A settle entrance rasters bigger. Once it lands it must match a word that never moved."""
    n = 40
    s = [(0.84 + 0.16 * math.exp(-i / 6.9)) / 0.84 for i in range(n)]
    plain = {"id": "w", "k": "word", "f": [0, n], "t": "chegando", "v": "a", "x": 0.3, "y": 0.5, "ink": "#FFFFFF"}
    a = stills(film([plate(n, "#000000"), plain], dur=n), [n - 1])[n - 1][..., 0].astype(float)
    b = stills(film([plate(n, "#000000"), dict(plain, s=s)], dur=n), [n - 1])[n - 1][..., 0].astype(float)
    assert np.corrcoef(a.ravel(), b.ravel())[0, 1] > 0.995
    sharp = lambda x: cv2.Laplacian(x.astype(np.float32), cv2.CV_32F).var()
    assert sharp(b) > 0.9 * sharp(a)


def test_reflowing_word_stays_on_the_grid_between_jumps():
    x = [0.31234] * 6 + [0.27777] * 6
    w = {"id": "w", "k": "word", "f": [0, 12], "t": "palavra", "v": "a", "x": x, "y": 0.5, "ink": "#FFFFFF"}
    p = {"id": "w", "k": "word", "f": [0, 12], "t": "palavra", "v": "a", "x": 0.27777, "y": 0.5, "ink": "#FFFFFF"}
    a = stills(film([plate(12, "#000000"), w], dur=12), [9])[9]
    b = stills(film([plate(12, "#000000"), p], dur=12), [9])[9]
    assert np.array_equal(a, b)


def test_a_missing_glyph_never_draws_a_box():
    v = {"t": {"font": "Anton-Regular.ttf", "px": 120, "track": 0}}
    l1 = film([plate(2, "#000000"), {"id": "w", "k": "word", "f": [0, 2], "t": "Ωλ", "v": "t", "x": 0.3, "y": 0.5, "ink": "#FFFFFF"}], dur=2, voices=v)
    e = Engine(l1, "/")
    e.stills([0])
    assert any("lacks a glyph" in w or "covers" in w for w in e.plan.warn), e.plan.warn
    e.close()


def test_kerning_is_on_and_the_slot_stays_centred():
    """AVAVA kerns tight. The compiler placed it unkerned, and the ink still lands on the slot's centre."""
    f = Face.get("Anton-Regular.ttf")
    px = 300
    assert f.measure("AVAVA", px) < sum(f.measure(c, px) for c in "AVAVA") - 4
    v = {"font": "Anton-Regular.ttf", "px": px, "track": 0}
    w = engine.compiler_advance("AVAVA", v)
    l1 = film([plate(2, "#000000"), {"id": "w", "k": "word", "f": [0, 2], "t": "AVAVA", "v": "t", "x": (1080 - w) / 2 / 1080, "y": 0.5, "ink": "#FFFFFF"}],
              dur=2, voices={"t": v})
    a = one(l1)[..., 0]
    xs = np.where((a > 128).any(0))[0]
    assert abs((xs.min() + xs.max()) / 2 - 539.5) <= 3, (xs.min(), xs.max())


def test_ligature_when_untracked_and_none_when_tracked():
    f = Face.get("Montserrat-Bold.ttf")
    a = [g[0] for g in f.shape("office", 100)[0]]
    b = [g[0] for g in f.shape("office", 100, track=5)[0]]
    assert len(b) == 6 and len(a) <= 6


@needs_arabic
def test_arabic_shapes_joins_and_runs_right_to_left():
    """HarfBuzz joins the letters, and the word draws in visual order from its left edge."""
    t = "سلام"
    f = Face.get(ARABIC)
    assert not f.missing(t)
    joined = [g[0] for g in f.shape(t, 100)[0]]
    isolated = [f.shape(c, 100)[0][0][0] for c in t]
    assert joined != isolated and joined[::-1] != isolated, "contextual forms, not the isolated letters"
    assert f.measure(t, 100) < sum(f.measure(c, 100) for c in t)
    v = {"t": {"font": ARABIC, "px": 200, "track": 0}}
    a = one(film([plate(2, "#000000"), {"id": "w", "k": "word", "f": [0, 2], "t": t, "v": "t", "x": 0.3, "y": 0.5, "ink": "#FFFFFF"}], dur=2, voices=v))[..., 0]
    xs = np.where((a > 128).any(0))[0]
    assert xs.max() - xs.min() > 150 and (a > 128).sum() > 2000


def test_two_voices_share_a_baseline():
    l1 = film([plate(2, "#000000"), {"id": "a", "k": "word", "f": [0, 2], "t": "HH", "v": "a", "x": 0.2, "y": 0.5, "ink": "#FFFFFF"},
               {"id": "b", "k": "word", "f": [0, 2], "t": "HH", "v": "b", "x": 0.6, "y": 0.5, "ink": "#FFFFFF"}], dur=2)
    a = one(l1)[..., 0]
    y1 = np.where((a[:, :540] > 128).any(1))[0].max()
    y2 = np.where((a[:, 540:] > 128).any(1))[0].max()
    assert abs(y1 - y2) <= 1 and abs(y1 - 0.5 * 1920) <= 2


def test_flicker_opacity_and_scale_about_centre():
    op = [0.5, 0.9, 0.25, 0.85, 0.3, 1.0]
    l1 = film([plate(6, "#000000"), {"id": "w", "k": "word", "f": [0, 6], "t": "IIII", "v": "a", "x": 0.4, "y": 0.5, "ink": "#FFFFFF", "op": op}], dur=6)
    fr = stills(l1, list(range(6)))
    peak = [int(fr[f][..., 0].max()) for f in range(6)]
    assert all(abs(p - 255 * o) <= 3 for p, o in zip(peak, op)), peak
    l2 = film([plate(2, "#000000"), {"id": "w", "k": "word", "f": [0, 2], "t": "IIII", "v": "a", "x": 0.4, "y": 0.5, "ink": "#FFFFFF", "s": [1.0, 2.0]}], dur=2)
    fr = stills(l2, [0, 1])

    def box(a):
        m = a[..., 0] > 128
        ys, xs = np.where(m)
        return xs.min(), xs.max(), ys.min(), ys.max()
    b0, b1 = box(fr[0]), box(fr[1])
    assert abs((b1[1] - b1[0]) / (b0[1] - b0[0]) - 2) < 0.08
    assert abs((b0[0] + b0[1]) / 2 - (b1[0] + b1[1]) / 2) <= 2


def test_word_blur_softens_then_resolves():
    blur = [14, 7, 2, 0]
    l1 = film([plate(4, "#000000"), {"id": "w", "k": "word", "f": [0, 4], "t": "palavra", "v": "a", "x": 0.3, "y": 0.5, "ink": "#FFFFFF", "blur": blur}], dur=4)
    fr = stills(l1, [0, 1, 2, 3])
    sharp = [cv2.Laplacian(fr[f][..., 0].astype(np.float32), cv2.CV_32F).var() for f in range(4)]
    assert sharp[0] < sharp[1] < sharp[2] < sharp[3]


def test_difference_blend():
    l1 = film([plate(2, "#FFFFFF"), {"id": "w", "k": "word", "f": [0, 2], "t": "IIII", "v": "a", "x": 0.4, "y": 0.5, "ink": "#FFFFFF", "blend": "difference"}], dur=2)
    assert one(l1)[..., 0].min() <= 2


# ---- flash maths, as the contract writes it

def ramp(tmp_path):
    a = np.tile(np.linspace(0, 255, 1080)[None, :, None], (1920, 1, 3)).astype(np.uint8)
    p = str(tmp_path / "ramp.png")
    Image.fromarray(a).save(p)
    return p


def test_flash_threshold_fill_pool_mosaic(tmp_path):
    p = ramp(tmp_path)
    assets = {"t": {"kind": "image", "path": p}}
    shot = {"id": "s", "k": "shot", "f": [0, 2], "pic": "t", "crop": [0, 0, 1, 1]}
    a = one(film([shot, {"id": "f", "k": "flash", "f": [0, 2], "mode": "threshold", "th": 0.5, "c": "#FF0000"}], dur=2, assets=assets))
    assert np.abs(a[900, 100] - (255, 0, 0)).max() <= 1 and np.abs(a[900, 1000] - (255, 255, 255)).max() <= 1
    a = one(film([shot, {"id": "f", "k": "flash", "f": [0, 2], "mode": "fill", "c": "#00FF00", "a": 0.5, "rect": [0, 0.4, 1, 0.6]}], dur=2, assets=assets))
    assert abs(a[960, 540, 1] - 192) <= 3 and abs(a[100, 540, 1] - 127) <= 2
    b = one(film([plate(2, "#808080"), {"id": "f", "k": "flash", "f": [0, 2], "mode": "pool", "a": 0.5, "rect": [0.2, 0.4, 0.8, 0.6]}], dur=2))
    assert abs(b[960, 540, 0] - 64) <= 2 and b[int(0.4 * 1920), 540, 0] >= 126, "gaussian falloff reaches nothing at the rect's edge"
    c = one(film([shot, {"id": "f", "k": "flash", "f": [0, 2], "mode": "mosaic", "cell": 32}], dur=2, assets=assets))
    assert len(np.unique(c[900, :64, 0])) <= 3


def test_burn_flash(tmp_path):
    p = ramp(tmp_path)
    shot = {"id": "s", "k": "shot", "f": [0, 2], "pic": "t", "crop": [0, 0, 1, 1]}
    a = one(film([shot, {"id": "f", "k": "flash", "f": [0, 2], "mode": "burn", "th": 0.3}], dur=2, assets={"t": {"kind": "image", "path": p}}))
    assert a[900, 1000].min() >= 250, "above the threshold is the white floor"


def test_grade_math(tmp_path):
    a = np.full((64, 64, 3), (100, 150, 200), np.uint8)
    p = str(tmp_path / "flat.png")
    Image.fromarray(a).save(p)
    g = {"lift": 0.02, "gamma": 1.1, "gain": 0.95, "sat": 1.2, "contrast": 1.1, "temp": 0.01, "tint": -0.005}
    shot = {"id": "s", "k": "shot", "f": [0, 2], "pic": "t", "crop": [0, 0, 1, 1]}
    got = one(film([shot], dur=2, assets={"t": {"kind": "image", "path": p}}, film_={"dither": 0.0, "grade": g}))[960, 540]
    c = np.array([100, 150, 200]) / 255.0
    c = np.maximum(c + g["lift"], 0) ** (1 / g["gamma"]) * g["gain"]
    l = float(np.dot(c, (0.2126, 0.7152, 0.0722)))
    c = l + (c - l) * g["sat"]
    c = (c - 0.5) * g["contrast"] + 0.5
    c = c + (g["temp"], g["tint"], -g["temp"])
    assert np.abs(got - c * 255).max() <= 1.5


# ---- film layers

def test_grain_is_seeded_sized_and_gentle():
    l1 = film([plate(4, "#808080")], dur=4, film_={"grain": 0.05, "dither": 0.0})
    fr = stills(l1, [1, 2])
    a, b = fr[1][600:900, 200:800, 0].astype(float), fr[2][600:900, 200:800, 0].astype(float)
    assert 2.0 <= a.std() <= 5.0, a.std()
    assert not np.array_equal(a, b)
    assert np.array_equal(stills(l1, [1])[1], fr[1])


def test_no_banding_on_a_gradient():
    p = {"id": "p", "k": "shot", "f": [0, 2], "pic": "plate", "plate": {"c0": "#222222", "c1": "#1C1C1C", "grain": 0}}
    a = stills(film([p], dur=2, film_={"dither": 1.0}), [0])[0][..., 0].astype(float)
    b = stills(film([p], dur=2, film_={"dither": 0.0}), [0])[0][..., 0].astype(float)
    assert a.std() > b.std() * 0.9 and a[0:200].std() > 0.3, "1 LSB of noise breaks the steps"


def test_optional_film_layers_draw():
    """Past the contract, off unless a plan asks: dot mesh, vertical raster, halation, bloom."""
    base = one(film([plate(2, "#606060")], dur=2))[..., 0].astype(float)
    mesh = one(film([plate(2, "#606060")], dur=2, film_={"dither": 0.0, "mesh": {"pitch": 8, "a": 0.4}}))[..., 0].astype(float)
    assert mesh.std() > 3 and mesh.max() <= base.max() + 1 and mesh.mean() < base.mean()
    rast = one(film([plate(2, "#606060")], dur=2, film_={"dither": 0.0, "raster": {"pitch": 6, "a": 0.3}}))
    assert rast[100, :, 0].std() > 3 and rast[:, 100, 0].std() < 0.5, "vertical lines, no horizontal component"
    word = {"id": "w", "k": "word", "f": [0, 2], "t": "IIIIIIII", "v": "a", "x": 0.3, "y": 0.5, "ink": "#FFFFFF"}
    over = one(film([plate(2, "#202020"), word], dur=2, film_={"dither": 0.0, "mesh": {"pitch": 8, "a": 0.5}, "mesh_over": True}))
    under = one(film([plate(2, "#202020"), word], dur=2, film_={"dither": 0.0, "mesh": {"pitch": 8, "a": 0.5}}))
    assert under[..., 0].max() >= 254 and over[..., 0][900:1000].astype(float).std() != under[..., 0][900:1000].astype(float).std()
    c = {"id": "c", "k": "card", "f": [0, 2], "pic": "plate", "plate": {"c0": "#FFFFFF", "c1": "#FFFFFF"}, "x": 0.5, "y": 0.5, "w": 0.3, "h": 0.15, "s": 1, "op": 1}
    plain = one(film([plate(2, "#101010"), c], dur=2))
    hal = one(film([plate(2, "#101010"), c], dur=2, film_={"dither": 0.0, "halation": 0.8}))
    x = int(0.5 * 1080 + 0.16 * 1080)
    assert hal[960, x, 0] > plain[960, x, 0] + 3 and hal[960, x, 0] > hal[960, x, 2], "highlights bleed warm"


# ---- sound

def env_peak(mix):
    """Peak of the 10 ms envelope, in seconds: what the manifest's peak_s measures."""
    e = np.abs(mix).sum(1)
    return float(np.argmax(np.convolve(e, np.ones(480) / 480, "same"))) / engine.SR


def test_sound_peak_lands_on_the_event():
    m = {x["id"]: x for x in json.load(open(engine.SOUNDS + "/manifest.json"))}["impact_1"]
    ev = 45
    l1 = film([plate(90)], dur=90, sound=[{"id": "a", "s": "impact_1", "file": m["file"], "event_f": ev, "start_s": ev / FPS - m["peak_s"], "trim_s": 0, "db": 0, "peak_s": m["peak_s"]}])
    p = engine.Plan(l1, "/")
    mix, rep = engine.mix_audio(p)
    assert rep["sounds"] == 1
    assert abs(env_peak(mix) - ev / FPS) < 0.002


def test_spine_jumps_back_for_a_cold_open(tmp_path):
    p = str(tmp_path / "tones.m4a")
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "aevalsrc='if(lt(t,2),sin(2*PI*500*t),sin(2*PI*2000*t))':d=4:s=48000", "-c:a", "aac", "-b:a", "256k", p], check=True)
    v = str(tmp_path / "v.mp4")
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=30:d=4", "-i", p, "-c:v", "libx264", "-c:a", "copy", "-shortest", v], check=True)
    l1 = film([plate(60)], dur=60, assets={"main": {"kind": "video", "path": v}},
              spine=[{"f": [0, 30], "src": [2.5, 3.5]}, {"f": [30, 60], "src": [0.5, 1.5]}])
    mix, _ = engine.mix_audio(engine.Plan(l1, "/"))

    def tone(s):
        x = np.abs(np.fft.rfft(s[:, 0] * np.hanning(len(s))))
        return np.argmax(x) * engine.SR / len(s)
    assert abs(tone(mix[4800:19200]) - 2000) < 8
    assert abs(tone(mix[52800:86400]) - 500) < 8


def test_trim_skips_the_samples_head():
    m = {x["id"]: x for x in json.load(open(engine.SOUNDS + "/manifest.json"))}["whoosh_soft_1"]
    l1 = film([plate(60)], dur=60, sound=[{"id": "a", "s": "w", "file": m["file"], "event_f": 3, "start_s": 0, "trim_s": m["peak_s"] - 0.1, "db": 0, "peak_s": m["peak_s"]}])
    mix, _ = engine.mix_audio(engine.Plan(l1, "/"))
    assert abs(env_peak(mix) - 0.1) < 0.002


# ---- a film always draws

def test_an_unknown_kind_is_ineligible_and_a_missing_picture_never_stops_a_film():
    """The product's rule: anything unknown is refused before any work, so it renders on today's
    renderer. A hole in a known plan is drawn around and named; strict mode refuses that too."""
    shot = {"id": "s", "k": "shot", "f": [0, 4], "pic": "bin:9", "crop": [0, 0, 1, 1]}
    assets = {"bin:9": {"kind": "image", "path": "/nope.jpg"}}
    odd = film([shot, {"id": "x", "k": "hologram", "f": [0, 4]}], dur=4, assets=assets)
    ok, why = scene.eligible(odd)
    assert not ok and any("hologram" in w for w in why)
    with pytest.raises(SceneFault) as ex:
        SC.compile(odd, "/")
    assert ex.value.code == "ineligible"
    l1 = film([shot], dur=4, assets=assets)
    e = Engine(l1, "/")
    a = e.stills([1])[1]
    assert a.shape == (1920, 1080, 4) and any("missing" in w for w in e.plan.warn)
    e.close()
    with pytest.raises(SceneFault) as ex:
        SC.compile(l1, "/", strict=True)
    assert ex.value.code == "plan"


def test_a_missing_font_never_stops_a_film():
    v = {"t": {"font": "NoSuchFont-Bold.ttf", "px": 80, "track": 0}}
    l1 = film([plate(2, "#000000"), {"id": "w", "k": "word", "f": [0, 2], "t": "ainda aqui", "v": "t", "x": 0.2, "y": 0.5, "ink": "#FFFFFF"}], dur=2, voices=v)
    e = Engine(l1, "/")
    a = e.stills([0])[0]
    assert a[..., 0].max() > 200 and any("not installed" in w for w in e.plan.warn)
    e.close()


def test_landscape_frame():
    l1 = film([{"id": "p", "k": "shot", "f": [0, 2], "pic": "plate", "plate": {"c0": "#202020", "c1": "#202020"}},
               {"id": "w", "k": "word", "f": [0, 2], "t": "wide", "v": "a", "x": 0.4, "y": 0.5, "ink": "#FFFFFF"}], dur=2, frame=(1920, 1080))
    assert one(l1).shape == (1080, 1920, 3)


def test_hdr_tag_reads_through(tmp_path):
    """An HLG-tagged phone file goes through the tone map, not straight into a washed-out picture."""
    p = str(tmp_path / "hlg.mp4")
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=128x228:r=30:d=1",
                    "-vf", "setparams=color_trc=arib-std-b67:color_primaries=bt2020:colorspace=bt2020nc:range=tv,format=yuv420p10le",
                    "-c:v", "libx264", "-color_trc", "arib-std-b67", "-color_primaries", "bt2020", "-colorspace", "bt2020nc", p], check=True)
    a = dict(engine.probe(p), abs=p, rate=FPS)
    assert a["trc"] == "arib-std-b67"
    d = engine.Decoder(a, 0)
    b = d.next()
    d.close()
    assert b is not None and 20 < np.frombuffer(b, np.uint8)[::4].mean() < 235


def test_gate_sees_a_salvo_on_its_planned_frames(tmp_path):
    """Short shots are the salvo's cards. The gate reads them from the encoded file, as it does flashes."""
    cols = [("#E11F79" if i % 2 else "#101820") for i in range(8)]
    items = [{"id": f"s{i}", "k": "shot", "f": [i * 2, i * 2 + 2], "pic": "plate", "plate": {"c0": c, "c1": c, "grain": 0}} for i, c in enumerate(cols)]
    l1 = film(items, dur=16)
    out = str(tmp_path / "s.mp4")
    engine.render(l1, str(tmp_path), out, audio=False)
    r = engine.check_film(l1, str(tmp_path), out)
    assert r["short_shots"] == 6 and r["short_shots_on_exact_frames"] == 6, r["shot_misses"]


# ------------------------------------------------------------------ the depth sandwich, joined scripts


def _matte_case(tmp_path):
    """Picture gray, a white square person at the centre of the matte, on main's clock."""
    w, h = 270, 480
    pic = np.full((h, w, 3), 90, np.uint8)
    m = np.zeros((h, w, 3), np.uint8)
    m[180:300, 90:180] = 255
    encode(str(tmp_path / "s.mp4"), [pic] * 30, w, h)
    encode(str(tmp_path / "m.mp4"), [m] * 30, w, h, crf=4)
    assets = {"main": {"kind": "video", "path": "s.mp4", "w": w, "h": h, "dur": 1.0, "fps": 30},
              "matte": {"kind": "video", "path": "m.mp4", "w": w, "h": h, "dur": 1.0, "fps": 30}}
    shot = {"id": "s", "k": "shot", "f": [0, 10], "pic": "main", "src": [0.0, 0.333], "crop": [0, 0, 1, 1]}
    return assets, shot


def test_behind_word_passes_behind_the_person(tmp_path):
    """The word covers the whole frame row, and the person's pixels stay his."""
    assets, shot = _matte_case(tmp_path)
    voices = {"big": {"font": "InterDisplay-Black.ttf", "px": 500, "track": 0}}
    w = {"id": "w", "k": "word", "f": [0, 10], "t": "HHH", "v": "big", "x": 0.02, "y": 0.68, "ink": "#FF0000", "role": "big", "on": 0, "behind": True}
    l1 = film([shot, w], assets=assets, voices=voices)
    e = Engine(l1, str(tmp_path))
    a = e.stills([5])[5]
    e.close()
    H, W = a.shape[:2]
    inside = a[int(H * 0.5), int(W * 0.5), :3].astype(int)          # the person, where the word also falls
    assert abs(inside[0] - inside[1]) < 12, inside                   # gray, not red
    red = (a[..., 0].astype(int) - a[..., 1].astype(int) > 120)
    assert red.mean() > 0.04                                         # the word is there, around him


def test_behind_card_passes_behind_the_person(tmp_path):
    assets, shot = _matte_case(tmp_path)
    c = {"id": "c", "k": "card", "f": [0, 10], "z": 1, "pic": "plate", "plate": {"c0": "#00FF00", "c1": "#00FF00", "grain": 0},
         "x": 0.5, "y": 0.5, "w": 0.9, "h": 0.5, "s": 1, "rot": 0, "op": 1, "behind": True}
    l1 = film([shot, c], assets=assets)
    e = Engine(l1, str(tmp_path))
    a = e.stills([5])[5]
    e.close()
    H, W = a.shape[:2]
    assert a[int(H * 0.5), int(W * 0.5), 1] < 130                    # his pixels
    assert a[int(H * 0.45), int(W * 0.1), 1] > 220                   # the card beside him


@needs_arabic
def test_persian_line_joins_and_fills_its_slot(tmp_path):
    """Shaped right to left by HarfBuzz: joined forms, and the sprite stands where the compiler centred it."""
    t = "صدام کن"
    v = {"font": os.path.basename(ARABIC), "px": 120, "track": 0}
    px = Face.get(ARABIC).measure(t, 120)          # a joined script is placed by its shaped width
    assert 200 < px < 520
    w = {"id": "w", "k": "word", "f": [0, 3], "t": t, "v": "big", "x": 0.5 - px / 2160, "y": 0.5, "ink": "#FFFFFF", "role": "big", "on": 0}
    l1 = film([plate(3, "#000000"), w], dur=3, voices={"big": v})
    e = Engine(l1, str(tmp_path))
    a = e.stills([1])[1][..., 0] > 128
    e.close()
    xs = np.where(a.any(axis=0))[0]
    assert abs((xs[0] + xs[-1]) / 2 - 540) < 8
    assert 0.6 < (xs[-1] - xs[0]) / px < 1.1


def test_a_reflection_falls_below_the_baseline_and_fades(tmp_path):
    voices = {"big": {"font": "InterDisplay-Black.ttf", "px": 200, "track": 0}}
    w = {"id": "w", "k": "word", "f": [0, 3], "t": "HH", "v": "big", "x": 0.3, "y": 0.5, "ink": "#FFFFFF", "role": "big", "on": 0, "reflect": 0.6}
    l1 = film([plate(3, "#000000"), w], dur=3, voices=voices)
    e = Engine(l1, str(tmp_path))
    a = e.stills([1])[1][..., 0].astype(float)
    e.close()
    H = a.shape[0]
    near, far = a[int(H * 0.5) + 25:int(H * 0.5) + 45].max(), a[int(H * 0.5) + 120:int(H * 0.5) + 140].max()
    assert 30 < near < 200          # a reflection, dimmer than the word
    assert far < near * 0.5
    assert a[int(H * 0.45)].max() > 240


def test_a_filled_word_shows_its_picture_inside_the_letters(tmp_path):
    w_, h_ = 270, 480
    pic = np.zeros((h_, w_, 3), np.uint8)
    pic[:] = (0, 220, 40)
    encode(str(tmp_path / "s.mp4"), [pic] * 30, w_, h_)
    assets = {"main": {"kind": "video", "path": "s.mp4", "w": w_, "h": h_, "dur": 1.0, "fps": 30}}
    voices = {"big": {"font": "InterDisplay-Black.ttf", "px": 500, "track": 0}}
    w = {"id": "w", "k": "word", "f": [0, 10], "t": "HH", "v": "big", "x": 0.05, "y": 0.6, "ink": "#FFFFFF", "role": "big", "on": 0,
         "fill": {"pic": "main", "src": [0.0, 0.33], "crop": [0, 0, 1, 1]}}
    l1 = film([plate(10, "#000000"), w], assets=assets, voices=voices)
    e = Engine(l1, str(tmp_path))
    a = e.stills([5])[5]
    e.close()
    green = (a[..., 1].astype(int) - a[..., 0].astype(int)) > 120
    white = (a[..., 0] > 200) & (a[..., 1] > 200)
    assert green.mean() > 0.05 and white.mean() < 0.002


def test_a_plate_is_lit_not_flat(tmp_path):
    l1 = film([{"id": "p", "k": "shot", "f": [0, 10], "pic": "plate", "plate": {"c0": "#E11F79", "c1": "#A0145A", "grain": 0}}], film_={"dither": 0.0})
    e = Engine(l1, str(tmp_path))
    r = e.stills([1, 9])
    e.close()
    a, b = r[1][..., 0].astype(float), r[9][..., 0].astype(float)
    assert a.max() - a.min() > 25            # a pool of light
    assert np.abs(a - b).max() > 0.2         # and it breathes


def test_recolour_moves_only_the_key_colour(tmp_path):
    w_, h_ = 200, 100
    a = np.zeros((h_, w_, 3), np.uint8)
    a[:, :100] = (200, 30, 40)          # the red car
    a[:, 100:] = (230, 140, 40)         # the fire, orange
    Image.fromarray(a).save(tmp_path / "s.png")
    assets = {"p": {"kind": "image", "path": "s.png", "w": w_, "h": h_}}
    shot = {"id": "s", "k": "shot", "f": [0, 4], "pic": "p", "crop": [0, 0, 1, 1]}
    fl = {"id": "r", "k": "flash", "f": [0, 4], "mode": "recolour", "key": "#C81E28", "c": "#1FA8A0", "th": 0.055}
    l1 = film([shot, fl], dur=4, assets=assets)
    e = Engine(l1, str(tmp_path))
    o = e.stills([1])[1]
    e.close()
    H, W = o.shape[:2]
    left, right = o[H // 2, W // 4, :3].astype(int), o[H // 2, W * 3 // 4, :3].astype(int)
    assert left[2] > left[0] + 40 and left[1] > left[0] + 40, left          # red went teal
    assert abs(right[0] - 230) < 8 and abs(right[1] - 140) < 8, right       # the fire stayed


def test_echo_throws_the_last_moment_into_the_gap_and_swell_rises_into_the_event():
    sr = engine.SR
    dry = np.zeros((sr * 3, 2), np.float32)
    dry[int(0.5 * sr):int(1.0 * sr)] = 0.5         # a phrase, then silence
    dry[int(2.0 * sr):int(2.5 * sr)] = 0.5         # the next phrase
    at, out = engine.voice_layer(dry, {"s": "echo", "event_f": 30, "d_s": 0.5, "n": 3, "gap_s": 0.25, "db": 0}, 30)
    assert at == sr
    assert np.abs(out).max() > 0.2 and len(out) >= int(0.75 * sr)
    assert np.abs(out[:, 0]).sum() != np.abs(out[:, 1]).sum()      # it moves side to side
    at2, s2 = engine.voice_layer(dry, {"s": "swell", "event_f": 60, "d_s": 0.5, "db": 0}, 30)
    assert at2 == int(1.5 * sr)
    e = np.abs(s2).mean(axis=1)
    assert e[-100:].mean() > 4 * e[:len(e) // 2].mean()            # it rises into the anchor


# ================================================================== worker/scene, past the slice

SMALL = (360, 640)


def scenery(path, w=360, h=640, seed=5, warm=True):
    """A picture with large shapes, an edge at every angle and no noise: small as a golden, and any
    flip, shift or wrong crop moves something. The warm one and the cold one differ at every pixel."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = np.zeros((h, w, 3), np.float32)
    if warm:
        a[..., 0] = 150 + 90 * xx / w
        a[..., 1] = 90 + 80 * yy / h
        a[..., 2] = 70 - 50 * (xx + yy) / (w + h)
    else:
        a[..., 0] = 60 - 40 * xx / w
        a[..., 1] = 110 - 60 * yy / h
        a[..., 2] = 150 + 90 * (xx + yy) / (w + h)
    rng = np.random.RandomState(seed)
    for i in range(7):
        cx, cy, r = rng.randint(30, w - 30), rng.randint(30, h - 30), rng.randint(18, 70)
        m = np.clip(r - np.hypot(xx - cx, yy - cy) + 0.5, 0, 1)[..., None]
        tone = np.array(rng.randint(0, 110, 3), np.float32) + (np.array([120, 40, 0]) if warm else np.array([0, 40, 120]))
        a = a * (1 - m) + tone * m
    a[int(h * 0.82):int(h * 0.86), int(w * 0.1):int(w * 0.9)] = (250, 215, 150) if warm else (150, 215, 250)
    a[:int(h * 0.05), :int(w * 0.12)] = (230, 20, 20) if warm else (20, 20, 230)      # a corner, top left
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path)
    return path


@pytest.fixture(scope="session")
def pics(tmp_path_factory):
    d = tmp_path_factory.mktemp("pics")
    return {"a": scenery(str(d / "a.png"), seed=5), "b": scenery(str(d / "b.png"), seed=11, warm=False), "dir": str(d)}


def small(items, dur, pics=None, voices=None, film_=None):
    assets = {k: {"kind": "image", "path": pics[k]} for k in ("a", "b")} if pics else {}
    return film(items, dur=dur, assets=assets, voices=voices, film_=film_ if film_ is not None else {"dither": 0.0}, frame=SMALL)


def pic_shot(pic="a", dur=4, **kw):
    return dict({"id": "s", "k": "shot", "f": [0, dur], "pic": pic, "crop": [0, 0, 1, 1]}, **kw)


def psnr(a, b):
    mse = float(((a.astype(np.float64) - b.astype(np.float64)) ** 2).mean())
    return 99.0 if mse == 0 else 10 * math.log10(255 ** 2 / mse)


# ---- E4: the re-renders

@pytest.mark.parametrize("mode", ["neg", "threshold", "burn", "mono", "tint", "complement", "posterise", "halftone", "solarise", "thermal",
                                  "night_vision", "line", "blueprint", "xray", "kaleidoscope", "pixel_stretch"])
def test_every_treat_draws_and_draws_the_same_twice(pics, mode):
    plain = one(small([pic_shot()], 4, pics))
    treat = {"mode": mode, "c": "#D83820"} if mode == "tint" else {"mode": mode}
    l1 = small([pic_shot(treat=treat)], 4, pics)
    a, b = one(l1), one(l1)
    assert np.array_equal(a, b), "the same plan gives the same pixels"
    assert a.min() >= 0 and a.max() <= 255 and np.abs(a - plain).mean() > 3.0, "and they are not the plain picture"


def test_the_ported_looks_keep_their_character(pics):
    H, W = SMALL[1], SMALL[0]
    grey = small([{"id": "s", "k": "shot", "f": [0, 2], "pic": "plate", "plate": {"c0": "#000000", "c1": "#000000"}}], 2)
    # thermal: iron palette, dark to near-black violet and light to pale yellow
    ramp_p = str(os.path.join(pics["dir"], "ramp.png"))
    Image.fromarray(np.tile(np.linspace(0, 255, 360).astype(np.uint8)[None, :, None], (640, 1, 3))).save(ramp_p)
    r = lambda mode, **kw: one(film([dict(pic_shot("r"), treat=dict({"mode": mode}, **kw))], dur=4, assets={"r": {"kind": "image", "path": ramp_p}}, frame=SMALL))   # noqa: E731
    th = r("thermal")[320]
    assert th[6].sum() < 60 and th[6][2] > th[6][1] and th[350][0] > 240 and th[350][1] > 240 and th[180][0] > th[180][2] + 80
    nv = r("night_vision")[320]
    assert nv[110][1] > nv[110][0] + 60 and nv[110][1] > nv[110][2] + 60, "phosphor green"
    xr = r("xray")[320]
    assert xr[20].sum() > xr[340].sum() + 200, "a radiograph is inverted"
    so = r("solarise")[320]
    assert so[180].sum() > so[8].sum() + 150 and so[180].sum() > so[352].sum() + 150, "solarised: the mid-tones are the lightest"
    po = np.unique(r("posterise", th=0.34)[320][:, 0])
    assert 3 <= len(po) <= 6
    k = one(small([pic_shot(treat={"mode": "kaleidoscope"})], 4, pics))
    assert np.abs(k - k[:, ::-1]).mean() < 1.0 and np.abs(k - k[::-1]).mean() < 1.0, "mirrored four ways"
    ps = one(small([pic_shot(treat={"mode": "pixel_stretch", "origin": 0.5, "direction": "left"})], 4, pics))
    plain = one(small([pic_shot()], 4, pics))
    assert np.abs(ps[:, :W // 2 - 2] - ps[:, W // 2 - 2:W // 2 - 1]).max() <= 2, "one column pulled to the left edge"
    assert np.abs(ps[:, W // 2 + 4:] - plain[:, W // 2 + 4:]).max() <= 2, "and the rest untouched"
    bp = one(small([pic_shot(treat={"mode": "blueprint"})], 4, pics))
    assert bp[..., 2].mean() > bp[..., 0].mean() + 40, "white lines on blue"
    ln = one(small([pic_shot(treat={"mode": "line"})], 4, pics))
    assert ln.mean() > 170 and (ln.min(axis=2) < 90).mean() > 0.003, "dark lines on paper"
    assert one(grey).max() < 8


def test_a_frame_can_be_re_rendered_by_a_flash(pics):
    """A flash carries the point looks and the neighbourhood ones; the two that move pixels need a picture."""
    base = [pic_shot()]
    plain = one(small(base, 4, pics))
    for mode in ("thermal", "solarise", "line", "xray", "blueprint"):
        a = one(small(base + [{"id": "f", "k": "flash", "f": [0, 4], "mode": mode, "rect": [0, 0, 1, 0.5]}], 4, pics))
        assert np.abs(a[:300] - plain[:300]).mean() > 5 and np.abs(a[330:] - plain[330:]).max() <= 1, mode
    ok, why = scene.eligible(small(base + [{"id": "f", "k": "flash", "f": [0, 4], "mode": "kaleidoscope"}], 4, pics))
    assert not ok and "needs a picture" in why[0]


# ---- join: two pictures into one

def test_a_seam_shows_the_new_picture_to_its_left_and_leans(pics):
    a, b = one(small([pic_shot("a")], 4, pics)), one(small([pic_shot("b")], 4, pics))
    j = {"id": "j", "k": "join", "f": [0, 4], "mode": "seam", "pic": "b", "crop": [0, 0, 1, 1], "x": 0.5, "lean": 6.0, "edge": {"w": 1.3, "a": 0.85}}
    got = one(small([pic_shot("a"), j], 4, pics))
    H, W = SMALL[1], SMALL[0]
    assert np.abs(got[:, :120] - b[:, :120]).max() <= 2 and np.abs(got[:, 240:] - a[:, 240:]).max() <= 2
    is_b = np.abs(got - b).sum(axis=2) < np.abs(got - a).sum(axis=2)
    line = [float(is_b[y - 4:y + 5].sum(axis=1).mean()) for y in (20, 320, 620)]   # how far the new picture reaches on each row
    assert abs(line[1] - W / 2) <= 2, line
    lean = 300 * math.tan(math.radians(6.0))
    assert abs((line[1] - line[0]) - lean) <= 3 and abs((line[2] - line[1]) - lean) <= 3, "6 degrees, top to bottom"
    assert got[320, W // 2 - 3:W // 2 + 4].min(axis=1).max() > 190, "a hairline of white rides the seam"
    dim = one(small([pic_shot("a"), dict(j, dim=0.5)], 4, pics))
    assert abs(dim[320, 300].sum() / max(a[320, 300].sum(), 1) - 0.5) < 0.03, "the old picture can sit back while it is read across"
    moved = one(small([pic_shot("a"), dict(j, x=[0.2, 0.4, 0.6, 0.8])], 4, pics), 3)
    assert np.abs(moved[:, :240] - b[:, :240]).max() <= 2, "the seam follows its baked x"


def test_a_weave_goes_over_and_under(pics):
    a, b = one(small([pic_shot("a")], 4, pics)), one(small([pic_shot("b")], 4, pics))
    j = {"id": "j", "k": "join", "f": [0, 4], "mode": "weave", "pic": "b", "crop": [0, 0, 1, 1], "pitch": 0.1, "falloff": 0.0, "shadow": 0.0}
    w = lambda t: one(small([pic_shot("a"), dict(j, t=t)], 4, pics))       # noqa: E731
    assert np.abs(w(0.0) - a).max() <= 1 and np.abs(w(1.0) - b).max() <= 1, "nothing at either end"
    mid = w(0.5)
    is_b = np.abs(mid - b).sum(axis=2) < np.abs(mid - a).sum(axis=2)
    assert 0.4 < is_b.mean() < 0.6
    cells = is_b[:612].reshape(17, 36, 10, 36).mean(axis=(1, 3))
    assert ((cells > 0.9) | (cells < 0.1)).all() and (cells[:, 1:] > 0.5).tolist() != (cells[:, :-1] > 0.5).tolist(), "over on even cells, under on odd"
    q = w(0.25)
    is_bq = np.abs(q - b).sum(axis=2) < np.abs(q - a).sum(axis=2)
    assert 0.2 < is_bq.mean() < 0.3, "at a quarter the new strips are half open and show where they pass over"
    shaded = one(small([pic_shot("a"), dict(j, t=0.5, falloff=0.55, shadow=0.3)], 4, pics))
    assert shaded.sum() < mid.sum() * 0.97, "strips fall off towards their edges"


def test_a_glyph_swells_from_its_own_place_and_eats_the_type(pics):
    v = {"a": {"font": "InterDisplay-Bold.ttf", "px": 40, "track": 0, "shaped": True}}
    a, b = one(small([pic_shot("a", 8)], 8, pics, v)), one(small([pic_shot("b", 8)], 8, pics, v))
    j = {"id": "j", "k": "join", "f": [0, 8], "mode": "glyph", "stage": "top", "pic": "b", "crop": [0, 0, 1, 1], "t": ".", "v": "a",
         "x0": 0.5, "y0": 0.5, "s": {"curve": "grow", "a": 1.0, "k": 1.9}}
    wd = {"id": "w", "k": "word", "f": [0, 8], "t": "money", "v": "a", "x": 0.2, "y": 0.5, "ink": "#FFFFFF"}
    l1 = small([pic_shot("a", 8), wd, j], 8, pics, v)
    fr = stills(l1, list(range(8)))
    area = []
    for f in range(8):
        g = fr[f][..., :3].astype(np.int16)
        area.append(int((np.abs(g - b).sum(axis=2) < np.abs(g - a).sum(axis=2)).sum()))
    assert area[0] < 60 and all(y > x for x, y in zip(area, area[1:])), area
    assert 2.8 < area[4] / area[3] < 4.4, "radius 1.9 times a frame is area 3.6 times"
    assert area[7] > 0.5 * SMALL[0] * SMALL[1]
    g1 = fr[1][..., :3].astype(np.int16)
    ys, xs = np.where(np.abs(g1 - b).sum(axis=2) + 30 < np.abs(g1 - a).sum(axis=2))
    assert abs(xs.mean() - 0.5 * SMALL[0]) < 12 and abs(ys.mean() - 0.5 * SMALL[1]) < 12, "it grows from the period's own place"
    white0 = int((fr[0][..., :3].min(axis=2) > 240).sum())
    white7 = int((fr[7][..., :3].astype(np.int16) - b > 60).all(axis=2).sum())
    assert white0 > 300 and white7 < white0 * 0.2, "the letters are eaten as it passes"


# ---- A1: static, and a word that is only there while it plays

def test_static_is_one_bit_blocks_that_move_a_cell_a_frame():
    pl = {"id": "s", "k": "shot", "f": [0, 6], "pic": "plate", "plate": {"kind": "static", "cell": 36, "speed": 36, "seed": 3}}
    fr = stills(film([pl], dur=6, frame=SMALL, film_={"dither": 0.0}), [1, 2, 3])
    f1, f2 = fr[1][..., 0], fr[2][..., 0]
    assert set(np.unique(f1)) == {0, 255} and 0.42 < (f1 > 0).mean() < 0.58
    cell = 12                                                   # 36 at 1080 wide is 12 at 360
    blocks = f1[: 53 * cell, : 30 * cell].reshape(53, cell, 30, cell)
    assert (blocks == blocks[:, :1, :, :1]).all(), "every block is one level"
    assert np.array_equal(f2[cell:], f1[:-cell]), "positive speed moves the blocks down"
    again = stills(film([pl], dur=6, frame=SMALL, film_={"dither": 0.0}), [1])[1][..., 0]
    assert np.array_equal(again, f1)


def test_the_pause_proof_word_is_in_the_motion_and_in_no_still():
    v = {"big": {"font": "InterDisplay-Black.ttf", "px": 110, "track": 0, "shaped": True}}
    pl = {"id": "s", "k": "shot", "f": [0, 12], "pic": "plate", "plate": {"kind": "static", "cell": 12, "speed": 12, "seed": 11}}
    wd = {"id": "w", "k": "word", "f": [0, 12], "t": "PLAY", "v": "big", "x": 0.12, "y": 0.55, "ink": "#FFFFFF", "hard": True,
          "fill": {"plate": {"kind": "static", "cell": 12, "speed": -12, "seed": 23}}}
    mask = one(film([{"id": "s", "k": "shot", "f": [0, 2], "pic": "plate", "plate": {"c0": "#000000", "c1": "#000000"}}, dict(wd, f=[0, 2], fill=None, hard=False)],
                    dur=2, frame=SMALL, voices=v, film_={"dither": 0.0}))[..., 0] > 127
    fr = stills(film([pl, wd], dur=12, frame=SMALL, voices=v, film_={"dither": 0.0}), list(range(12)))
    g = [fr[f][..., 0].astype(np.float32) for f in range(12)]
    assert all(set(np.unique(x)) == {0.0, 255.0} for x in g), "no soft edge: a still holds two levels and nothing else"
    still = max(abs(float(np.corrcoef(mask.ravel(), x.ravel())[0, 1])) for x in g)
    assert still < 0.04, still
    cell, vote = 4, np.zeros(mask.shape, np.float32)
    for a_, b_ in zip(g, g[1:]):
        up, down = np.abs(b_[:-cell] - a_[cell:]), np.abs(b_[cell:] - a_[:-cell])
        vote[cell:-cell] += down[:-cell] - up[cell:]
    assert float(((vote > 0) == mask)[cell:-cell].mean()) > 0.97, "the letters are where the static runs the other way"


# ---- type from the atlas: the distance field, and a window onto a shot

def test_a_stroke_and_a_weight_shift_come_from_the_distance_field():
    v = {"a": {"font": "InterDisplay-Bold.ttf", "px": 120, "track": 0, "shaped": True}}
    bg = {"id": "s", "k": "shot", "f": [0, 2], "pic": "plate", "plate": {"c0": "#808080", "c1": "#808080"}}
    wd = {"id": "w", "k": "word", "f": [0, 2], "t": "Hn", "v": "a", "x": 0.2, "y": 0.5, "ink": "#FFFFFF"}
    plain = one(film([bg, wd], dur=2, frame=SMALL, voices=v, film_={"dither": 0.0}))
    stroked = one(film([bg, dict(wd, stroke={"w": 9, "c": "#000000"})], dur=2, frame=SMALL, voices=v, film_={"dither": 0.0}))
    white = lambda x: int((x.min(axis=2) > 200).sum())     # noqa: E731
    black = lambda x: int((x.max(axis=2) < 50).sum())      # noqa: E731
    assert black(plain) == 0 and black(stroked) > 1500 and abs(white(stroked) - white(plain)) < 0.05 * white(plain)
    ys, xs = np.where(stroked.max(axis=2) < 50)
    wy, wx = np.where(plain.min(axis=2) > 200)
    assert xs.min() < wx.min() and xs.max() > wx.max() and ys.min() < wy.min(), "the stroke rings the letters"
    heavy = one(film([bg, dict(wd, grow=6)], dur=2, frame=SMALL, voices=v, film_={"dither": 0.0}))
    light = one(film([bg, dict(wd, grow=-4)], dur=2, frame=SMALL, voices=v, film_={"dither": 0.0}))
    assert white(heavy) > 1.15 * white(plain) and white(light) < 0.85 * white(plain) and white(light) > 0


def test_a_word_can_be_a_window_onto_a_shot_and_register_to_it(pics):
    v = {"a": {"font": "Anton-Regular.ttf", "px": 200, "track": 0, "shaped": True}}
    grade = {"grade": {"contrast": 1.1, "gain": 1.02, "gamma": 0.97, "lift": 0.01, "sat": 1.06, "temp": 0.02, "tint": -0.02}, "dither": 0.0}
    wd = {"id": "w", "k": "word", "f": [0, 4], "t": "NEVER", "v": "a", "x": 0.06, "y": 0.6, "ink": "#FFFFFF"}
    same = one(small([pic_shot("a"), dict(wd, fill={"pic": "a", "crop": [0, 0, 1, 1], "frame": True})], 4, pics, v, grade))
    alone = one(small([pic_shot("a")], 4, pics, v, grade))
    assert psnr(same, alone) > 45, "letters of the shot, over the shot, are not there"
    over_b = one(small([pic_shot("b"), dict(wd, fill={"pic": "a", "crop": [0, 0, 1, 1], "frame": True})], 4, pics, v, grade))
    b_alone = one(small([pic_shot("b")], 4, pics, v, grade))
    solid = one(small([pic_shot("b"), wd], 4, pics, v, grade)).min(axis=2) > 235
    assert solid.sum() > 8000
    assert np.abs(over_b[solid] - alone[solid]).mean() < 2.0, "inside the letters: the old shot, at the old shot's own pixels"
    assert np.abs(over_b[~solid] - b_alone[~solid]).mean() < 2.5


# ---- E5 in a plan: a curve spec is the list it bakes to

def test_a_curve_spec_draws_as_its_baked_list(pics):
    import scene.bake as BK
    card = {"id": "c", "k": "card", "f": [0, 8], "z": 1, "pic": "b", "crop": [0, 0.2, 1, 0.8], "x": 0.5, "y": 0.5, "w": 0.6, "h": 0.36, "rot": 0, "op": 1}
    spec = small([pic_shot("a", 8), dict(card, s={"curve": "settle"}, op={"curve": "flicker"})], 8, pics)
    flat = small([pic_shot("a", 8), dict(card, s=BK.settle(8), op=BK.flicker(8))], 8, pics)
    a, b = stills(spec, [0, 2, 5]), stills(flat, [0, 2, 5])
    assert all(np.array_equal(a[f], b[f]) for f in (0, 2, 5))
    assert not np.array_equal(a[0], a[5])


# ---- E7 and E2: segments on disk, the cache, a stopped render, the watchdog

def _talky(pics, n_shots=6, per=50):
    items, at = [], 0
    for i in range(n_shots):
        items.append({"id": f"s{i}", "k": "shot", "f": [at, at + per], "pic": "ab"[i % 2], "crop": [[0.0, 0.0, 0.8 + 0.004 * k, 0.8 + 0.004 * k] for k in range(per)]})
        at += per
    v = {"a": {"font": "InterDisplay-Bold.ttf", "px": 30, "track": -2}}
    words = [{"id": f"w{i}", "k": "word", "f": [f, f + 12], "t": f"w{i}", "v": "a", "x": 0.3, "y": 0.7, "ink": "#FFFFFF", "on": f} for i, f in enumerate(range(4, at - 14, 12))]
    return small(items + words, at, pics, v, {"grain": 0.04, "vignette": 0.2}), words


def test_segments_join_into_the_whole_film_and_only_what_changed_is_redrawn(tmp_path, pics):
    l1, words = _talky(pics)
    cache, out = str(tmp_path / "cache"), str(tmp_path / "f.mp4")
    plan = SC.compile(l1, "/")
    assert len(plan.segments) >= 3
    log = RUN.render_plan(plan, out, tier="final", audio=False, cache=cache)
    assert log["frames"] == l1["dur_f"] and log["segments_drawn"] == len(plan.segments)
    first = open(out, "rb").read()
    log2 = RUN.render_plan(SC.compile(l1, "/"), str(tmp_path / "g.mp4"), tier="final", audio=False, cache=cache)
    assert log2["segments_drawn"] == 0 and open(str(tmp_path / "g.mp4"), "rb").read() == first, "nothing redrawn, the same bytes"
    l2 = json.loads(json.dumps(l1))
    next(i for i in l2["items"] if i["id"] == words[7]["id"])["t"] = "outra"
    log3 = RUN.render_plan(SC.compile(l2, "/"), str(tmp_path / "h.mp4"), tier="final", audio=False, cache=cache)
    assert 1 <= log3["segments_drawn"] <= 2 and log3["frames"] == l1["dur_f"]
    # the joined film is the film drawn in one piece: frame for frame, through the encoder
    whole = stills(l1, [0, 49, 50, 149, 150, 299])
    d = engine.Decoder(dict(engine.probe(out), abs=out, rate=FPS), 0)
    got, n = {}, 0
    while True:
        b = d.next()
        if b is None:
            break
        if n in whole:
            got[n] = np.frombuffer(b, np.uint8).reshape(SMALL[1], SMALL[0], 4)[..., :3]
        n += 1
    d.close()
    assert n == l1["dur_f"]
    plan_s = SC.compile(l1, "/")
    seg_stills = RUN.stills(plan_s, sorted(whole))
    for f in whole:
        assert psnr(got[f], seg_stills[f][..., :3]) > 30, f


def test_a_render_that_dies_leaves_its_finished_segments(tmp_path, pics, monkeypatch):
    l1, _ = _talky(pics)
    cache = str(tmp_path / "cache")
    plan = SC.compile(l1, "/")
    real, seen = RUN.S.verify, []

    def dies(path, n, fps, *a, **k):
        seen.append(path)
        if len(seen) == 3:
            raise SceneFault("crash", "killed here")
        return real(path, n, fps, *a, **k)
    monkeypatch.setattr(RUN.S, "verify", dies)
    with pytest.raises(SceneFault):
        RUN.render_plan(plan, str(tmp_path / "f.mp4"), tier="final", audio=False, cache=cache)
    monkeypatch.setattr(RUN.S, "verify", real)
    kept = sum(len(fs) for _, _, fs in os.walk(cache))
    assert kept == 2 and not [f for f in os.listdir(str(tmp_path)) if f.startswith("scene_")], "two whole segments kept, no temp folder left"
    log = RUN.render_plan(SC.compile(l1, "/"), str(tmp_path / "f.mp4"), tier="final", audio=False, cache=cache)
    assert log["segments_drawn"] == len(plan.segments) - 2 and log["frames"] == l1["dur_f"]


def test_render_in_a_child_process_and_its_faults(tmp_path, pics):
    l1, _ = _talky(pics, n_shots=3)
    out = str(tmp_path / "f.mp4")
    log = scene.render(l1, "/", out, tier="preview", audio=False, cache=str(tmp_path / "cache"))
    assert log["frames"] == l1["dur_f"] and os.path.getsize(out) > 2000 and log["tier"] == "preview"
    with pytest.raises(SceneFault) as ex:
        scene.render(dict(l1, items=l1["items"] + [{"id": "x", "k": "pieces", "f": [0, 3]}]), "/", out)
    assert ex.value.code == "ineligible"
    gone = dict(l1, assets=dict(l1["assets"], a={"kind": "image", "path": "/nope.png"}))
    with pytest.raises(SceneFault) as ex:
        scene.render(gone, "/", out, strict=True, audio=False, cache=str(tmp_path / "cache2"))
    assert ex.value.code == "plan" and "missing" in ex.value.detail
    with pytest.raises(SceneFault) as ex:
        scene.render(l1, "/", out, audio=False, stall_s=0.0, use_cache=False, cache=str(tmp_path / "cache3"))
    assert ex.value.code == "stall", "no frame and no reader progress: the watchdog kills the child"


# ---- E2: golden frames per op

GOLDEN_OPS = {
    "src_fit": lambda p: small([pic_shot(crop=[0.2, 0.25, 0.7, 0.75])], 4, p),
    "grade": lambda p: small([pic_shot(grade={"lift": 0.02, "gamma": 0.9, "gain": 1.1, "sat": 1.3, "contrast": 1.15, "temp": 0.04, "tint": -0.02})], 4, p),
    "treat_burn": lambda p: small([pic_shot(treat={"mode": "burn", "th": 0.45})], 4, p),
    "treat_halftone": lambda p: small([pic_shot(treat={"mode": "halftone"})], 4, p),
    "treat_thermal": lambda p: small([pic_shot(treat={"mode": "thermal"})], 4, p),
    "treat_line": lambda p: small([pic_shot(treat={"mode": "line"})], 4, p),
    "treat_blueprint": lambda p: small([pic_shot(treat={"mode": "blueprint"})], 4, p),
    "treat_xray": lambda p: small([pic_shot(treat={"mode": "xray"})], 4, p),
    "plate": lambda p: small([{"id": "s", "k": "shot", "f": [0, 4], "pic": "plate", "plate": {"c0": "#C4161C", "c1": "#5E0A0D", "grain": 0}}], 4, p),
    "blit_card": lambda p: small([pic_shot(), {"id": "c", "k": "card", "f": [0, 4], "z": 1, "pic": "b", "crop": [0, 0.2, 1, 0.8], "x": 0.5, "y": 0.45, "w": 0.7,
                                               "h": 0.4, "s": 0.95, "rot": -4, "op": 1, "radius": 0.03, "border": {"w": 0.012, "c": "#FFFFFF"},
                                               "shadow": {"dx": 0.01, "dy": 0.02, "blur": 0.03, "a": 0.6}}], 4, p),
    "blit_word": lambda p: small([pic_shot(), {"id": "w", "k": "word", "f": [0, 4], "t": "Valmera", "v": "a", "x": 0.14, "y": 0.5, "ink": "#FFFFFF"},
                                  {"id": "w2", "k": "word", "f": [0, 4], "t": "office", "v": "b", "x": 0.3, "y": 0.62, "ink": "#FFE14A", "stroke": {"w": 5, "c": "#101010"}}],
                                 4, p, {"a": {"font": "InterDisplay-Bold.ttf", "px": 72, "track": -3, "shadow": {"a": 0.5, "blur": 0.012}},
                                        "b": {"font": "InstrumentSerif-Italic.ttf", "px": 64, "track": 0}}),
    "flash": lambda p: small([pic_shot(), {"id": "f1", "k": "flash", "f": [0, 4], "mode": "neg", "rect": [0.1, 0.1, 0.6, 0.45]},
                              {"id": "f2", "k": "flash", "f": [0, 4], "mode": "threshold", "th": 0.5, "rect": [0.4, 0.5, 0.95, 0.9]},
                              {"id": "f3", "k": "flash", "f": [0, 4], "mode": "pool", "a": 0.7, "rect": [0.0, 0.6, 0.5, 1.0]}], 4, p),
    "join_seam": lambda p: small([pic_shot(), {"id": "j", "k": "join", "f": [0, 4], "mode": "seam", "pic": "b", "crop": [0, 0, 1, 1], "x": 0.45, "dim": 0.82}], 4, p),
    "join_weave": lambda p: small([pic_shot(), {"id": "j", "k": "join", "f": [0, 4], "mode": "weave", "pic": "b", "crop": [0, 0, 1, 1], "t": 0.4}], 4, p),
    "join_glyph": lambda p: small([pic_shot(), {"id": "j", "k": "join", "f": [0, 4], "mode": "glyph", "pic": "b", "crop": [0, 0, 1, 1], "t": "?", "v": "a",
                                                "x0": 0.42, "y0": 0.6, "s": 9.0}], 4, p, {"a": {"font": "InterDisplay-Bold.ttf", "px": 40, "track": 0, "shaped": True}}),
    "finish": lambda p: small([pic_shot()], 4, p, None, {"grain": 0.08, "vignette": 0.4, "bloom": 0.3, "halation": 0.3, "raster": {"a": 0.2}, "dither": 1.0}),
}


@pytest.mark.parametrize("op", sorted(GOLDEN_OPS))
def test_golden_frame_per_op(pics, op):
    """Each op against a kept frame: 45 dB on the runtime that drew the golden, 36 dB across runtimes
    (BUILD E2). VALMERA_SCENE_WRITE_GOLDEN=1 draws them again."""
    e = Engine(GOLDEN_OPS[op](pics), "/")
    got = e.stills([1])[1][..., :3]
    gl = e.rt.gl
    e.close()
    path = os.path.join(GOLD, f"op_{op}.png")
    meta = os.path.join(GOLD, "ops.json")
    if os.environ.get("VALMERA_SCENE_WRITE_GOLDEN"):
        Image.fromarray(got).save(path, optimize=True)
        doc = json.load(open(meta)) if os.path.exists(meta) else {}
        doc[op] = gl
        json.dump(doc, open(meta, "w"), indent=1, sort_keys=True)
    want = np.asarray(Image.open(path).convert("RGB"))
    drawn_by = json.load(open(meta)).get(op)
    floor = 45.0 if drawn_by == gl else 36.0
    assert psnr(got, want) >= floor, f"{op}: {psnr(got, want):.1f} dB against the golden drawn by {drawn_by}, on {gl}"


# ---- E3: odd sources render, or fault by name

def _ff(*args):
    r = subprocess.run([FF, "-v", "error", "-y", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]


def _shot_stills(path, frames=(0, 5, 10), dur=30, src=(0.0, 1.0), work="/"):
    l1 = film([{"id": "s", "k": "shot", "f": [0, dur], "pic": "main", "src": list(src), "crop": [0, 0, 1, 1]}], dur=dur,
              assets={"main": {"kind": "video", "path": path}}, frame=(360, 640))
    return stills(l1, list(frames), work=work)


def test_a_rotated_phone_clip_stands_upright(tmp_path, src):
    """Stored on its side with a display matrix, as a phone writes it."""
    side = str(tmp_path / "side.mp4")
    encode(side, (np.ascontiguousarray(np.rot90(barcode(n), 1)) for n in range(40)), 640, 360)
    rot = None
    for angle in ("-90", "90"):
        p = str(tmp_path / f"rot{angle}.mp4")
        _ff("-display_rotation", angle, "-i", side, "-c", "copy", p)
        info = engine.probe(p)
        d = engine.Decoder(dict(info, abs=p, rate=FPS, w=360, h=640), 7)
        fr = np.frombuffer(d.next(), np.uint8).reshape(640, 360, 4)
        d.close()
        if read_code(fr) == 7 and fr[-30:, :30, 0].mean() > 150 and fr[-30:, :30, 1].mean() < 90:
            rot = p
            break
    assert rot, "no display rotation gave an upright picture through ffmpeg itself"
    info = engine.probe(rot)
    assert (info["w"], info["h"]) == (360, 640), "the size as shown, not as stored"
    st = _shot_stills(rot)
    assert [read_code(st[f]) for f in (0, 5, 10)] == [0, 5, 10]
    assert st[5][-30:, :30, 0].mean() > 150 and st[5][-30:, :30, 2].mean() < 90, "the red corner is bottom left"


def test_non_square_pixels_fill_the_frame_they_were_shot_for(tmp_path):
    p = str(tmp_path / "ana.mp4")
    r = subprocess.Popen([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "180x640", "-r", "30", "-i", "-",
                          "-vf", "setsar=2/1,format=yuv420p", "-c:v", "libx264", "-crf", "10", p], stdin=subprocess.PIPE)
    for n in range(20):
        r.stdin.write(np.ascontiguousarray(barcode(n)[:, ::2]).tobytes())
    r.stdin.close()
    assert r.wait() == 0
    info = engine.probe(p)
    assert (info["w"], info["h"]) == (360, 640)
    st = _shot_stills(p, frames=(0, 6), dur=18, src=(0.0, 0.6))
    assert [read_code(st[f]) for f in (0, 6)] == [0, 6]
    red = st[6][-60:, :, 0] > 150
    assert 40 < int(red[:, :].any(axis=0).sum()) < 80, "the corner is as wide as it was drawn: not squeezed"


def test_variable_rate_and_a_late_start_land_on_the_right_frames(tmp_path, src):
    vfr = str(tmp_path / "vfr.mp4")                          # every other frame arrives late: an uneven clock
    _ff("-i", src, "-vf", "setpts='(N+0.4*mod(N\\,2))/(30*TB)'", "-fps_mode", "vfr", "-c:v", "libx264", "-crf", "10", "-t", "2", vfr)
    st = _shot_stills(vfr, frames=(0, 9, 18, 27), dur=30)
    got = [read_code(st[f]) for f in (0, 9, 18, 27)]
    assert all(abs(g - w) <= 1 for g, w in zip(got, (0, 9, 18, 27))), got
    late = str(tmp_path / "late.mp4")                        # a stream that starts at 5 s, as some phones write
    _ff("-i", src, "-c", "copy", "-output_ts_offset", "5", late)
    st = _shot_stills(late, frames=(0, 12), dur=30)
    assert [read_code(st[f]) for f in (0, 12)] == [0, 12], "source time counts from the clip's first frame"


def test_a_4k_source_is_read_no_larger_than_a_frame_needs(tmp_path):
    p = str(tmp_path / "4k.mp4")
    big = cv2.resize(barcode(5), (2160, 3840), interpolation=cv2.INTER_NEAREST)
    encode(p, (big for _ in range(4)), 2160, 3840, crf=20)
    e = Engine(film([{"id": "s", "k": "shot", "f": [0, 3], "pic": "main", "src": [0.0, 0.1], "crop": [0, 0, 1, 1]}], dur=3,
                    assets={"main": {"kind": "video", "path": p}}, frame=(360, 640)), "/")
    assert max(e.plan.assets["main"]["w"], e.plan.assets["main"]["h"]) == 2160
    assert read_code(e.stills([1])[1]) == 5
    e.close()


def test_a_cut_off_upload_holds_its_last_picture_and_a_broken_one_faults_by_name(tmp_path, src):
    whole = open(src, "rb").read()
    fast = str(tmp_path / "fast.mp4")
    _ff("-i", src, "-c", "copy", "-movflags", "+faststart", fast)
    cut = str(tmp_path / "cut.mp4")
    data = open(fast, "rb").read()
    open(cut, "wb").write(data[:int(len(data) * 0.55)])       # the upload stopped part way
    st = _shot_stills(cut, frames=(0, 20, 140), dur=150, src=(0.0, 5.0))
    assert read_code(st[0]) == 0 and read_code(st[20]) == 20
    assert 30 < read_code(st[140]) < 140, "past the end of what arrived, the last picture holds"
    junk = str(tmp_path / "junk.mp4")
    open(junk, "wb").write(whole[:200])
    l1 = film([{"id": "s", "k": "shot", "f": [0, 4], "pic": "main", "src": [0.0, 0.1], "crop": [0, 0, 1, 1]}], dur=4,
              assets={"main": {"kind": "video", "path": junk}}, frame=(360, 640))
    with pytest.raises(SceneFault) as ex:
        SC.compile(l1, "/", strict=True)
    assert ex.value.code == "plan" and "missing" in ex.value.detail, "no picture in the file: a fault with a name, so the film falls through"


def test_a_source_with_no_sound_gives_a_silent_track_of_the_film_length(tmp_path, src):
    l1 = film([{"id": "s", "k": "shot", "f": [0, 45], "pic": "main", "src": [0.0, 1.5], "crop": [0, 0, 1, 1]}], dur=45,
              assets={"main": {"kind": "video", "path": src}}, spine=[{"f": [0, 45], "src": [0.0, 1.5]}], frame=(360, 640))
    out = str(tmp_path / "o.mp4")
    log = engine.render(l1, "/", out)
    q = subprocess.run([engine.FP, "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", out], capture_output=True, text=True)
    dur = {x["codec_type"]: float(x["duration"]) for x in json.loads(q.stdout)["streams"]}
    assert log["frames"] == 45 and abs(dur["video"] - 1.5) < 0.02 and abs(dur["audio"] - 1.5) < 0.06 and log["audio"]["lufs"] is None


def test_a_shader_that_does_not_compile_faults_before_the_first_frame(monkeypatch, tmp_path):
    """Every program is built when the runtime starts, so a driver that rejects one says so in the
    first second, by name, and the film falls through to today's renderer."""
    import scene.runtime as RT
    bad = str(tmp_path / "glsl")
    shutil.copytree(RT.GLSL, bad)
    with open(os.path.join(bad, "out.frag.glsl"), "w") as f:
        f.write("in vec2 v;\nout vec4 o;\nvoid main() { o = this is not glsl; }\n")
    monkeypatch.setattr(RT, "GLSL", bad)
    with pytest.raises(SceneFault) as ex:
        Engine(film([plate(2)], dur=2), "/")
    assert ex.value.code == "gl" and "shader out" in ex.value.detail
