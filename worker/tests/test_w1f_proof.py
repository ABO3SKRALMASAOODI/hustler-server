"""Track 1F: proof_device on real renders (motion_qa.proof).

A child process with ``VALMERA_FX_PROBE=1`` (tests/w1f_proof_probe.py)
proves the engine-only ``fx_probe`` devices — a frame-exact negate at two
stages and a dissolve junction across a cut — through the changed-section
preview path (renderer._render_changed_sections), rendered with and without
them, on synthetic 25 / 29.97 / 24 / 59.94 fps sources. No footage: CI-safe.
"""

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import motion_qa   # noqa: E402
from localfix import fixture, local_only   # noqa: E402


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    wd = str(tmp_path_factory.mktemp("w1f_proof"))
    env = dict(os.environ, VALMERA_FX_PROBE="1", SKIP_DB_INIT="1",
               DATABASE_URL="postgresql://stub/stub")
    env.pop("VALMERA_FX_PREVIEW", None)
    r = subprocess.run([sys.executable,
                        os.path.join(HERE, "w1f_proof_probe.py"), wd],
                       cwd=WORKER, env=env, capture_output=True, text=True,
                       timeout=900)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for k, v in out.items():
        assert "error" not in v, (k, v)
    return out


@pytest.mark.parametrize("rate", ["25", "29.97", "24", "59.94"])
def test_proof_measures_each_landing_frame_exactly(report, rate):
    r = report[rate]
    assert r["pass"] is True, r
    for did in ("n1", "n2"):
        d = r["devices"][did]
        assert d["basis"] == "without_device"
        assert d["event_f"] == d["planned_f"] == d["landing_f"], (rate, d)
        assert d["metrics"]["land_err_f"]["value"] == 0.0
        assert d["metrics"]["flash_risk"]["pass"] is True
        assert d["strip"] and d["onion"]
    j = r["devices"]["j1"]
    assert abs(j["event_f"] - j["planned_f"]) <= 1, j
    integrity = j["metrics"]["duration_exact"]
    assert integrity["value"] == 0.0 and integrity["pass"] is True
    assert "collisions" not in j["metrics"]


def test_proof_renders_only_the_windows_around_the_devices(report):
    r = report["25"]
    covered = sum(b - a for a, b in r["window"])
    assert covered < 2.5          # of a 3.3 s program, three short windows
    assert r["reel_fps"] == pytest.approx(25.0)
    hi = report["59.94"]
    assert hi["reel_fps"] == pytest.approx(59.94, abs=0.01)   # full rate


def test_without_a_reference_the_strip_reads_its_own_change(report):
    t = report["temporal"]
    assert t["basis"] == "temporal"
    assert t["event_f"] == t["planned_f"] and t["land"] == 0.0
    assert any("strip's own" in n for n in t["notes"])


def test_a_long_junction_lands_on_its_cut(report):
    """A 0.6 s junction (9 frames each side) is read over its whole run:
    reading only the -3..+6 strip would put its centre two frames late."""
    j = report["long_junction"]
    assert "error" not in j, j
    assert j["event_f"] == j["planned_f"] == j["landing_f"], j
    assert j["metrics"]["land_err_f"]["pass"] is True
    integrity = j["metrics"]["duration_exact"]
    assert integrity["pass"] is True and \
        integrity["detail"]["audio_identical"] is True, integrity
    assert j["pass"] is True, j


def test_a_frameless_reference_on_another_clock_is_not_misread(report):
    """A frameless program whose only fx item is the junction: without it
    the reference takes the cheap graph (another clock), so its audio
    cannot match — the proof compares frames only and says why, instead of
    failing a junction that preserved the duration."""
    j = report["long_junction_frameless"]
    assert "error" not in j, j
    assert any("another clock" in n for n in j["notes"]), j["notes"]
    integrity = j["metrics"]["duration_exact"]
    assert integrity["value"] == 0.0 and integrity["pass"] is True
    assert integrity["detail"]["audio_identical"] is None
    assert "another clock" in integrity["detail"]["why"]
    assert abs(j["event_f"] - j["planned_f"]) <= 1, j


def test_production_path_proves_on_preview_check_jobs(report):
    """proof_jobs: one preview_check job for the current version and one for
    the earlier version that lacks the devices (the reference), read from
    the concatenated reels by their proof_segments."""
    j = report["jobs"]
    assert j["pass"] is True, j
    assert set(j["jobs"]) == {"with", "without"}
    for did, want in (("n1", 25), ("n2", 65)):
        d = j["devices"][did]
        assert d["basis"] == "without_device"
        assert d["event_f"] == d["landing_f"] == want, (did, d)


