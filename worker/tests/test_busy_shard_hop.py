"""A busy shard hops to an identity on another shard (Oct 2026, rel).

A 14-file upload hashed its analyses onto 8 batch shards; each collision
waited up to 120 s on its own busy shard and then went back to the queue
while other shards sat idle. The dispatcher mirrors the Worker's shard
routing and relaunches the claim (nothing ran: a busy refusal stores
nothing) on an alternate identity that lands on a shard not yet refused.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import db as dbx  # noqa: E402
import remote  # noqa: E402

# Pinned on both sides: worker/cloudflare/tests/shard.test.cjs.
VECTORS = [
    ("batch", "cf-index-21a53b11decccd9a5549", 1),
    ("batch", "cf-alt1-index-p3655-21a53b11decccd9a5549", 4),
    ("batch", "cf-alt7-final-p3395-5ec08d0813e4c419c43a", 4),
    ("batch", "cf-faces-0123456789abcdef0123", 7),
    ("batch", "cf-render-g12-1-5ec08d0813e4c419c43a", 6),
    ("mcp", "cf-mcp-p3614-e5ab61fc8ba0594f5760", 12),
    ("mcp", "cf-alt3-mcp_tool-p3614-e5ab61fc8ba0594f5760", 9),
    ("interactive", "cf-preview-p3690-aaaaaaaaaaaaaaaaaaaa", 8),
    ("interactive", "cf-filmstrip-bbbbbbbbbbbbbbbbbbbb", 16),
    ("interactive", "cf-render-g7-0-cccccccccccccccccccc", 1),
]


@pytest.mark.parametrize("lane,call_id,shard", VECTORS)
def test_python_routing_mirrors_the_worker(lane, call_id, shard):
    assert remote._cloudflare_shard_index(lane, call_id) == shard


def test_shard_counts_mirror_the_worker():
    adapter = open(os.path.join(os.path.dirname(__file__), "..",
                                "cloudflare", "src", "index.ts")).read()
    assert ("interactive: 20, batch: 8, agent: 5, mcp: 20, shorts: 8"
            in adapter)
    assert remote._CLOUDFLARE_SHARD_COUNTS == {
        "interactive": 20, "batch": 8, "agent": 5, "mcp": 20, "shorts": 8}
    assert "const FACES_SHARDS = 2;" in adapter


def test_hop_picks_an_unrefused_shard(monkeypatch):
    monkeypatch.setattr(remote, "_cloudflare_features",
                        lambda: ["alt_slots_32"])
    job = {"id": 69484, "type": "index", "project_id": 3655,
           "total_claims": 1}
    home = remote._cloudflare_shard_index("batch",
                                          remote._cloudflare_call_id(job))
    refused = {home}
    seen = set()
    for _ in range(7):
        slot = remote._busy_hop_slot(job, refused)
        assert slot is not None
        probe = dict(job, _cloudflare_admission_slot=slot,
                     _cloudflare_capacity_spread=True)
        shard = remote._cloudflare_shard_index(
            "batch", remote._cloudflare_call_id(probe))
        assert shard not in refused
        refused.add(shard)
        seen.add(shard)
        job["_cloudflare_admission_slot"] = slot
    assert len(refused) == 8                     # every shard tried once
    assert remote._busy_hop_slot(job, refused) is None


def test_old_worker_limits_hops_to_two_identities(monkeypatch):
    monkeypatch.setattr(remote, "_cloudflare_features", lambda: [])
    job = {"id": 5, "type": "index", "project_id": 1, "total_claims": 1,
           "_cloudflare_admission_slot": 2}
    assert remote._busy_hop_slot(job, {0}) is None


class _Lease:
    def run(self, fn, *_a):
        assert fn is dbx.lease_is_current
        return True

    def reset(self):
        pass


def _enable(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", False)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", False)


def test_busy_index_hops_at_once_instead_of_waiting(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote, "_cloudflare_features",
                        lambda: ["skew_guard", "alt_slots_32"])
    monkeypatch.setattr(dbx, "Db", _Lease)
    monkeypatch.setattr(remote.time, "sleep",
                        lambda _s: pytest.fail("an idle shard exists"))
    tried = []

    def launch(job):
        call_id = remote._cloudflare_call_id(job)
        tried.append(remote._cloudflare_shard_index("batch", call_id))
        if len(tried) < 3:
            raise remote.CloudflareCapacityBusy(
                "Cloudflare Container shard is busy")
        return {"ok": True}

    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    job = {"id": 69484, "type": "index", "project_id": 3655,
           "total_claims": 1}
    assert remote._run_cloudflare_with_capacity_wait(job) == {"ok": True}
    assert len(set(tried)) == 3                  # three distinct shards


def test_busy_mcp_tool_keeps_its_project_shard_briefly(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote, "_cloudflare_features",
                        lambda: ["skew_guard", "alt_slots_32"])
    monkeypatch.setattr(dbx, "Db", _Lease)
    now = [0.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(remote.time, "sleep",
                        lambda d: now.__setitem__(0, now[0] + d))
    ids = []

    def launch(job):
        call_id = remote._cloudflare_call_id(job)
        ids.append((now[0], call_id))
        if call_id.startswith("cf-mcp-p3614-"):
            # The project shard stays busy (a long render on the project).
            raise remote.CloudflareCapacityBusy(
                "Cloudflare Container shard is busy")
        return {"ok": True}

    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    job = {"id": 7, "type": "mcp_tool", "project_id": 3614,
           "total_claims": 1, "payload": {"tool": "look_at",
                                          "mutation": False}}
    assert remote._run_cloudflare_with_capacity_wait(job) == {"ok": True}
    # The project shard is retried first; the hop comes only after the
    # configured wait, onto an alternate identity, well before the 120 s
    # busy wait and its queue deferral.
    assert ids[0][1].startswith("cf-mcp-p3614-")
    assert ids[1][1].startswith("cf-mcp-p3614-")
    hop_at, hop_id = ids[-1]
    assert hop_id.startswith("cf-alt")
    assert config.CLOUDFLARE_MCP_BUSY_HOP_AFTER_S <= hop_at < 40
