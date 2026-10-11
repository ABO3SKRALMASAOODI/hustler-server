"""Track 1E department verbs (compositing): what each writes, what it
refuses, and that a verb writes EXACTLY what the equivalent apply_edit_batch
operation writes (one validation path, INTERFACES_W1 §5). Budgets of the
department's catalog share are pinned here too."""

import copy
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import agent_tools  # noqa: E402
import db as dbx  # noqa: E402
import edit_batch  # noqa: E402
import gradewin  # noqa: E402
import lutlib  # noqa: E402
import schemas  # noqa: E402
import storage  # noqa: E402
import tools_ext  # noqa: E402
from tools_ext import finish_tools as ft  # noqa: E402

DUR = 30.0
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                  reason="needs ffmpeg")


class FakeDB:
    def __init__(self, keys=()):
        self.keys = {k: {"storage_key": k, "duration_s": None}
                     for k in keys}
        self.assets = []
        self.latest = {}

    def run(self, fn, *a, **kw):
        if fn is edit_batch.project_media_keys:
            return dict(self.keys)
        if fn is dbx.insert_asset:
            _pid, kind, key = a[:3]
            self.assets.append({"kind": kind, "key": key, **kw})
            self.keys[key] = {"storage_key": key, "duration_s": None}
            return len(self.assets)
        if fn is dbx.latest_asset:
            return self.latest.get(a[1])
        raise AssertionError(f"unexpected db call {fn}")


class Ctx:
    def __init__(self, tmp, edl=None, keys=(), proxy=None):
        self.db = FakeDB(keys)
        self.project_id = 7
        self.workdir = str(tmp)
        self.duration = DUR
        self.has_main_video = True
        self._edl = edl or _base()
        self.version = 3
        self.writes = []
        self._proxy = proxy
        self.index = {"video": {"fps": 29.97, "duration": DUR}}

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self._edl)}

    def write_edl(self, edl, desc):
        self.writes.append((edl, desc))
        self._edl = edl
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"

    def proxy_path(self):
        return self._proxy


def _base():
    edl = schemas.default_edl(DUR)
    edl["keep"] = [[0.0, 10.0], [12.0, 30.0]]
    edl["captions"] = {"mode": "from_transcript",
                       "style": {"highlight_color": "#E9B44C"}}
    return schemas.validate_edl(edl, DUR).model_dump()


@pytest.fixture(autouse=True)
def no_executor(monkeypatch):
    import remote
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: True)


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    store = {}

    def upload_file(path, key, ctype=None):
        dst = tmp_path / ("store_" + key.replace("/", "_"))
        shutil.copy(path, dst)
        store[key] = str(dst)

    def download_to(key, local):
        shutil.copy(store[key], local)
    monkeypatch.setattr(storage, "upload_file", upload_file)
    monkeypatch.setattr(storage, "download_to", download_to)
    monkeypatch.setattr(storage, "presign_get",
                        lambda key, expires=0: store.get(key, "/nonexistent"))
    return store


# ── registration and budgets ─────────────────────────────────────────────

def test_the_verbs_are_registered_in_compositing():
    for v in ft.VERBS:
        assert v in agent_tools.TOOLS, v
        assert v in agent_tools.TOOL_DOMAINS["compositing"]
        assert v in tools_ext.EXT_TOOL_NAMES
    assert ft.WRITE_TOOLS <= agent_tools.WRITE_TOOLS
    assert "make_variant_set" not in ft.WRITE_TOOLS


def test_catalog_budgets():
    names = list(ft.TOOL_SPECS)
    compact = agent_tools.openai_tools("x", compact=True, names=names)
    size = len(json.dumps(compact, separators=(",", ":")))
    assert size <= 5000, size                      # 1E's compositing share
    for name, (_fn, desc, _props) in ft.TOOL_SPECS.items():
        assert len(desc) <= 900, name
    core = tools_ext.core_names(agent_tools.TOOLS)
    cat = agent_tools.openai_tools("any-model", compact=True, names=core)
    assert len(json.dumps(cat, separators=(",", ":"))) <= 82_700


