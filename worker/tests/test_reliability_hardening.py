"""Python-side reliability and per-call speed (October 2026 audit).

Each block pins one production finding from the 14-day reliability audit:
  * renders issued before analysis finished failed as 'unknown' after a
    pointless retry; they now wait without spending attempts;
  * MCP calls fetched the full index JSON (2-5 MB) per call to compare a sha,
    re-read the same EDL row repeatedly and reconnected to Postgres per job;
  * look_at re-downloaded the ~180 MB proxy whenever a context was evicted;
  * child shorts had no source-audio sidecar, so review_audio refused;
  * motion items a render could not draw vanished silently;
  * lanes without Chromium claimed the motion renderer was available.
"""

import os
import shutil
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools  # noqa: E402
import config  # noqa: E402
import db as dbx  # noqa: E402
import executor_runtime  # noqa: E402
import failure_policy  # noqa: E402
import indexer  # noqa: E402
import io_telemetry  # noqa: E402
import main  # noqa: E402
import mcp_exec  # noqa: E402
import media_cache  # noqa: E402
import motion_engine  # noqa: E402
import motion_layer  # noqa: E402
import renderer  # noqa: E402
import shorts  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


class _Cur:
    def __init__(self, sink, rowcount=1, one=None):
        self.sink, self.rowcount, self.one = sink, rowcount, one

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.sink.append((sql, params))

    def fetchone(self):
        return self.one


class _Conn:
    def __init__(self, rowcount=1, one=None):
        self.sql, self.rowcount, self.one = [], rowcount, one

    def cursor(self):
        return _Cur(self.sql, self.rowcount, self.one)


