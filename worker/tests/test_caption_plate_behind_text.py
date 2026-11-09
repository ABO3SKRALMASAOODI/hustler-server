"""A caption's plate and shadow sit BEHIND its glyphs (owner, 9 Nov 2026).

Project 5574, render v35 (preview job 137750): set_caption_style gave the
manual captions preset "clean", anchor_y 0.84, a 35% black plate and a thin
shadow. The plate was centred on the preset's anchor (0.80) while the text
sat on the style's own 0.84, so a dark band floated above "dan polanya
sama". The geometry tests fail on the old captions.py; the raster sweep
draws every preset, language, wrap and size through libass and measures the
pixels with caption_qc (the same check the render runs).

    cd worker && PYTHONPATH=.. python -m pytest tests/test_caption_plate_behind_text.py -q
"""
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import caption_qc  # noqa: E402
import captions  # noqa: E402
from timeline import Timeline  # noqa: E402

# The stored style of project 5574 v35 (one of its 32 manual items).
STYLE_5574 = {
    "font": "Montserrat", "size": "m", "color": "#FFFFFF", "preset": "clean",
    "shadow": 1.0, "anchor_y": 0.84, "position": "bottom",
    "animation": "fade", "uppercase": False, "size_scale": 0.733,
    "motion_look": "clean", "single_line": True, "outline_width": 0.0,
    "background_color": "#000000", "background_opacity": 0.35}

_POS = re.compile(r"\\pos\((-?\d+),(-?\d+)\)")
_NUM = re.compile(r"-?\d+")


def _items(text, style, t0=1.0, t1=2.5):
    return [{"start": t0, "end": t1, "text": text, "style": style}]


def _manual_events(text, style, play_res):
    tl = Timeline([[0.0, 30.0]], [], None)
    return captions.events_from_items(_items(text, style), tl,
                                      play_res=play_res)


def _panel_and_text(events):
    panel = next(e for e in events if r"\p1" in e["text"])
    text = next(e for e in events if r"\p1" not in e["text"])
    ys = [int(v) for v in _NUM.findall(panel["text"].split("}", 1)[1])][1::2]
    return (min(ys), max(ys)), int(_POS.search(text["text"]).group(2))


@pytest.mark.parametrize("play_res", [(576, 1024), (1080, 1920)])
def test_5574_plate_is_centred_on_the_caption_text(play_res):
    (y0, y1), text_y = _panel_and_text(
        _manual_events("dan polanya sama", STYLE_5574, play_res))
    # Old code: plate rows 788-851 around a text centre at 860 (576x1024).
    assert abs((y0 + y1) / 2 - text_y) <= 2, (y0, y1, text_y)
    assert y0 < text_y < y1


@pytest.mark.parametrize("anchor_y", [0.12, 0.5, 0.84, 0.95])
@pytest.mark.parametrize("position", ["top", "middle", "bottom"])
def test_every_anchor_puts_the_plate_on_the_text(anchor_y, position):
    style = dict(STYLE_5574, anchor_y=anchor_y, position=position)
    (y0, y1), text_y = _panel_and_text(
        _manual_events("dan polanya sama", style, (1080, 1920)))
    assert abs((y0 + y1) / 2 - text_y) <= 2, (y0, y1, text_y)


def test_transcript_captions_plate_follows_the_style_anchor():
    words = [{"w": w, "t0": i * 0.35, "t1": i * 0.35 + 0.3}
             for i, w in enumerate("dan polanya sama".split())]
    style = dict(STYLE_5574, single_line=None)
    events = captions.events_premium(words, style, play_res=(1080, 1920),
                                     design_version=2)
    panel = next(e for e in events if r"\p1" in e["text"])
    ys = [int(v) for v in _NUM.findall(panel["text"].split("}", 1)[1])][1::2]
    texts = [int(m.group(2)) for e in events if r"\p1" not in e["text"]
             for m in [_POS.search(e["text"])] if m]
    assert texts and min(ys) < min(texts) and max(texts) < max(ys)
    assert abs((min(ys) + max(ys)) / 2 - sum(texts) / len(texts)) < 0.03 * 1920


# ── Raster sweep: libass pixels measured by caption_qc ─────────────────────

FFMPEG = shutil.which("ffmpeg")
LANGS = {
    "id": "dan polanya sama",
    "id-long": "orang sering mikir kalau bisnis bekam itu cuma soal teknik "
               "padahal pemasarannya juga penting sekali",
    "en": "THE WHOLE POINT IS CONSISTENCY",
    "ar": "النمط نفسه في كل مرة",
    "ja": "毎回同じパターンです",
    "de": "Größenwahnsinnige Überraschungsmomente",
}
PLATED = [name for name in captions.PRESETS]


