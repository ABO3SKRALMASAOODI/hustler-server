"""Unit tests for graphics.py (the EDL.texts motion-graphics compiler).

Pure logic — no ffmpeg, no fonts loaded, no network. Run from worker/:
    python3 -m pytest tests/test_graphics.py -q
"""

import os
import re
import glob
import shutil
import subprocess
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import graphics                                              # noqa: E402
from schemas import TEXT_ANIMS, TEXT_TEMPLATES               # noqa: E402

DIALOGUE_RE = re.compile(
    r"^Dialogue: 0,(\d+:\d{2}:\d{2}\.\d{2}),(\d+:\d{2}:\d{2}\.\d{2}),"
    r"(G\d+),,0,0,0,,(.+)$")


def _libass_ffmpeg():
    """Find an ffmpeg that can execute the production ASS burn."""
    candidates = [shutil.which("ffmpeg")]
    candidates.extend(sorted(glob.glob(
        "/usr/local/Cellar/ffmpeg-full/*/bin/ffmpeg"), reverse=True))
    for binary in filter(None, candidates):
        probe = subprocess.run([binary, "-hide_banner", "-filters"],
                               capture_output=True, text=True)
        if re.search(r"\bsubtitles\b", probe.stdout + probe.stderr):
            return binary
    return None


LIBASS_FFMPEG = _libass_ffmpeg()


def _t(s):
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def _build(texts, out_dur=100.0, play_res=(1280, 720), tmp_path=None,
           name="g.ass"):
    path = str(tmp_path / name)
    return graphics.build_gfx_ass({"texts": texts}, out_dur, path,
                                  play_res=play_res)


def _events(path):
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    lines = content.splitlines()
    evs = [DIALOGUE_RE.match(l) for l in lines if l.startswith("Dialogue:")]
    assert all(evs), "every Dialogue line must match the expected shape"
    styles = {m.group(1) for m in
              (re.match(r"^Style: (G\d+),", l) for l in lines) if m}
    return content, evs, styles


def test_every_template_every_entrance(tmp_path):
    """Every template x every entrance anim compiles to a parseable event
    that references a declared style, with balanced override braces."""
    texts = []
    i = 0
    for tpl in TEXT_TEMPLATES:
        for anim in TEXT_ANIMS:
            texts.append({"id": f"t{i:03d}", "text": "Griffith Park at dawn",
                          "start": i * 1.0, "end": i * 1.0 + 0.8,
                          "template": tpl, "entrance": anim})
            i += 1
    path = _build(texts, out_dur=float(i + 5), tmp_path=tmp_path)
    assert path
    content, evs, styles = _events(path)
    assert len(evs) == len(texts)
    for m in evs:
        assert m.group(3) in styles, "event references an undeclared style"
        body = m.group(4)
        assert body.count("{") == body.count("}")
        assert _t(m.group(2)) > _t(m.group(1))


def test_every_exit_anim(tmp_path):
    """Every non-typewriter anim as an EXIT compiles on every template."""
    exits = [a for a in TEXT_ANIMS if a != "typewriter"]
    texts = []
    i = 0
    for tpl in TEXT_TEMPLATES:
        for anim in exits:
            texts.append({"id": f"x{i:03d}", "text": "Chapter Two | The Build",
                          "start": i * 1.0, "end": i * 1.0 + 0.9,
                          "template": tpl, "exit": anim})
            i += 1
    path = _build(texts, out_dur=float(i + 5), tmp_path=tmp_path)
    content, evs, _ = _events(path)
    assert len(evs) == len(texts)
    for m in evs:
        body = m.group(4)
        assert body.count("{") == body.count("}")


def test_determinism(tmp_path):
    """Same EDL -> byte-identical files across two builds."""
    texts = [
        {"id": "a", "text": "BIG LAUNCH", "start": 1.0, "end": 4.0,
         "template": "title", "accent_color": "#FF3355"},
        {"id": "b", "text": "Sarah Chen | Head of Product", "start": 5.0,
         "end": 9.0, "template": "lower_third"},
        {"id": "c", "text": "42 | DAYS LEFT", "start": 10.0, "end": 13.0,
         "template": "big_number", "entrance": "typewriter"},
    ]
    p1 = _build(texts, tmp_path=tmp_path, name="a.ass")
    p2 = _build(texts, tmp_path=tmp_path, name="b.ass")
    with open(p1, "rb") as f1, open(p2, "rb") as f2:
        assert f1.read() == f2.read()