def test_production_reel_windows_never_drift(report):
    """Three windows in one concatenated preview_check reel at 29.97: each
    window is placed by the reel's own frame times, so the second and third
    land exactly (counting frames from the durations drifted a frame per
    window and failed correct devices)."""
    j = report["jobs_ntsc"]
    assert "error" not in j, j
    assert len(j["window"]) == 3, j["window"]
    for did, d in j["devices"].items():
        assert d["event_f"] == d["planned_f"] == d["landing_f"], (did, d)
    assert j["pass"] is True, j


def test_a_60p_program_proved_at_the_preview_cap_is_not_failed(report):
    """A 59.94 program proved at the 30 fps preview cap: a one-frame device
    can fall between proof frames. It reads 'not measured' (pass None), and
    the proof says why, instead of failing a correct device."""
    j = report["jobs_60p"]
    assert "error" not in j, j
    assert j["reel_fps"] == pytest.approx(30.0, abs=0.05)
    assert any("preview cap" in n for n in j["notes"]), j["notes"]
    assert j["pass"] is not False, j
    for did, d in j["devices"].items():
        land = d["land"]
        assert land["pass"] is not False, (did, d)
        if land["pass"] is None:
            assert "frame rate" in land["detail"]["why"]


def test_proof_windows_and_landing_helpers():
    w = motion_qa.proof_windows([30, 33, 200], 30.0, 10.0)
    assert w[0][0] == pytest.approx(25 / 30.0, abs=0.01)
    assert len(w) == 2 and w[1][0] > w[0][1]
    item = {"id": "a", "type": "x", "stage": "pre_captions", "start": 2.0,
            "end": 2.04}
    assert motion_qa.landing(item, 25.0) == (50, 50)
    j = {"id": "j", "type": "x", "stage": "junction", "at": 4.0}
    assert motion_qa.landing(j, 25.0)[0] == 100


def test_device_items_and_ablation():
    edl = {"fx": [{"id": "a", "stage": "world", "start": 1.0, "end": 1.1},
                  {"id": "m", "stage": "music_bus", "music_id": "x"}],
           "motion": [{"id": "g", "start": 2.0, "end": 3.0,
                       "composite": {"blend": "difference"}},
                      {"id": "h", "start": 4.0, "end": 5.0,
                       "layer": "behind_subject"},
                      {"id": "p", "start": 6.0, "end": 7.0}]}
    devs = motion_qa.device_items(edl)
    assert [d["id"] for d in devs] == ["a", "g", "h"]
    assert [d["id"] for d in motion_qa.device_items(edl, window=(3.5, 6))] \
        == ["h"]
    prev = {"fx": [{"id": "a", "stage": "world", "start": 1.0,
                    "end": 1.1}], "motion": edl["motion"]}
    assert motion_qa.device_items(edl, previous_edl=prev) == []
    out = motion_qa.ablate(edl, devs)
    assert [i["id"] for i in out["fx"]] == ["m"]
    assert [m["id"] for m in out["motion"]] == ["g", "p"]
    assert out["motion"][0]["composite"] is None
    assert "frame" not in out
    # a frameless program left with no picture fx leaves the block clock
    # (a 'source' frame validates to no frame: it cannot hold it there)
    assert motion_qa.same_clock(edl, out) is False
    framed = dict(edl, frame={"ratio": "9:16", "mode": "crop"})
    assert motion_qa.same_clock(framed, motion_qa.ablate(framed, devs))
    assert motion_qa.same_clock(
        dict(edl, frame={"ratio": "source"}),
        dict(out, frame={"ratio": "source"})) is False
    assert motion_qa.same_clock({"fx": []}, {"fx": []})


class _JobCtx:
    """The ToolContext parts JobRenderer reads, over a fake queue."""

    def __init__(self, versions, jobs=None, lease=True, done=None):
        self.versions, self.jobs = versions, dict(jobs or {})
        self.lease, self.done, self.enqueued = lease, done, []
        self.project_id, self.workdir = 3, "."
        self.job = {"id": 9, "user_id": 2, "total_claims": 1,
                    "type": "agent_turn", "payload": {}}
        ctx = self

        class DB:
            def run(self, fn, *a):
                name = getattr(fn, "__name__", "")
                if name == "edl_history":
                    return [{"version": v} for v in sorted(ctx.versions,
                                                           reverse=True)]
                if name == "get_edl_version":
                    return {"version": a[1], "json": ctx.versions[a[1]]}
                if name == "lease_is_current":
                    return ctx.lease
                if name == "get_job":
                    return ctx.jobs.get(a[0])
                if name == "q":                 # the finished-job lookup
                    return ctx.done
                if name == "get_or_enqueue_preview_check_job":
                    ctx.enqueued.append(a[2])
                    return 100 + len(ctx.enqueued), True
                raise AssertionError(name)
        self.db = DB()

    def latest_edl(self):
        v = max(self.versions)
        return {"version": v, "json": self.versions[v]}


