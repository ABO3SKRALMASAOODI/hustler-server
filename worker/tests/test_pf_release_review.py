"""Release review of the product-fix integration (Oct 2026).

1. What one lane running the previous release for a few minutes of a rolling
deploy does to work the other lane writes.

The repaint v2 erase (P2 track) first folded its algorithm version into the
patch fingerprint. Every renderer already deployed checks a patch's stored
fp against the round-92 formula and drops a mismatch as 'a repaint of a
replaced video', so a preview or final rendered by a lane one release
behind showed the erased thing again — and a write by such a lane (which
strips the unknown 'repaint' field) made the drop permanent. The fp is the
round-92 identity again; the algorithm keys the stored clips instead, and a
clean executor that is still on the round-92 repaint has its clip recorded
as what it is.

2. The render's own check runs before the render job completes (a preview
waits for it): its new type pass stays inside the check's budget.

Run:  python -m pytest tests/test_pf_release_review.py -q     (from worker/)
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools  # noqa: E402
import inpaint  # noqa: E402
import render_qc  # noqa: E402
import schemas  # noqa: E402
from schemas import patch_clip_key, patch_fingerprint  # noqa: E402

REGS = [{"id": "er1", "x": 0.375, "y": 0.25, "w": 0.1875, "h": 0.4167,
         "start": 0.5, "end": 2.5, "fill": "box"}]


def _round92_fp(src_sha, regions, window):
    """The fingerprint every renderer deployed before this release checks
    (worker/schemas.py at 073ed5f), written out so this test does not move
    with the code under test."""
    payload = json.dumps(
        {"w": [round(float(window[0]), 2), round(float(window[1]), 2)],
         "r": [{k: r.get(k) for k in ("x", "y", "w", "h", "start", "end", "fill")}
               for r in (regions or [])]}, sort_keys=True)
    return hashlib.sha1(f"{src_sha}|patch|{payload}".encode()).hexdigest()


def test_the_fingerprint_is_the_one_every_deployed_renderer_checks():
    assert patch_fingerprint("sha", REGS, (0.0, 3.25)) == _round92_fp("sha", REGS, (0.0, 3.25))


def test_the_repaint_keys_the_clips_and_round92_keys_are_unchanged():
    fp = patch_fingerprint("sha", REGS, (0.0, 3.0))
    # round 92 (and a renderer one release behind, deriving the twin from fp)
    assert patch_clip_key(7, fp) == f"patches/7/{fp[:16]}.mp4"
    assert patch_clip_key(7, fp, None, full=True) == f"patches/7/{fp[:16]}_full.mp4"
    # a v2 clip is never served as a round-92 one, nor its twin
    v2 = patch_clip_key(7, fp, 2)
    v2_full = patch_clip_key(7, fp, 2, full=True)
    assert len({v2, v2_full, patch_clip_key(7, fp), patch_clip_key(7, fp, full=True)}) == 4


def test_a_v2_patch_validates_on_this_release_and_its_field_is_optional():
    p = {"id": "pa1", "asset_key": "patches/1/x_r2.mp4", "fp": "abc", "src_start": 0.0,
         "src_end": 3.0, "regions": REGS, "repaint": 2}
    e = schemas.validate_edl({"keep": [[0, 10]], "patches": [p]}, 10.0)
    assert e.patches[0].repaint == 2
    e = schemas.validate_edl({"keep": [[0, 10]], "patches": [dict(p, repaint=None)]}, 10.0)
    assert e.patches[0].repaint is None


# ── the erase tool, its clip built on the executor ─────────────────────────

class _Ctx:
    def __init__(self):
        self.workdir = "/nonexistent"
        self.duration = 8.0
        self.has_main_video = True
        self.project_id = 1
        self.db = self
        self.index = {"video": {"duration": 8.0, "width": 320, "height": 240, "fps": 12.0}}
        self.job = {"user_id": 1}
        self._edl = {"version": 3, "json": schemas.default_edl(8.0)}
        self.written = None
        self.what = None

    def latest_edl(self):
        return self._edl

    def write_edl(self, edl, desc):
        self.written = schemas.validate_edl(edl, 8.0).model_dump()
        self.what = desc
        self._edl = {"version": self._edl["version"] + 1, "json": self.written}
        return f"EDL v3 -> v4: {desc}"

    def run(self, fn, *a, **k):
        name = getattr(fn, "__name__", "")
        if name == "latest_asset":
            if a[1] == "original":
                return {"storage_key": "orig/x.mp4", "sha256": "shatest"}
            return {"storage_key": "prox/x.mp4"}
        return None


def _erase(monkeypatch, remote_stats):
    copied, deleted, sent = [], [], []
    monkeypatch.setattr(agent_tools.storage, "exists", lambda k: False)
    monkeypatch.setattr(agent_tools.storage, "copy_object",
                        lambda a, b: copied.append((a, b)))
    monkeypatch.setattr(agent_tools.storage, "delete_keys",
                        lambda keys: deleted.extend(keys) or len(keys))
    monkeypatch.setattr(agent_tools.remote, "clean_available", lambda: True)

    def run_clean_remote(project_id, payload, user_id=None):
        sent.append(payload)
        return dict(remote_stats)
    monkeypatch.setattr(agent_tools.remote, "run_clean_remote", run_clean_remote)
    ctx = _Ctx()
    res = agent_tools._apply_patches(ctx, [dict(r) for r in REGS], "erased the call's self-view")
    return ctx, res, sent, copied, deleted


def test_a_v2_executor_builds_a_v2_patch_under_the_round92_fingerprint(monkeypatch):
    ctx, res, sent, copied, deleted = _erase(monkeypatch, {
        "src_start": 0.0, "src_end": 3.25, "before": [100.0], "after": [4.0],
        "metric": ["pattern"]})
    assert res.startswith("EDL v"), res
    pt = ctx.written["patches"][0]
    # a renderer one release behind accepts it (and builds its own twin at
    # the round-92 key from this fp); this release builds the v2 twin
    assert pt["fp"] == _round92_fp("shatest", REGS, (pt["src_start"], pt["src_end"]))
    assert pt["repaint"] == inpaint.REPAINT_VERSION
    assert pt["asset_key"] == patch_clip_key(1, pt["fp"], inpaint.REPAINT_VERSION)
    assert sent[0]["repaint"] == inpaint.REPAINT_VERSION
    assert sent[0]["out_key"] == pt["asset_key"]
    assert not copied and not deleted
    # the version's label is the edit, not the last measurement line
    assert ctx.what == "erased the call's self-view"
    assert "keeps 4% of the box's original picture — gone" in res


def test_a_clip_built_by_a_round92_executor_is_recorded_as_round92(monkeypatch):
    # the old executor ignores 'repaint' and answers with stroke ink only
    ctx, res, sent, copied, deleted = _erase(monkeypatch, {
        "src_start": 0.0, "src_end": 3.25, "before": [12.0], "after": [0.4]})
    assert res.startswith("EDL v"), res
    pt = ctx.written["patches"][0]
    assert pt["repaint"] is None
    assert pt["fp"] == _round92_fp("shatest", REGS, (pt["src_start"], pt["src_end"]))
    # moved to the round-92 key: never served or cached as a v2 clip
    v2_key = patch_clip_key(1, pt["fp"], inpaint.REPAINT_VERSION)
    assert pt["asset_key"] == patch_clip_key(1, pt["fp"], None)
    assert copied == [(v2_key, pt["asset_key"])] and deleted == [v2_key]
    assert "ink 12 -> 0.4 — gone" in res
    assert ctx.what == "erased the call's self-view"


# ── the render check's budget ──────────────────────────────────────────────

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_the_type_pass_stays_inside_the_checks_budget(tmp_path, monkeypatch):
    out = str(tmp_path / "out.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=270x480:rate=30:duration=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", out], check=True)
    seen = []

    def type_pass(tplan, deadline):
        seen.append(deadline - time.monotonic())
        return None
    monkeypatch.setattr(render_qc, "type_pass", type_pass)
    plan = {"program_s": 1.0, "fps": 30.0, "W": 270, "H": 480, "end_frame": 30,
            "cut_frames": [], "events": [], "cards": [], "watermark": None,
            "endcard": None, "type": {"items": [{"id": "h"}], "captions": []}}
    res = render_qc.check(out, plan, budget_s=4)
    assert seen and seen[0] <= 4.0 + 1e-3
    assert any(s.startswith("type:") for s in res["skipped"])
    # a spent budget is named as such, not as a missing browser
    seen.clear()
    res = render_qc.check(out, plan, budget_s=0.01)
    assert any("budget" in s for s in res["skipped"] if s.startswith("type:")), res["skipped"]


# ── the project state never fails over the source-layout report ───────────

def test_the_layout_report_never_costs_the_project_state():
    import source_layout

    class Ctx:
        has_main_video = True
        index = {"video": {"width": 1920, "height": 1080, "duration": 60.0},
                 "source_layout": {"v": source_layout.LAYOUT_VERSION, "w": 1920, "h": 1080,
                                   "windows": [{"id": "w1", "rect": [0.38, 0.04, 0.62, 0.96]}],
                                   "spans": []}}

        def latest_edl(self):
            raise RuntimeError("database unavailable")
    assert agent_tools._layout_report(Ctx(), calls_only=True) == ""
