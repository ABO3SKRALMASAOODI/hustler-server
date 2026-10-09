"""Media-card and full-frame effect templates: specs, every enum, real assets."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import sfx_kit  # noqa: E402

TEMPLATES = ["image_card", "photo_stack", "flash_transition", "light_leak",
             "glitch_burst", "film_burn", "focus_spotlight"]
FULL_FRAME = {"flash_transition", "light_leak", "glitch_burst", "film_burn"}


def _chromium_ok():
    if not motion_engine.available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(args=motion_engine.CHROME_ARGS).close()
        return True
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not _chromium_ok(), reason="headless Chromium required")


@pytest.mark.parametrize("name", TEMPLATES)
def test_spec_is_complete_and_cues_land_inside_the_item(name):
    spec = motion_templates.spec(name)
    for key in ("title", "category", "description", "duration", "layer", "example"):
        assert spec.get(key) not in (None, ""), (name, key)
    assert spec["mutes_captions"] is False
    for key, p in spec["params"].items():
        assert p.get("required") or "default" in p, (name, key)
    motion_templates.check_params(name, spec["example"])
    assert spec["sfx"], name
    for cue in spec["sfx"]:
        assert cue["kind"] in sfx_kit.KINDS, (name, cue)
        assert 0 <= cue["at"] < spec["duration"], (name, cue)


def test_asset_params_default_to_placeholder_and_resolve_when_given():
    params = motion_templates.check_params("photo_stack", {"asset_2": "uploads/1/b.jpg"})
    assert params["asset_1"] is None and params["asset_2"] == "uploads/1/b.jpg"
    assert motion_templates.asset_params("photo_stack", params) == {"asset_2": "uploads/1/b.jpg"}
    assert motion_templates.asset_params("image_card", motion_templates.check_params("image_card", {})) == {}


def _variants():
    """(name, params) covering every enum value of every template plus the
    awkward extremes (long copy, tiny/huge spotlight, short/long effects)."""
    out = []
    for name in TEMPLATES:
        spec = motion_templates.spec(name)
        base = dict(spec["example"])
        for key, p in spec["params"].items():
            if p.get("type") == "enum":
                for v in p["values"]:
                    out.append((name, dict(base, **{key: v}), None))
    long_caption = "A deliberately long caption that never overflows its card"
    out += [
        ("image_card", {"caption": long_caption, "chip": "SOURCE: THE ARCHIVE", "style": "polaroid"}, None),
        ("image_card", {"caption": long_caption, "size": 0.4, "y": 0.85, "exit": "drop"}, None),
        ("photo_stack", {"caption": "Five products in five years", "y": 0.8, "size": 0.75}, None),
        ("focus_spotlight", {"x": 0.02, "y": 0.97, "w": 0.05, "h": 0.03, "label": "Corner"}, None),
        ("focus_spotlight", {"x": 0.5, "y": 0.5, "w": 1.0, "h": 1.0, "feather": 0, "ring": False}, None),
        ("flash_transition", {"pattern": "double", "strength": 0.3}, 0.2),
        ("flash_transition", {"tone": "color", "color": "#FF2D55"}, 0.6),
        ("glitch_burst", {"intensity": 0.2, "seed": 999}, 0.15),
        ("glitch_burst", {"intensity": 1.0, "seed": 1}, 0.6),
        ("film_burn", {"coverage": 0.35, "seed": 999}, 0.5),
        ("film_burn", {"coverage": 1.0}, 1.2),
        ("light_leak", {"intensity": 0.3}, 0.6),
        ("light_leak", {"intensity": 1.0, "mode": "edge", "from": "bottom"}, 1.6),
    ]
    return out


@needs_browser
@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_every_variant_renders_visibly_without_script_errors(size):
    jobs, times, labels = [], [], []
    for name, params, dur in _variants():
        spec = motion_templates.spec(name)
        dur = dur or float(spec["duration"])
        item = {"id": name, "template": name, "start": 0, "end": dur,
                "params": motion_templates.check_params(name, params)}
        jobs.append(motion_templates.build_job(item, size[0] // 4, size[1] // 4, 30))
        times.append([dur * f for f in (0.2, 0.5, 0.85)])
        labels.append((name, params, dur))
    reports = motion_engine.probe(jobs, times, budget_s=300)
    for label, rep in zip(labels, reports):
        assert not rep["errors"], (label, rep["errors"])
        assert rep["visible_frames"] >= 2, (label, rep)


@needs_browser
def test_real_images_fill_the_cards(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    paths = []
    for i, (size, color) in enumerate([((640, 480), (220, 40, 40)), ((480, 720), (40, 200, 60)),
                                       ((800, 450), (40, 80, 230))]):
        p = tmp_path / f"img{i}.png"
        Image.new("RGB", size, color).save(p)
        paths.append(str(p))
    card = {"id": "c", "template": "image_card", "start": 0, "end": 3.2,
            "params": motion_templates.check_params("image_card", {"image": "k/0", "caption": "Hi"})}
    stack = {"id": "s", "template": "photo_stack", "start": 0, "end": 3.0,
             "params": motion_templates.check_params(
                 "photo_stack", {"asset_1": "k/0", "asset_2": "k/1", "asset_3": "k/2"})}
    locs = {"k/0": paths[0], "k/1": paths[1], "k/2": paths[2]}
    jobs = [motion_templates.build_job(it, 270, 480, 30, locs) for it in (card, stack)]
    assert all(j.assets for j in jobs)
    clips = motion_engine.render_jobs(jobs, str(tmp_path / "out"), pages=2)
    for clip in clips:
        assert not clip.empty and clip.captured < clip.frames   # holds are re-used
    # the card's photo colour shows up in the held frame
    import subprocess
    png = tmp_path / "f.png"
    subprocess.run([motion_engine._ffmpeg(), "-v", "error", "-y", "-ss", "1.5", "-i", clips[0].path,
                    "-frames:v", "1", str(png)], check=True)
    im = Image.open(png).convert("RGBA")
    px = im.getpixel((im.width // 2, im.height // 2))
    assert px[3] > 200 and px[0] > 150 and px[1] < 120, px