# ── verb == batch ────────────────────────────────────────────────────────

def _batch(edl, ops, keys=()):
    return edit_batch.apply_batch(edl, ops, DUR, set(keys))


def test_set_grade_window_writes_what_the_batch_writes(tmp_path):
    ctx = Ctx(tmp_path)
    out = ft.set_grade_window(ctx, start=0, end=9.9, saturation=0,
                              ramp_out_f=3)
    assert out.startswith("EDL v3 -> v4"), out
    verb_edl = ctx.writes[-1][0]
    item = verb_edl["fx"][0]
    assert item["id"] == "gw1" and item["device"] == "grade_section"
    op = {"action": "upsert", "layer": "fx", "id": "gw1", "value": {
        "id": "gw1", "type": "grade_window", "stage": "world", "start": 0.0,
        "end": 9.9, "saturation": 0.0, "ramp_out_f": 3,
        "device": "grade_section"}}
    assert _batch(_base(), [op]) == verb_edl


def test_grade_refusals(tmp_path):
    ctx = Ctx(tmp_path)
    assert ft.set_grade_window(ctx, start=5, end=5).startswith("REJECTED")
    assert "changes at least one" in ft.set_grade_window(ctx, start=1, end=2)
    assert ft.set_grade_window(ctx, start=1, end=2, saturation=5) \
        .startswith("REJECTED")
    assert not ctx.writes


def test_brand_protection_takes_the_look_accent(tmp_path):
    ctx = Ctx(tmp_path)
    ft.set_grade_window(ctx, split_tone={"shadows": "#1f4e79",
                                         "highlights": "accent"},
                        protect=["skin", "brand"])
    item = ctx.writes[-1][0]["fx"][0]
    assert item["brand_hue"] == "#e9b44c"
    assert item["split_tone"]["highlights"] == "#e9b44c"
    assert item["device"] == "grade_split_tone"
    assert item["start"] == 0.0 and item["end"] == pytest.approx(28.0)


def test_isolate_colour(tmp_path, monkeypatch):
    ctx = Ctx(tmp_path)
    out = ft.isolate_colour(ctx, hue="#C8202A", start=2, end=8,
                            tolerance=0.1)
    assert out.startswith("EDL"), out
    item = ctx.writes[-1][0]["fx"][0]
    assert item["isolate"]["hue"] == "#c8202a"
    assert item["device"] == "grade_isolate_hue"
    monkeypatch.setattr(gradewin, "auto_key",
                        lambda c, s, e, col=None: (col or "#b03030", 0.05,
                                                   0.06))
    out = ft.isolate_colour(ctx, start=2, end=8, keep_skin=True)
    assert "(auto: #b03030, key +-9 deg)" in out
    it2 = ctx.writes[-1][0]["fx"][1]
    assert it2["protect"] == ["skin"]
    assert it2["isolate"]["tolerance"] == 0.05
    monkeypatch.setattr(gradewin, "auto_key",
                        lambda c, s, e, col=None: (None, None, None))
    assert ft.isolate_colour(ctx).startswith("REJECTED")
    if "subject" not in schemas.FX_TYPES:
        assert ft.isolate_colour(ctx, keep="subject").startswith(
            "UNAVAILABLE")
    assert ft.isolate_colour(ctx, keep="sky").startswith("REJECTED")


def test_a_render_service_without_the_type_refuses_the_write(tmp_path,
                                                             monkeypatch):
    import remote
    monkeypatch.setattr(remote, "executor_supports", lambda f, **k: False)
    ctx = Ctx(tmp_path)
    assert ft.add_finish(ctx, "crt", 1, 2).startswith("UNAVAILABLE")
    assert ft.set_grade_window(ctx, saturation=0).startswith("UNAVAILABLE")
    assert not ctx.writes


