"""Ticket #45 / #39 / #40 (30 Oct 2026): an MCP preview is judged by its facts.

Project 2842 (paying MCP user 2007): watch_video refused the customer's own
v1 preview ("created without the MCP deterministic-only receipt") and
download_url refused the changed-section render render_preview had produced
seconds earlier. The deterministic-only policy was decided by the
``audio_model_review`` STAMP: the changed-section proof job never carried the
MCP owner's policy (688 MCP proofs in 30 days, all stamped true), and every
Studio preview carries the Studio's default stamp, although the listening
model only ever hears the excerpts recorded in listen_keys/listen_clips and
none were cut. These tests walk every path: render_preview (proof and
complete) -> watch_video / download_url, across EDL versions and kinds, plus
the two duplicate-render paths the stamp caused.
"""

import pytest

import agent_tools
import db as dbx
import mcp_media
import read_tools
import renderer
from schemas import default_edl


MB = 1048576


def _asset(asset_id=19359, version=1, stamp=True, keys=(), lists=True,
           job=47562, variant="preview", duration=2460.36):
    meta = {"variant": variant, "edl_version": version,
            "audio_model_review": stamp, "render_job_id": job,
            "quality": "draft"}
    if lists:
        meta["listen_keys"] = list(keys)
        meta["listen_clips"] = [{"key": k} for k in keys]
    if stamp is None:
        meta.pop("audio_model_review")
    return {"id": asset_id, "storage_key": f"media/2842/{asset_id}.mp4",
            "duration_s": duration, "height": 540, "fps": 25.0,
            "bytes": 160 * MB, "sha256": "ab" * 32, "meta": meta}


# ── the one rule ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("meta, policy", [
    ({"audio_model_review": False}, "deterministic_only"),
    ({"audio_model_review": False, "listen_keys": [], "listen_clips": []},
     "deterministic_only"),
    ({"audio_model_review": False, "listen_keys": ["l.mp3"]}, None),
    ({"audio_model_review": True, "listen_keys": [], "listen_clips": []},
     "no_listening_excerpts"),
    ({"listen_keys": [], "listen_clips": []}, "no_listening_excerpts"),
    ({"audio_model_review": True, "listen_keys": ["l.mp3"],
      "listen_clips": [{"key": "l.mp3"}]}, None),
    ({"audio_model_review": True}, None),          # lists never recorded
    ({}, None),
])
def test_listener_policy_is_the_fact_not_the_stamp(meta, policy):
    assert read_tools.listener_policy(meta) == policy
    assert dbx.listener_policy is read_tools.listener_policy


# ── watch_video ──────────────────────────────────────────────────────────

class _Db:
    def __init__(self, asset):
        self.asset = asset

    def run(self, fn, *_args):
        if fn is dbx.find_render_asset:
            return self.asset
        raise AssertionError(f"unexpected DB call {fn}")


class _Ctx:
    project_id = 2842
    workdir = "/nonexistent"
    job = {"user_id": 2007}

    def __init__(self, asset, version=1):
        self.pending_images = []
        self.db = _Db(asset)
        self.version = version

    def latest_edl(self):
        return {"version": self.version, "json": {}}


def test_watch_video_hands_over_the_studio_preview_no_listener_heard():
    """14:26:15, ticket #40: watch_video(kind='timeline', start=20, end=30,
    render=false) on the customer's v1 Studio preview."""
    asset = _asset()
    got, version, note = mcp_media._preview_for_watching(_Ctx(asset), False)
    assert (got, version, note) == (asset, 1, "")
    receipt = mcp_media._preview_receipt(asset, version)
    assert receipt["audio_model_review"] is False
    assert receipt["listener_policy"] == "no_listening_excerpts"
    assert receipt["asset_id"] == 19359 and receipt["render_job_id"] == 47562
    out = mcp_media.prepare(_Ctx(asset), {"render": False}, 12 * MB)
    assert not out.get("is_error"), out["text"]
    assert out["preview"]["listener_policy"] == "no_listening_excerpts"


@pytest.mark.parametrize("asset", [
    _asset(keys=["media/2842/x_l0.mp3"]),             # excerpts were cut
    _asset(lists=False),                              # pre-recording render
    _asset(stamp=False, keys=["media/2842/x_l0.mp3"]),
    _asset(stamp=False, job=None),                    # no lineage
], ids=["excerpts", "unrecorded", "false-with-excerpts", "no-lineage"])
def test_watch_video_still_refuses_what_a_listener_may_have_heard(asset):
    out = mcp_media.prepare(_Ctx(asset), {"render": False}, 12 * MB)
    assert out["is_error"] is True
    assert "deterministic-only" in out["text"]
    assert "render=true" in out["text"]


