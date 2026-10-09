"""Browser motion engine: schema, templates, captions, stitching, tools, kit."""

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import motion_captions  # noqa: E402
import motion_engine  # noqa: E402
import motion_layer  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import sound_library  # noqa: E402
import stitch  # noqa: E402
from schemas import (EDLValidationError, default_edl, describe_edl,  # noqa: E402
                     edl_signature, validate_edl)
from timeline import Timeline, remap_program_items  # noqa: E402


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
FFMPEG = shutil.which("ffmpeg")
needs_browser = pytest.mark.skipif(not (CHROMIUM and FFMPEG),
                                   reason="headless Chromium + ffmpeg required")


def _mo(mid="mg1", template="hook_title", start=1.0, end=3.0, **params):
    return {"id": mid, "template": template, "start": start, "end": end,
            "params": params or {"text": "Big *idea* here"}}


# ── schema ──────────────────────────────────────────────────────────────

def test_motion_items_validate_and_keep_old_signatures():
    edl = default_edl(10.0)
    assert edl_signature(validate_edl(edl, 10.0).model_dump()) == edl_signature(
        validate_edl(dict(edl, motion=[]), 10.0).model_dump())
    edl["motion"] = [_mo()]
    out = validate_edl(edl, 10.0).model_dump()
    item = out["motion"][0]
    assert item["params"]["text"] == "Big *idea* here"
    assert item["params"]["treatment"] == "serif"          # default filled
    assert item["layer"] == "above_captions"
    assert "motion x1" in describe_edl(out, 10.0)


def test_unknown_template_and_missing_required_param_are_rejected():
    edl = default_edl(10.0)
    edl["motion"] = [_mo(template="nope")]
    with pytest.raises(EDLValidationError, match="unknown motion template"):
        validate_edl(edl, 10.0)
    edl["motion"] = [{"id": "m", "template": "hook_title", "start": 1, "end": 2, "params": {}}]
    with pytest.raises(EDLValidationError, match="needs 'text'"):
        validate_edl(edl, 10.0)
    edl["motion"] = [{"id": "m", "template": "html", "start": 1, "end": 2}]
    with pytest.raises(EDLValidationError, match="html composition"):
        validate_edl(edl, 10.0)


def test_lenient_normalize_drops_unknown_and_clamps_but_strict_check_explains():
    norm = motion_templates.normalize_params("hook_title", {"text": "x", "y": 9, "bogus": 1})
    assert norm["y"] == 0.92 and "bogus" not in norm
    with pytest.raises(ValueError, match="no parameter"):
        motion_templates.check_params("hook_title", {"text": "x", "bogus": 1})
    with pytest.raises(ValueError, match="must be one of"):
        motion_templates.check_params("hook_title", {"text": "x", "treatment": "sparkle"})


def test_every_template_spec_parses_and_internal_ones_are_hidden():
    names = motion_templates.names()
    assert "hook_title" in names and "caption_motion" in names
    listed = {t["name"] for t in motion_templates.catalog()}
    assert "caption_motion" not in listed and "hook_title" in listed
    for name in names:
        spec = motion_templates.spec(name)
        for key, p in spec["params"].items():
            assert p.get("required") or "default" in p, f"{name}.{key} lacks a default"
        if not spec.get("internal"):
            assert spec.get("example"), f"{name} needs an example for tests/catalog"
            motion_templates.check_params(name, spec["example"])
        for cue in spec.get("sfx") or []:
            # every declared role must map onto an owner-approved recording
            assert sound_library.catalog(cue.get("kind")), (name, cue)


# ── timeline / stitch ───────────────────────────────────────────────────

def test_motion_follows_its_moment_through_an_upstream_cut():
    edl = validate_edl(dict(default_edl(10.0), motion=[_mo(start=6.0, end=7.5)]), 10.0).model_dump()
    old = Timeline([[0.0, 10.0]])
    edl["keep"] = [[0.0, 2.0], [4.0, 10.0]]
    notes = remap_program_items(edl, old, Timeline(edl["keep"]))
    item = edl["motion"][0]
    assert (item["start"], item["end"]) == (4.0, 5.5)
    assert any("moved" in n for n in notes)
    edl2 = validate_edl(dict(default_edl(10.0), motion=[_mo(start=2.5, end=3.5)]), 10.0).model_dump()
    edl2["keep"] = [[0.0, 2.0], [4.0, 10.0]]
    notes = remap_program_items(edl2, Timeline([[0.0, 10.0]]), Timeline(edl2["keep"]))
    assert edl2["motion"] == [] and any("removed" in n for n in notes)


def test_stitch_windows_carry_motion_phase():
    edl = validate_edl(dict(default_edl(10.0), motion=[_mo(start=2.0, end=6.0)]), 10.0).model_dump()
    tl = Timeline(edl["keep"])
    win = stitch.window_edl(edl, tl, 4.0, 8.0)
    item = win["motion"][0]
    assert item["start"] == 0.0 and item["end"] == 2.0
    assert item["phase_s"] == 2.0 and item["full_duration_s"] == 4.0
    validate_edl(win, 10.0)
    prev = dict(edl)
    new = json.loads(json.dumps(edl))
    new["motion"][0]["params"]["accent"] = "#FF0000"
    windows, why = stitch.plan(prev, new, tl, tl, 10.0, 10.0)
    assert windows and why is None


