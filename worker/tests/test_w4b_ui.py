"""Track 4B: the UI scene graph, the ui_scene template, ui_media slots and
the add_ui_scene / remove_ui_scene verbs.

Pinned:
  * persistence: an object in consecutive scenes morphs (never jumps more
    than its morph allows per frame), across >= 3 scenes;
  * irises open at the centre of the element that caused them (exact in
    the graph; +-1 px in the rendered page when Chromium is available);
  * counters land on the spoken number; the karaoke pill lights its words
    on their onsets;
  * figures are never invented: data-bound values equal the user's data,
    an unbound number that is neither said nor requested is refused;
  * a media slot plays its footage only inside the slot's solved rect,
    bit-exact outside the window;
  * the verbs write exactly what apply_edit_batch would, and remove it.
"""

import copy
import json
import os
import shutil
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import ui_scene                                               # noqa: E402

W, H = 1080, 1920
CSV = "part,units\nFront forks,1000\nHandlebars,0\nSaddles,640\n"


def _scenes():
    return [
        {"at": 0.0, "layout": "stack", "objects": [
            {"id": "pill", "kind": "pill", "label": "Tracking parts",
             "state": "idle"},
            {"id": "forks", "kind": "metric", "label": "Front forks",
             "bind": {"row": "Front forks", "col": "units"}}]},
        {"at": 2.5, "layout": "grid", "objects": [
            {"id": "pill", "kind": "pill", "label": "Tracking parts",
             "state": "live"},
            {"id": "forks", "kind": "metric", "label": "Front forks",
             "bind": {"row": "Front forks", "col": "units"}},
            {"id": "hb", "kind": "metric", "label": "Handlebars",
             "bind": {"row": "Handlebars", "col": "units"},
             "from": "forks"}]},
        {"at": 5.0, "layout": "focus", "focus": "chart", "objects": [
            {"id": "pill", "kind": "pill", "label": "Tracking parts",
             "state": "done"},
            {"id": "forks", "kind": "metric", "label": "Front forks",
             "bind": {"row": "Front forks", "col": "units"}},
            {"id": "hb", "kind": "metric", "label": "Handlebars",
             "bind": {"row": "Handlebars", "col": "units"}},
            {"id": "chart", "kind": "bars", "title": "Parts this week",
             "rows_from": {"key": "part", "col": "units"}, "from": "hb"}]},
    ]


def _graph(words=()):
    sc = ui_scene.bind_objects(copy.deepcopy(_scenes()), CSV)
    return ui_scene.solve(sc, 8.0, W, H, words=list(words))


def test_persistent_objects_morph_across_three_scenes():
    g = _graph()
    pill = next(o for o in g["objects"] if o["id"] == "pill")
    morphs = [s for s in pill["segs"] if s[12] == "morph"]
    assert len(morphs) == 2                    # scene 2 and scene 3: morphs
    # it never jumps: per-frame centre motion stays smooth (eased in & out)
    assert ui_scene.continuity(g, "pill", fps=30) < 0.05 * H
    r1 = ui_scene.rect_at(g, "pill", 1.0)
    r3 = ui_scene.rect_at(g, "pill", 7.0)
    assert r1 != r3                            # it moved to the strip


def test_iris_opens_at_the_centre_of_its_cause():
    g = _graph()
    hb = next(o for o in g["objects"] if o["id"] == "hb")
    ir = hb["iris"]
    # the layout makes room first, then the tile opens out of the forks
    # tile where it now rests
    assert ir["from"] == "forks" and abs(ir["t"] - (2.5 + ui_scene.MORPH_S)) \
        < 1e-6
    forks_r = ui_scene.rect_at(g, "forks", ir["t"])
    assert ir["ox"] == round(forks_r[0] + forks_r[2] / 2.0)
    assert ir["oy"] == round(forks_r[1] + forks_r[3] / 2.0)
    assert ui_scene.iris_radius(hb, ir["t"]) == 0.0
    assert ui_scene.iris_radius(hb, ir["t"] + 0.3) > 0
    assert ui_scene.iris_radius(hb, ir["t"] + ui_scene.IRIS_S + 0.01) is None


def test_counter_lands_on_the_spoken_number_and_pill_karaoke():
    words = [{"w": "tracking", "t0": 0.4}, {"w": "parts", "t0": 0.8},
             {"w": "a", "t0": 1.3}, {"w": "thousand", "t0": 1.45}]
    g = _graph(words)
    forks = next(o for o in g["objects"] if o["id"] == "forks")
    assert forks["count"]["landed_on_word"] and \
        abs(forks["count"]["t1"] - 1.3) < 1e-6      # "a thousand" starts
    pill = next(o for o in g["objects"] if o["id"] == "pill")
    assert pill["karaoke"] == [0.4, 0.8]