def _burn(tmp_path, text, style, play_res):
    tl = Timeline([[0.0, 30.0]], [], None)
    path = tmp_path / "c.ass"
    events = captions.events_from_items(_items(text, style), tl,
                                        play_res=play_res)
    captions.write_ass(events, str(path), style, play_res=play_res)
    return str(path)


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg with libass")
@pytest.mark.parametrize("preset", PLATED)
@pytest.mark.parametrize("lang", sorted(LANGS))
@pytest.mark.parametrize("play_res", [(576, 1024), (1080, 1920)])
def test_raster_plate_and_shadow_sit_behind_the_glyphs(tmp_path, preset,
                                                       lang, play_res):
    style = dict(STYLE_5574, preset=preset, font=None, single_line=None,
                 shadow=1.5)
    if lang in ("ar", "ja"):
        style["uppercase"] = False
    ass = _burn(tmp_path, LANGS[lang], style, play_res)
    defects = caption_qc.check(ass, *play_res, str(tmp_path / "qc"),
                               fontsdir=captions.FONTS_DIR, ffmpeg=FFMPEG,
                               times=[1.2, 1.75, 2.3])
    assert defects == [], defects


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg with libass")
def test_raster_check_catches_the_5574_plate_offset(tmp_path):
    """The check on the shipped 5574 geometry (plate 63 px above the text
    centre at 576x1024): a plate-offset defect at the caption's frame."""
    W, H = 576, 1024
    path = tmp_path / "old.ass"
    path.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 576\nPlayResY: 1024\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: VS1,Montserrat,38,&H00FFFFFF,&H00FFFFFF,&H00101010,"
        "&H96000000,0,0,0,0,100,100,0,0,1,0.0,1.4,5,58,58,0,-1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:13.73,0:00:14.86,VS1,,0,0,0,,{\\an7\\pos(0,0)\\p1"
        "\\1c&H000000&\\fad(180,140)\\1a&HA6&\\bord0\\shad0}m 121 788 l 455 "
        "788 l 455 851 l 121 851\n"
        "Dialogue: 5,0:00:13.73,0:00:14.86,VS1,,0,0,0,,{\\an5\\pos(288,860)}"
        "{\\fad(180,140)}dan polanya sama\n", encoding="utf-8")
    defects = caption_qc.check(str(path), W, H, str(tmp_path / "qc"),
                               fontsdir=captions.FONTS_DIR, ffmpeg=FFMPEG)
    kinds = {d["cause_key"] for d in defects}
    assert "tool:captions:plate-offset" in kinds, defects
    d = next(d for d in defects if d["defect"] == "plate-offset")
    assert 13.73 <= d["t"] < 14.86


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg with libass")
def test_raster_check_catches_cut_text_and_overlapping_items(tmp_path):
    W, H = 576, 1024
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 576\nPlayResY: 1024\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: A,Montserrat,48,&H00FFFFFF,&H00FFFFFF,&H00101010,"
        "&H96000000,0,0,0,0,100,100,0,0,1,0,0,5,10,10,0,-1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n")
    cut = tmp_path / "cut.ass"
    cut.write_text(head + "Dialogue: 0,0:00:01.00,0:00:02.00,A,,0,0,0,,"
                   "{\\an5\\pos(560,500)}cut by the edge\n", encoding="utf-8")
    kinds = {d["defect"] for d in caption_qc.check(
        str(cut), W, H, str(tmp_path / "q1"), fontsdir=captions.FONTS_DIR,
        ffmpeg=FFMPEG)}
    assert "text-cut" in kinds
    both = tmp_path / "both.ass"
    both.write_text(head + "Dialogue: 0,0:00:01.00,0:00:03.00,A,,0,0,0,,"
                    "{\\an5\\pos(288,500)}first item\n"
                    "Dialogue: 0,0:00:01.50,0:00:02.50,A,,0,0,0,,"
                    "{\\an5\\pos(290,505)}second item\n", encoding="utf-8")
    kinds = {d["defect"] for d in caption_qc.check(
        str(both), W, H, str(tmp_path / "q2"), fontsdir=captions.FONTS_DIR,
        ffmpeg=FFMPEG)}
    assert "type-overlap" in kinds