def test_job_reference_is_this_edit_minus_exactly_the_devices():
    base = {"keep": [[0.0, 9.0]], "frame": {"ratio": "9:16", "mode": "crop"},
            "captions": {"mode": "from_transcript"}, "fx": [], "motion": []}
    dev = {"id": "n1", "type": "accent", "stage": "pre_captions",
           "start": 1.0, "end": 1.04}
    cur = dict(base, fx=[dev])
    devs = motion_qa.device_items(cur)
    # the devices written in their own batch: v1 is the exact reference
    jr = motion_qa.JobRenderer(_JobCtx({1: base, 2: cur}), devs=devs)
    assert jr._version_for("without", cur) == (1, base)
    # devices written together with a caption change: v1 would read the
    # caption change as the device's, so there is no exact reference
    other = dict(base, captions={"mode": "from_transcript", "style": "x"})
    jr = motion_qa.JobRenderer(_JobCtx({1: other, 2: cur}), devs=devs)
    v, why = jr._version_for("without", cur)
    assert v is None and "differs" in why
    # a frameless program: the reference without fx renders on another clock
    bare = dict(base, frame=None)
    jr = motion_qa.JobRenderer(_JobCtx({1: bare, 2: dict(bare, fx=[dev])}),
                               devs=devs)
    v, why = jr._version_for("without", dict(bare, fx=[dev]))
    assert v is None and "clock" in why
    # a cleared composite and an absent key are the same picture
    mo = dict(base, motion=[{"id": "g", "start": 1, "end": 2}])
    mc = dict(base, motion=[{"id": "g", "start": 1, "end": 2,
                             "composite": {"blend": "difference"}}])
    jr = motion_qa.JobRenderer(_JobCtx({1: mo, 2: mc}),
                               devs=motion_qa.device_items(mc))
    assert jr._version_for("without", mc)[0] == 1


def test_job_waits_never_misread_a_superseded_or_dead_render():
    import db as dbx
    jobs = {5: {"state": "done", "result": {"superseded_by": 8}},
            6: {"state": "done", "result": {"render_asset_id": 41}},
            7: {"state": "failed", "error": "boom"},
            8: {"state": "running"}}
    jr = motion_qa.JobRenderer(_JobCtx({1: {}}, jobs), poll_s=0.01)
    deadline = __import__("time").time() + 0.05
    for jid, want in ((5, "superseded"), (7, "boom"), (8, "still")):
        jr.jobs["t"] = (jid, 1)
        res, why = jr.wait("t", deadline)
        assert res is None and want in why, (jid, why)
    jr.jobs["t"] = (6, 1)
    assert jr.wait("t", deadline) == ({"render_asset_id": 41}, None)
    # a turn whose lease ended stops waiting at once
    jr = motion_qa.JobRenderer(_JobCtx({1: {}}, jobs, lease=False))
    jr.jobs["t"] = (8, 1)
    with pytest.raises(dbx.JobLeaseLost):
        jr.wait("t", deadline + 60)


def test_a_finished_proof_render_is_reused_not_rendered_again():
    cur = {"keep": [[0.0, 9.0]], "fx": [{"id": "n1", "stage": "world",
                                         "start": 1.0, "end": 1.04}]}
    ctx = _JobCtx({2: cur}, done=55)
    jr = motion_qa.JobRenderer(ctx, devs=motion_qa.device_items(cur))
    assert jr.enqueue("with", cur, [[0.5, 1.5]]) == (55, None)
    assert ctx.enqueued == []
    ctx.done = None
    jid, _why = jr.enqueue("with", cur, [[0.5, 1.5]])
    assert jid == 101 and ctx.enqueued[0]["proof_device"] == "with"


def test_summary_lines_flag_failures():
    res = {"edl_version": 4, "render": {"window": [[1, 2]], "wall_s": 3.1,
                                         "variant": "proof480"},
           "pass": False, "notes": ["x"], "devices": [
               {"id": "a", "type": "accent", "device": "accent_negative",
                "landing_f": 30, "planned_f": 28, "basis": "without_device",
                "rung": "full", "metrics": {
                    "land_err_f": {"value": 2.0, "bar": "<=1",
                                   "pass": False},
                    "flash_risk": {"value": 1.0, "bar": "<=3",
                                   "pass": True}}}]}
    text = motion_qa.summary_lines(res)
    assert "NEEDS ATTENTION" in text and "land_err_f +2f" in text
    assert "FAIL" in text and "Note: x" in text


