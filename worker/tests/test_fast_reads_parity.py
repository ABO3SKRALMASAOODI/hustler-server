"""MCP fast reads: the backend's answer is the queued answer, byte for byte.

backend/video_services/mcp_fast_reads.py answers the pure reads in
read_tools.FAST_READ_TOOLS inside the backend process instead of queueing a
job for a Cloudflare container. Both paths run here on the same fixture
database (one fake PostgreSQL connection serving the same rows):

  queued: mcp_exec.run_mcp_job on a fresh MCP context, its result stored the
          way video_jobs.result (JSONB) returns it;
  fast:   mcp_fast_reads.answer on the backend's read context.

Every call must produce the identical stored result and the identical
activity-feed row. A call the fast path cannot prove (unknown argument, an
EDL with fields this release does not know, a missing EDL) must return None
so the backend queues it exactly as before. routes/mcp.py turns either
stored result into the reply through one function (_tool_answer), so equal
stored results are equal replies (backend/tests/test_mcp_fast_reads.py pins
that half).
"""

import copy
import importlib.util
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools  # noqa: E402
import config  # noqa: E402
import db as dbx  # noqa: E402
import mcp_exec  # noqa: E402
import read_tools  # noqa: E402

_FAST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                          "..", "backend", "video_services",
                          "mcp_fast_reads.py")
_spec = importlib.util.spec_from_file_location("mcp_fast_reads_under_test",
                                               _FAST_PATH)
fast = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fast)

PROJECT_ID = 3419
USER_ID = 60
SESSION_ID = 9001
CLIP_KEY = "media/3419/clips/broll-park.mp4"


