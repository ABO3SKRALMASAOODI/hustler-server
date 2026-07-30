"""The browser runs on the executor, not on the dispatcher (round 61).

On 2026-07-30 the FIRST production `record_website` call killed the worker:

    13:15:28  "record a website (like a Google search for 'plumber near me'
               or a business listing) For b-rolls"
    13:15:57  the model asks for get_kept_transcript + three record_website
    13:16:15  last heartbeat
    13:18:27  "Worker died and retries are exhausted"

A tool call runs inside an agent turn, and agent turns run LOCALLY on the
dispatcher by design (they are network-bound waiting on an LLM). That is the
wrong box for a full Chromium at 1080x1920 rendering Google Search: the same
small instance whose memory ceiling had been killing filmstrip jobs that
morning. Nothing in record_website's careful "could not record that page"
handling could fire, because an OOM kill is a disappearance, not an exception —
and MAX_ATTEMPTS_AGENT is 1, so there was no retry either.

    cd worker && python -m pytest tests/test_capture_offload.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import config                                                    # noqa: E402
import http_server                                               # noqa: E402
import remote                                                    # noqa: E402
import webrecord                                                 # noqa: E402


# ── routing ────────────────────────────────────────────────────────────────

def test_the_executor_can_run_a_capture():
    """It has Chromium already — one image, two roles (the Dockerfile bakes
    `playwright install --with-deps chromium` for both)."""
    assert "capture" in http_server.RUNNERS
    assert http_server.RUNNERS["capture"] is webrecord.run_capture_job


def test_a_capture_is_dispatched_as_a_job_with_no_row():
    """It is one tool call inside a turn, not a queued job — there is no id to
    claim. _run_remote must only need the fields we can synthesise."""
    seen = {}
    orig = remote._run_remote
    remote._run_remote = lambda job: seen.setdefault("job", job) or {"ok": 1}
    try:
        remote.run_capture_remote(42, {"mode": "record", "url": "https://x"},
                                  user_id=7)
    finally:
        remote._run_remote = orig
    job = seen["job"]
    assert job["type"] == "capture" and job["project_id"] == 42
    assert job["id"] is None and job["user_id"] == 7
    assert job["payload"]["url"] == "https://x"


def test_capture_has_its_own_timeout_and_it_outlasts_the_far_side_wall():
    """WEB_RECORD_WALL_S bounds the work on the executor. The dispatcher's HTTP
    timeout has to be longer than that — otherwise it gives up on a capture
    that is about to succeed — while staying far below the agent turn's own
    ceiling, because a user is synchronously waiting inside one."""
    t = config.executor_timeout_for("capture")
    assert t > config.WEB_RECORD_WALL_S, (t, config.WEB_RECORD_WALL_S)
    assert t < config.AGENT_TURN_TIMEOUT_S, (t, config.AGENT_TURN_TIMEOUT_S)
    # and it is NOT the render/index default — those are sized in thousands of
    # seconds and would hold an agent turn open for the whole of one.
    assert t < config.REMOTE_EXECUTOR_TIMEOUTS["index"]


# ── the fallbacks, and the one that must NOT exist ─────────────────────────

def test_no_executor_means_record_here_exactly_as_before():
    """A single-box deployment is still legitimate; the browser only has to
    move when there IS somewhere better to put it."""
    orig = config.REMOTE_EXECUTOR_URL
    try:
        config.REMOTE_EXECUTOR_URL = ""
        assert remote.capture_available() is False
        config.REMOTE_EXECUTOR_URL = "https://executor.example"
        assert remote.capture_available() is True
    finally:
        config.REMOTE_EXECUTOR_URL = orig


def test_a_remote_failure_never_falls_back_to_the_local_browser():
    """THE point of the change. Falling back would reproduce the crash on the
    box we already know is too small — and it would do it on the retry, when
    the user has been waiting longest."""
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "agent_tools.py")).read()
    body = src[src.index("def _run_capture("):src.index("def _store_capture(")]
    remote_branch = body[body.index("if remote.capture_available():"):
                         body.index("workdir = os.path.join")]
    assert "webrecord.record" not in remote_branch
    for line in remote_branch.splitlines():
        assert "return got, None" in line or "webrecord" not in line


def test_the_executor_refuses_in_words_when_it_has_no_browser():
    """A deployment fault must reach the agent as 'cannot', never as a stack
    trace — and it asks local_available(), not available(): the latter answers
    for the fleet, so an executor that also had REMOTE_EXECUTOR_URL set would
    claim a capability it does not have."""
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "webrecord.py")).read()
    body = src[src.index("def run_capture_job("):]
    assert "local_available()" in body
    assert "raise WebRecordError" in body


def test_local_available_is_the_import_probe_only():
    orig = config.REMOTE_EXECUTOR_URL
    try:
        config.REMOTE_EXECUTOR_URL = "https://executor.example"
        # available() follows where the work happens; local_available() does
        # not — that difference is what keeps the executor honest.
        assert webrecord.available() is True
        assert webrecord.local_available() in (True, False)
        config.REMOTE_EXECUTOR_URL = ""
        assert webrecord.available() == webrecord.local_available()
    finally:
        config.REMOTE_EXECUTOR_URL = orig


# ── the bytes ──────────────────────────────────────────────────────────────

def test_the_recording_never_crosses_the_wire():
    """The executor uploads and returns the KEY. Streaming an mp4 back through
    the dispatcher would put the file on the box we just took work off."""
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "webrecord.py")).read()
    body = src[src.index("def run_capture_job("):]
    assert 'got["storage_key"] = key' in body
    assert 'got.pop("path")' in body     # the local path is not returned


def test_the_asset_row_is_written_by_the_dispatcher():
    """The executor makes bytes; the dispatcher owns the project's DB writes.
    A capture whose caller died then leaves an orphaned object, not a
    half-registered asset pointing at nothing."""
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "webrecord.py")).read()
    body = src[src.index("def run_capture_job("):]
    assert "insert_asset" not in body


def test_store_capture_accepts_an_already_uploaded_key():
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "agent_tools.py")).read()
    body = src[src.index("def _store_capture("):src.index("def record_website(")]
    assert 'key = got.get("storage_key")' in body
    assert "if not key:" in body


def test_the_workdir_is_cleaned_on_every_path():
    """Both branches of _run_capture, including the raising ones — the tool
    used to rmtree in four separate places and one of them was a finally."""
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "agent_tools.py")).read()
    body = src[src.index("def _run_capture("):src.index("def _store_capture(")]
    assert "finally:" in body and "shutil.rmtree(workdir" in body


@pytest.mark.parametrize("tool", ["record_website", "record_website_demo"])
def test_both_capture_tools_go_through_the_one_dispatcher(tool):
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "agent_tools.py")).read()
    body = src[src.index(f"def {tool}(ctx"):]
    body = body[:body.index("\ndef ", 10)]
    assert "_run_capture(ctx," in body
    # ...and neither reaches the browser directly any more.
    assert "webrecord.record" not in body
