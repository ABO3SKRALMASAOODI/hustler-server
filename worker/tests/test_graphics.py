"""Unit tests for graphics.py (the EDL.texts motion-graphics compiler).

Pure logic — no ffmpeg, no fonts loaded, no network. Run from worker/:
    python3 -m pytest tests/test_graphics.py -q
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import graphics                                              # noqa: E402
from schemas import TEXT_ANIMS, TEXT_TEMPLATES               # noqa: E402

DIALOGUE_RE = re.compile(
    r"^Dialogue: 0,(\d+:\d{2}:\d{2}\.\d{2}),(\d+:\d{2}:\d{2}\.\d{2}),"
    r"(G\d+),,0,0,0,,(.+)$")


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