def test_watch_video_render_true_accepts_a_joined_studio_render(monkeypatch):
    """render=true joins whatever preview job the DB hands back; a Studio
    render without excerpts (the join below) is accepted, not refused as
    'finished without the required audio_model_review=false receipt'."""
    studio = _asset(asset_id=38127, version=3, job=126609)

    class JoinDb:
        def __init__(self):
            self.seen = 0

        def run(self, fn, *args):
            if fn is dbx.find_render_asset:
                self.seen += 1
                return None if self.seen == 1 else studio
            if fn is dbx.latest_render:
                return None
            if fn is dbx.get_or_enqueue_preview_job:
                assert args[2]["audio_model_review"] is False
                return 126609, False
            if fn is dbx.get_job:
                return {"id": 126609, "state": "done"}
            raise AssertionError(fn)

    ctx = _Ctx(None, version=3)
    ctx.db = JoinDb()
    monkeypatch.setattr(mcp_media.time, "sleep", lambda _s: None)
    asset, version, _note = mcp_media._preview_for_watching(ctx, True)
    assert (asset["id"], version) == (38127, 3)


# ── render_preview(complete=false): the proof job carries the policy ───

def _proof_ctx(policy):
    payloads = []

    class Db:
        @staticmethod
        def run(fn, *args):
            if fn is dbx.get_or_enqueue_preview_check_job:
                payloads.append(args[-1])
                return 126608, True
            if fn is dbx.get_job:
                return {"state": "done", "result": {
                    "duration_s": 346.141, "render_asset_id": 38126,
                    "changed_ranges": [[0, 8]]}}
            raise AssertionError(fn)

    class Ctx:
        checked_versions = set()
        spec_preview_check_jobs = {}
        project_id = 2842
        job = {"id": 126607, "user_id": 2007,
               "payload": {"execution_policy": "redesign"}}
        db = Db()
        last_preview_check = None
        last_visual_critic = None
        last_preview_check_critic = None
        _proof_ranges_by_version = {}

    ctx = Ctx()
    if policy is not None:
        ctx.audio_model_review = policy
    return ctx, payloads


@pytest.mark.parametrize("policy, expected", [(False, False), (True, True),
                                              (None, True)])
def test_changed_section_proof_carries_the_owners_listening_policy(
        monkeypatch, policy, expected):
    """14:47:19: render_preview(complete=false) from MCP enqueued proof job
    126608 without the MCP policy, so the renderer stamped asset 38126
    audio_model_review=true and download_url refused it."""
    monkeypatch.setattr(agent_tools, "_proof_pages",
                        lambda ranges: [list(ranges)])
    monkeypatch.setattr(agent_tools, "_queue_check_frames", lambda *_a: False)
    monkeypatch.setattr(agent_tools, "_preview_critic_report",
                        lambda *_a: None)
    monkeypatch.setattr(agent_tools.time, "sleep", lambda _s: None)
    ctx, payloads = _proof_ctx(policy)
    row = {"version": 3, "json": default_edl(40.0)}
    out = agent_tools._run_changed_preview_check(ctx, row, [], [[0, 8]])
    assert "asset 38126" in out
    assert payloads[0]["audio_model_review"] is expected
    # The signature (and with it the proof set) is policy-specific, so an
    # MCP proof never inherits a Studio proof's failure record.
    assert payloads[0]["render_signature"] == agent_tools._render_signature(
        row, "preview_check", [[0, 8]], audio_model_review=expected)
    # The renderer reads the same policy the payload now carries.
    assert renderer._audio_model_review_requested(payloads[0]) is expected


def test_speculative_proof_carries_the_policy_too(monkeypatch):
    payloads = []

    class Db:
        @staticmethod
        def run(fn, *args):
            if fn is dbx.get_or_enqueue_preview_check_job:
                payloads.append(args[-1])
                return 5, True
            raise AssertionError(fn)

    class Ctx:
        write_calls = ["add_motion_graphic"]
        rendered_versions = set()
        spec_enqueued = set()
        project_id = 2842
        job = {"id": 9, "user_id": 2007, "payload": {}}
        db = Db()
        audio_model_review = False

        def latest_edl(self):
            return {"version": 4, "json": default_edl(40.0)}

    monkeypatch.setattr(agent_tools.config, "SPECULATIVE_PREVIEWS", True)
    monkeypatch.setattr(agent_tools, "_verify_plan_for", lambda *_a: [])
    monkeypatch.setattr(agent_tools, "_change_check_ranges",
                        lambda *_a: ([[0, 8]], None))
    agent_tools.speculative_preview(Ctx())
    assert payloads and payloads[0]["audio_model_review"] is False