def test_add_finish_and_remove(tmp_path):
    ctx = Ctx(tmp_path)
    out = ft.add_finish(ctx, "pixel_stretch", 3, 3.4, origin=0.55,
                        direction="left")
    assert out.startswith("EDL"), out
    item = ctx.writes[-1][0]["fx"][0]
    assert item["device"] == "finish_pixel_stretch"
    assert item["stage"] == "pre_captions"
    op = {"action": "upsert", "layer": "fx", "id": item["id"],
          "value": {k: v for k, v in item.items()}}
    assert _batch(_base(), [op])["fx"][0] == item
    assert ft.add_finish(ctx, "hologram").startswith("REJECTED")
    assert "palette for thermal" in ft.add_finish(ctx, "thermal", 1, 2,
                                                  palette="sepia")
    ft.add_finish(ctx, "thermal", 4, 4.1, matte_weighted=True)
    ids = [i["id"] for i in ctx._edl["fx"]]
    assert ft.remove_finish(ctx, ids="nope").startswith("REJECTED")
    out = ft.remove_finish(ctx, ids=ids)
    assert out.startswith("EDL") and ctx._edl["fx"] == []
    assert schemas.edl_signature(ctx._edl) == schemas.edl_signature(_base())


def test_remove_finish_refuses_other_tracks_items(tmp_path):
    edl = _base()
    edl["fx"] = []
    ctx = Ctx(tmp_path, edl=edl)
    ft.add_finish(ctx, "film", 1, 3)
    ctx._edl["fx"].append({"id": "zz", "type": "accent"})
    assert "not a grade window or finish" in ft.remove_finish(ctx, ["zz"])


# ── LUTs ─────────────────────────────────────────────────────────────────

def _warm_cube(path, n=9):
    t = lutlib.identity(n).table * np.array([1.0, 0.92, 0.8],
                                            dtype=np.float32)
    with open(path, "w") as f:
        f.write(lutlib.cube_text(lutlib.Lut(t, title="warm")))
    return path


def test_import_lut_from_a_project_asset(tmp_path, uploads):
    src = _warm_cube(str(tmp_path / "warm.cube"))
    uploads["media/7/warm.cube"] = src
    ctx = Ctx(tmp_path, keys=["media/7/warm.cube"])
    out = ft.import_lut(ctx, asset_key="media/7/warm.cube", start=1, end=9,
                        strength=0.7)
    assert out.startswith("EDL") and "9^3 (3d)" in out, out
    item = ctx.writes[-1][0]["fx"][0]
    key = item["lut"]["asset_key"]
    assert key.startswith("generated/7/lut-") and key.endswith(".cube")
    assert item["lut"] == {"asset_key": key, "strength": 0.7,
                           "origin": "import", "match_t": None}
    asset = ctx.db.assets[-1]
    assert asset["kind"] == "sheet" and asset["meta"]["lut"] is True
    back = lutlib.parse_cube(open(uploads[key]).read())
    assert back.size == 9


def test_import_lut_refusals(tmp_path, uploads):
    bad = tmp_path / "bad.cube"
    bad.write_text("LUT_3D_SIZE 99\n")
    uploads["media/7/bad.cube"] = str(bad)
    ctx = Ctx(tmp_path, keys=["media/7/bad.cube"])
    assert "outside 2..65" in ft.import_lut(ctx, asset_key="media/7/bad.cube")
    assert "not in this project" in ft.import_lut(ctx, asset_key="x/y.cube")
    assert ft.import_lut(ctx).startswith("REJECTED")
    assert not ctx.writes


def test_match_grade_bakes_a_cube_that_closes_the_gap(tmp_path, uploads,
                                                      monkeypatch):
    rng = np.random.default_rng(5)
    hero = (rng.random((80, 120, 3)) * 200 + 20).astype(np.uint8)
    warm = np.clip(hero * np.array([1.1, 0.95, 0.8]), 0, 255).astype(
        np.uint8)

    def frames(ctx, edl, times):
        return [hero if t >= 20 else warm for t in times]
    monkeypatch.setattr(ft, "_frames", frames)
    ctx = Ctx(tmp_path)
    out = ft.match_grade(ctx, hero_t=25, start=0, end=9.9)
    assert out.startswith("EDL"), out
    before, after = (float(x) for x in out.split("dE ")[1].rstrip(")")
                     .split(" -> "))
    assert after < before / 3, out
    item = ctx.writes[-1][0]["fx"][0]
    assert item["lut"]["origin"] == "match" and item["lut"]["match_t"] == 25
    assert item["device"] == "grade_shot_match"
    assert "outside the section" in ft.match_grade(ctx, hero_t=5, start=0,
                                                   end=9.9)