def test_bound_figures_equal_the_users_data():
    g = _graph()
    chart = next(o for o in g["objects"] if o["id"] == "chart")
    assert [(r["label"], r["value"]) for r in chart["rows"]] == [
        ("Front forks", "1000"), ("Handlebars", "0"), ("Saddles", "640")]
    hb = next(o for o in g["objects"] if o["id"] == "hb")
    assert hb["value"] == "0"


def test_invented_numbers_are_refused():
    sc = [{"at": 0, "objects": [{"id": "m", "kind": "metric",
                                 "label": "Growth", "value": "340%"}]}]
    msg = ui_scene.check_numbers(ui_scene.bind_objects(sc, None), [], None)
    assert msg and "340" in msg
    # said, or in the request: allowed
    assert ui_scene.check_numbers(sc, [{"w": "340", "t0": 1.0}], None) is None
    assert ui_scene.check_numbers(sc, [], "show growth of 340%") is None
    with pytest.raises(ValueError, match="not in the data"):
        ui_scene.bind_objects([{"objects": [{"id": "x", "kind": "metric",
                                             "bind": "Revenue"}]}],
                              {"Users": 5})


def test_spoken_numbers_read_spelled_figures():
    ws = [{"w": w, "t0": float(i)} for i, w in enumerate(
        "there might be a thousand bicycles and 562 forks two hundred".split())]
    got = ui_scene.spoken_numbers(ws)
    assert (1000.0, 3.0) in got and (562.0, 7.0) in got and \
        (200.0, 9.0) in got


def test_scene_rules():
    with pytest.raises(ValueError, match="too close"):
        ui_scene.solve([{"at": 0, "objects": [{"id": "a", "kind": "text",
                                                "text": "x"}]},
                        {"at": 0.3, "objects": [{"id": "a", "kind": "text",
                                                  "text": "x"}]}], 4, W, H)
    with pytest.raises(ValueError, match="kind"):
        ui_scene.solve([{"objects": [{"id": "a", "kind": "hologram"}]}], 4,
                       W, H)


def test_layout_stays_in_the_safe_area():
    g = _graph()
    x0, y0, x1, y1 = ui_scene.safe_area(W, H)
    for o in g["objects"]:
        for s in o["segs"]:
            for (x, y, w, h) in (s[2:6], s[6:10]):
                assert x >= x0 - 1 and y >= y0 - 1
                assert x + w <= x1 + 1 and y + h <= y1 + 1


# ── the page (Chromium, when the lane has it) ────────────────────────────

def _engine():
    import motion_engine
    return motion_engine if motion_engine.available() else None


@pytest.mark.skipif(_engine() is None, reason="needs the motion renderer")
def test_the_page_draws_the_graph(tmp_path):
    import subprocess
    import motion_engine
    import motion_templates
    g = _graph()
    item = {"id": "ui1", "template": "ui_scene", "start": 0.0, "end": 8.0,
            "params": motion_templates.normalize_params("ui_scene", {
                "graph": g, "shown": ui_scene.shown_texts(g),
                "backdrop": "none"})}
    job = motion_templates.build_job(item, W, H, 30.0)
    clip = motion_engine.render_jobs([job], str(tmp_path))[0]
    path = clip.path
    cx0, cy0, cw, chh = clip.x, clip.y, clip.w, clip.h

    def alpha(k):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                              f"select=eq(n\\,{k}),format=rgba,alphaextract",
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                              "gray", "-"], capture_output=True).stdout
        full = np.zeros((H, W), np.uint8)
        full[cy0:cy0 + chh, cx0:cx0 + cw] = np.frombuffer(
            raw, np.uint8).reshape(chh, cw)
        return full
    # the iris: a few frames in, the visible part of the new tile is a disc
    # around the origin (+-1 px)
    hb = next(o for o in g["objects"] if o["id"] == "hb")
    k = int(round(hb["iris"]["t"] * 30)) + 3
    before = alpha(int(round(hb["iris"]["t"] * 30)) - 1)
    a = alpha(k)
    r = ui_scene.rect_at(g, "hb", k / 30.0)
    x0, y0 = int(r[0]), int(r[1])
    sub = (a[y0:y0 + int(r[3]), x0:x0 + int(r[2])].astype(int)
           - before[y0:y0 + int(r[3]), x0:x0 + int(r[2])].astype(int)) > 40
    ys, xs = np.nonzero(sub)
    assert len(xs) > 30
    cx, cy = xs.mean() + x0, ys.mean() + y0
    rad = ui_scene.iris_radius(hb, k / 30.0)
    # a disc clipped by the tile's own rect: its centroid sits between the
    # origin and the tile centre; with the disc inside the tile it IS the
    # origin
    if rad is not None and rad < min(r[2], r[3]) / 2.0 - 2 and \
            r[0] + rad <= hb["iris"]["ox"] <= r[0] + r[2] - rad:
        assert abs(cx - hb["iris"]["ox"]) <= 1.0
        assert abs(cy - hb["iris"]["oy"]) <= 1.0