def _words():
    """A 6-minute two-speaker transcript with fillers, missing confidences
    and a phrase the edit uses twice."""
    vocab = ("so the thing about building a company is that you never "
             "really know what is going to work until you try it and "
             "the market tells you").split()
    words, t = [], 0.4
    for i in range(1500):
        w = vocab[i % len(vocab)]
        if i % 97 == 13:
            w = "um"
        dur = 0.18 + (i % 5) * 0.03
        word = {"w": w, "t0": round(t, 2), "t1": round(t + dur, 2),
                "speaker": (i // 40) % 2}
        if i % 11:
            word["confidence"] = round(0.62 + (i % 37) / 100.0, 3)
        if w == "um":
            word["filler"] = True
        words.append(word)
        t += dur + (0.9 if i % 23 == 0 else 0.05)
    return words


def _sentences(words):
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == 12:
            out.append({"id": len(out), "t0": cur[0]["t0"], "t1": cur[-1]["t1"],
                        "speaker": cur[0]["speaker"],
                        "text": " ".join(x["w"] for x in cur)})
            cur = []
    return out


WORDS = _words()
DURATION = round(WORDS[-1]["t1"] + 2.0, 2)
INDEX = {
    "video": {"duration": DURATION, "width": 1920, "height": 1080,
              "fps": 29.97, "has_audio": True},
    "words": WORDS,
    "sentences": _sentences(WORDS),
    "speakers": 2,
    "shots": [{"id": i, "start": round(i * DURATION / 9, 2),
               "end": round((i + 1) * DURATION / 9, 2),
               "caption": ({"action": "two people talk", "setting": "studio",
                            "people": "host and guest",
                            "on_screen_text": "EPISODE 12" if i == 2 else None,
                            "subtitles": i == 5} if i % 3 else {})}
              for i in range(9)],
    "moments": [{"t": round(t * 4.0 + 0.37, 2), "shot": 1 + t // 10,
                 "caption": {
                     "action": ("host speaks" if (t // 7) % 2
                                else "guest laughs"),
                     "people": "two people", "setting": "studio",
                     "subtitles": t % 13 == 0,
                     "on_screen_text": "EP 12" if t % 17 == 0 else None}}
                for t in range(int(DURATION // 4))],
    "perception": {"vb_env_fps": 25.0,
                   "vb_env": [round(0.05 + (i % 9) / 10.0, 2)
                              for i in range(int(DURATION * 25))],
                   "bpm": 0},
    "silences": [[10.0, 11.2]],
    "language": "en",
}
CLIP_INDEX = {"sentences": [{"id": 0, "t0": 0.0, "t1": 2.5,
                             "text": "birds in the park"}], "speakers": 1}

# A valid EDL (default_edl's shape) whose keeps reuse source 20-30 s, so the
# kept transcript reports an edit duplication, with a placed B-roll clip and
# captions.
EDL = dict(
    __import__("schemas").default_edl(DURATION),
    keep=[[2.0, 40.0], [55.5, 90.25], [20.0, 30.0], [140.0, 180.0]],
    inserts=[{"id": "ins1", "kind": "video", "asset_key": CLIP_KEY,
              "at_output_s": 20.0, "duration_s": 3.0}],
    captions={"mode": "from_transcript", "style": {"size": "m"}},
    music=[{"id": "mu1", "storage_key": "media/3419/music/ambient.mp3",
            "start": 0.0, "end": 30.0}])


def _rows(plan="plus_2026", original=True, edl=EDL, preview=True,
          extra_assets=()):
    project = {"id": PROJECT_ID, "user_id": USER_ID, "title": "Founders Pod",
               "chat_session_id": SESSION_ID, "parent_project_id": None,
               "kind": "edit"}
    assets = []
    if original:
        assets.append({"id": 11, "project_id": PROJECT_ID, "kind": "original",
                       "storage_key": "originals/3419/pod.mp4",
                       "sha256": "a" * 64, "duration_s": DURATION,
                       "meta": {"filename": "pod.mp4"}})
    assets.append({"id": 12, "project_id": PROJECT_ID, "kind": "video_clip",
                   "storage_key": CLIP_KEY, "sha256": "c" * 64,
                   "duration_s": 3.0, "meta": {"filename": "Park B-roll.mp4"}})
    assets.extend([
        {"id": 20, "project_id": PROJECT_ID, "kind": "music",
         "storage_key": "media/3419/music/ambient.mp3", "sha256": "d" * 64,
         "duration_s": 182.4,
         "meta": {"filename": "ambient.mp3", "provider": "freemusicarchive",
                  "id": "freemusicarchive:991", "license": "CC BY-NC 4.0",
                  "creator": "Lee Ann", "page_url": "https://fma.example/t/991",
                  "source_audio_stream_status": "probed",
                  "source_has_audio_stream": True,
                  "source_audio_stream_codec": "mp3",
                  "source_audio_stream_channels": 2}},
        {"id": 21, "project_id": PROJECT_ID, "kind": "music",
         "storage_key": "media/3419/music/unknown.wav", "sha256": None,
         "duration_s": None, "meta": {"filename": "unknown.wav"}},
        {"id": 22, "project_id": PROJECT_ID, "kind": "image_ref",
         "storage_key": "media/3419/img/chart.png", "sha256": "e" * 64,
         "duration_s": None,
         "meta": {"filename": "chart.png", "caption": "A bar chart of revenue"}},
        {"id": 23, "project_id": PROJECT_ID, "kind": "video_clip",
         "storage_key": "media/3419/clips/staged.mov", "sha256": "9" * 64,
         "duration_s": 12.0, "meta": {"filename": "staged.mov",
                                      "staged": True}},
        {"id": 24, "project_id": PROJECT_ID, "kind": "video_clip",
         "storage_key": "media/3419/clips/ref.mp4", "sha256": "8" * 64,
         "duration_s": 30.0, "meta": {"filename": "ref.mp4",
                                      "role": "edit_reference"}},
    ])
    if preview:
        assets.append({"id": 55, "project_id": PROJECT_ID, "kind": "render",
                       "storage_key": "renders/3419/v7.mp4",
                       "sha256": "f" * 64, "duration_s": 126.75,
                       "meta": {"variant": "preview", "edl_version": 7,
                                "render_job_id": 900, "quality": "draft",
                                "audio_model_review": False,
                                "motion_warnings": ["mg1 not drawn"]}})
    assets.extend(extra_assets)
    return {
        "project": project, "assets": assets,
        "edl": ({"id": 70, "project_id": PROJECT_ID, "version": 7,
                 "json": edl, "created_by": "mcp"} if edl is not None else None),
        "indexes": {"a" * 64: INDEX, "c" * 64: CLIP_INDEX},
        "user": {"is_subscribed": True, "plan": plan,
                 "trial_status": None, "credits_balance": 120},
        "messages": [],
    }


class _Json:
    def __init__(self, adapted):
        self.adapted = adapted


class FakeConn:
    """One fixture database answering exactly the SQL both paths send."""

    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def cursor(self, *a, **k):
        return _Cursor(self)

    def rollback(self):
        pass

    def commit(self):
        pass


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)

    def execute(self, sql, params=()):
        sql = " ".join(str(sql).split())
        rows = self.conn.rows
        self.conn.statements.append(sql)
        params = tuple(params or ())
        out = None
        assets = rows["assets"]
        if sql.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO",
                           "SET LOCAL")):
            out = []
        elif sql == "SELECT * FROM projects WHERE id = %s":
            out = [dict(rows["project"])] if params[0] == PROJECT_ID else []
        elif sql.startswith("SELECT * FROM assets WHERE project_id = %s AND "
                            "kind = %s ORDER BY id DESC"):
            out = [dict(a) for a in reversed(assets) if a["kind"] == params[1]][:1]
        elif sql.startswith("SELECT * FROM assets WHERE project_id = %s AND "
                            "kind = ANY(%s) ORDER BY id DESC LIMIT %s"):
            out = [copy.deepcopy(a) for a in sorted(
                assets, key=lambda a: a["id"], reverse=True)
                if a["kind"] in params[1]][:params[2]]
        elif sql.startswith("SELECT * FROM assets WHERE project_id = %s AND "
                            "storage_key = %s"):
            out = [dict(a) for a in reversed(assets)
                   if a["storage_key"] == params[1]][:1]
        elif sql.startswith("SELECT * FROM assets WHERE project_id = %s AND "
                            "kind = 'render' AND meta->>'variant' = %s"):
            out = [copy.deepcopy(a) for a in reversed(assets)
                   if a["kind"] == "render"
                   and a["meta"].get("variant") == params[1]
                   and int(a["meta"].get("edl_version")) == params[2]][:1]
        elif sql.startswith("SELECT is_subscribed, plan, trial_status FROM "
                            "users"):
            out = [dict(rows["user"])]
        elif sql == "SELECT credits_balance FROM users WHERE id = %s":
            out = [{"credits_balance": rows["user"]["credits_balance"]}]
        elif sql == "SELECT * FROM indexes WHERE video_sha256 = %s":
            idx = rows["indexes"].get(params[0])
            out = ([{"video_sha256": params[0], "json": copy.deepcopy(idx)}]
                   if idx is not None else [])
        elif sql.startswith("SELECT xmin::text AS gen FROM indexes"):
            out = [{"gen": "4242"}] if params[0] in rows["indexes"] else []
        elif sql.startswith("SELECT xmin::text AS gen, (json->%s)::text"):
            *keys, sha = params
            idx = rows["indexes"].get(sha)
            row = {"gen": "4242"}
            for i, key in enumerate(keys):
                row[f"s{i}"] = (json.dumps(idx[key]) if idx and key in idx
                                else None)
            out = [row] if idx is not None else []
        elif sql.startswith("SELECT pg_column_size(json) AS size FROM indexes"):
            out = [{"size": 2048}]
        elif sql.startswith("SELECT * FROM edls WHERE project_id = %s ORDER BY "
                            "version DESC"):
            out = [copy.deepcopy(rows["edl"])] if rows["edl"] else []
        elif sql.startswith("INSERT INTO chat_messages (session_id, role, "
                            "content, meta)"):
            session, role, content, meta = params
            meta = getattr(meta, "adapted", meta)
            rows["messages"].append((session, role, content,
                                     json.loads(json.dumps(meta))))
            out = [{"id": len(rows["messages"])}]
        elif sql.startswith("SELECT meta->'creative_blueprint' AS blueprint"):
            out = []
        else:
            raise AssertionError(f"unexpected SQL in fixture: {sql[:200]}")
        self._result = out
        self.rowcount = len(out)


class WorkerDb:
    """The executor's leased handle on the same fixture database."""

    def __init__(self, conn):
        self.conn = conn

    def run(self, fn, *args, **kwargs):
        if fn is dbx.user_credits_balance:
            return float(self.conn.rows["user"]["credits_balance"])
        return fn(self.conn, *args, **kwargs)

    def reset(self):
        pass


@pytest.fixture(autouse=True)
def _fresh_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(mcp_exec.llm, "agent_client_for",
                        lambda *_a, **_k: (None, "editor-model"))
    mcp_exec._sessions.clear()
    yield
    mcp_exec._sessions.clear()


def queued(rows, tool, args):
    """mcp_exec on a fresh context; its result as video_jobs returns it."""
    conn = FakeConn(rows)
    job = {"id": 74001, "type": "mcp_tool", "project_id": PROJECT_ID,
           "user_id": USER_ID, "attempts": 1, "total_claims": 1,
           "payload": {"tool": tool, "args": copy.deepcopy(args),
                       "mutation": False}}
    mcp_exec._sessions.clear()
    result = mcp_exec.run_mcp_job(WorkerDb(conn), job)
    return fast.stored_form(result), rows["messages"][-1]


def fast_answer(rows, tool, args):
    conn = FakeConn(rows)
    result = fast.answer(conn, USER_ID, dict(rows["project"]), PROJECT_ID,
                         tool, copy.deepcopy(args))
    message = rows["messages"][-1] if result is not None else None
    return result, message


def _comparable(message):
    session, role, content, meta = message
    meta = dict(meta)
    served_by = meta.pop("served_by", None)
    meta.pop("elapsed_ms", None)
    return (session, role, content, meta), served_by


CALLS = [
    ("get_words", {}),
    ("get_words", {"start": 10, "end": 30}),
    ("get_words", {"start": "12.5", "end": 40.25}),
    ("get_words", {"start": 300, "end": 9999}),
    ("get_words", {"start": 30, "end": 10}),
    ("get_words", {"start": 0, "end": 200}),
    ("get_transcript", {}),
    ("get_transcript", {"start": 0, "end": 60}),
    ("get_transcript", {"asset_key": "main"}),
    ("get_transcript", {"asset_key": CLIP_KEY}),
    ("get_transcript", {"asset_key": "media/3419/none.mp4"}),
    ("get_kept_transcript", {}),
    ("search_transcript", {"query": "the market"}),
    ("search_transcript", {"query": "markte tells"}),
    ("search_transcript", {"query": "zebra crossing"}),
    ("search_transcript", {"query": ""}),
    ("get_shots", {}),
    ("get_shots", {"start": 60, "end": 200}),
    ("get_edl", {}),
    ("get_edl", {"sections": ["keep", "inserts"]}),
    ("get_edl", {"compact": True}),
    ("get_edl", {"offset": 1, "limit": 2}),
    ("list_assets", {}),
    ("list_assets", {"kind": "all"}),
    ("list_assets", {"kind": "clips"}),
    ("list_assets", {"kind": "\"image\""}),
    ("list_assets", {"kind": "render"}),
    ("list_assets", {"kind": "nonsense"}),
    ("list_motion_templates", {}),
    ("list_motion_templates", {"category": "callout"}),
    ("list_motion_templates", {"category": "no-such-category"}),
]


@pytest.mark.parametrize("tool,args", CALLS)
def test_fast_read_is_the_queued_answer(tool, args):
    rows_q, rows_f = _rows(), _rows()
    expected, queued_message = queued(rows_q, tool, args)
    got, fast_message = fast_answer(rows_f, tool, args)
    assert got is not None, "the fast path must answer this read"
    assert json.dumps(got, ensure_ascii=False) == json.dumps(
        expected, ensure_ascii=False)
    queued_row, _ = _comparable(queued_message)
    fast_row, served_by = _comparable(fast_message)
    assert fast_row == queued_row
    assert served_by == "backend"


@pytest.mark.parametrize("plan,original,preview", [
    ("free", True, False),
    ("ai_pro", True, True),
    ("plus_2026", False, False),          # a canvas program: no main video
])
def test_parity_across_plans_previews_and_canvas_programs(plan, original,
                                                          preview):
    for tool, args in (("get_words", {"start": 5, "end": 25}),
                       ("get_edl", {}), ("get_shots", {}),
                       ("get_kept_transcript", {})):
        rows_q = _rows(plan=plan, original=original, preview=preview)
        rows_f = _rows(plan=plan, original=original, preview=preview)
        expected, queued_message = queued(rows_q, tool, args)
        got, fast_message = fast_answer(rows_f, tool, args)
        assert got == expected and list(got) == list(expected), (tool, plan)
        assert _comparable(fast_message)[0] == _comparable(queued_message)[0]
        assert ("metered_contract" in got) == (plan in read_tools.METERED_PLANS)
        assert ("preview" in got) == preview


@pytest.mark.parametrize("tool,args", [
    ("get_words", {"start": 1, "foo": 2}),     # the tool takes no foo
    ("get_words", {"start": "soon"}),          # the tool raises
    ("get_transcript", {"ctx": 1}),
    ("look_at", {"times": [1]}),               # not a pure read
    ("get_video_info", {}),
])
def test_anything_unproven_is_queued_instead(tool, args):
    rows = _rows()
    got, _message = fast_answer(rows, tool, args)
    assert got is None
    assert rows["messages"] == []


def test_an_edl_from_a_newer_release_is_queued_like_the_skew_guard_would():
    rows = _rows(edl=dict(EDL, future_device={"kind": "lens"}))
    assert read_tools.edl_problem(EDL) is None, read_tools.edl_problem(EDL)
    got, _message = fast_answer(rows, "get_edl", {})
    assert got is None


def test_a_project_without_an_edl_is_queued_because_the_executor_writes_one():
    got, _message = fast_answer(_rows(edl=None), "get_words", {})
    assert got is None


def test_an_index_that_changes_while_it_is_read_is_queued(monkeypatch):
    rows = _rows()
    conn = FakeConn(rows)
    real = _Cursor.execute

    def changed(self, sql, params=()):
        real(self, sql, params)
        if " ".join(str(sql).split()).startswith(
                "SELECT xmin::text AS gen, (json->%s)::text"):
            self._result[0]["gen"] = "4243"

    monkeypatch.setattr(_Cursor, "execute", changed)
    fast._cache.clear()
    assert fast.answer(conn, USER_ID, dict(rows["project"]), PROJECT_ID,
                       "get_words", {}) is None
    assert any(s.startswith("ROLLBACK TO SAVEPOINT") for s in conn.statements)


def test_index_sections_are_cached_by_row_version_and_bounded():
    fast._cache.clear()
    rows = _rows()
    conn = FakeConn(rows)
    assert fast.answer(conn, USER_ID, dict(rows["project"]), PROJECT_ID,
                       "get_words", {"start": 1, "end": 5}) is not None
    fetches = [s for s in conn.statements
               if s.startswith("SELECT xmin::text AS gen, (json")]
    assert len(fetches) == 1
    conn2 = FakeConn(_rows())
    assert fast.answer(conn2, USER_ID, dict(rows["project"]), PROJECT_ID,
                       "get_words", {"start": 5, "end": 9}) is not None
    # Warm: one version probe, no section transfer.
    assert not [s for s in conn2.statements
                if s.startswith("SELECT xmin::text AS gen, (json")]
    assert fast._cache_size <= fast.CACHE_BYTES
    # Only the sections the tool reads cross the wire.
    assert all(key in {"video", "words", "speakers"}
               for (_sha, _gen, key) in fast._cache)


def test_stored_form_matches_postgres_jsonb_key_order():
    """Observed on production video_jobs rows (16 Oct): JSONB returns object
    keys shorter first, then bytewise."""
    stored = fast.stored_form({
        "text": "x", "visual_evidence": read_tools.visual_evidence(),
        "edl_version": 3, "edl_changed": False, "is_error": False,
        "tool_outcome": {"status": "success", "message": "m", "evidence": {},
                         "retryable": False, "idempotent": True,
                         "safe_fallback": None, "state_changed": False,
                         "affected_ranges": [], "prerequisite_tool": None,
                         "corrected_argument_guidance": None},
        "preview": {"render_type": "complete_preview", "render_job_id": 1,
                    "quality": "draft", "sha256": "s", "edl_version": 3,
                    "duration_s": 1.0, "audio_model_review": False,
                    "asset_id": 2}})
    assert list(stored) == ["text", "preview", "is_error", "edl_changed",
                            "edl_version", "tool_outcome", "visual_evidence"]
    assert list(stored["tool_outcome"]) == [
        "status", "message", "evidence", "retryable", "idempotent",
        "safe_fallback", "state_changed", "affected_ranges",
        "prerequisite_tool", "corrected_argument_guidance"]
    assert list(stored["visual_evidence"]) == [
        "remaining", "available_before", "publish_attempts",
        "created_this_call", "published_this_call"]
    assert list(stored["preview"]) == [
        "sha256", "quality", "asset_id", "duration_s", "edl_version",
        "render_type", "render_job_id", "audio_model_review"]


def test_read_tools_are_the_executor_tools_not_copies():
    for name in read_tools.FAST_READ_TOOLS:
        assert agent_tools.TOOLS[name][0] is read_tools.FAST_READ_FUNCTIONS[name]
    assert agent_tools._cap is read_tools._cap
    assert dbx.latest_edl is read_tools.latest_edl
    assert config.TRANSCRIPT_CHAR_BUDGET == read_tools.TRANSCRIPT_CHAR_BUDGET
    assert config.VISUAL_SAMPLE_S == read_tools.VISUAL_SAMPLE_S


def test_the_fast_path_module_imports_nothing_heavy():
    """The backend loads it standalone on Render: no media, model, browser
    or worker database stack."""
    source = open(_FAST_PATH).read() + open(read_tools.__file__).read()
    for heavy in ("cv2", "numpy", "PIL", "ffmpeg", "playwright", "torch",
                  "import db", "import config", "import agent_tools",
                  "import storage", "subprocess"):
        assert not re.search(rf"^\s*(import|from) {heavy}\b", source,
                             re.M) or heavy.startswith("import "), heavy
    for module in ("agent_tools", "db", "config", "storage", "llm",
                   "renderer", "media"):
        assert not re.search(rf"^(import|from) {module}\b", source, re.M), module