def test_general_motion_compiles_all_axes_as_continuous_spans(tmp_path):
    texts = [{
        "id": "motion", "text": "MOVE", "start": 1.0, "end": 3.0,
        "template": "title", "entrance": "none", "exit": "none",
        "motion": {
            "x": [{"t": 0.0, "v": -0.1},
                  {"t": 2.0, "v": 0.8, "ease": "in_out"}],
            "y": [{"t": 0.0, "v": 0.35}, {"t": 2.0, "v": 0.55}],
            "scale": [{"t": 0.0, "v": 0.6},
                      {"t": 2.0, "v": 1.3, "ease": "out"}],
            "rotation": [{"t": 0.0, "v": -12.0},
                         {"t": 2.0, "v": 16.0}],
            "opacity": [{"t": 0.0, "v": 0.0},
                        {"t": 0.2, "v": 1.0, "ease": "out"},
                        {"t": 1.8, "v": 1.0},
                        {"t": 2.0, "v": 0.0, "ease": "in"}],
        },
    }]
    path = _build(texts, out_dur=4.0, play_res=(640, 360),
                  tmp_path=tmp_path)
    content, events, _ = _events(path)
    assert len(events) > 4                # nonlinear curves are sampled
    assert r"\move(" in content
    assert r"\fscx" in content and r"\fscy" in content
    assert r"\frz" in content and r"\alpha&H" in content
    assert r"\t(0," in content
    # The motion path is allowed to originate intentionally off-frame.
    first_x = int(re.search(r"\\move\((-?\d+),", events[0].group(4)).group(1))
    assert first_x < 0
    # Segments meet exactly: no visible hole in the authored window.
    windows = [(_t(m.group(1)), _t(m.group(2))) for m in events]
    assert windows[0][0] == 1.0 and windows[-1][1] == 3.0
    assert all(abs(a[1] - b[0]) <= 0.011 for a, b in zip(windows, windows[1:]))


@pytest.mark.skipif(not LIBASS_FFMPEG,
                    reason="needs ffmpeg with the libass subtitles filter")
def test_general_motion_renders_position_scale_rotation_and_opacity(
        tmp_path):
    """Golden pixel proof: the emitted ASS changes actual rendered frames."""
    texts = [{
        "id": "golden", "text": "MOVE", "start": 0.0, "end": 2.0,
        "template": "title", "entrance": "none", "exit": "none",
        "motion": {
            "x": [{"t": 0.0, "v": 0.2},
                  {"t": 2.0, "v": 0.8, "ease": "in_out"}],
            "scale": [{"t": 0.0, "v": 0.6},
                      {"t": 2.0, "v": 1.3, "ease": "out"}],
            "rotation": [{"t": 0.0, "v": -10.0},
                         {"t": 2.0, "v": 15.0}],
            "opacity": [{"t": 0.0, "v": 0.0},
                        {"t": 0.2, "v": 1.0, "ease": "out"},
                        {"t": 1.8, "v": 1.0},
                        {"t": 2.0, "v": 0.0, "ease": "in"}],
        },
    }]
    ass = _build(texts, out_dur=2.0, play_res=(640, 360),
                 tmp_path=tmp_path, name="golden.ass")
    video = str(tmp_path / "golden.mp4")
    filt = f"subtitles=filename='{ass}':fontsdir='{graphics.FONTS_DIR}'"
    subprocess.run([
        LIBASS_FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=black:s=640x360:r=30:d=2", "-vf", filt,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", video,
    ], check=True)

    def pixels_at(t, name):
        frame = str(tmp_path / name)
        subprocess.run([
            LIBASS_FFMPEG, "-y", "-v", "error", "-ss", str(t), "-i",
            video, "-frames:v", "1", frame,
        ], check=True)
        image = Image.open(frame).convert("RGB")
        pts = [(x, y) for y in range(image.height) for x in range(image.width)
               if sum(image.getpixel((x, y))) > 80]
        energy = sum(sum(image.getpixel((x, y)))
                     for y in range(image.height) for x in range(image.width))
        if not pts:
            return None, 0, energy
        box = (min(x for x, _ in pts), min(y for _, y in pts),
               max(x for x, _ in pts), max(y for _, y in pts))
        return box, len(pts), energy

    almost_hidden, hidden_pixels, hidden_energy = pixels_at(
        0.02, "hidden.png")
    early, early_pixels, early_energy = pixels_at(0.25, "early.png")
    late, late_pixels, _late_energy = pixels_at(1.70, "late.png")
    assert early and late
    early_cx = (early[0] + early[2]) / 2.0
    late_cx = (late[0] + late[2]) / 2.0
    assert late_cx - early_cx > 220       # position curve rendered
    assert late[2] - late[0] > (early[2] - early[0]) * 1.45  # scale
    assert late[3] - late[1] > early[3] - early[1]            # rotation/scale
    assert hidden_energy < early_energy * 0.45                # opacity fade


