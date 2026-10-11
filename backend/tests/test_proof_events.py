"""The proof-first funnel's events (landing results wall, before/after,
demo replay, onboarding examples) are stored, not silently skipped."""
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import website_analytics as wa  # noqa: E402

PROOF = ["proof_view", "showcase_sound", "before_after_toggle", "demo_replay_start",
         "demo_replay_step", "demo_replay_complete", "starter_request_pick",
         "onboarding_example_view"]


def test_proof_events_are_recorded():
    data = {"device_id": "d" * 24, "session_id": "s" * 24, "visit_id": str(uuid.uuid4()),
            "active_seconds": 30, "path": "/",
            "events": [{"kind": k, "id": str(uuid.uuid4()), "active_seconds": 5} for k in PROOF]}
    out = wa.clean_payload(data)
    assert set(PROOF) <= wa.EVENTS
    assert [e[2] for e in out["events"]] == PROOF