class _FakeReel:
    """Frames by program index (what Reel.frames returns)."""

    def __init__(self, frames, path="x.mp4"):
        self._f = frames
        self.segments = [{"path": path, "first_f": 0, "reel_f0": 0,
                          "frames": len(frames)}]

    def frames(self, fs):
        return {f: self._f[f] for f in fs if f in self._f}

    def locate(self, f):
        return (self.segments[0], f) if f in self._f else (None, None)


def _plate(n=20, h=120, w=90):
    import numpy as np
    rng = np.random.default_rng(3)
    base = (rng.random((h, w, 3)) * 40 + 60).astype("uint8")
    return {f: base.copy() for f in range(n)}


def test_measure_device_reads_glyph_visibility_and_type_legibility(tmp_path):
    import numpy as np
    without = _plate()
    front = _plate()
    withd = _plate()
    for f in range(10, 20):                 # a word appears at frame 10
        for d in (front, withd):
            d[f][40:80, 10:80] = 250
        withd[f][40:80, 40:60] = without[f][40:80, 40:60]  # the subject
    dev = {"id": "hero", "kind": "behind", "type": "motion",
           "device": None, "item": {"id": "hero", "start": 1.0,
                                    "end": 2.0, "layer": "behind_subject"}}
    m = motion_qa.measure_device(
        dev, _FakeReel(withd), _FakeReel(without), fps=10.0, land_f=10,
        planned_f=10, cards={}, workdir=str(tmp_path),
        front_reel=_FakeReel(front))
    gv = m["metrics"]["glyph_visible"]
    assert gv["value"] == pytest.approx(50 / 70, abs=0.01)
    assert gv["pass"] is True                       # >= 0.65
    assert m["metrics"]["land_err_f"]["value"] == 0.0
    assert m["metrics"]["legibility"]["value"] > 45
    assert os.path.exists(m["strip"]) and os.path.exists(m["onion"])
    # the same word hidden more than a third: glyph visibility fails
    for f in range(10, 20):
        withd[f][40:80, 10:60] = without[f][40:80, 10:60]
    m2 = motion_qa.measure_device(
        dev, _FakeReel(withd), _FakeReel(without), fps=10.0, land_f=10,
        planned_f=10, cards={}, workdir=str(tmp_path),
        front_reel=_FakeReel(front))
    assert m2["metrics"]["glyph_visible"]["pass"] is False


def test_card_verify_entries_apply_their_bars(tmp_path):
    without = _plate()
    withd = _plate()
    withd[12] = 255 - withd[12]                      # lands 2 frames late
    dev = {"id": "a", "kind": "fx", "type": "accent",
           "device": "accent_negative",
           "item": {"id": "a", "stage": "pre_captions", "start": 1.0,
                    "end": 1.1, "device": "accent_negative"}}
    cards = {"accent_negative": {"family": "punctuation",
                                 "verify": ["land_err_f<=1", "flash_risk",
                                            "matte_iou>=0.9"]}}
    m = motion_qa.measure_device(dev, _FakeReel(withd), _FakeReel(without),
                                 fps=10.0, land_f=10, planned_f=10,
                                 cards=cards, workdir=str(tmp_path))
    assert m["event_f"] == 12
    assert m["metrics"]["land_err_f"]["value"] == 2.0
    assert m["metrics"]["land_err_f"]["pass"] is False
    assert m["metrics"]["matte_iou"]["value"] is None
    assert "collisions" not in m["metrics"]      # punctuation is full frame


@local_only(fixture("V02", "thiel_flying_cars.mp4"))
def test_proof_on_the_sc_thiel_window_matches_a_manual_count(tmp_path):
    """AT-1F-6 on footage: two devices on the planner's stressed words; the
    proof's rendered landing equals the frame counted on an independent
    full render of the program."""
    env = dict(os.environ, VALMERA_FX_PROBE="1", SKIP_DB_INIT="1",
               DATABASE_URL="postgresql://stub/stub")
    env.pop("VALMERA_FX_PREVIEW", None)
    r = subprocess.run([sys.executable,
                        os.path.join(HERE, "w1f_proof_probe.py"),
                        str(tmp_path), "vfl"], cwd=WORKER, env=env,
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr[-3000:]
    v = json.loads(r.stdout.strip().splitlines()[-1])["vfl"]
    assert v["pass"] is True
    for d in v["devices"].values():
        assert d["event_f"] == d["planned_f"] == d["manual_f"], d
    assert v["wall_s"] < 40