def test_motion_mute_ownership():
    edl = dict(default_edl(10.0), motion=[dict(_mo(), mute_captions=True)])
    assert motion_layer.caption_mute_spans(edl) == [[1.0, 3.0]]


# ── captions ────────────────────────────────────────────────────────────

def _index(words):
    return {"words": [{"w": w, "t0": s, "t1": e} for w, s, e in words]}


def test_motion_caption_cues_follow_cuts_and_mutes():
    words = [("Every", 0.1, 0.4), ("computer", 0.45, 0.9), ("to", 0.95, 1.05), ("date", 1.1, 1.5),
             ("has", 2.4, 2.6), ("used", 2.65, 2.9), ("a", 2.95, 3.0), ("weird", 3.05, 3.5)]
    edl = default_edl(5.0)
    edl["captions"] = {"mode": "from_transcript", "style": {"motion_look": "pop"},
                       "emphasis_words": ["weird"]}
    edl = validate_edl(edl, 5.0).model_dump()
    tl = Timeline(edl["keep"])
    cues = motion_captions.cues(edl, _index(words), tl)
    flat = [w["t"] for c in cues for w in c["w"]]
    assert flat == ["Every", "computer", "to", "date", "has", "used", "a", "weird"]
    assert all(len(c["w"]) <= 3 for c in cues)
    assert any(w["x"] for c in cues for w in c["w"] if w["t"] == "weird")
    # cut the second sentence away: its words disappear and nothing shifts wrongly
    edl["keep"] = [[0.0, 2.0]]
    tl2 = Timeline(edl["keep"])
    cues2 = motion_captions.cues(edl, _index(words), tl2)
    assert [w["t"] for c in cues2 for w in c["w"]] == ["Every", "computer", "to", "date"]
    items = motion_captions.items(validate_edl(dict(edl, keep=[[0.0, 5.0]]), 5.0).model_dump(),
                                  _index(words), tl)
    assert items and items[0]["template"] == "caption_motion"
    assert items[0]["params"]["cues"][0]["s"] == 0.0


def test_motion_look_is_a_caption_style_field():
    edl = default_edl(5.0)
    edl["captions"] = {"mode": "from_transcript", "style": {"motion_look": "glow"}}
    assert motion_captions.look_of(validate_edl(edl, 5.0).model_dump()) == "glow"
    edl["captions"]["style"]["motion_look"] = "sparkle"
    with pytest.raises(EDLValidationError):
        validate_edl(edl, 5.0)


# ── tools ───────────────────────────────────────────────────────────────

class _Ctx:
    project_id = 1

    def __init__(self, duration=10.0):
        self.duration = duration
        self._edl = validate_edl(default_edl(duration), duration).model_dump()
        self.writes = []
        self.index = {"video": {"width": 1080, "height": 1920}}

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": self._edl}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"


def test_tools_add_set_remove_with_owned_sound_cues(monkeypatch):
    monkeypatch.setattr(motion_tools, "ensure_library_asset", lambda ctx, sid: f"sfx/1/lib-{sid}.flac")
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0:
                        {"errors": [], "visible_frames": 4, "samples": 4, "bboxes": [[.1, .3, .9, .5]]})
    ctx = _Ctx()
    silent = motion_tools.add_motion_graphic(ctx, "hook_title", 0.5, params={"text": "Quiet"})
    assert silent.startswith("EDL v1") and not ctx.latest_edl()["json"]["sfx"]   # silent by default
    assert motion_tools.remove_motion_graphic(ctx, "mg1").startswith("EDL v")
    out = motion_tools.add_motion_graphic(ctx, "hook_title", 2.0, params={"text": "Hello *world*"},
                                          sfx=True)
    assert out.startswith("EDL v3"), out
    edl = ctx.latest_edl()["json"]
    assert edl["motion"][0]["id"] == "mg1" and edl["motion"][0]["end"] == 4.6
    cues = sorted((s["id"], s["at"]) for s in edl["sfx"])
    assert cues == [("mg_mg1_sfx1", 2.0), ("mg_mg1_sfx2", 2.18)]
    assert all(s["storage_key"].startswith("sfx/1/lib-") for s in edl["sfx"])
    assert motion_tools.set_motion_graphic(ctx, "mg1", start=5.0, end=7.0).startswith("EDL v")
    edl = ctx.latest_edl()["json"]
    assert sorted(s["at"] for s in edl["sfx"]) == [5.0, 5.18]
    assert motion_tools.remove_motion_graphic(ctx, "mg1").startswith("EDL v")
    edl = ctx.latest_edl()["json"]
    assert edl["motion"] == [] and edl["sfx"] == []


