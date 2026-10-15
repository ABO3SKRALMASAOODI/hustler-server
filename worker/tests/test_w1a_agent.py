"""Track 1A: the behind-subject verbs with the program matte on.

add_text_behind (and add_motion_graphic(layer='behind_subject'), which
shares _measure_subject_matte) on a project whose person matte is measured
in source space (matte.person chunks in a local store, tests/w1a_support):

  * a window over a CUT or a SPEED RAMP — refused in Wave 0 — is written
    through the program matte, measured or "measuring (~N s)";
  * a window under a crop that FOLLOWS the speaker keeps its measured clip
    for the numbers and says the program matte draws it;
  * where nothing could ever measure the program matte (no proxy to read,
    nothing measured) the Wave 0 rules and refusals stand
    (tests/test_text_behind.py keeps proving those);
  * apply_edit_batch reaches the same item a stale MCP client writes.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import w1a_support as sup  # noqa: E402


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """A synthetic source with a cut at 4.0 s, its chunks measured."""
    import media
    import sidecars
    d = tmp_path_factory.mktemp("w1a_agent")
    src = sup.make_source(str(d / "src.mp4"), cut_at=4.0)
    info = media.probe(src)
    sha = media.sha256_file(src)
    ref = sidecars.source_from(sha, src, info, 0, [4.0])
    store = str(d / "store")
    sup.write_chunks(store, ref, cut_at=4.0)
    return {"src": src, "info": info, "sha": sha, "store": store,
            "dir": str(d)}


class _Db:
    def __init__(self, assets):
        self.assets = assets

    def run(self, fn, *args, **kw):
        import db as dbx
        if fn is dbx.latest_asset:
            return self.assets.get(args[1])
        return None


class _Ctx:
    def __init__(self, p, edl, workdir):
        from schemas import validate_edl
        info = p["info"]
        self.project_id = 7
        self.workdir = workdir
        self.duration = float(info["duration"])
        self.index = {"video": {"duration": info["duration"],
                                "width": info["width"],
                                "height": info["height"],
                                "fps": info["fps"]},
                      "shots": [{"start": 4.0}],
                      "words": [], "sentences": []}
        self.has_main_video = True
        self._orig_sha = p["sha"]
        self._proxy = p["src"]
        self.db = _Db({
            "original": {"sha256": p["sha"], "storage_key": p["src"]},
            "proxy": {"storage_key": p["src"], "width": info["width"],
                      "height": info["height"], "fps": info["fps"],
                      "duration_s": info["duration"]}})
        self.job = {"user_id": 1}
        self.written = []
        self._edl = validate_edl(edl, self.duration).model_dump()

    def proxy_path(self):
        return self._proxy

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        from schemas import validate_edl
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


@pytest.fixture
def live_store(project, monkeypatch):
    import agent_tools
    import sidecars
    sidecars.use_store(sidecars.LocalStore(project["store"]))
    put = []
    monkeypatch.setattr(agent_tools.storage, "exists", lambda k: False)
    monkeypatch.setattr(agent_tools.storage, "upload_file",
                        lambda path, key, ct: put.append(key))
    monkeypatch.setattr(agent_tools.remote, "matte_available", lambda: False)
    yield put
    sidecars.use_store(None)


def test_a_window_over_a_cut_goes_behind_through_the_program_matte(
        project, live_store, tmp_path):
    import agent_tools
    import matte_track
    ctx = _Ctx(project, {"keep": [[0.0, 3.0], [4.5, 8.0]]}, str(tmp_path))
    res = agent_tools.add_text_behind(ctx, "ACROSS", 2.0, duration_s=3.0)
    assert res.startswith("EDL v"), res
    tx = ctx._edl["texts"][0]
    assert tx["behind"]["asset_key"] == matte_track.PROGRAM_KEY
    assert tx["behind"]["src_start"] == 2.0 and tx["behind"]["src_end"] == 6.5
    assert "already measured on the source" in res
    assert live_store == []                 # no clip measured or uploaded


def test_a_window_over_a_speed_ramp_goes_behind(project, live_store,
                                                tmp_path):
    import agent_tools
    import matte_track
    ctx = _Ctx(project, {"keep": [[0.0, 8.0]],
                         "speed": [{"id": "sp1", "start": 0.5, "end": 3.0,
                                    "factor": 2.0}]}, str(tmp_path))
    res = agent_tools.add_text_behind(ctx, "FAST", 0.6, duration_s=1.0)
    assert res.startswith("EDL v"), res
    assert ctx._edl["texts"][0]["behind"]["asset_key"] == \
        matte_track.PROGRAM_KEY


def test_a_follow_crop_keeps_its_clip_and_names_the_program_matte(
        project, live_store, tmp_path, monkeypatch):
    """A crop that follows the speaker across the window: the clip is
    measured (the numbers in the reply) and the program matte draws it."""
    import agent_tools
    import config
    # the clip's numbers from the photometric engine (a white rectangle is
    # no person to RVM or u2net, wherever their model files are installed)
    monkeypatch.setattr(config, "MATTE_RVM_MODEL", "")
    monkeypatch.setattr(config, "MATTE_SEG_MODEL", "")
    edl = {"keep": [[0.0, 3.8]],
           "frame": {"ratio": "9:16", "mode": "crop",
                     "follow": [{"t0": 0.0, "t1": 4.0,
                                 "k": [[0.0, 0.2, 0.5], [3.8, 0.7, 0.5]]}]}}
    ctx = _Ctx(project, edl, str(tmp_path))
    res = agent_tools.add_text_behind(ctx, "FOLLOW", 0.5, duration_s=2.5)
    assert res.startswith("EDL v"), res
    tx = ctx._edl["texts"][0]
    assert tx["behind"]["asset_key"].startswith("matte/7/")
    assert "PROGRAM matte" in res and "follows the speaker" in res
    assert live_store, "the clip was measured for its numbers"


def test_a_window_not_measured_yet_says_measuring(project, live_store,
                                                  tmp_path, monkeypatch):
    """A window whose chunks nobody has measured: the write asks for them
    (interactive priority), never waits, and says so."""
    import agent_tools
    import sidecars
    launched = []

    class _L:
        def submit(self, handle, source, chunks, **kw):
            launched.extend(chunks)

        def inflight(self, key):
            return False
    monkeypatch.setattr(sidecars, "_LAUNCHER", _L())
    monkeypatch.setattr(sidecars, "_launcher", lambda: sidecars._LAUNCHER)
    empty = os.path.join(str(tmp_path), "empty_store")
    os.makedirs(empty, exist_ok=True)
    sidecars.use_store(sidecars.LocalStore(empty))
    ctx = _Ctx(project, {"keep": [[0.0, 3.0], [4.5, 8.0]]}, str(tmp_path))
    res = agent_tools.add_text_behind(ctx, "LATER", 2.0, duration_s=3.0)
    assert res.startswith("EDL v"), res
    assert "MEASURING" in res and "~" in res
    assert launched, "the chunks under the window were requested"


def test_the_program_matte_off_keeps_the_wave0_refusal(project, live_store,
                                                       tmp_path, monkeypatch):
    import agent_tools
    import matte_track
    monkeypatch.setattr(matte_track, "LIVE", False)
    monkeypatch.delenv("VALMERA_FX_PREVIEW", raising=False)
    ctx = _Ctx(project, {"keep": [[0.0, 3.0], [4.5, 8.0]]}, str(tmp_path))
    res = agent_tools.add_text_behind(ctx, "X", 2.0, duration_s=3.0)
    assert res.startswith("REJECTED") and "CUT inside" in res


def test_matte_status_reads_the_windows_in_use(project, live_store):
    import matte_track
    from schemas import validate_edl
    edl = validate_edl({"keep": [[0.0, 3.0], [4.5, 8.0]], "texts": [{
        "id": "t1", "text": "A", "start": 2.0, "end": 5.0,
        "behind": {"asset_key": matte_track.PROGRAM_KEY, "src_start": 2.0,
                   "src_end": 6.5, "fp": "program-matte"}}]},
        8.0).model_dump()
    index = {"video": {"duration": 8.0, "width": 640, "height": 360,
                       "fps": 25.0, "sha256": project["sha"]},
             "shots": [{"start": 4.0}]}
    st = matte_track.matte_status(edl, index)
    assert st["status"] == "ready", st
    assert st["windows"] and all(w["ok"] for w in st["windows"])
    assert matte_track.matte_status(
        {"keep": [[0.0, 8.0]]}, index)["status"] == "unused"
