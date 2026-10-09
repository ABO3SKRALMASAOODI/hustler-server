"""Callout motion templates: arrow, circle, marker text, lower third, emoji,
checklist and versus. Specs, sound cues and real-Chromium renders."""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import sfx_kit  # noqa: E402

CALLOUTS = ["arrow_callout", "circle_highlight", "marker_text", "lower_third",
            "emoji_pop", "checklist", "versus_split"]


def _chromium_ok():
    if not motion_engine.available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(args=motion_engine.CHROME_ARGS)
            b.close()
        return True
    except Exception:
        return False


CHROMIUM = _chromium_ok()
needs_browser = pytest.mark.skipif(not (CHROMIUM and shutil.which("ffmpeg")),
                                   reason="headless Chromium + ffmpeg required")


def test_callout_specs_are_complete():
    for name in CALLOUTS:
        spec = motion_templates.spec(name)
        assert spec["category"] == "callout", name
        assert spec.get("description") and spec.get("duration"), name
        motion_templates.check_params(name, spec["example"])
        assert spec["sfx"], f"{name} has no sound cue"
        for cue in spec["sfx"]:
            assert cue["kind"] in sfx_kit.KINDS, (name, cue)
    # aiming templates must be told where to aim
    for name, keys in (("arrow_callout", ("target_x", "target_y")), ("circle_highlight", ("cx", "cy"))):
        for k in keys:
            assert motion_templates.spec(name)["params"][k].get("required"), (name, k)
    assert motion_templates.spec("marker_text")["mutes_captions"] is True


def test_checklist_ticks_follow_each_rows_time():
    spec = motion_templates.spec("checklist")
    params = motion_templates.check_params("checklist", {"items": [
        {"text": "One", "at": "0.5"}, {"text": "Two"}, {"text": "Three", "at": "2,25"}]})
    cues = motion_tools._sfx_cues(spec, params, 10.0, 14.0)
    assert [(t, k) for t, k, _ in cues] == [(10.62, "tick"), (10.87, "tick"), (12.37, "tick")]
    # no 'at' anywhere: an even cadence that matches the template's default spacing
    params = motion_templates.check_params("checklist", spec["example"])
    assert [t for t, _, _ in motion_tools._sfx_cues(spec, params, 0.0, 4.0)] == [0.32, 0.87, 1.42]


def test_emoji_pop_sounds_once_per_emoji():
    spec = motion_templates.spec("emoji_pop")
    params = motion_templates.check_params("emoji_pop", {"emoji": ["🔥", "💸", "🚀"]})
    assert [(t, k) for t, k, _ in motion_tools._sfx_cues(spec, params, 2.0, 4.0)] == [
        (2.04, "pop_bright"), (2.16, "pop_bright"), (2.28, "pop_bright")]


VARIANTS = [
    ("arrow_callout", {"label": "this is the trick", "target_x": 0.62, "target_y": 0.4}),
    ("arrow_callout", {"label": "a long label that has to wrap", "target_x": 0.05, "target_y": 0.05, "font": "sans"}),
    ("arrow_callout", {"label": "here", "target_x": 0.95, "target_y": 0.95, "side": "below", "font": "serif"}),
    ("arrow_callout", {"label": "him", "target_x": 0.5, "target_y": 0.5, "side": "right", "size": 1.5}),
    ("circle_highlight", {"cx": 0.5, "cy": 0.42, "label": "watch this", "style": "double"}),
    ("circle_highlight", {"cx": 0.97, "cy": 0.03, "w": 0.9, "h": 0.6, "label": "too big", "label_side": "left", "glow": False}),
    ("circle_highlight", {"cx": 0.2, "cy": 0.8, "w": 0.04, "h": 0.02, "label_side": "right"}),
    ("marker_text", {"text": "Most people *quit* right before it works"}),
    ("marker_text", {"text": "*Build* / something / *people want*", "style": "box", "uppercase": True}),
    ("marker_text", {"text": "No stars here, but a rather long line that has to wrap onto three lines somehow",
                     "style": "underline", "font": "serif"}),
    ("marker_text", {"text": "*ten* times", "font": "condensed", "y": 0.1}),
    ("lower_third", {"name": "Peter Thiel", "role": "Co-founder, PayPal"}),
    ("lower_third", {"name": "Alexandria Ocasio-Cortez", "role": "U.S. Representative, New York 14th",
                     "style": "glass_pill", "side": "right"}),
    ("lower_third", {"name": "Elon Musk", "style": "minimal", "y": 0.8, "size": 1.4}),
    ("emoji_pop", {"emoji": ["🤯"]}),
    ("emoji_pop", {"emoji": ["💸", "📉", "?!"], "x": 0.08, "y": 0.82, "burst": False}),
    ("checklist", {"items": [{"text": "Talk to users"}, {"text": "Ship"}], "panel": False}),
    ("checklist", {"items": [{"text": "A row that is quite long indeed for a list", "at": "0.1"}] * 5,
                   "title": "Five myths", "mark": "cross", "strike": True, "y": 0.8}),
    ("versus_split", {"left": "Tesla", "right": "Ford", "left_sub": "$800B", "right_sub": "$45B"}),
    ("versus_split", {"left": "Independent media", "right": "TV", "tint": False, "y": 0.15}),
]


