"""Regression tests for complex-script (RTL / shaped) burned text.

The bug these pin: libass turns OFF complex shaping the moment a line has
non-zero letter-spacing (\\fsp), falling back to its SIMPLE FriBidi-only
path. That disables HarfBuzz cursive joining AND bidi reordering at once, so
Arabic burned in unjoined and BACKWARDS while every tool call still reported
success. A real customer's title cards ("اصنع مستقبلك معنا") shipped as
"ان عم كلبقتسم عنصا".

Verified against libass 0.17.5 by rendering both spellings to a bitmap; these
tests pin the two emission properties that fix carries, because the render
itself needs an ffmpeg with libass that CI does not have.

Run from worker/:  python3 -m pytest tests/test_rtl_text.py -q
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                                # noqa: E402
import graphics                                              # noqa: E402
import renderer                                              # noqa: E402

ARABIC = "اصنع مستقبلك معنا"
HEBREW = "בנה את העתיד שלך"
LATIN = "BUILD YOUR FUTURE"


def _build(texts, tmp_path, play_res=(1080, 1920)):
    path = str(tmp_path / "g.ass")
    graphics.build_gfx_ass({"texts": texts}, 60.0, path, play_res=play_res)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _item(text, **kw):
    d = {"id": "t1", "text": text, "start": 1.0, "end": 9.0,
         "template": "title"}
    d.update(kw)
    return d


# ── needs_shaping ────────────────────────────────────────────────────────

def test_needs_shaping_detects_rtl_and_indic():
    assert graphics.needs_shaping(ARABIC)
    assert graphics.needs_shaping(HEBREW)
    assert graphics.needs_shaping("नमस्ते")          # Devanagari
    assert graphics.needs_shaping("สวัสดี")           # Thai
    assert graphics.needs_shaping("Hello مرحبا")     # mixed still counts


def test_needs_shaping_false_for_latin_digits_cjk():
    for s in (LATIN, "SAVE 40% IN 2026", "CRÉEZ VOTRE AVENIR", "未来を作る",
              "", "!!! ---"):
        assert not graphics.needs_shaping(s), s


# ── the \fsp suppression (the actual bug) ────────────────────────────────

def test_arabic_emits_no_letter_spacing(tmp_path):
    """A non-zero \\fsp is what silently reversed the text."""
    assert r"\fsp" not in _build([_item(ARABIC)], tmp_path)


def test_every_template_drops_fsp_for_arabic(tmp_path):
    """4 of 5 templates carry a non-zero spacing; none may apply it here."""
    for tpl in graphics.TEMPLATES:
        out = _build([_item(ARABIC, template=tpl)], tmp_path)
        assert r"\fsp" not in out, f"template {tpl} still letter-spaces Arabic"


def test_latin_keeps_its_letter_spacing(tmp_path):
    """The fix must not flatten Latin typography — tracking is the look."""
    assert r"\fsp" in _build([_item(LATIN)], tmp_path)


def test_second_deck_also_drops_fsp(tmp_path):
    """lower_third's deck2 has its own spacing and its own emission path."""
    out = _build([_item(ARABIC + " | " + ARABIC, template="lower_third")],
                 tmp_path)
    assert r"\fsp" not in out


# ── base direction ───────────────────────────────────────────────────────

def test_style_encoding_is_auto_direction(tmp_path):
    """Encoding -1 = FONT_ENCODING_AUTO_DIRECTION. Any other value hard-forces
    LTR, which laid "مرحبا VALMERA" out in Latin word order."""
    for line in _build([_item(ARABIC)], tmp_path).splitlines():
        if line.startswith("Style: G"):
            assert line.rstrip().endswith(",-1"), line


# ── typewriter degradation ───────────────────────────────────────────────

def test_typewriter_reveals_arabic_by_line_not_by_glyph(tmp_path):
    """Per-glyph \\alpha segments make each glyph its own shaping run, so the
    letters render unjoined and overlapping. One window per line keeps the
    line a single run."""
    out = _build([_item(ARABIC, entrance="typewriter")], tmp_path)
    dialogue = [l for l in out.splitlines() if l.startswith("Dialogue")][0]
    # 3 wrapped lines -> at most 3 reveal windows, not one per letter.
    assert dialogue.count(r"\alpha&HFF&") <= 3
    assert r"\fsp" not in dialogue


def test_typewriter_still_per_glyph_for_latin(tmp_path):
    out = _build([_item(LATIN, entrance="typewriter")], tmp_path)
    dialogue = [l for l in out.splitlines() if l.startswith("Dialogue")][0]
    assert dialogue.count(r"\alpha&HFF&") > 3


# ── cache invalidation is scoped to shaped EDLs only ─────────────────────

def test_only_shaped_edls_bust_the_render_cache():
    latin = {"texts": [{"text": LATIN}]}
    arabic = {"texts": [{"text": ARABIC}]}
    assert not renderer.edl_has_shaped_text(latin)
    assert renderer.edl_has_shaped_text(arabic)
    # A pre-fix render: Latin keeps its cache, Arabic re-encodes.
    assert renderer.shaping_current({"outro_v": 2}, latin)
    assert not renderer.shaping_current({"outro_v": 2}, arabic)
    # A post-fix render is current either way.
    stamp = {"gfx_shape_v": config.GFX_SHAPING_VERSION}
    assert renderer.shaping_current(stamp, arabic)


def test_edl_with_no_texts_never_busts():
    assert renderer.shaping_current({}, {})
    assert renderer.shaping_current({}, {"texts": []})
