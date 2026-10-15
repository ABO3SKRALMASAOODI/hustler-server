"""The MCP endpoint never stalls (Oct 2026, rel).

On Oct 11 the endpoint answered nothing for 40 minutes while every job it had
queued finished in under 225 s: replies waited on per-call S3 clients with
botocore's legacy timeouts (60 s connect + 60 s read, five attempts) while
fetching frames to embed. What is pinned:

  1. Frame embedding has a time budget; past it frames arrive as links.
  2. S3 clients are created once per configuration with bounded timeouts.
  3. Database connects are bounded and keep-alive.
  4. Long job waits are capped per process (backpressure): past the cap a
     call answers with its job id after a short wait.
  5. A worker told to stop ends its waits at once, so replies are delivered.
  6. upload_start plans fit in one tool reply (at most 16 parts).
"""

import threading
import time

import storage
import routes.mcp as mcpmod
import routes.video as video


def test_frames_past_the_budget_are_links_not_waits(monkeypatch):
    calls = []

    def slow_fetch(key, cap):
        calls.append(key)
        time.sleep(0.05)
        return b"\xff\xd8jpeg"

    monkeypatch.setattr(mcpmod.storage, "get_object_whole", slow_fetch)
    monkeypatch.setattr(mcpmod.storage, "presign_get",
                        lambda key, *a, **k: f"https://r2.example/{key}")
    monkeypatch.setattr(mcpmod, "IMAGE_EMBED_BUDGET_S", 0.08)
    images = [{"storage_key": f"thumbs/1/{i}.jpg", "label": f"f{i}",
               "edl_version": 3} for i in range(6)]
    started = time.monotonic()
    out = mcpmod._image_blocks(images)
    assert time.monotonic() - started < 1.0
    embedded = [b for b in out if b["type"] == "image"]
    skipped = [b for b in out if b["type"] == "text"
               and "FRAME EMBED SKIPPED" in b["text"]]
    assert 1 <= len(embedded) < 6
    assert len(embedded) + len(skipped) == 6
    # A skipped frame still carries its link and is not a delivery failure.
    assert all("https://r2.example/" in b["text"] for b in skipped)
    assert not any("FRAME DELIVERY FAILED" in b.get("text", "") for b in out)


def test_s3_clients_are_cached_with_bounded_timeouts(monkeypatch):
    made = []

    class Fake:
        def __init__(self, config):
            self.config = config

    def fake_client(_service, **kwargs):
        made.append(kwargs)
        return Fake(kwargs["config"])

    monkeypatch.setattr(storage.boto3, "client", fake_client)
    monkeypatch.setenv("S3_ENDPOINT", "https://r2.example")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "secret")
    storage._clients.clear()
    a = storage.client()
    b = storage.client()
    fast = storage.client(fast=True)
    assert a is b and fast is not a
    assert len(made) == 2
    assert a.config.connect_timeout <= 10 and a.config.read_timeout <= 60
    assert a.config.retries["mode"] == "standard"
    assert a.config.retries["max_attempts"] <= 3
    assert fast.config.connect_timeout <= 5 and fast.config.read_timeout <= 15
    assert fast.config.retries["max_attempts"] <= 2
    storage._clients.clear()


def test_database_connects_are_bounded():
    assert 0 < video._VDB_CONNECT_ARGS["connect_timeout"] <= 15
    assert video._VDB_CONNECT_ARGS["keepalives"] == 1


def _job(state):
    return {"id": 9, "type": "mcp_tool", "state": state, "progress": 0,
            "error": None, "result": None, "payload": {}, "project_id": 1}


