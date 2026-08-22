"""Worker process roles keep memory-heavy queue families isolated.

These are subprocess tests because config is intentionally import-time env.
They pin the production contract without leaking one test's role into another.
"""

import json
import os
import subprocess
import sys


WORKER_DIR = os.path.dirname(os.path.dirname(__file__))


def _topology(role, **values):
    env = dict(os.environ, PYTHONPATH=WORKER_DIR)
    for key in (
            "WORKER_ROLE", "WORKER_AGENT_SLOTS", "WORKER_SHORTS_SLOTS",
            "WORKER_MCP_SLOTS", "WORKER_MEDIA_SLOTS", "WORKER_INDEX_SLOTS",
            "REMOTE_EXECUTOR_URL", "REMOTE_AGENT_EXECUTOR_URL",
            "MODAL_EXECUTOR_ENABLED", "MODAL_EXECUTOR_TYPES",
            "REMOTE_AGENT_DISPATCH_SLOTS", "CLOUDFLARE_EXECUTOR_ENABLED",
            "CLOUDFLARE_EXECUTOR_URL"):
        env.pop(key, None)
    env.update({
        "WORKER_ROLE": role,
        "REMOTE_EXECUTOR_URL": (
            "https://valmera-executor-123.us-central1.run.app"),
        "WORKER_AGENT_SLOTS": "2",
        "WORKER_SHORTS_SLOTS": "2",
        "WORKER_MCP_SLOTS": "2",
        "WORKER_MEDIA_SLOTS": "4",
        "WORKER_INDEX_SLOTS": "4",
        **{k: str(v) for k, v in values.items()},
    })
    raw = subprocess.check_output(
        [sys.executable, "-c",
         "import json,config; print(json.dumps(config.worker_lane_slots(), "
         "sort_keys=True))"],
        cwd=WORKER_DIR, env=env, text=True)
    return json.loads(raw)


def test_legacy_worker_role_keeps_every_lane():
    assert _topology("worker") == {
        "agent": 5, "filmstrip": 1, "index": 4, "mcp": 2,
        "media": 4, "shorts": 2,
    }


def test_dispatcher_cannot_claim_stateful_editor_work():
    lanes = _topology("dispatcher")
    assert lanes == {
        "agent": 0, "filmstrip": 1, "index": 4, "mcp": 0,
        "media": 4, "shorts": 0,
    }


def test_agent_worker_cannot_claim_render_or_index_work():
    lanes = _topology("agent")
    assert lanes == {
        "agent": 5, "filmstrip": 0, "index": 0, "mcp": 2,
        "media": 0, "shorts": 2,
    }


def test_explicit_agent_executor_rollback_restores_local_slot_limit():
    lanes = _topology("worker", REMOTE_AGENT_EXECUTOR_URL="")
    assert lanes["agent"] == 2


def test_modal_agent_uses_remote_dispatch_slots_without_cloud_run_url():
    lanes = _topology(
        "worker", REMOTE_EXECUTOR_URL="", REMOTE_AGENT_EXECUTOR_URL="",
        MODAL_EXECUTOR_ENABLED="1", MODAL_EXECUTOR_TYPES="preview,agent_turn",
        REMOTE_AGENT_DISPATCH_SLOTS="7")
    assert lanes["agent"] == 7


def test_modal_without_agent_turn_keeps_memory_safe_local_limit():
    lanes = _topology(
        "worker", REMOTE_EXECUTOR_URL="", REMOTE_AGENT_EXECUTOR_URL="",
        MODAL_EXECUTOR_ENABLED="1", MODAL_EXECUTOR_TYPES="preview")
    assert lanes["agent"] == 2


def test_cloudflare_only_media_plane_uses_remote_dispatch_capacity():
    lanes = _topology(
        "dispatcher", REMOTE_EXECUTOR_URL="", MODAL_EXECUTOR_ENABLED="0",
        CLOUDFLARE_EXECUTOR_ENABLED="1",
        CLOUDFLARE_EXECUTOR_URL="https://valmera-executor.example.workers.dev")
    assert lanes == {
        "agent": 0, "filmstrip": 1, "index": 4, "mcp": 0,
        "media": 4, "shorts": 0,
    }


def test_executor_never_polls_the_database_queue():
    assert set(_topology("executor").values()) == {0}
    assert set(_topology("agent_executor").values()) == {0}
    assert set(_topology("mcp_executor").values()) == {0}
    assert set(_topology("shorts_executor").values()) == {0}
    assert set(_topology("batch_executor").values()) == {0}


def test_agent_role_refuses_to_start_without_remote_compute():
    env = dict(os.environ, PYTHONPATH=WORKER_DIR)
    env.update({
        "WORKER_ROLE": "agent",
        "DATABASE_URL": "postgresql://unused/unused",
        "S3_ENDPOINT": "https://storage.invalid",
        "S3_ACCESS_KEY_ID": "unused",
        "S3_SECRET_ACCESS_KEY": "unused",
        "S3_BUCKET": "unused",
        "REMOTE_EXECUTOR_URL": "",
    })
    proc = subprocess.run(
        [sys.executable, "-c", "import config; config.require_core()"],
        cwd=WORKER_DIR, env=env, text=True, capture_output=True)
    assert proc.returncode != 0
    assert "without REMOTE_EXECUTOR_URL" in (proc.stdout + proc.stderr)


def test_unknown_role_fails_closed():
    env = dict(os.environ, PYTHONPATH=WORKER_DIR, WORKER_ROLE="typo")
    proc = subprocess.run(
        [sys.executable, "-c", "import config; config.worker_lane_slots()"],
        cwd=WORKER_DIR, env=env, text=True, capture_output=True)
    assert proc.returncode != 0
    assert "Unknown WORKER_ROLE" in (proc.stdout + proc.stderr)