# ── the renderer's cache and the preview-job join ───────────────────────

def test_mcp_request_reuses_a_studio_render_no_listener_heard():
    edl = default_edl(40.0)
    studio = _asset()["meta"]
    assert renderer._audio_model_review_cache_compatible(False, edl, studio)
    assert not renderer._audio_model_review_cache_compatible(
        False, edl, _asset(keys=["l.mp3"])["meta"])
    assert not renderer._audio_model_review_cache_compatible(
        False, edl, _asset(lists=False)["meta"])
    # The Studio side is unchanged: with designed audio it still wants its
    # excerpts, so a deterministic-only render is not reused for it.
    music = dict(edl, music=[{"id": "m1", "start": 0, "end": 8}])
    assert not renderer._audio_model_review_cache_compatible(
        True, music, {"audio_model_review": False})


class _JoinCursor:
    """get_or_enqueue_preview_job's statements, against one running Studio
    preview job (126609) of v3 and v3's EDL."""

    def __init__(self, edl, studio_job=True):
        self.edl = edl
        self.studio_job = studio_job
        self.inserted = None
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.result = None
        if "INSERT INTO video_jobs" in s:
            self.inserted = params
            self.result = {"id": 126611}
        elif "FROM video_jobs" in s and "state IN ('queued', 'running')" in s:
            policy = params[2] if len(params) > 3 else True
            if self.studio_job and policy is True:
                self.result = {"id": 126609}
        elif s.startswith("SELECT json FROM edls"):
            self.result = {"json": self.edl}

    def fetchone(self):
        return self.result


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_mcp_preview_joins_the_running_studio_render_of_that_version():
    """14:49:15: the AI app's job 126611 rendered v3 of a 41-minute lecture
    while the Studio's job 126609 was rendering the same v3 (25 such
    concurrent pairs in 30 days)."""
    cur = _JoinCursor(default_edl(2460.36))
    job_id, created = dbx.get_or_enqueue_preview_job(
        _Conn(cur), 2842, 2007,
        {"edl_version": 3, "audio_model_review": False, "quality": "draft",
         "source": "agent_preview"})
    assert (job_id, created) == (126609, False)
    assert cur.inserted is None


@pytest.mark.parametrize("field", ["music", "sfx", "voiceover"])
def test_designed_audio_keeps_its_own_deterministic_render(field):
    """With designed audio the Studio job cuts listening excerpts, so the
    MCP request renders its own."""
    edl = dict(default_edl(60.0), **{field: [{"id": "a", "start": 0,
                                              "at": 1, "end": 4}]})
    cur = _JoinCursor(edl)
    job_id, created = dbx.get_or_enqueue_preview_job(
        _Conn(cur), 2842, 2007,
        {"edl_version": 3, "audio_model_review": False})
    assert (job_id, created) == (126611, True)


def test_studio_request_never_joins_an_mcp_render():
    cur = _JoinCursor(default_edl(60.0), studio_job=False)
    job_id, created = dbx.get_or_enqueue_preview_job(
        _Conn(cur), 2842, 2007, {"edl_version": 3})
    assert (job_id, created) == (126611, True)


# ── what an MCP result says about a preview ─────────────────────────────

def test_preview_summary_reports_the_fact():
    studio = read_tools.preview_summary(_asset())
    assert studio["audio_model_review"] is False
    assert studio["listener_policy"] == "no_listening_excerpts"
    mcp = read_tools.preview_summary(_asset(stamp=False))
    assert mcp["audio_model_review"] is False
    assert mcp["listener_policy"] == "deterministic_only"
    # An unstamped render without recorded lists used to report False.
    legacy = read_tools.preview_summary(_asset(stamp=None, lists=False))
    assert legacy["audio_model_review"] is True
    assert legacy["listener_policy"] is None
    heard = read_tools.preview_summary(_asset(keys=["l.mp3"]))
    assert heard["audio_model_review"] is True
