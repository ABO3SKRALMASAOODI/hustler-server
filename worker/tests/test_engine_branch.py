"""Lane D (12 Nov): the scene engine behind app_kv.director.engine, with today's renderer as the fallback
in the same job. Every fault falls back, files one ticket, and never reaches the customer."""
import json
import os
import time

import pytest

import db as dbx
import engine_branch as eb
from score import flags


# ------------------------------------------------------------------ fakes

class Store:
    def __init__(self):
        self.objects = {}

    def upload_file(self, path, key, content_type):
        with open(path, "rb") as f:
            self.objects[key] = f.read()

    def exists(self, key):
        return key in self.objects

    def download_to(self, key, path, check_capacity=True):
        if key not in self.objects:
            raise FileNotFoundError(key)
        with open(path, "wb") as f:
            f.write(self.objects[key])


class Db:
    def __init__(self, row=None):
        self.row = row

    def run(self, fn, *a, **kw):
        if fn is flags.read_row:
            return self.row
        return fn(None, *a, **kw)


class Fault(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


class Scene:
    """Stands in for worker/scene: writes a film, or raises as told."""
    SceneFault = Fault

    def __init__(self, fault=None):
        self.fault, self.calls = fault, []

    def render(self, l1, work, out, **kw):
        self.calls.append((l1, work, kw))
        if self.fault:
            raise self.fault
        with open(out, "wb") as f:
            f.write(b"film")
        return {"frames": l1["dur_f"], "stage_ms": {"base": 1.0}, "gl": "llvmpipe"}


EDL = {"keep": [[0.0, 10.0]], "captions": None}
L1 = {"frame": [1080, 1920], "fps": 30, "dur_f": 300, "voices": {"a": {"font": "InterDisplay-Bold.ttf", "px": 58}},
      "assets": {"main": {"kind": "video", "path": "source.mp4"}, "bin:3": {"kind": "video", "path": "bin/3.mp4"},
                 "matte": {"kind": "video", "path": "matte.mp4"}},
      "items": [{"k": "shot", "id": "s", "f": [0, 300], "pic": "main"}]}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    eb._faults.clear()
    eb._tripped_until[0] = 0.0
    events = []
    monkeypatch.setattr(dbx, "record_client_event", lambda conn, uid, pid, kind, detail=None, asset_id=None:
                        events.append((uid, pid, kind, detail)))
    yield events


def _row(**kw):
    return json.dumps(dict({"mode": "on"}, **kw))


def _kept(tmp_path, store):
    work = tmp_path / "director"
    (work / "bin").mkdir(parents=True)
    (work / "source.mp4").write_bytes(b"src")
    (work / "matte.mp4").write_bytes(b"matte")
    keys = {"main": "originals/7/a.mp4", "bin:3": "fetched/7/b3.mp4"}
    store.objects["fetched/7/b3.mp4"] = b"bin3"
    return eb.keep_plan(7, EDL, L1, keys, str(work), storage=store)


def _src(tmp_path):
    p = tmp_path / "proxy.mp4"
    p.write_bytes(b"src")
    return str(p)


# ------------------------------------------------------------------ the switch

def test_engine_is_off_unless_the_row_says_shadow_or_on():
    for raw in (None, "{", _row(), _row(engine="maybe"), _row(engine=1), '{"mode": "x", "engine": "on"}'):
        assert flags.parse(raw)["engine"] == "off", raw
    assert flags.parse(_row(engine="shadow"))["engine"] == "shadow"
    assert flags.parse(_row(engine="on"))["engine"] == "on"
    assert flags.parse('{"mode": "off", "engine": "on"}')["engine"] == "on"      # the engine draws films already kept


def test_named_accounts_get_the_engine_on_while_the_row_is_off():
    cfg = flags.parse(_row(engine="off", engine_user_ids=[5]))
    assert flags.engine_mode(cfg, 5) == "on"
    assert flags.engine_mode(cfg, 6) == "off"
    assert flags.engine_mode(flags.parse(_row(engine="shadow", engine_user_ids=["x"])), 6) == "shadow"


# ------------------------------------------------------------------ the plan

def test_the_plan_is_kept_by_the_exact_edl_and_its_pictures_by_storage_key(tmp_path):
    store = Store()
    base = _kept(tmp_path, store)
    assert base == eb.plan_base(7, EDL) and base.startswith("scene/7/")
    assert eb.plan_base(7, dict(EDL, keep=[[0.0, 9.0]])) != base           # any later edit is another EDL
    doc = json.loads(store.objects[f"{base}/plan.json"])
    a = doc["l1"]["assets"]
    assert a["bin:3"]["storage_key"] == "fetched/7/b3.mp4"
    assert a["matte"]["storage_key"] == f"{base}/matte.mp4" and store.objects[f"{base}/matte.mp4"] == b"matte"
    assert all(x["path"].startswith("a/") for x in a.values())


def test_keeping_a_plan_never_raises_into_the_director(tmp_path):
    class Broken(Store):
        def upload_file(self, *a):
            raise OSError("storage down")
    assert eb.keep_plan(7, EDL, L1, {}, str(tmp_path), storage=Broken()) is None


def test_prepare_needs_the_switch_and_a_kept_plan(tmp_path):
    store = Store()
    job = {"id": 1, "project_id": 7, "user_id": 5}
    assert eb.prepare(Db(_row(engine="off")), job, EDL, storage=store) is None
    assert eb.prepare(Db(_row(engine="on")), job, EDL, storage=store) is None              # no plan kept
    _kept(tmp_path, store)
    p = eb.prepare(Db(_row(engine="on")), job, EDL, storage=store)
    assert p.mode == "on" and p.base == eb.plan_base(7, EDL)
    assert eb.prepare(Db(_row(engine="on")), job, EDL, proof_only=True, storage=store) is None
    assert eb.prepare(Db(_row(engine="on")), job, dict(EDL, keep=[[1.0, 5.0]]), storage=store) is None

    class Boom:
        def run(self, *a, **k):
            raise RuntimeError("db down")
    assert eb.prepare(Boom(), job, EDL, storage=store) is None


# ------------------------------------------------------------------ drawing and the fallback

def _draw(tmp_path, monkeypatch, scene, finish=None, verify=None, store=None):
    store = store or Store()
    _kept(tmp_path, store)
    monkeypatch.setattr(eb, "target_size", lambda l1, v: (720, 1280))
    real = eb.materialize
    monkeypatch.setattr(eb, "materialize", lambda prep, src, sdir: real(prep, src, sdir, storage=store))
    prep = eb.prepare(Db(_row(engine="on")), {"id": 9, "project_id": 7, "user_id": 5}, EDL, storage=store)
    out = tmp_path / "out.mp4"
    out.write_bytes(b"partial")

    def fin(engine_out, path):
        open(path, "wb").write(open(engine_out, "rb").read() + b"+brand")
        return 10.0
    dur = eb.draw(prep, Db(), src_local=_src(tmp_path), out_local=str(out), variant="preview",
                  finish=finish or fin, verify=verify or (lambda p, d: None), sdir=str(tmp_path / "scene"), scene=scene)
    return prep, dur, out


def test_the_engine_draws_the_plan_at_the_variants_size_and_the_brand_step_finishes_it(tmp_path, monkeypatch, _clean):
    scene = Scene()
    prep, dur, out = _draw(tmp_path, monkeypatch, scene)
    assert dur == 10.0 and out.read_bytes() == b"film+brand"
    l1, work, kw = scene.calls[0]
    assert l1["frame"] == [720, 1280] and l1["voices"]["a"]["px"] == pytest.approx(58 * 720 / 1080, abs=1e-3)
    assert kw["strict"] is True and kw["audio"] is True and kw["timeout_s"] > 0
    assert os.path.islink(os.path.join(work, "a", "main.mp4"))                # main is the render's own source
    assert open(os.path.join(work, "a", "bin_3.mp4"), "rb").read() == b"bin3"
    assert prep.info["draw_s"] >= 0 and "fault" not in prep.info
    assert _clean == []                                                         # no ticket


@pytest.mark.parametrize("where", ["scene", "finish", "verify", "crash"])
def test_every_fault_falls_back_with_one_ticket_and_no_partial_file(tmp_path, monkeypatch, _clean, where):
    scene = Scene(Fault("gl", "no context") if where == "scene" else RuntimeError("boom") if where == "crash" else None)

    def finish(engine_out, path):
        raise RuntimeError("ffmpeg refused")

    def verify(path, d):
        import media
        raise media.MediaError("render is 3.2 s, expected 10.0 s")
    prep, dur, out = _draw(tmp_path, monkeypatch, scene, finish=finish if where == "finish" else None,
                           verify=verify if where == "verify" else None)
    assert dur is None                       # the caller renders the same EDL on today's renderer
    assert not out.exists()                  # nothing half-made is left for the fallback to trip on
    assert len(_clean) == 1
    uid, pid, kind, detail = _clean[0]
    assert (uid, pid, kind) == (5, 7, "director_fell_back")      # the ops intake files it as P1
    assert detail["stage"] == "engine" and detail["engine_mode"] == "on" and detail["job_id"] == 9


def test_a_missing_engine_package_is_a_fault_not_a_failure(tmp_path, monkeypatch, _clean):
    class NoScene:
        def render(self, *a, **k):
            raise ImportError("No module named 'moderngl'")
    _, dur, _ = _draw(tmp_path, monkeypatch, NoScene())
    assert dur is None and _clean[0][2] == "director_fell_back"


def test_the_breaker_trips_on_three_faults_from_two_projects_and_the_engine_goes_to_shadow(tmp_path):
    prep = eb.Prepared("on", "scene/7/x", 7, 5, 1)
    eb.file_fault(Db(), prep, Fault("stall"))
    eb.file_fault(Db(), prep, Fault("stall"))
    assert not eb._tripped()                         # one project's trouble does not trip it
    eb.file_fault(Db(), eb.Prepared("on", "scene/8/x", 8, 5, 2), Fault("stall"))
    assert eb._tripped()
    store = Store()
    _kept(tmp_path, store)
    p = eb.prepare(Db(_row(engine="on")), {"id": 3, "project_id": 7, "user_id": 5}, EDL, storage=store)
    assert p.mode == "shadow"
    eb._faults.clear()
    eb._faults.append((time.monotonic() - 3600, 9))
    eb._tripped_until[0] = 0.0
    eb._note_fault(9)
    assert not eb._tripped()                         # an old fault ages out


def test_sized_keeps_a_plan_that_is_already_the_right_size():
    assert eb.sized(L1, 1080, 1920) == L1
    s = eb.sized(L1, 540, 960)
    assert s["frame"] == [540, 960] and s["voices"]["a"]["px"] == 29.0 and L1["voices"]["a"]["px"] == 58


def test_shadow_runs_on_its_own_folder_and_never_blocks(tmp_path, monkeypatch, _clean):
    store = Store()
    _kept(tmp_path, store)
    monkeypatch.setattr(eb, "target_size", lambda l1, v: (720, 1280))
    real = eb.materialize
    monkeypatch.setattr(eb, "materialize", lambda prep, src, sdir: real(prep, src, sdir, storage=store))
    import sys
    import types
    fake = types.ModuleType("scene")
    fake.render = Scene().render
    fake.SceneFault = Fault
    monkeypatch.setitem(sys.modules, "scene", fake)
    monkeypatch.setenv("VALMERA_SCENE_SHADOW_DIR", str(tmp_path))
    prep = eb.Prepared("shadow", eb.plan_base(7, EDL), 7, 5, 11)

    def finish_factory(sdir):
        def fin(engine_out, path):
            open(path, "wb").write(b"shadow film")
            return 10.0
        return fin
    src = _src(tmp_path)
    t = eb.shadow(prep, Db(), src_local=src, variant="preview", finish_factory=finish_factory,
                  verify_factory=lambda s: (lambda p, d: None), storage=store)
    os.unlink(src)                           # the job's folder goes as soon as the job ends
    t.join(10)
    assert not t.is_alive()
    assert store.objects[f"{prep.base}/shadow_preview.mp4"] == b"shadow film"
    assert [e[2] for e in _clean] == ["engine_shadow"]
    assert not [p for p in os.listdir(tmp_path) if p.startswith("scene_shadow_")]     # it cleans up after itself
    assert eb.shadow(eb.Prepared("on", "b", 7, 5, 12), Db(), src_local=src, variant="preview",
                     finish_factory=finish_factory, verify_factory=None) is None       # only in shadow mode