def test_tool_rejects_bad_params_and_invisible_compositions(monkeypatch):
    ctx = _Ctx()
    assert "REJECTED" in motion_tools.add_motion_graphic(ctx, "hook_title", 1.0, params={})
    assert "REJECTED" in motion_tools.add_motion_graphic(ctx, "nope", 1.0)
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0:
                        {"errors": [], "visible_frames": 0, "samples": 4, "bboxes": []})
    out = motion_tools.add_motion_graphic(ctx, "hook_title", 1.0, params={"text": "x"}, sfx=False)
    assert out.startswith("REJECTED") and "nothing visible" in out


def test_sound_library_is_real_approved_recordings_with_licences():
    rows = sound_library.catalog()
    assert len(rows) >= 20
    for r in rows:
        assert r["license"] == "CC0 1.0" and r["source_url"].startswith("https://freesound.org/")
        assert os.path.exists(sound_library.path(r["id"]))
        assert -20 <= r["gain_db"] <= -6
    lic = open(os.path.join(sound_library.LIB_DIR, "LICENSES.md")).read()
    assert all(r["id"] in lic for r in rows)
    assert sound_library.get("whoosh_soft_1") and sound_library.get("../manifest.json") is None
    assert sound_library.pick("whoosh_soft", "a")["role"] == "whoosh"      # template role alias
    assert sound_library.pick("paper") is None                              # nothing approved -> skipped
    hits = motion_tools.sound_search("camera photo")
    assert hits and hits[0]["role"] == "shutter" and hits[0]["id"].startswith("sound:")
    listing = motion_tools.list_sound_library(None)
    assert "sound:impact_1" in listing and "never on" in listing.lower()


# ── engine (real Chromium) ──────────────────────────────────────────────

@needs_browser
def test_every_library_template_renders_its_example():
    jobs, times = [], []
    for name in motion_templates.names():
        spec = motion_templates.spec(name)
        if spec.get("internal"):
            continue
        params = motion_templates.check_params(name, spec["example"])
        dur = float(spec.get("duration") or 3.0)
        item = {"id": name, "template": name, "start": 0, "end": dur, "params": params}
        jobs.append(motion_templates.build_job(item, 1080, 1920, 30))
        times.append([dur * f for f in (0.2, 0.5, 0.85)])
    reports = motion_engine.probe(jobs, times)
    for job, rep in zip(jobs, reports):
        assert not rep["errors"], (job.label, rep["errors"])
        assert rep["visible_frames"] >= 2, (job.label, rep)


@needs_browser
def test_engine_renders_alpha_clip_reuses_static_frames_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    item = {"id": "a", "template": "hook_title", "start": 0, "end": 2.6,
            "params": motion_templates.check_params("hook_title", {"text": "Hello *there*"})}
    job = motion_templates.build_job(item, 540, 960, 30)
    clip = motion_engine.render_jobs([job], str(tmp_path / "out"), pages=1)[0]
    assert not clip.empty and clip.frames == 78
    assert clip.captured < clip.frames            # the hold was re-used
    probe = subprocess.run([FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=pix_fmt,nb_frames,width,height", "-of", "json",
                            clip.path], capture_output=True, text=True)
    if probe.returncode == 0:
        st = json.loads(probe.stdout)["streams"][0]
        assert st["pix_fmt"] in ("argb", "rgba", "bgra") and st["width"] == clip.w
    again = motion_engine.render_jobs([job], str(tmp_path / "out2"), pages=1)[0]
    assert again.cached and (again.x, again.y, again.w, again.h) == (clip.x, clip.y, clip.w, clip.h)


@needs_browser
def test_script_errors_surface_as_motion_render_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    html = motion_engine.build_document("<div id=x>hi</div><script>MG.frame(t => { nope(); });</script>",
                                        duration=1, fps=30)
    job = motion_engine.RenderJob(html=html, out_w=270, out_h=480, fps=30, duration=1.0)
    rep = motion_engine.probe([job], [[0.5]])[0]
    assert rep["errors"]


@needs_browser
def test_caption_track_renders_every_look(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    ws = [{"t": w, "s": 0.1 + i * 0.35, "e": 0.4 + i * 0.35, "x": int(w == "weird")}
          for i, w in enumerate("Every computer has used a weird font".split())]
    cues = [{"s": 0.1, "e": 1.15, "y": 0.72, "w": ws[:3]}, {"s": 1.15, "e": 2.8, "y": 0.72, "w": ws[3:]}]
    jobs = []
    for look in motion_captions.LOOKS:
        item = {"id": look, "template": "caption_motion", "start": 0, "end": 2.8,
                "params": {"look": look, "cues": cues}}
        jobs.append(motion_templates.build_job(item, 540, 960, 30))
    reps = motion_engine.probe(jobs, [[0.5, 1.5, 2.5]] * len(jobs))
    for job, rep in zip(jobs, reps):
        assert not rep["errors"] and rep["visible_frames"] == 3, (job.label, rep)
