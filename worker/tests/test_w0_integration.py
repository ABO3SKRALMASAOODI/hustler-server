"""Wave 0 integration seams: where two tracks' code meets.

* clip_store (0A) behind motion_engine's cache (0B): the clip key names its
  asset files by CONTENT, so the shared store hits across containers and two
  different files never share a key.
* the device resolver (0C) on the render path (0B's fxgraph.prepare): rungs
  from sidecar status (0A), lane features and one render budget spent in
  program order; the resolver's substitute reaches the compiler; a stitched
  preview keeps the rows of the frames it reused.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import devices                                               # noqa: E402
import fxgraph                                               # noqa: E402
import motion_engine                                         # noqa: E402
from timeline import Timeline                                # noqa: E402
from w0c_support import six_matte_cards, use_cards           # noqa: E402


def _job(asset):
    return motion_engine.RenderJob(
        html="<div>card</div>", out_w=1080, out_h=1920, fps=30.0,
        duration=1.0, assets={"asset_image.png": str(asset)})


def test_the_clip_key_names_asset_content_not_its_file_stamp(tmp_path):
    a = tmp_path / "a" / "img.png"
    b = tmp_path / "b" / "img.png"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_bytes(b"\x89PNG-project-A-picture")
    b.write_bytes(b"\x89PNG-project-B-picture")
    assert a.stat().st_size == b.stat().st_size
    os.utime(a, (1_700_000_000, 1_700_000_000))
    os.utime(b, (1_700_000_000, 1_700_000_000))
    # same size, same second: different pictures never share a clip
    assert _job(a).key() != _job(b).key()
    # the same picture downloaded later by another container: one clip
    c = tmp_path / "cold" / "elsewhere.png"
    c.parent.mkdir()
    c.write_bytes(a.read_bytes())
    os.utime(c, (1_800_000_000, 1_800_000_000))
    assert _job(c).key() == _job(a).key()
    # an edited file at the same path re-keys (the memo is per stamp)
    a.write_bytes(b"\x89PNG-project-A-edited!")
    os.utime(a, (1_700_000_100, 1_700_000_100))
    assert _job(a).key() != _job(c).key()
    # a missing file still keys (and renders without the picture)
    assert _job(tmp_path / "gone.png").key()


# ── the device resolver on the render path ──────────────────────────────

class _Comp:
    """A stand-in compiler that records what each item resolved to."""
    FILTERS = ("null",)

    def __init__(self):
        self.seen = []

    def stage(self, ctx, items):
        for it in items:
            self.seen.append((it["id"], ctx.rung(it),
                              (ctx.device(it) or {}).get("substitute")))
        ctx.parts.append(f"[{ctx.label}]null[fxo]")
        return "fxo"


@pytest.fixture
def comp(monkeypatch, tmp_path):
    c = _Comp()
    monkeypatch.setattr(fxgraph, "compiler_for", lambda t: c)
    use_cards(monkeypatch, six_matte_cards(), tmp_path / "cards")
    return c


def _dev(fid, device, start, end, stage="world"):
    return {"id": fid, "type": "subject", "stage": stage, "device": device,
            "start": start, "end": end}


def _prepare(edl, *, preview=True, source=None):
    tl = Timeline(edl["keep"], [], [])
    return fxgraph.prepare(edl, tl=tl, W=1080, H=1920, fps=30.0,
                           preview=preview, workdir="/tmp", source=source)


def test_a_device_without_its_sidecar_draws_its_substitute(comp):
    edl = {"keep": [[0.0, 20.0]], "fx": [
        _dev("a", "subject_fill_flash", 1.0, 1.1),
        _dev("b", "subject_glow", 3.0, 4.0)]}
    plan = _prepare(edl)                      # no source: matte absent
    rows = {r["id"]: r for r in plan.report()}
    assert rows["a"]["rung"] == "substitute"
    assert "matte.person absent" in rows["a"]["reason"]
    assert plan.devices["a"]["substitute"] == "full_frame_flash"
    assert plan.devices["b"]["substitute"] == "halation"
    assert rows["a"]["family"] == "matte_fx"
    # the compiler sees the rung and the substitute it must draw
    fxgraph.apply_stage("world", [], "v0", plan)
    assert comp.seen == [("a", "substitute", "full_frame_flash"),
                         ("b", "substitute", "halation")]
    # and the DEGRADED gate lists both
    import quality_verifier
    found = quality_verifier.device_findings({"devices": plan.report()})
    assert {f["evidence"]["item_id"] for f in found} == {"a", "b"}


def test_ready_sidecars_draw_full_within_one_render_budget(comp,
                                                           monkeypatch):
    calls = []

    def status(item, source, plan):
        calls.append(item["id"])
        return {"matte.person": "ready"}
    monkeypatch.setattr(fxgraph, "_sidecar_status", status)
    edl = {"keep": [[0.0, 20.0]], "fx": [
        _dev(f"p{i}", "poster_silhouette", i * 4.0, i * 4.0 + 3.0)
        for i in range(4)]}
    one = devices.estimate_ms(six_matte_cards()["poster_silhouette"],
                              fxgraph.lane(), 90)
    monkeypatch.setattr(fxgraph, "_device_budget",
                        lambda plan, edl: one * 2.5)
    plan = _prepare(edl)
    rungs = [r["rung"] for r in plan.report()]
    # the budget is spent in program order: two full, a cheaper rung with
    # what is left, then nothing left at all
    assert rungs == ["full", "full", "reduced", "omit"]
    assert "over the 0 ms budget" in plan.report()[3]["reason"]
    assert calls == ["p0", "p1", "p2", "p3"]
    assert plan.report()[0]["est_ms"] == pytest.approx(one, rel=0.01)


def test_the_render_budget_is_the_cost_models(comp):
    edl = {"keep": [[0.0, 30.0]],
           "fx": [_dev("a", "subject_glow", 1.0, 2.0)]}
    plan = _prepare(edl)
    base = devices.baseline_ms(30.0, 1080, 1920, preview=True,
                               lane=fxgraph.lane())
    spent = plan.report()[0]["est_ms"] if plan.report()[0]["rung"] in (
        "full", "reduced") else 0.0
    assert plan.budget_ms is not None
    # previews may add at most +30% over the no-device baseline
    assert plan.budget_ms + spent <= base * 0.30 + 1.0
    # an EDL without devices never touches the cost model
    assert _prepare({"keep": [[0.0, 30.0]], "fx": [
        {"id": "x", "type": "subject", "stage": "world", "start": 1.0,
         "end": 2.0}]}).budget_ms is None


def test_a_stitched_preview_keeps_the_rows_of_the_frames_it_reused(comp):
    a = _dev("a", "subject_glow", 1.0, 2.0)
    b = _dev("b", "subject_glow", 5.0, 6.0)
    prev_rows = [{"id": "a", "device": "subject_glow", "family": "matte_fx",
                  "stage": "world", "rung": "full", "reason": "ok",
                  "est_ms": 9.0},
                 {"id": "b", "device": "subject_glow", "family": "matte_fx",
                  "stage": "world", "rung": "full", "reason": "ok",
                  "est_ms": 9.0}]
    b2 = dict(b, end=6.5)                     # b changed since
    rows = fxgraph.job_report({"fx": [a, b2]}, [], prev_rows,
                              {"fx": [a, b]})
    assert rows[0] == prev_rows[0]            # the copied frames: drawn full
    assert rows[1]["id"] == "b" and rows[1]["rung"] != "full"   # re-decided
    # without a previous preview: the static decision
    assert fxgraph.job_report({"fx": [a]}, [])[0]["rung"] != "full"


# ── keep changes: re-anchor, then the device re-solvers (0B + 0C) ───────

def test_a_keep_change_runs_the_device_re_solvers_after_re_anchoring():
    import agent_tools
    import hooks
    seen = []

    def resolver(old_edl, new_edl, old_tl, new_tl):
        seen.append((old_edl["fx"][0]["start"], new_edl["fx"][0]["start"]))
        new_edl["fx"][0]["purpose"] = "re-solved"
        return new_edl, ["1 device re-solved"]
    edl = {"keep": [[0.0, 4.0], [6.0, 12.0]], "fx": [
        {"id": "w", "type": "subject", "stage": "world", "start": 7.0,
         "end": 8.0}]}
    old_tl = Timeline([[0.0, 10.0], [12.0, 14.0]], [], [])
    new_tl = Timeline(edl["keep"], [], [])
    hooks.register("keep_change", resolver)
    try:
        notes = agent_tools._remap_program_items(edl, old_tl, new_tl)
    finally:
        hooks.unregister("keep_change", resolver)
    assert "1 device re-solved" in notes
    assert edl["fx"][0]["purpose"] == "re-solved"
    # the re-solver saw the item before and after the shared re-anchoring
    assert len(seen) == 1 and seen[0][0] == 7.0
    # no fx and no re-solver: exactly the shared pass
    plain = {"keep": [[0.0, 4.0]], "zooms": []}
    assert agent_tools._remap_program_items(
        plain, old_tl, Timeline(plain["keep"], [], [])) == \
        agent_tools.timeline_mod.remap_program_items(
            dict(plain), old_tl, Timeline(plain["keep"], [], []))


# ── the code fingerprint covers Wave 0's runtime data ───────────────────

def test_wave0_runtime_data_moves_the_code_fingerprint():
    import version
    rel = {os.path.relpath(p, os.path.dirname(HERE)).replace(os.sep, "/")
           for p in version._source_files()}
    # a model pin, the cost model or a page library changed alone still
    # reads as different code on a lane (deploy verification, source layer)
    for required in ("models.lock", "models/yunet.json",
                     "models/rvm_mobilenetv3.json", "cost_model.json",
                     "motion/lib/beats.js"):
        assert required in rel
    # the baker is a build tool, the README is prose
    assert "models/bake.py" not in rel
    assert "motion/lib/README.md" not in rel


# ── owner rule 2: a web sound fetched before the flag went off ──────────

def test_a_web_sound_fetched_earlier_is_not_placed_while_web_sfx_is_off(
        monkeypatch):
    import agent_tools
    import config
    from w0c_support import tool_context, upload_asset
    monkeypatch.setattr(config, "WEB_SFX_ENABLED", False)
    ctx, fake = tool_context({"keep": [[0.0, 10.0]]})
    upload_asset(fake, "music/7/whoosh.mp3",
                 caption="found online by search_sfx", source="freesound")
    upload_asset(fake, "music/7/mine.wav", filename="mine.wav")
    out = agent_tools.add_sfx(ctx, "music/7/whoosh.mp3", 2.0)
    assert out.startswith("REJECTED") and "web sounds are off" in out
    # the user's own upload still places
    assert not agent_tools.add_sfx(ctx, "music/7/mine.wav", 2.0).startswith(
        "REJECTED")


# ── AT-0C-3 over the merged sites: every fault x fx at every stage ──────
#
# 0C's matrix (test_w0c_faults.py) renders six product specs; an fx item
# there can only be a future type, stripped on the render path. Here the
# engine-only probe type drives every FX stage (0B) with a device item that
# reads its sidecar through the real store (0A) and resolves against real
# cards (0C), under each pinned fault, through renderer.render_edl.

_FX_SHA = "ab" * 32
_FX_IDX = {"words": [], "silences": [], "sentences": [], "shots": [],
           "tile_keys": [f"tiles/7/{_FX_SHA}/t0.jpg"],
           "video": {"duration": 12.0, "fps": 25.0, "width": 640,
                     "height": 360}}
_FX_MUSIC = "media/w0i/music.m4a"


def _fx_edl():
    p = {"type": "fx_probe"}
    return {
        "keep": [[0.0, 4.0], [5.0, 9.0]], "frame": {"ratio": "9:16"},
        "captions": {"enabled": False},
        "music": [{"id": "m1", "storage_key": _FX_MUSIC, "start": 0.0,
                   "end": 8.0, "gain_db": -18}],
        "motion": [{"id": "mg1", "template": "hook_title", "start": 0.5,
                    "end": 2.5, "params": {"text": "Big *idea*"}}],
        "fx": [
            dict(p, id="w", stage="world", op="negate", start=2.0, end=2.04),
            dict(p, id="s", stage="screen", op="negate_window", start=2.4,
                 end=2.44),
            dict(p, id="pc", stage="pre_captions", op="region_negate",
                 rect=[0.25, 0.25, 0.75, 0.75], start=3.0, end=3.04),
            dict(p, id="ot", stage="over_type", op="blend_screen",
                 rect=[0.1, 0.1, 0.5, 0.5], start=5.0, end=5.08),
            dict(p, id="j", stage="junction", op="dissolve", at=4.0,
                 frames=4),
            dict(p, id="b", stage="music_bus", op="gain", music_id="m1",
                 gain_db=-6),
            dict(p, id="mv", stage="world", op="matte_view", start=6.0,
                 end=7.0),
            dict(p, id="dev", stage="world", op="noop", start=6.5, end=7.0,
                 device="subject_glow"),
        ]}


@pytest.fixture(scope="module")
def fault_matrix(tmp_path_factory):
    import shutil
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("needs ffmpeg")
    import faultinject
    import w0b_probe
    import w0i_probe
    from w0c_support import write_cards
    d = tmp_path_factory.mktemp("w0i_faults")
    cards = d / "cards"
    write_cards(str(cards), six_matte_cards())
    src = w0b_probe.make_source(str(d / "src25.mp4"), rate="25")
    mus = w0b_probe.make_tone(str(d / "music.m4a"))
    base = {k: v for k, v in _fx_edl().items() if k != "fx"}
    cases = [{"name": "base", "src": src, "idx": _FX_IDX, "edl": base,
              "asset_locals": {_FX_MUSIC: mus}}]
    for fault in ("none",) + tuple(faultinject.FAULTS):
        cases.append({"name": fault, "src": src, "idx": _FX_IDX,
                      "edl": _fx_edl(), "asset_locals": {_FX_MUSIC: mus},
                      "faults": [] if fault == "none" else [fault]})
    cases.append({"name": "final", "src": src, "idx": _FX_IDX,
                  "edl": _fx_edl(), "asset_locals": {_FX_MUSIC: mus},
                  "preview": False})
    return w0i_probe.run(cases, str(d / "run"), str(cards),
                         str(d / "sidecars"))


def _fault_names():
    import faultinject
    return ("none",) + tuple(faultinject.FAULTS) + ("final",)


@pytest.mark.parametrize("case", _fault_names())
def test_every_fault_over_every_fx_stage_renders(fault_matrix, case):
    base, r = fault_matrix["base"], fault_matrix[case]
    assert base["error"] is None, base["error"]
    assert r["error"] is None, (case, r["error"])
    # the program is exactly as long as the edit, whatever degraded (a
    # final adds the 5 s Valmera end card)
    want = 8.0 + (5.0 if case == "final" else 0.0)
    assert r["duration"] == pytest.approx(want, abs=0.05)
    assert len(r["frames"]) == round(want * 25) and len(base["frames"]) == 200
    rows = {x["id"]: x for x in r["report"]}
    assert set(rows) == {"w", "s", "pc", "ot", "j", "b", "mv", "dev"}
    assert all(x["rung"] in devices.RUNGS for x in rows.values())
    # the person matte is a stub until 1A: never drawn, always said
    assert rows["mv"]["rung"] == "omit"
    # the device read its sidecar through the store: absent, so its
    # substitute — named, never silent (the DEGRADED gate's input)
    assert rows["dev"]["rung"] in ("substitute", "omit")
    assert rows["dev"]["device"] == "subject_glow"
    if case in ("none", "final", "sidecar_timeout", "model_missing",
                "executor_skew", "chromium_crash"):
        assert "matte.person absent" in rows["dev"]["reason"]
        assert all(rows[i]["rung"] == "full"
                   for i in ("w", "s", "pc", "ot", "j", "b")), rows
    if case == "ffmpeg_filter_missing":
        # every filter "missing": every probe item omitted, render intact
        assert all(rows[i]["rung"] == "omit"
                   for i in ("w", "s", "pc", "ot", "j", "b"))
        assert r["frames"] == base["frames"]
    if case == "oom":
        # the precompute was OOM-killed: only that item degrades
        assert rows["pc"]["rung"] == "omit"
        assert rows["w"]["rung"] == "full"


# ── fx composites join the program's pixel-format group (0B x motion) ──
#
# Measured on the v5 Thiel showcase: a no-op window at pre_captions changed
# EVERY frame of the final (overlay's default yuv420 re-formatted a program
# the motion graphics run in an alpha format), and an alpha composite on a
# plain program moved the watermark robot by a pixel. These graphs rebuild
# both situations with real ffmpeg negotiation and no Chromium.

def _framemd5(graph, out_label, extra_inputs=()):
    import subprocess
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
           "testsrc2=s=270x480:r=25:d=2", "-f", "lavfi", "-i",
           "color=c=0xff3366@0.6:s=96x64:r=25:d=2,format=rgba",
           "-f", "lavfi", "-i",
           "color=c=0xffffff:s=20x28:r=25:d=2,format=rgba"]
    for a in extra_inputs:
        cmd += a
    cmd += ["-filter_complex", ";".join(graph), "-map", f"[{out_label}]",
            "-pix_fmt", "yuv420p", "-f", "framemd5", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [ln.split(",")[-1].strip() for ln in out.stdout.splitlines()
            if ln and not ln.startswith("#")]


def _graph(alpha, fx):
    """testsrc -> [a motion-graphic-like alpha overlay] -> fx -> the
    robot-like corner overlay at odd offsets (as the watermark sits)."""
    import fxprims
    parts = ["[0:v]format=yuv420p,setpts=PTS-STARTPTS[v0]"]
    label = "v0"
    if alpha:
        parts.append("[v0][1:v]overlay=x=31:y=97:eof_action=pass:"
                     "format=auto[va]")
        label = "va"
    if fx == "noop":
        label = fxprims.window(parts, label, "fw", [(10, 30)], "null",
                               uid="fxw", timeline=False, fps=25)
    elif fx == "region":
        parts.append("color=c=black:s=270x480:r=25:d=2,format=gray,"
                     "drawbox=x=0:y=0:w=120:h=90:color=white:t=fill[msk]")
        label = fxprims.region_fx(parts, label, "fr", "msk", "negate",
                                  [(10, 30)], uid="fxr", fps=25,
                                  bbox=(0, 0, 120, 90))
    parts.append(f"[{label}][2:v]overlay=27:29:format=auto:shortest=1[vo]")
    return parts


@pytest.mark.parametrize("alpha", [False, True])
def test_a_composite_never_reformats_the_program(alpha):
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    import fxprims
    base = _framemd5(_graph(alpha, None), "vo")
    assert len(base) == 50
    # the composite joins the group it lands in
    parts = _graph(alpha, "noop")
    assert any(("format=auto" in p) == alpha for p in parts
               if "[fxwa][fxww]" in p)
    assert fxprims.alpha_chain(_graph(alpha, None)[:2], "va" if alpha
                               else "v0") is alpha
    # a no-op window: every frame identical, the corner mark included
    assert _framemd5(parts, "vo") == base
    # a region effect: only its window's frames change
    got = _framemd5(_graph(alpha, "region"), "vo")
    assert [i for i, (a, b) in enumerate(zip(base, got)) if a != b] == \
        list(range(10, 30))
