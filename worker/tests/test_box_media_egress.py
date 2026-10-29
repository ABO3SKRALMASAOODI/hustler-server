"""Media egress children (fetch, search, stock_acquire) on the render box.

Found by E1 (30 Oct 2026): inside a box slot (BOX_ROUTE_ALL=1,
CLOUDFLARE_EXECUTOR_ENABLED=0, no Modal, no Cloud Run) the egress walker had
no provider at all, so fetch_url, find_song/search_footage and
add_stock_media answered "no alternate media-fetch executor is set" for every
job the box ran. The box is now the ONLY provider for a child its routing
selects (no fallback, the owner's rule), and the owner's YouTube exception
(decision (c)) is an app_kv switch that is OFF by default.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import box_routing  # noqa: E402
import config  # noqa: E402
import remote  # noqa: E402

YT = "https://www.youtube.com/watch?v=qqi793k9M0o"
DIRECT = "https://example.com/clip.mp4"


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def box(job):
        seen.append(("box", job["type"]))
        return {"ok": True, "storage_key": "k/box"}

    def cloudflare(job):
        seen.append(("cloudflare", job["type"]))
        return {"ok": True, "storage_key": "k/cf"}

    def modal(job, function_override=None):
        seen.append(("modal", job["type"]))
        return {"ok": True, "storage_key": "k/modal"}

    monkeypatch.setattr(remote, "_run_box_with_wait", box)
    monkeypatch.setattr(remote, "_run_cloudflare_with_capacity_wait",
                        cloudflare)
    monkeypatch.setattr(remote, "_run_modal", modal)
    monkeypatch.setattr(remote, "_run_cloud",
                        lambda job: seen.append(("cloud_run", job["type"])))
    monkeypatch.setattr(box_routing, "_user_row", lambda uid: {
        "is_subscribed": 1 if uid == 7 else 0,
        "plan": "ai" if uid == 7 else "free", "trial_status": None,
        "trial_live": False})
    yield seen
    box_routing.set_routing_for_tests({})


def _box_slot(monkeypatch, routing=None, cloudflare_url=""):
    """The environment box/host/slot.env gives every box slot."""
    monkeypatch.setenv("BOX_ROUTE_ALL", "1")
    monkeypatch.setenv("BOX_EXECUTOR_URL", "http://172.30.0.1:8090")
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", False)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL", cloudflare_url)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", False)
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_URL", "")
    box_routing.set_routing_for_tests(routing or {})


def _cloudflare_host(monkeypatch, routing):
    """A Cloudflare container or the dispatcher (stages from app_kv)."""
    monkeypatch.delenv("BOX_ROUTE_ALL", raising=False)
    monkeypatch.delenv("BOX_EXECUTOR_URL", raising=False)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://cloudflare.example")
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_PERCENT", 100)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_URL", "")
    box_routing.set_routing_for_tests(routing)


def _stages(**on):
    stages = {s: bool(on.get(s)) for s in box_routing.STAGES}
    return {"enabled": True, "url": "https://box.example",
            "owner_user_ids": [60], "stages": stages}


def test_every_egress_child_inside_a_box_slot_runs_on_the_box(
        monkeypatch, calls):
    _box_slot(monkeypatch)
    assert remote.fetch_available() and remote.stock_acquire_available()
    got = remote.run_fetch_remote(5, {"url": DIRECT}, user_id=7)
    assert got["ok"] and got["fetch_provider"] == "box"
    assert remote.run_search_remote(5, {"query": "q", "mode": "footage"},
                                    user_id=7)["fetch_provider"] == "box"
    assert remote.run_stock_acquire_remote(5, {"item": {}}, user_id=60)[
        "fetch_provider"] == "box"
    assert calls == [("box", "fetch"), ("box", "search"),
                     ("box", "stock_acquire")]


def test_a_youtube_fetch_inside_a_box_slot_stays_on_the_box_by_default(
        monkeypatch, calls):
    # Even with a Cloudflare URL present (it is inert without the switch).
    _box_slot(monkeypatch, cloudflare_url="https://cloudflare.example")
    assert remote.run_fetch_remote(5, {"url": YT}, user_id=7)[
        "fetch_provider"] == "box"
    assert calls == [("box", "fetch")]


def test_a_box_child_never_falls_back(monkeypatch, calls):
    """A blocked or failed box answer is the answer: no Cloudflare/Modal."""
    _cloudflare_host(monkeypatch, _stages(owner=True))
    monkeypatch.setattr(remote, "_run_box_with_wait", lambda job: (
        calls.append(("box", job["type"])) or
        {"ok": False, "access_blocked": True, "error": "bot wall"}))
    got = remote.run_fetch_remote(5, {"url": YT}, user_id=60)
    assert got["ok"] is False and got["fetch_provider"] == "box"
    assert calls == [("box", "fetch")]

    def boom(job):
        raise remote.RemoteExecutorError("box: no capacity")
    monkeypatch.setattr(remote, "_run_box_with_wait", boom)
    with pytest.raises(remote.RemoteExecutorError):
        remote.run_fetch_remote(5, {"url": DIRECT}, user_id=60)
    assert calls == [("box", "fetch")]


def test_stages_decide_for_children_of_cloudflare_parents(monkeypatch, calls):
    _cloudflare_host(monkeypatch, _stages(owner=True))
    remote.run_fetch_remote(5, {"url": DIRECT}, user_id=60)    # owner: box
    remote.run_fetch_remote(5, {"url": DIRECT}, user_id=7)     # paying: CF
    assert calls == [("box", "fetch"), ("cloudflare", "fetch")]


def test_routing_off_keeps_todays_cloudflare_path(monkeypatch, calls):
    _cloudflare_host(monkeypatch, {})
    remote.run_search_remote(5, {"query": "q", "mode": "song"}, user_id=60)
    assert calls == [("cloudflare", "search")]


def test_owner_youtube_switch_sends_only_youtube_fetches_to_cloudflare(
        monkeypatch, calls):
    switch = dict(_stages(owner=True, mcp=True, trial=True, free=True,
                          paying=True), youtube_egress="cloudflare")
    _box_slot(monkeypatch, routing=switch,
              cloudflare_url="https://cloudflare.example")
    assert remote.run_fetch_remote(5, {"url": YT}, user_id=7)[
        "fetch_provider"] == "cloudflare"
    remote.run_fetch_remote(5, {"url": "https://youtu.be/qqi793k9M0o"},
                            user_id=60)
    remote.run_fetch_remote(5, {"url": DIRECT}, user_id=7)
    remote.run_search_remote(5, {"query": "q", "mode": "song"}, user_id=7)
    remote.run_stock_acquire_remote(5, {"item": {}}, user_id=7)
    assert calls == [("cloudflare", "fetch"), ("cloudflare", "fetch"),
                     ("box", "fetch"), ("box", "search"),
                     ("box", "stock_acquire")]


def test_youtube_switch_without_a_cloudflare_url_stays_on_the_box(
        monkeypatch, calls):
    _box_slot(monkeypatch, routing=dict(_stages(owner=True),
                                        youtube_egress="cloudflare"))
    assert remote.run_fetch_remote(5, {"url": YT}, user_id=7)[
        "fetch_provider"] == "box"


def test_youtube_switch_reaches_children_of_cloudflare_parents(
        monkeypatch, calls):
    _cloudflare_host(monkeypatch, dict(_stages(owner=True),
                                       youtube_egress="cloudflare"))
    remote.run_fetch_remote(5, {"url": YT}, user_id=60)
    remote.run_fetch_remote(5, {"url": DIRECT}, user_id=60)
    assert calls == [("cloudflare", "fetch"), ("box", "fetch")]