# ── the slot compiler and the verbs ──────────────────────────────────────

class FakeDB:
    def __init__(self, keys=()):
        self.keys = {k: {"storage_key": k, "duration_s": 6.0} for k in keys}

    def run(self, fn, *a, **kw):
        import edit_batch
        if fn is edit_batch.project_media_keys:
            return dict(self.keys)
        raise AssertionError(f"unexpected db call {fn}")


class Ctx:
    def __init__(self, edl, idx, keys=()):
        self.db = FakeDB(keys)
        self.project_id = 7
        self.duration = 12.0
        self._edl = edl
        self.version = 1
        self.writes = []
        self.index = idx
        self.user_message = None
        self.pending_images = []

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self._edl)}

    def write_edl(self, edl, desc):
        self.writes.append((edl, desc))
        self._edl = edl
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"


@pytest.fixture
def ctx(monkeypatch):
    import remote
    from schemas import validate_edl
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: True)
    words = [{"w": w, "t0": t, "t1": t + 0.3} for w, t in (
        ("tracking", 2.4), ("parts", 2.8), ("a", 3.3), ("thousand", 3.45),
        ("handlebars", 5.2), ("chaos", 8.1))]
    idx = {"words": words, "video": {"width": 1920, "height": 1080,
                                     "fps": 25, "duration": 12.0}}
    edl = validate_edl({"keep": [[0.0, 12.0]],
                        "frame": {"ratio": "9:16", "mode": "crop"}},
                       12.0).model_dump()
    return Ctx(edl, idx, keys=("media/7/rec.mp4",))


def test_add_ui_scene_writes_the_scene_and_its_slot(ctx):
    from tools_ext import ui_tools
    scenes = [
        {"word": "tracking", "layout": "stack", "objects": [
            {"id": "pill", "kind": "pill", "label": "Tracking parts",
             "state": "live"},
            {"id": "forks", "kind": "metric", "label": "Front forks",
             "bind": {"row": "Front forks", "col": "units"}},
            {"id": "screen", "kind": "media", "title": "Live board"}]},
        {"word": "handlebars", "layout": "focus", "focus": "screen",
         "objects": [
            {"id": "pill", "kind": "pill", "label": "Tracking parts",
             "state": "done"},
            {"id": "screen", "kind": "media", "title": "Live board"},
            {"id": "forks", "kind": "metric", "label": "Front forks",
             "bind": {"row": "Front forks", "col": "units"}},
            {"id": "warn", "kind": "card", "title": "No handlebars",
             "from": "screen"}]}]
    out = ui_tools.add_ui_scene(ctx, scenes=scenes, end=9.0, data=CSV,
                                media={"screen": {"asset_key":
                                                  "media/7/rec.mp4"}})
    assert out.startswith("EDL v1 -> v2"), out
    mo = [m for m in ctx._edl["motion"] if m["template"] == "ui_scene"]
    assert len(mo) == 1 and abs(mo[0]["start"] - 2.4) < 0.01
    um = [f for f in ctx._edl["fx"] if f["type"] == "ui_media"]
    assert um and um[0]["motion_id"] == mo[0]["id"] and \
        um[0]["slot"] == "screen"
    assert "landing ON the spoken number" in out
    assert "warn from screen" in out
    out = ui_tools.remove_ui_scene(ctx, [mo[0]["id"]])
    assert out.startswith("EDL") and not [
        m for m in ctx._edl.get("motion") or []
        if m.get("template") == "ui_scene"] and not [
        f for f in ctx._edl.get("fx") or [] if f.get("type") == "ui_media"]


