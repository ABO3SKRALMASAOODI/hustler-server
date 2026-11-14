"""Patches and overlays keep faces whole (worker/face_guard.py, 14 Nov 2026).

The rectangles are the ones the Gauntlet saw: p5518, where gpt-6.1-sol repainted a logo across the
presenter's eye, and p5533, where a darkening box over the top of the frame dimmed the dancers."""

import agent_tools
import face_guard
from tools_ext import composite_tools

# The presenter of p5518, measured as a face box (source fractions).
FACE = (0.42, 0.13, 0.60, 0.30)
SOL_P5518 = [{"x": 0.4572, "y": 0.1556, "w": 0.2072, "h": 0.1333, "fill": "text"},   # on the eye
             {"x": 0.2303, "y": 0.4019, "w": 0.4375, "h": 0.1907, "fill": "text"},   # chest logo
             {"x": 0.23, "y": 0.92, "w": 0.32, "h": 0.08, "fill": "text"}]          # bottom text


def _measure(faces=(FACE,)):
    seen = []

    def m(t):
        seen.append(t)
        return list(faces)
    m.seen = seen
    return m


class _Ctx:
    def __init__(self):
        self.has_main_video = True
        self.duration = 5.6
        self.writes = []
        self._edl = {"version": 3, "json": {"keep": [[0.0, 5.6]]}}

    def latest_edl(self):
        return self._edl

    def write_edl(self, edl, what):
        self.writes.append((edl, what))
        return f"EDL v3 -> v4: {what}"


def test_a_rectangle_on_the_face_is_never_repainted_the_others_are():
    ctx = _Ctx()
    items = [dict(r, id=f"er{i}", start=None, end=None) for i, r in enumerate(SOL_P5518, 1)]
    kept, refused = face_guard.erase_split(ctx, items, measure=_measure())
    assert [it["id"] for it in kept] == ["er2", "er3"]
    assert [it["id"] for it, _t, _f in refused] == ["er1"]
    line = face_guard.erase_refusal_line(refused)
    assert "[er1]" in line and "Do NOT say it was removed" in line


def test_erase_region_writes_the_off_face_rectangles_and_says_which_stayed(monkeypatch):
    ctx = _Ctx()
    passes = []
    monkeypatch.setattr(face_guard, "_measure", lambda c, **k: _measure())
    monkeypatch.setattr(agent_tools, "_apply_patches",
                        lambda c, items, what, drop=None: passes.append(items)
                        or f"EDL v3 -> v4: {what}")
    r = agent_tools.erase_region(ctx, regions=SOL_P5518)
    assert r.startswith("EDL v") and "NOT REPAINTED" in r
    assert len(passes) == 1 and len(passes[0]) == 2
    assert all(it["y"] > 0.3 for it in passes[0])


def test_erase_region_with_only_the_face_rectangle_writes_nothing(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(face_guard, "_measure", lambda c, **k: _measure())
    monkeypatch.setattr(agent_tools, "_apply_patches",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrote")))
    r = agent_tools.erase_region(ctx, **{k: SOL_P5518[0][k] for k in ("x", "y", "w", "h")})
    assert r.startswith("REJECTED") and "face" in r


def test_no_face_or_no_footage_changes_nothing(monkeypatch):
    ctx = _Ctx()
    passes = []
    monkeypatch.setattr(agent_tools, "_apply_patches",
                        lambda c, items, what, drop=None: passes.append(items)
                        or f"EDL v3 -> v4: {what}")
    monkeypatch.setattr(face_guard, "_measure", lambda c, **k: _measure(faces=()))
    assert agent_tools.erase_region(ctx, regions=SOL_P5518).startswith("EDL v")
    monkeypatch.setattr(face_guard, "_measure", lambda c, **k: None)   # cannot decode here
    r = agent_tools.erase_region(ctx, regions=SOL_P5518)
    assert r.startswith("EDL v") and "NOT REPAINTED" not in r
    assert [len(p) for p in passes] == [3, 3]


def test_a_window_is_measured_inside_itself():
    times = face_guard.sample_times(1.0, 2.0, 5.6)
    assert len(times) == 3 and all(1.0 <= t <= 2.0 for t in times)
    assert len(face_guard.sample_times(None, None, 60.0)) == face_guard.SAMPLES


def test_the_censor_tool_stays_the_one_that_may_cover_a_face(monkeypatch):
    """blur_region is the tool a customer's "blur my kid's face" calls for (its own contract), so
    it is not measured; the region effects and the repaints are."""
    ctx = _Ctx()
    monkeypatch.setattr(face_guard, "_measure", lambda c, **k: _measure())
    r = agent_tools.blur_region(ctx, x=0.4, y=0.1, w=0.25, h=0.25)
    assert r.startswith("EDL v") and len(ctx.writes) == 1


def test_a_darkening_box_over_people_is_refused_unless_asked(monkeypatch):
    commits = []
    monkeypatch.setattr(composite_tools, "_executor_refusal", lambda: None)
    monkeypatch.setattr(composite_tools, "_latest", lambda c: {"keep": [[0, 24.8]], "fx": []})
    monkeypatch.setattr(composite_tools, "_flash_refusal", lambda edl, item: None)
    monkeypatch.setattr(composite_tools._write, "commit",
                        lambda c, ops, what: commits.append(ops) or f"EDL v1 -> v2: {what}")
    monkeypatch.setattr(composite_tools, "_done", lambda res, notes=None: res)
    monkeypatch.setattr(composite_tools, "_notes", lambda *a, **k: None)
    hits = []
    monkeypatch.setattr(face_guard, "output_hit",
                        lambda c, edl, box, s, e: hits.append(box) or {"hit": True, "when": (0.0, 24.8)})
    r = composite_tools.add_region_effect(object(), "exposure", "rect:0,0,1,0.7", 0, 24.8, ev=-1.5)
    assert r.startswith("REJECTED") and "add_subject_effect" in r and not commits
    assert hits == [(0.0, 0.0, 1.0, 0.7)]
    r = composite_tools.add_region_effect(object(), "exposure", "rect:0,0,1,0.7", 0, 24.8, ev=-1.5,
                                          on_face=True)
    assert r.startswith("EDL v") and len(commits) == 1
    # the 'face' region is the explicit choice, never measured against itself
    r = composite_tools.add_region_effect(object(), "mono", "face", 0, 5)
    assert r.startswith("EDL v") and len(hits) == 1