def test_offframe_xy_clamped(tmp_path):
    """x/y at the extremes still position the block fully on-frame."""
    W, H = 1280, 720
    texts = [
        {"id": "a", "text": "EDGE", "start": 0.0, "end": 2.0,
         "template": "title", "x": 1.0, "y": 0.0},
        {"id": "b", "text": "EDGE", "start": 3.0, "end": 5.0,
         "template": "lower_third", "x": 0.0, "y": 1.0},
        {"id": "c", "text": "EDGE", "start": 6.0, "end": 8.0,
         "template": "big_number", "x": 0.5, "y": 1.0},
    ]
    path = _build(texts, out_dur=20.0, play_res=(W, H), tmp_path=tmp_path)
    content, evs, _ = _events(path)
    coords = re.findall(r"\\(?:pos|move)\((-?\d+),(-?\d+)", content)
    assert coords
    for x, y in coords:
        assert 0 <= int(x) <= W
        assert 0 <= int(y) <= H


def test_end_clamped_to_program(tmp_path):
    """Event ends never exceed out_duration_s; min event length holds."""
    texts = [
        {"id": "a", "text": "outro", "start": 8.0, "end": 999.0,
         "template": "subtitle"},
        {"id": "b", "text": "sliver", "start": 9.9, "end": 9.95,
         "template": "callout"},
    ]
    path = _build(texts, out_dur=10.0, tmp_path=tmp_path)
    content, evs, _ = _events(path)
    assert len(evs) == 2
    for m in evs:
        s, e = _t(m.group(1)), _t(m.group(2))
        assert e <= 10.0 + 0.01
        assert e - s >= graphics.GFX_MIN_EVENT_S - 0.02


def test_fully_past_program_dropped(tmp_path):
    """An item entirely past the program compiles to nothing -> None."""
    texts = [{"id": "a", "text": "ghost", "start": 50.0, "end": 60.0,
              "template": "title"}]
    assert _build(texts, out_dur=10.0, tmp_path=tmp_path) is None


def test_empty_texts_none(tmp_path):
    assert _build([], tmp_path=tmp_path) is None
    path = str(tmp_path / "n.ass")
    assert graphics.build_gfx_ass({}, 10.0, path) is None
    assert not os.path.exists(path)


def test_typewriter_glyph_cap(tmp_path):
    """A 200-char typewriter item animates at most TYPEWRITER_MAX_GLYPHS
    glyph windows — the rest of the text appears with the last window."""
    long_text = ("the quick brown fox jumps over the lazy dog and keeps "
                 "going far past any reasonable title length to stress the "
                 "per glyph reveal window cap in the typewriter entrance "
                 "animation recipe right here")[:200]
    texts = [{"id": "a", "text": long_text, "start": 0.0, "end": 6.0,
              "template": "subtitle", "entrance": "typewriter"}]
    path = _build(texts, out_dur=10.0, tmp_path=tmp_path)
    content, evs, _ = _events(path)
    n_windows = content.count(r"\alpha&HFF&\t(")
    assert n_windows <= graphics.TYPEWRITER_MAX_GLYPHS
    # and the full text is still present (no truncation)
    flat = re.sub(r"\{[^}]*\}", "", evs[0].group(4)).replace(r"\N", " ")
    assert "typewriter" in flat.lower()   # subtitle template uppercases


def test_move_conflict_degrades_to_fade(tmp_path):
    """\\move is single-occupancy: a moving entrance + a moving exit keeps
    the entrance's \\move and degrades the exit to a fade (never dropped)."""
    texts = [{"id": "a", "text": "MOVE FIGHT", "start": 0.0, "end": 3.0,
              "template": "title", "entrance": "rise", "exit": "drop"}]
    path = _build(texts, out_dur=10.0, tmp_path=tmp_path)
    content, evs, _ = _events(path)
    body = evs[0].group(4)
    assert body.count(r"\move(") == 1
    assert r"\fad(120,220)" in body     # entrance move fade-in + exit fade