# ── auto hue on real frames (proxy decoding) ─────────────────────────────

@needs_ffmpeg
def test_auto_hue_finds_the_subjects_colour_not_the_skin(tmp_path):
    proxy = str(tmp_path / "proxy.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x606060:s=320x180:r=25:d=6",
                    "-vf", "drawbox=x=110:y=40:w=100:h=40:color=0xe0ac8c:"
                    "t=fill,drawbox=x=100:y=95:w=120:h=85:color=0xb8202a:"
                    "t=fill,drawbox=x=0:y=0:w=60:h=60:color=0x2050c8:t=fill",
                    "-pix_fmt", "yuv420p", proxy], check=True)
    edl = _base()
    edl["keep"] = [[0.0, 6.0]]
    ctx = Ctx(tmp_path, edl=edl, proxy=proxy)
    ctx.duration = 6.0
    got = gradewin.auto_hue(ctx, 0.5, 4.0)
    r, g, b = lutlib.hex_rgb(got)
    assert r > 150 and g < 70 and b < 80, got


# ── variants through the verb ────────────────────────────────────────────

@needs_ffmpeg
def test_make_variant_set_uploads_registered_stills(tmp_path, uploads):
    proxy = str(tmp_path / "proxy.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=480x270:r=25:d=6", "-pix_fmt", "yuv420p",
                    proxy], check=True)
    edl = _base()
    edl["keep"] = [[1.0, 6.0]]
    ctx = Ctx(tmp_path, edl=edl, proxy=proxy)
    ctx.duration = 6.0
    out = ft.make_variant_set(ctx, t=2.0)
    assert out.startswith("Made 5 registered variant stills"), out
    assert "source 3.00s" in out and "proxy frame" in out
    kinds = [a["meta"]["variant"] for a in ctx.db.assets]
    assert kinds == ["photo", "negative", "line", "blueprint", "thermal"]
    assert all(a["kind"] == "image_ref" for a in ctx.db.assets)
    assert not ctx.writes                           # places nothing
    assert ft.make_variant_set(ctx, t=2, kinds=["hologram"]).startswith(
        "REJECTED")
    assert "at most" in ft.make_variant_set(ctx, t=2, kinds=list(
        "photo negative line blueprint thermal xray solarise".split()))


def test_section_edges_snap_onto_the_cut_frame(tmp_path):
    """A cut at program 3.99 s (29.97 fps) is first shown on frame 120
    (ceil 119.58); round(3.99 * 29.97) = 120 too, but 7.99 -> frame 239.46:
    round = 239, the block starts on 240. A section given the cut's own
    second must start ON the cut frame."""
    import fxprims
    import renderer
    edl = _base()
    edl["keep"] = [[0.0, 3.99], [5.0, 9.0], [12.0, 20.0]]
    edl = schemas.validate_edl(edl, DUR).model_dump()
    ctx = Ctx(tmp_path, edl=edl)
    ft.set_grade_window(ctx, start=3.99, end=7.99, saturation=0)
    it = ctx.writes[-1][0]["fx"][0]
    for t, cut in ((it["start"], 3.99), (it["end"], 7.99)):
        assert fxprims.frame_at(t, 29.97) == renderer.first_frame_at(
            cut, 29.97), (t, cut)
    ft.add_finish(ctx, "film", 2.0, 3.0)                 # not near a cut
    f = ctx.writes[-1][0]["fx"][-1]
    assert (f["start"], f["end"]) == (2.0, 3.0)