class _FnDb:
    """Dispatch Db.run by function identity; record every call."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.calls = []

    def run(self, fn, *args, **kwargs):
        self.calls.append(fn)
        if fn in self.handlers:
            handler = self.handlers[fn]
            return handler(*args, **kwargs) if callable(handler) else handler
        raise AssertionError(f"unexpected DB call: {getattr(fn, '__name__', fn)}")


# ── 1. index not ready is a typed prerequisite ─────────────────────────

def test_prerequisite_pending_is_retryable_and_deferred_without_an_attempt():
    err = dbx.PrerequisitePending("still analyzing")
    decision = failure_policy.classify(err, "preview")
    assert decision.kind == "prerequisite_pending" and decision.retryable

    seen = []
    worker_db = _FnDb({dbx.defer_prerequisite_pending:
                       lambda *args: seen.append(args) or True})
    job = {"id": 42, "type": "preview", "attempts": 1}
    assert failure_policy.defer_prerequisite(worker_db, job, err, decision, 3)
    assert seen == [(42, 3, err, config.PREREQUISITE_MAX_DEFERRALS)]
    other = failure_policy.classify(RuntimeError("boom"), "preview")
    assert not failure_policy.defer_prerequisite(worker_db, job, err, other, 3)


def test_deferral_refunds_the_attempt_under_the_lease_and_is_bounded():
    conn = _Conn(rowcount=1)
    assert dbx.defer_prerequisite_pending(conn, 42, 6, RuntimeError("x"), 3)
    sql, params = conn.sql[0]
    assert "attempts = GREATEST(0, attempts - 1)" in sql
    assert "'{prerequisite_wait}', 'true'::jsonb" in sql
    assert "prerequisite_deferrals" in sql
    assert "total_claims = %s" in sql
    assert "total_claims = total_claims" not in sql   # never refunded
    assert sql.count("%s") == len(params) and params[1:] == (42, 6, 3)
    unfenced = _Conn(rowcount=0)
    assert not dbx.defer_prerequisite_pending(unfenced, 42, None, "x", 3)
    sql, params = unfenced.sql[0]
    assert "total_claims = %s" not in sql and sql.count("%s") == len(params)


@pytest.mark.parametrize("types", [["preview", "preview_check", "final"],
                                   ["mcp_tool"], ["index"], ["agent_turn"]])
def test_claim_holds_a_deferred_render_until_analysis_is_not_live(
        monkeypatch, types):
    monkeypatch.setattr(dbx, "claims_column_ready", lambda _c: True)
    monkeypatch.setattr(dbx, "remote_executions_table_ready", lambda _c: True)
    conn = _Conn(one={"id": 7})
    dbx.claim_job(conn, types, config.MAX_ATTEMPTS_MEDIA)
    sql, params = conn.sql[-1]
    assert sql.count("%s") == len(params)
    assert "payload->>'prerequisite_wait'" in sql
    assert "FROM video_jobs prereq" in sql and "prereq.type = 'index'" in sql
    # The floor delay sits right after the busy delay, in SQL order.
    busy = params.index(config.CLOUDFLARE_BUSY_RETRY_DELAY_S)
    assert params[busy + 1] == config.PREREQUISITE_RETRY_DELAY_S
    assert params[busy + 2] == config.MAX_ATTEMPTS_MEDIA
    assert params[busy + 3] == config.STALE_AFTER_S


def test_live_index_job_narrows_to_the_main_video():
    conn = _Conn(one={"id": 9, "state": "running", "progress": 40})
    assert dbx.live_index_job(conn, 5, 11)["id"] == 9
    sql, params = conn.sql[0]
    assert "payload->>'asset_id' = %s" in sql and params == (5, 3, "11")
    assert sql.count("%s") == len(params)


def test_dispatcher_requeues_prerequisite_without_failing(monkeypatch):
    calls = []

    class Db:
        def run(self, fn, *args):
            calls.append(fn.__name__)
            if fn is dbx.defer_prerequisite_pending:
                return True
            if fn is dbx.bump_metric:
                return None
            raise AssertionError(fn.__name__)

    def runner(_db, _job):
        raise dbx.PrerequisitePending("analysis running")

    monkeypatch.setattr(main.remote, "stamp_execution_provider",
                        lambda *_: "local")
    monkeypatch.setattr(main, "_runner_for_job", lambda _job: (runner, "x"))
    main.process_one(Db(), {"id": 77, "type": "preview", "project_id": 3,
                            "attempts": 1, "total_claims": 2,
                            "payload": {}})
    assert calls == ["defer_prerequisite_pending", "bump_metric"]


def test_render_waits_while_the_source_is_being_analyzed():
    original = {"id": 11, "sha256": None, "storage_key": "o.mp4", "meta": {}}
    worker_db = _FnDb({
        dbx.user_is_paid: True, dbx.video_settings: {},
        dbx.get_edl_version: {"json": default_edl(10)},
        dbx.latest_asset: original,
        dbx.live_index_job: {"id": 501, "state": "running", "progress": 37},
    })
    job = {"id": 1, "project_id": 3, "user_id": 4, "type": "preview",
           "payload": {"edl_version": 2}}
    with pytest.raises(dbx.PrerequisitePending) as raised:
        renderer.run_render_job(worker_db, job)
    assert "37% done" in str(raised.value)
    assert raised.value.waiting_on == {"index_job_id": 501}


def test_render_without_live_analysis_fails_once_with_the_reason():
    seen = {"latest": 0}

    def latest(_pid, kind):
        seen["latest"] += 1
        return {"id": 11, "sha256": None, "storage_key": "o.mp4", "meta": {}}

    worker_db = _FnDb({dbx.live_index_job: None, dbx.latest_asset: latest})
    err = renderer._source_not_ready(worker_db, 3, {"id": 11}, "No index")
    assert isinstance(err, dbx.PermanentJobError)
    assert not failure_policy.classify(err, "preview").retryable
    assert "no analysis is running" in str(err)
    # Analysis finished between the reads: wait, do not fail.
    finished = _FnDb({dbx.live_index_job: None,
                      dbx.latest_asset: {"id": 11, "sha256": "abc"},
                      dbx.index_exists: True})
    assert isinstance(renderer._source_not_ready(finished, 3, {"id": 11}, "x"),
                      dbx.PrerequisitePending)
    # A failing state check keeps the historical behaviour.
    broken = _FnDb({dbx.live_index_job: lambda *_: 1 / 0})
    assert type(renderer._source_not_ready(broken, 3, {"id": 11}, "x")) \
        is RuntimeError


# ── 3. MCP per-call cost ───────────────────────────────────────────────

class _Sess:
    def __init__(self, sha, workdir):
        self.sha, self.workdir = sha, str(workdir)
        self.used = time.time()
        self.lock = threading.Lock()
        self.ctx = SimpleNamespace()


def test_cached_session_never_ships_the_index_json(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_exec, "_sessions", {7: _Sess("sha-a", tmp_path)})
    worker_db = _FnDb({dbx.latest_asset: {"id": 1, "sha256": "sha-a"}})
    s = mcp_exec._session(worker_db, {"id": 1}, {"id": 7})
    assert s is mcp_exec._sessions[7]
    assert worker_db.calls == [dbx.latest_asset]       # no get_index_by_sha


def test_session_for_unanalyzed_video_raises_typed_not_ready(monkeypatch):
    monkeypatch.setattr(mcp_exec, "_sessions", {})
    worker_db = _FnDb({dbx.latest_asset: {"id": 1, "sha256": None}})
    with pytest.raises(mcp_exec.IndexNotReady):
        mcp_exec._session(worker_db, {"id": 1}, {"id": 7})
    analyzed_without_row = _FnDb({dbx.latest_asset: {"id": 1, "sha256": "s"},
                                  dbx.get_index_by_sha: None})
    with pytest.raises(mcp_exec.IndexNotReady):
        mcp_exec._session(analyzed_without_row, {"id": 1}, {"id": 7})


def test_mcp_call_before_analysis_returns_retryable_prerequisite(monkeypatch):
    monkeypatch.setattr(mcp_exec, "_sessions", {})
    monkeypatch.setattr(config, "MCP_INDEX_WAIT_S", 0.0)
    progress = []
    worker_db = _FnDb({
        mcp_exec._call_snapshot: {
            "project": {"id": 7, "chat_session_id": 1},
            "original": {"id": 1, "sha256": None}, "billing": None},
        dbx.latest_asset: {"id": 1, "sha256": None},
        dbx.live_index_job: {"id": 900, "state": "running", "progress": 64},
        dbx.set_progress: lambda *a: progress.append(a),
    })
    out = mcp_exec.run_mcp_job(worker_db, {
        "id": 5, "project_id": 7, "user_id": 2,
        "payload": {"tool": "add_zoom", "args": {}}})
    assert out["is_error"] and out["code"] == "index_not_ready"
    assert out["retryable"] is True and out["index_job_id"] == 900
    assert out["tool_outcome"]["status"] == "prerequisite"
    assert out["tool_outcome"]["prerequisite_tool"] == "wait_for_job"
    assert "64% done" in out["text"] and "retry add_zoom" in out["text"]
    assert out["edl_changed"] is False


def test_mcp_call_waits_for_analysis_then_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_exec, "_sessions", {})
    monkeypatch.setattr(config, "MCP_INDEX_WAIT_S", 30.0)
    monkeypatch.setattr(mcp_exec, "_INDEX_POLL_S", 0.01)
    state = {"ready": False, "polls": 0}

    def latest(_pid, _kind):
        state["polls"] += 1
        if state["polls"] >= 3:
            state["ready"] = True
        return {"id": 1, "sha256": "sha" if state["ready"] else None}

    built = []
    monkeypatch.setattr(mcp_exec, "_new_context",
                        lambda *a: built.append(a) or _Sess("sha", tmp_path))
    worker_db = _FnDb({
        dbx.latest_asset: latest,
        dbx.live_index_job: {"id": 900, "state": "running", "progress": 90},
        dbx.set_progress: None,
        dbx.index_exists: lambda sha: bool(sha),
        dbx.get_index_by_sha: {"json": {"video": {"duration": 10}}},
    })
    session, outcome = mcp_exec._session_when_indexed(
        worker_db, {"id": 5}, {"id": 7},
        mcp_exec.IndexNotReady({"id": 1, "sha256": None}), "add_zoom")
    assert outcome is None and session.sha == "sha" and len(built) == 1


def test_warm_mcp_call_needs_few_database_transactions(monkeypatch,
                                                       tmp_path):
    """A warm read-only call: one snapshot, one EDL read, the activity row and
    the preview lookup. It used to be get_project, latest_asset, a 2-5 MB
    get_index_by_sha, user_billing and one latest_edl per helper read."""
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(mcp_exec.llm, "agent_client_for",
                        lambda *_: (object(), "outside-model"))
    boot = _FnDb({dbx.latest_creative_blueprint: None,
                  dbx.user_billing: (True, "ai", False)})
    session = mcp_exec._new_context(
        boot, {"id": 1, "user_id": 4}, {"id": 7, "chat_session_id": 13},
        {"video": {"duration": 10.0}}, "sha-a")
    monkeypatch.setattr(mcp_exec, "_sessions", {7: session})
    reads = []

    def probe_reads(ctx):
        for _ in range(3):
            reads.append(ctx.latest_edl()["version"])
        return f"EDL is v{reads[-1]}."

    monkeypatch.setitem(agent_tools.TOOLS, "probe_reads",
                        (probe_reads, "Read the EDL.", {}))
    monkeypatch.setattr(mcp_exec.agent_loop, "_activity",
                        lambda db, *a, **k: db.run(dbx.add_message))
    worker_db = _FnDb({
        mcp_exec._call_snapshot: {
            "project": {"id": 7, "chat_session_id": 13},
            "original": {"id": 1, "sha256": "sha-a"},
            "billing": (True, "ai", False)},
        dbx.latest_edl: {"version": 4, "json": default_edl(10.0)},
        dbx.add_message: None,
        dbx.find_render_asset: None,
    })
    out = mcp_exec.run_mcp_job(worker_db, {
        "id": 5, "project_id": 7, "user_id": 4,
        "payload": {"tool": "probe_reads", "args": {}}})
    assert out["text"] == "EDL is v4." and reads == [4, 4, 4]
    assert [fn.__name__ for fn in worker_db.calls] == [
        "_call_snapshot", "latest_edl", "add_message", "find_render_asset"]
    assert session.ctx._call_edl_cache is None         # scoped to the call


def test_call_scoped_edl_cache_reads_once_and_never_leaks_mutation():
    rows = {"n": 0}

    def latest(_pid):
        rows["n"] += 1
        return {"version": 4, "json": {"texts": [{"id": "t1"}]}}

    ctx = agent_tools.ToolContext.__new__(agent_tools.ToolContext)
    ctx.db = _FnDb({dbx.latest_edl: latest})
    ctx.project_id = 9123
    ctx._call_edl_cache = None
    ctx.latest_edl()
    ctx.latest_edl()
    assert rows["n"] == 2                              # agent turns: fresh

    ctx.begin_call_edl_cache()
    first = ctx.latest_edl()
    first["json"]["texts"].append({"id": "rejected-edit"})
    second = ctx.latest_edl()
    assert rows["n"] == 3
    assert second["json"]["texts"] == [{"id": "t1"}]
    dbx._bump_edl_write_generation(9123)               # any EDL insert
    ctx.latest_edl()
    assert rows["n"] == 4
    ctx.end_call_edl_cache()
    ctx.latest_edl()
    assert rows["n"] == 5


def test_insert_edl_invalidates_cached_reads():
    before = dbx.edl_write_generation(4321)
    conn = _Conn(one={"version": 2})
    dbx.insert_edl(conn, 4321, {"keep": []}, "agent")
    assert dbx.edl_write_generation(4321) == before + 1


def test_db_round_trips_reach_job_telemetry(monkeypatch):
    token = io_telemetry.begin()

    class Conn:
        closed = False

        def cursor(self):
            return _Cur([])

        def commit(self):
            pass

    db = dbx.Db()
    db._conn = Conn()
    assert db.run(lambda conn: 5) == 5
    db.run(lambda conn: 6)
    io_telemetry.add_db_statement()
    io_telemetry.add_db_connect(0.25)
    row = io_telemetry.finish(token)
    assert row["db_calls"] == 2 and row["db_statements"] == 1
    assert row["db_connects"] == 1 and row["db_connect_s"] == 0.25
    assert row["db_roundtrips_est"] == 1 + 2 * 2
    assert row["downloaded_bytes"] == 0


def test_connections_use_the_counting_cursor(monkeypatch):
    seen = {}

    class Conn:
        autocommit = True

    def fake_connect(url, **kwargs):
        seen.update(kwargs)
        return Conn()

    monkeypatch.setattr(dbx.psycopg2, "connect", fake_connect)
    token = io_telemetry.begin()
    dbx.connect()
    assert io_telemetry.finish(token)["db_connects"] == 1
    assert seen["cursor_factory"] is dbx._MeteredCursor
    assert issubclass(dbx._MeteredCursor, dbx.RealDictCursor)


def test_executor_pools_healthy_connections_between_jobs(monkeypatch):
    monkeypatch.setattr(executor_runtime, "_DB_POOL", [])
    monkeypatch.setattr(executor_runtime, "_DB_POOL_MAX", 2)

    class Conn:
        closed = False
        status = executor_runtime.psycopg2.extensions.STATUS_READY

        def rollback(self):
            pass

    resets = []

    class Db:
        def __init__(self, conn=None):
            self._conn = conn

        def reset(self):
            resets.append(self)

    monkeypatch.setattr(executor_runtime.dbx, "Db", Db)
    healthy = Db(Conn())
    executor_runtime._return_db(healthy)
    assert executor_runtime._borrow_db() is healthy     # reused, no connect
    dead = Db(SimpleNamespace(closed=True))
    executor_runtime._return_db(dead)
    assert resets == [dead] and executor_runtime._DB_POOL == []
    leased = executor_runtime.LeasedDb(1, 2)
    inner = leased._db
    inner._conn = Conn()
    leased.release()
    assert executor_runtime._DB_POOL[0][0] is inner and leased._db is not inner
    executor_runtime._DB_POOL[0] = (inner, time.monotonic() - 10_000)
    assert executor_runtime._borrow_db() is not inner   # stale: closed
    assert resets[-1] is inner


# ── 4. look_at proxy cache ─────────────────────────────────────────────

@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(config, "TOOL_MEDIA_CACHE_MAX_BYTES", 1000)
    monkeypatch.setattr(config, "TOOL_MEDIA_CACHE_MAX_ITEM_BYTES", 600)
    monkeypatch.setattr(config, "TOOL_MEDIA_CACHE_MIN_FREE_BYTES", 100)
    downloads = []
    sizes = {"proxies/1/a.mp4": 400, "proxies/1/b.mp4": 400,
             "proxies/1/c.mp4": 400, "huge.mp4": 700}

    def download_to(key, path, check_capacity=True):
        downloads.append(key)
        with open(path, "wb") as handle:
            handle.write(b"x" * sizes[key])

    monkeypatch.setattr(media_cache.storage, "object_bytes", sizes.get)
    monkeypatch.setattr(media_cache.storage, "download_to", download_to)
    monkeypatch.setattr(media_cache.storage, "free_workdir_bytes",
                        lambda _p=None: 10_000)
    return SimpleNamespace(downloads=downloads, root=tmp_path)


def test_proxy_downloads_once_per_container_across_contexts(cache_env):
    one = media_cache.lease("proxies/1/a.mp4", str(cache_env.root / "s1"),
                            "proxy.mp4")
    two = media_cache.lease("proxies/1/a.mp4", str(cache_env.root / "s2"),
                            "proxy.mp4")
    assert cache_env.downloads == ["proxies/1/a.mp4"]
    assert os.path.samefile(one, two) and one != two
    shutil.rmtree(cache_env.root / "s1")                # context evicted
    three = media_cache.lease("proxies/1/a.mp4", str(cache_env.root / "s3"),
                              "proxy.mp4")
    assert cache_env.downloads == ["proxies/1/a.mp4"] and os.path.exists(three)


def test_cache_is_bounded_and_never_starves_scratch(cache_env, monkeypatch):
    assert media_cache.lease("huge.mp4", str(cache_env.root / "s"), "h.mp4") \
        is None and cache_env.downloads == []
    media_cache.lease("proxies/1/a.mp4", str(cache_env.root / "s"), "a.mp4")
    time.sleep(0.02)
    media_cache.lease("proxies/1/b.mp4", str(cache_env.root / "s"), "b.mp4")
    media_cache.lease("proxies/1/c.mp4", str(cache_env.root / "s"), "c.mp4")
    names = os.listdir(media_cache.cache_dir())
    assert len(names) == 2                               # LRU evicted 'a'
    assert os.path.exists(cache_env.root / "s" / "a.mp4")  # lease survives
    # Low scratch: older entries are evicted to keep the free-space floor...
    monkeypatch.setattr(media_cache.storage, "free_workdir_bytes",
                        lambda _p=None: 450)
    assert media_cache.lease("proxies/1/a.mp4", str(cache_env.root / "t"),
                             "a.mp4")
    assert len(os.listdir(media_cache.cache_dir())) == 2
    # ...and nothing is cached when even an empty cache cannot keep it.
    shutil.rmtree(media_cache.cache_dir())
    monkeypatch.setattr(media_cache.storage, "free_workdir_bytes",
                        lambda _p=None: 350)
    fetched = len(cache_env.downloads)
    assert media_cache.lease("proxies/1/b.mp4", str(cache_env.root / "u"),
                             "b.mp4") is None
    assert len(cache_env.downloads) == fetched


def test_tool_context_proxy_path_uses_the_cache(cache_env, monkeypatch):
    def ctx(name):
        c = agent_tools.ToolContext.__new__(agent_tools.ToolContext)
        c._proxy_local = None
        c.project_id = 1
        c.workdir = str(cache_env.root / name)
        os.makedirs(c.workdir, exist_ok=True)
        c.db = _FnDb({dbx.latest_asset: {"storage_key": "proxies/1/a.mp4"}})
        return c

    assert ctx("x").proxy_path().endswith("proxy.mp4")
    assert ctx("y").proxy_path().endswith("proxy.mp4")
    assert cache_env.downloads == ["proxies/1/a.mp4"]


# ── 2. child shorts and source sound ───────────────────────────────────

def test_review_audio_hears_the_proxy_when_no_sidecar_exists(
        tmp_path, monkeypatch):
    monkeypatch.setattr(agent_tools.llm, "audio_review_available",
                        lambda: True)
    heard = []

    def extract(src, t0, t1, dst):
        heard.append((src, t0, t1))
        raise RuntimeError("stop after extraction")

    monkeypatch.setattr(agent_tools.media, "extract_audio_clip", extract)
    ctx = SimpleNamespace(
        has_main_video=True, duration=30.0, project_id=7,
        workdir=str(tmp_path), proxy_path=lambda: "/cache/proxy.mp4",
        db=_FnDb({dbx.latest_asset: None}))
    out = agent_tools.review_audio(ctx, times=[10.0])
    assert "REJECTED" not in out and "extraction failed" in out
    assert heard == [("/cache/proxy.mp4", 7.0, 13.0)]


def test_source_sound_falls_back_to_a_ranged_original(monkeypatch):
    monkeypatch.setattr(agent_tools.storage, "presign_get",
                        lambda key, expires=0: f"https://r2/{key}")

    def no_proxy():
        raise RuntimeError("no proxy available")

    ctx = SimpleNamespace(project_id=7, proxy_path=no_proxy, db=_FnDb({
        dbx.latest_asset: {"storage_key": "originals/7/a.mov", "meta": {}}}))
    source, label = agent_tools._source_sound_fallback(ctx)
    assert source == "https://r2/originals/7/a.mov" and "original" in label


def test_shorts_children_receive_the_parent_audio_sidecar(monkeypatch):
    original = {"id": 1, "kind": "original", "sha256": "sha",
                "storage_key": "originals/3/a.mp4"}
    proxy = {"id": 2, "kind": "proxy", "storage_key": "proxies/3/sha.mp4"}
    audio = {"id": 3, "kind": "audio", "storage_key": "audio/3/sha.wav"}
    shared = []
    assets = {"original": original, "proxy": proxy, "audio": audio}
    worker_db = _FnDb({
        dbx.get_project: {"id": 3, "chat_session_id": None, "meta": {},
                          "title": "Pod"},
        dbx.latest_asset: lambda _pid, kind: assets.get(kind),
        dbx.get_index_by_sha: {"json": {"video": {"duration": 600.0}}},
        dbx.user_billing: (True, "ai", False),
        dbx.user_credits_balance: 50.0,
        dbx.set_progress: None,
        shorts._find_reference: None,
        shorts._save_shorts_meta: None,
        shorts._create_child: (90, 91),
        shorts._share_asset: lambda child, asset, note: shared.append(
            (child, asset["kind"])),
        shorts._add_short_intro: None,
    })
    monkeypatch.setattr(shorts, "_caller_planned_clips", lambda *a: [
        {"order": 0, "title": "Arc", "start": 10.0, "end": 50.0}])
    monkeypatch.setattr(shorts, "_seed_story_child",
                        lambda *a: (2, "seeded"))
    monkeypatch.setattr(shorts.llm, "set_recorder", lambda *_: None)
    monkeypatch.setattr(shorts.llm, "set_turn_plan", lambda *_: None)
    out = shorts.run_shorts_plan(worker_db, {
        "id": 8, "project_id": 3, "user_id": 4,
        "payload": {"clips": [{"start": 10, "end": 50}]}})
    assert out["clips"] == 1
    assert shared == [(90, "original"), (90, "proxy"), (90, "audio")]


def test_index_cache_hit_registers_the_audio_sidecar(monkeypatch):
    inserted, copied = [], []
    monkeypatch.setattr(indexer.storage, "exists", lambda key: True)
    monkeypatch.setattr(indexer.storage, "copy_object",
                        lambda src, dst: copied.append((src, dst)))
    donor = {"storage_key": "audio/1/sha.wav", "bytes": 10, "duration_s": 60}
    worker_db = _FnDb({
        dbx.latest_asset: None,
        dbx.any_asset_by_sha: donor,
        dbx.insert_asset: lambda *a, **k: inserted.append((a, k)) or 5,
    })
    indexer._ensure_audio(worker_db, 9, "sha", {"duration": 60})
    assert copied == [("audio/1/sha.wav", "audio/9/sha.wav")]
    assert inserted[0][0] == (9, "audio", "audio/9/sha.wav")
    assert inserted[0][1]["sha256"] == "sha"
    # Already registered for these bytes: nothing to do.
    current = _FnDb({dbx.latest_asset: {"sha256": "sha"}})
    indexer._ensure_audio(current, 9, "sha", {"duration": 60})
    assert current.calls == [dbx.latest_asset]
    # Best-effort: a storage failure never fails the index.
    monkeypatch.setattr(indexer.storage, "exists", lambda key: 1 / 0)
    indexer._ensure_audio(worker_db, 9, "sha", {"duration": 60})


# ── 6. motion render warnings ──────────────────────────────────────────

def test_motion_warnings_are_collected_per_render():
    sink = []
    token = motion_layer.collect_warnings(sink)
    try:
        motion_layer.warn("motion 'mg1' skipped: boom")
    finally:
        motion_layer.stop_collecting(token)
    motion_layer.warn("another render's skip")
    assert sink == ["motion 'mg1' skipped: boom"]
    line = agent_tools._motion_warning_line({"motion_warnings": sink})
    assert "MOTION RENDER WARNING" in line and "NOT in this render" in line
    assert agent_tools._motion_warning_line({}) == ""


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
def test_render_without_a_browser_still_renders_and_names_the_skip(
        tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "available", lambda: False)
    monkeypatch.setattr(motion_engine, "unavailable_reason",
                        lambda: "headless Chromium is not installed on this "
                                "render lane")
    src = str(tmp_path / "src.mp4")
    renderer.media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=320x180:r=30:d=4", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=4", "-c:v", "libx264",
                        "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                        src])
    edl = default_edl(4.0)
    edl["motion"] = [{"id": "mg1", "template": "hook_title", "start": 0.5,
                      "end": 2.5, "params": {"text": "Big *idea*"}}]
    edl = validate_edl(edl, 4.0).model_dump()
    sink = []
    token = motion_layer.collect_warnings(sink)
    try:
        duration = renderer.render_edl(
            edl, {"video": {"duration": 4.0}, "words": []}, src,
            str(tmp_path / "out.mp4"), str(tmp_path), True,
            suppress_outro=True)
    finally:
        motion_layer.stop_collecting(token)
    assert duration == pytest.approx(4.0, abs=0.2)
    assert len(sink) == 1 and "mg1" in sink[0]
    assert "headless Chromium is not installed" in sink[0]


# ── 7. motion engine availability ──────────────────────────────────────

def test_availability_requires_the_browser_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "package_installed", lambda: True)
    missing = str(tmp_path / "pw" / "chromium-1148" / "chrome-linux" / "chrome")
    probes = []

    def probe():
        probes.append(1)
        return missing

    monkeypatch.setattr(motion_engine, "_chromium_executable_path", probe)
    motion_engine._reset_availability_cache()
    try:
        assert motion_engine.available() is False
        assert motion_engine.available() is False and len(probes) == 1
        assert "Chromium is not installed" in motion_engine.unavailable_reason()
        with pytest.raises(motion_engine.MotionRenderError,
                           match="Chromium is not installed"):
            motion_engine.render_jobs([object()], str(tmp_path / "o"))
        # Playwright >=1.49 headless launches use the shell beside it.
        shell = tmp_path / "pw" / "chromium_headless_shell-1148" / \
            "chrome-linux" / "headless_shell"
        shell.parent.mkdir(parents=True)
        shell.write_text("#!/bin/sh\n")
        shell.chmod(0o755)
        motion_engine._reset_availability_cache()
        assert motion_engine.available() is True
        assert motion_engine.unavailable_reason() == ""
    finally:
        motion_engine._reset_availability_cache()


def test_a_driver_timeout_is_not_cached_as_unavailable(monkeypatch):
    monkeypatch.setattr(motion_engine, "package_installed", lambda: True)
    answers = [TimeoutError("slow"), "/bin/sh"]

    def probe():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(motion_engine, "_chromium_executable_path", probe)
    motion_engine._reset_availability_cache()
    try:
        assert motion_engine.available() is False
        assert motion_engine.available() is True
    finally:
        motion_engine._reset_availability_cache()


def test_motion_tools_stay_authorable_on_lanes_without_a_browser(
        monkeypatch):
    monkeypatch.setattr(motion_engine, "available", lambda: False)
    monkeypatch.setattr(motion_engine, "package_installed", lambda: True)
    assert not agent_tools._tool_disabled("add_motion_graphic")
    monkeypatch.setattr(motion_engine, "package_installed", lambda: False)
    assert agent_tools._tool_disabled("add_motion_graphic")