@needs_browser
def test_callout_variants_render_without_errors():
    jobs, times = [], []
    for name, params in VARIANTS:
        clean = motion_templates.check_params(name, params)
        dur = float(motion_templates.spec(name)["duration"])
        item = {"id": name, "template": name, "start": 0, "end": dur, "params": clean}
        jobs.append(motion_templates.build_job(item, 540, 960, 30))
        times.append([0.12, dur * 0.5, dur - 0.4])
    reports = motion_engine.probe(jobs, times)
    for (name, params), rep in zip(VARIANTS, reports):
        assert not rep["errors"], (name, params, rep["errors"])
        assert rep["visible_frames"] >= 2, (name, params, rep)
        for bb in rep["bboxes"]:
            # never spill past the frame (fractions, quarter-scale probe)
            assert -0.01 <= bb[0] <= bb[2] <= 1.01 and -0.01 <= bb[1] <= bb[3] <= 1.01, (name, bb)


@needs_browser
def test_callouts_survive_short_long_and_landscape_items():
    jobs, times, labels = [], [], []
    for name in CALLOUTS:
        params = motion_templates.check_params(name, motion_templates.spec(name)["example"])
        for dur, size in ((0.8, (540, 960)), (8.0, (540, 960)), (3.0, (960, 540)), (3.0, (720, 720))):
            item = {"id": name, "template": name, "start": 0, "end": dur, "params": params}
            jobs.append(motion_templates.build_job(item, size[0], size[1], 30))
            times.append([dur * 0.5, dur - 0.1])
            labels.append((name, dur, size))
    reports = motion_engine.probe(jobs, times)
    for label, rep in zip(labels, reports):
        assert not rep["errors"], (label, rep["errors"])
        assert rep["visible_frames"] >= 1, (label, rep)


@needs_browser
def test_arrow_and_circle_land_on_the_requested_spot():
    cases = [("arrow_callout", {"label": "this", "target_x": 0.7, "target_y": 0.3}, (0.7, 0.3)),
             ("arrow_callout", {"label": "that", "target_x": 0.2, "target_y": 0.66, "side": "above"}, (0.2, 0.66)),
             ("circle_highlight", {"cx": 0.3, "cy": 0.55, "w": 0.2, "h": 0.08}, (0.3, 0.55))]
    jobs = []
    for name, params, _ in cases:
        item = {"id": name, "template": name, "start": 0, "end": 2.4,
                "params": motion_templates.check_params(name, params)}
        jobs.append(motion_templates.build_job(item, 540, 960, 30))
    reports = motion_engine.probe(jobs, [[1.2]] * len(jobs))
    for (name, params, (x, y)), rep in zip(cases, reports):
        assert not rep["errors"] and rep["bboxes"], (name, rep)
        x0, y0, x1, y1 = rep["bboxes"][0]
        assert x0 - 0.03 <= x <= x1 + 0.03 and y0 - 0.03 <= y <= y1 + 0.03, (name, (x, y), rep["bboxes"][0])