def test_lower_third_two_deck(tmp_path):
    """The ' | ' separator produces a smaller accent second deck."""
    texts = [{"id": "a", "text": "Sarah Chen | HEAD OF PRODUCT",
              "start": 0.0, "end": 4.0, "template": "lower_third",
              "accent_color": "#00C2FF"}]
    path = _build(texts, out_dur=10.0, tmp_path=tmp_path)
    content, evs, _ = _events(path)
    body = evs[0].group(4)
    assert r"\N" in body
    # deck 2 restates a smaller \fs and the accent color (&HBBGGRR& order)
    sizes = [int(n) for n in re.findall(r"\\fs(\d+)", body)]
    assert len(sizes) >= 2 and sizes[-1] < sizes[0]
    assert r"\1c&HFFC200&" in body


def test_none_is_instant(tmp_path):
    """entrance/exit 'none' emits NO animation tags: no \\fad, no \\move,
    no \\t transforms — just a static \\pos for the whole window."""
    texts = [{"id": "n1", "text": "I TRAINED AN AI AGENT",
              "start": 0.0, "end": 2.0, "template": "title",
              "entrance": "none", "exit": "none"}]
    path = _build(texts, out_dur=10.0, tmp_path=tmp_path)
    content, evs, _ = _events(path)
    body = evs[0].group(4)
    assert r"\pos(" in body
    assert r"\fad(" not in body
    assert r"\move(" not in body
    assert r"\t(" not in body


def test_none_mixes_with_real_anim(tmp_path):
    """'none' on one side leaves the other side's animation intact."""
    texts = [{"id": "m1", "text": "INSTANT IN, RISING OUT",
              "start": 0.0, "end": 2.0, "template": "title",
              "entrance": "none", "exit": "rise"},
             {"id": "m2", "text": "FADING IN, INSTANT OUT",
              "start": 3.0, "end": 5.0, "template": "title",
              "entrance": "fade", "exit": "none"}]
    path = _build(texts, out_dur=10.0, tmp_path=tmp_path)
    content, evs, _ = _events(path)
    b1, b2 = evs[0].group(4), evs[1].group(4)
    assert r"\move(" in b1                      # the rise exit animates
    assert r"\fad(" in b2 and r"\move(" not in b2
    assert ",0)" in b2.split(r"\fad(")[1][:12]  # fad(in,0): no fade-out


def test_typewriter_with_none_exit_keeps_no_exit(tmp_path):
    """typewriter forces exit='fade' UNLESS the caller explicitly asked
    for 'none' — an instant disappearance needs no exit tag."""
    texts = [{"id": "tw", "text": "TYPED", "start": 0.0, "end": 2.0,
              "template": "title", "entrance": "typewriter", "exit": "none"}]
    path = _build(texts, out_dur=10.0, tmp_path=tmp_path)
    content, evs, _ = _events(path)
    body = evs[0].group(4)
    assert r"\fad(" not in body


# ── round 79: pinned composition is never restacked ─────────────────────────

def _pos_ys(path):
    """Each event's SETTLED y — \\pos's y, or \\move's destination y."""
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    ys = []
    for m in re.finditer(
            r"\\(pos|move)\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)"
            r"(?:,(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?))?", content):
        ys.append(float(m.group(5) if m.group(5) is not None else m.group(3)))
    return ys


def test_pinned_pair_is_never_restacked(tmp_path):
    """Two chunks the author set side by side (a two-colour wordmark) keep
    their y — the collision stacker must not 'repair' deliberate layout."""
    texts = [
        {"id": "a", "text": "Valmera", "start": 1.0, "end": 4.0,
         "template": "title", "x": 0.47, "y": 0.72},
        {"id": "b", "text": ".io", "start": 1.0, "end": 4.0,
         "template": "title", "x": 0.60, "y": 0.72},
    ]
    ys = _pos_ys(_build(texts, tmp_path=tmp_path))
    assert len(ys) >= 2
    assert abs(max(ys) - min(ys)) < 1.0, ys


def test_unpinned_overlap_still_stacks(tmp_path):
    """Template-positioned texts sharing a moment and a patch of frame keep
    the round-58 collision layout — the pinned exemption must not undo it."""
    texts = [
        {"id": "a", "text": "A BIG CENTRED TITLE", "start": 1.0, "end": 4.0,
         "template": "title"},
        {"id": "b", "text": "ANOTHER TITLE RIGHT THERE", "start": 1.0,
         "end": 4.0, "template": "title"},
    ]
    ys = _pos_ys(_build(texts, tmp_path=tmp_path))
    assert len(ys) >= 2
    assert abs(max(ys) - min(ys)) > 10.0, ys