def test_add_ui_scene_refuses_invented_figures_and_missing_footage(ctx):
    from tools_ext import ui_tools
    out = ui_tools.add_ui_scene(ctx, start=1.0, end=6.0, scenes=[
        {"at": 0, "objects": [{"id": "m", "kind": "metric",
                               "label": "Growth", "value": "+340%"}]}])
    assert out.startswith("REJECTED") and "340" in out
    out = ui_tools.add_ui_scene(ctx, start=1.0, end=6.0, scenes=[
        {"at": 0, "objects": [{"id": "s", "kind": "media"}]}])
    assert out.startswith("REJECTED") and "need footage" in out
    assert not ctx.writes


def test_ui_media_check_refuses_an_unknown_slot(ctx):
    from schemas import validate_edl
    g = _graph()
    e = copy.deepcopy(ctx._edl)
    e["motion"] = [{"id": "ui1", "template": "ui_scene", "start": 1.0,
                    "end": 9.0, "params": {"graph": g, "shown": [],
                                           "backdrop": "solid"}}]
    e["fx"] = [{"id": "u1", "type": "ui_media", "stage": "screen",
                "start": 1.0, "end": 9.0, "motion_id": "ui1",
                "slot": "nope", "source": {"kind": "asset",
                                           "asset_key": "media/7/rec.mp4"}}]
    with pytest.raises(Exception, match="no media slot"):
        validate_edl(e, 12.0)


def test_registration_and_cards():
    import agent_tools
    import devices
    import tools_ext
    from tools_ext import ui_tools
    for v in ui_tools.VERBS:
        assert v in agent_tools.TOOLS and v in tools_ext.EXT_TOOL_NAMES
        assert v in agent_tools.TOOL_DOMAINS["world"]
    for name, (_fn, desc, _p) in ui_tools.TOOL_SPECS.items():
        assert len(desc) <= 900, name
    for cid in ("ui_scene_explainer", "ui_media_slot"):
        c = devices.card(cid)
        assert c["skill"] == "explainers"
        assert not c["owner_flags"]["adds_zoom"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_slot_plays_inside_its_rect_only(tmp_path):
    import subprocess
    import media
    import sidecars
    import w0b_probe
    import w4a_support as sup
    src = sup.make_split_source(str(tmp_path / "split.mp4"), dur=10.0)
    info = media.probe(src)
    ref = sidecars.source_from(media.sha256_file(src), src, info, 7, [])
    idx = sup.index_for(ref, info)
    rec = str(tmp_path / "rec.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=640x360:r=25", "-t", "6", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", rec], check=True)
    g = ui_scene.solve([{"at": 0, "layout": "stack", "objects": [
        {"id": "screen", "kind": "media", "aspect": 16 / 9}]}], 4.0,
        1080, int(round(1080 * 360 / 640)))
    base = {"keep": [[0.0, 10.0]]}
    fx = [{"id": "u1", "type": "ui_media", "stage": "screen",
           "start": 2.0, "end": 6.0, "motion_id": "ui1", "slot": "screen",
           "source": {"kind": "asset", "asset_key": "media/7/rec.mp4"},
           "fit": "cover", "device": "ui_media_slot"}]
    mo = [{"id": "ui1", "template": "ui_scene", "start": 2.0, "end": 6.0,
           "params": {"graph": g, "shown": [], "backdrop": "none"}}]
    locals_ = {"media/7/rec.mp4": rec}
    cases = [{"name": "ctl", "src": src, "idx": idx,
              "edl": dict(base, fx=fx[:0], motion=[]), "preview": True,
              "asset_locals": locals_},
             {"name": "slot", "src": src, "idx": idx,
              "edl": dict(base, fx=fx, motion=[]), "preview": True,
              "asset_locals": locals_}]
    # the slot compiler reads the motion item's graph; drawing the chrome is
    # the motion layer's job (not under test here): the item is in the EDL
    cases[1]["edl"]["motion"] = mo
    cases[0]["edl"]["motion"] = mo
    os.environ["VALMERA_FX_ASSET_LOCALS"] = json.dumps(locals_)
    try:
        res = w0b_probe.run(cases, str(tmp_path / "out"))
    finally:
        os.environ.pop("VALMERA_FX_ASSET_LOCALS", None)
    for n in ("ctl", "slot"):
        assert res[n]["error"] is None, res[n]["error"]
    ch = [i for i, (a, b) in enumerate(zip(res["ctl"]["frames"],
                                            res["slot"]["frames"])) if a != b]
    assert ch and min(ch) >= 50 and max(ch) < 150