def test_waits_beyond_the_cap_answer_quickly(monkeypatch):
    monkeypatch.setattr(mcpmod, "_job_row", lambda *_a: _job("running"))
    monkeypatch.setattr(mcpmod, "SHORT_WAIT_S", 0.2)
    monkeypatch.setattr(mcpmod, "_long_waits", threading.BoundedSemaphore(1))
    assert mcpmod._long_waits.acquire(blocking=False)   # the only slot busy
    try:
        started = time.monotonic()
        row = mcpmod._wait(9, 1, seconds=30)
        assert row["state"] == "running"
        assert time.monotonic() - started < 1.5
    finally:
        mcpmod._long_waits.release()
    # The slot is returned after use, never leaked.
    assert mcpmod._long_waits.acquire(blocking=False)
    mcpmod._long_waits.release()


def test_a_stopping_worker_delivers_its_replies(monkeypatch):
    monkeypatch.setattr(mcpmod, "_job_row", lambda *_a: _job("running"))
    monkeypatch.setattr(mcpmod, "SHUTTING_DOWN", threading.Event())
    mcpmod.SHUTTING_DOWN.set()
    started = time.monotonic()
    assert mcpmod._wait(9, 1, seconds=60)["state"] == "running"
    assert time.monotonic() - started < 1.0


def test_shutdown_hook_chains_the_worker_handler(monkeypatch):
    import signal
    seen = []
    installed = {}
    monkeypatch.setattr(signal, "getsignal",
                        lambda _s: (lambda signum, frame: seen.append(signum)))
    monkeypatch.setattr(signal, "signal",
                        lambda s, handler: installed.__setitem__(s, handler))
    monkeypatch.setattr(mcpmod, "SHUTTING_DOWN", threading.Event())
    assert mcpmod.install_shutdown_hook() is True
    installed[signal.SIGTERM](signal.SIGTERM, None)
    assert mcpmod.SHUTTING_DOWN.is_set()
    assert seen == [signal.SIGTERM]           # gunicorn's own handler ran


def test_upload_plans_fit_one_reply():
    gib = 1024 ** 3
    for nbytes in (17 * 1024 ** 2, int(1.43 * gib), 14 * gib):
        size = storage.part_size_for_parts(nbytes, storage.MCP_MAX_PARTS)
        parts = (nbytes + size - 1) // size
        assert parts <= storage.MCP_MAX_PARTS
        assert size >= storage.MIN_PART_SIZE and size % (1024 * 1024) == 0
    # The browser's resumable 8-16 MiB parts are unchanged.
    assert storage.part_size_for(int(1.43 * gib)) <= storage.MAX_PART_SIZE


def test_final_receipt_states_the_brand_check():
    ok = mcpmod._brand_check({"endcard": 0.972, "watermark": 0.839,
                              "findings": []})
    assert ok["end_card"] == "confirmed" and ok["corner_mark"] == "confirmed"
    paid = mcpmod._brand_check({"endcard": 0.972, "watermark": None})
    assert paid["corner_mark"].startswith("not on this plan")
    bad = mcpmod._brand_check({"endcard": 0.41, "watermark": 0.2})
    assert bad["end_card"] == "MISSING OR ALTERED"
    assert bad["corner_mark"] == "MISSING OR ALTERED"
    assert mcpmod._brand_check(None)["end_card"] == "not measured"
    skipped = mcpmod._brand_check({"endcard": None, "watermark": None,
                                   "skipped": ["watermark: budget"]})
    assert skipped["corner_mark"] == "not measured"


def test_brand_thresholds_match_the_worker_check():
    import os
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "worker",
                            "render_qc.py")).read()
    assert float(re.search(r"ENDCARD_MIN_NCC = ([0-9.]+)", src).group(1)) \
        == mcpmod._ENDCARD_MIN_NCC
    assert float(re.search(r"ROBOT_MIN_NCC = ([0-9.]+)", src).group(1)) \
        == mcpmod._ROBOT_MIN_NCC


def test_final_wait_receipt_says_final_quality_and_brand(monkeypatch):
    """C12/C13 on the wait_for_job path, including finals whose stored result
    predates the label fix (quality 'draft')."""
    import routes.mcp as m
    import inspect
    src = inspect.getsource(m._t_wait_for_job)
    assert 'receipt["quality"] = "final"' in src
    assert 'receipt["brand_check"] = _brand_check' in src
