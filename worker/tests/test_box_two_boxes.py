"""Two render boxes (worker/box_routing.py ``boxes``, remote._run_box_with_wait).

The rules pinned here:
* with ONE box (no ``boxes`` list, today's production) the dispatcher does
  exactly what it did before two-box admission existed: the same requests,
  the same call ids, the same waits and the same ledger writes, in the same
  order (a golden trace recorded on main e7d0c4fa with the same script);
* each job asks its preferred box first; a refusal that proves nothing ran
  moves the same claim to the other box at once, under that box's own
  identity, after the refused identity's ledger row is closed and the lease
  is re-checked (Cloudflare's shard-hop safety argument);
* an ambiguous launch never moves: it reconnects to its own box;
* children inside a slot never leave their parent's box;
* every request about a call names the box its id lives on.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import box_routing  # noqa: E402
import config  # noqa: E402
import db as dbx  # noqa: E402
import remote  # noqa: E402
import version  # noqa: E402

BOX1 = "https://box.example"
BOX2 = "https://box2.example"
ONE = {"enabled": True, "url": BOX1, "owner_user_ids": [60],
       "stages": {"owner": True}}
TWO = {"enabled": True, "owner_user_ids": [60],
       "stages": {"owner": True, "mcp": True, "trial": True, "free": True,
                  "paying": True},
       "boxes": [{"id": "h1", "url": BOX1, "classes": ["customer", "trial"]},
                 {"id": "h2", "url": BOX2, "classes": ["owner"]}],
       "overflow_wait_s": 30}


class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise remote.requests.HTTPError(str(self.status_code))


class _Ledger:
    events = []

    def run(self, fn, *args, **_kwargs):
        _Ledger.events.append((getattr(fn, "__name__", str(fn)), args))
        if fn is dbx.get_job:
            return None
        return True

    def reset(self):
        pass


def _names():
    return [name for name, _args in _Ledger.events]


REFUSAL = {"error": "Box is at capacity: no free interactive slot within "
           "150s; nothing ran", "safe_to_fallback": True, "box_wait": True,
           "capacity_unavailable": True, "retryable": True,
           "failure": {"kind": "provider_capacity_unavailable",
                       "retryable": True}}
DONE = {"result": {"ok": True}, "job_completed": True}
FAILED = {"error": "ffmpeg exited 1", "retryable": False,
          "failure": {"kind": "render_failed", "retryable": False}}


def _health(box_id=None):
    body = {"status": "ok", "provider": "box", "source_version": "v-mine",
            "code_version": "v-mine", "code_versions": ["v-mine"],
            "features": ["fence", "skew_guard", "box_admission",
                         "alt_slots_32"]}
    if box_id:
        body["box_id"] = box_id
    return body


def _trace(monkeypatch, script, job, health_id=None):
    """Run one box dispatch against a scripted provider and a fake clock.

    script: ("health",) | ("health_down",) | ("refuse", s) | ("done", s)
    | ("fail", s) | ("lost", s), consumed in order. Returns the trace with
    the box header kept apart, so the golden comparison is about behaviour.
    """
    clock = {"t": 1000.0}
    out, named = [], []
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["t"])

    def sleep(s):
        out.append(("sleep", round(s, 6)))
        clock["t"] += s

    monkeypatch.setattr(remote.time, "sleep", sleep)
    steps = list(script)

    def get(url, headers=None, timeout=None):
        out.append(("GET", url))
        if url.endswith("/health"):
            step = steps.pop(0)
            assert step[0] in ("health", "health_down"), (step, url)
            if step[0] == "health_down":
                raise remote.requests.ConnectionError("down")
            ids = health_id or {BOX2: "h2"}
            return _Response(_health(ids.get(url.rsplit("/health", 1)[0])))
        step = steps.pop(0)
        assert step[0] == "status", (step, url)
        return _Response(step[1])

    def post(url, json=None, headers=None, timeout=None):
        step = steps.pop(0)
        clock["t"] += step[1]
        out.append(("POST", url, json.get("max_wait_s"),
                    json.get("priority_class")))
        named.append((headers or {}).get(remote.BOX_ID_HEADER))
        if step[0] == "refuse":
            return _Response(dict(REFUSAL), 429)
        if step[0] == "fail":
            return _Response(dict(FAILED))
        if step[0] == "lost":
            raise remote.requests.ConnectionError("connection reset")
        return _Response(dict(DONE))

    monkeypatch.setattr(remote.requests, "get", get)
    monkeypatch.setattr(remote.requests, "post", post)
    remote.random.seed(7)
    result = remote._run_remote(job)
    return out, named, result


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("BOX_ROUTE_ALL", raising=False)
    monkeypatch.delenv("BOX_EXECUTOR_URL", raising=False)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://cloudflare.example")
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "WORKER_ROLE", "worker")
    monkeypatch.setattr(version, "code_version", lambda: "v-mine")
    _Ledger.events = []
    monkeypatch.setattr(remote.dbx, "Db", _Ledger)
    monkeypatch.setattr(remote.dbx, "mark_remote_owned", lambda _id: True)
    monkeypatch.setattr(remote.dbx, "remote_launch_recorded",
                        lambda _id: None)
    monkeypatch.setattr(remote.dbx, "unmark_remote_owned", lambda _id: None)
    monkeypatch.setattr(remote, "owner_alarm", lambda *a, **k: True)
    monkeypatch.setattr(remote, "_BOX_HEALTH_CACHE_S", 0.0)
    monkeypatch.setattr(remote, "_run_modal", lambda *a, **k: (
        _ for _ in ()).throw(AssertionError("Modal fallback on a box job")))
    monkeypatch.setattr(box_routing, "_user_row", lambda uid: {
        "is_subscribed": 1 if uid == 7 else 0,
        "plan": "ai" if uid == 7 else "free", "trial_status": None,
        "trial_live": False})
    remote._health_cache.clear()
    box_routing.set_routing_for_tests(ONE)
    yield
    box_routing.set_routing_for_tests({})


def _job(job_id=11, jtype="preview", user=60, **payload):
    payload.setdefault("execution_provider", "box")
    return {"id": job_id, "type": jtype, "project_id": 5, "user_id": user,
            "attempts": 1, "total_claims": 1, "payload": payload}


# ── one box: exactly today's path ──────────────────────────────────────────
SINGLE_SCRIPT = [("health",), ("refuse", 150.0), ("health",), ("refuse", 0.5),
                 ("health_down",), ("health_down",), ("health",),
                 ("refuse", 0.3), ("health",), ("refuse", 160.0),
                 ("health",), ("done", 2.0)]
_CALL = f"{BOX1}/calls/interactive/bx-preview-p5-dcf3005b04ef821c8534"
# Recorded with SINGLE_SCRIPT on main e7d0c4fa (before two-box admission).
GOLDEN_TRACE = [
    ("GET", f"{BOX1}/health"), ("POST", _CALL, 150.0, "owner"),
    ("sleep", 0.3943),
    ("GET", f"{BOX1}/health"), ("POST", _CALL, 150.0, "owner"),
    ("sleep", 3.301698),
    ("GET", f"{BOX1}/health"), ("sleep", 8.603738),
    ("GET", f"{BOX1}/health"), ("sleep", 12.57949),
    ("GET", f"{BOX1}/health"), ("POST", _CALL, 150.0, "owner"),
    ("sleep", 30.53823),
    ("GET", f"{BOX1}/health"), ("POST", _CALL, 150.0, "owner"),
    ("sleep", 0.419413),
    ("GET", f"{BOX1}/health"), ("POST", _CALL, 150.0, "owner"),
]
GOLDEN_LEDGER = [
    "record_remote_execution", "finish_remote_execution", "lease_is_current",
    "record_remote_execution", "finish_remote_execution", "lease_is_current",
    "lease_is_current", "lease_is_current", "record_remote_execution",
    "finish_remote_execution", "lease_is_current", "record_remote_execution",
    "finish_remote_execution", "lease_is_current", "record_remote_execution"]


def test_one_box_is_exactly_the_single_box_path(monkeypatch):
    out, headers, result = _trace(monkeypatch, SINGLE_SCRIPT, _job())
    assert [tuple(e) for e in out] == GOLDEN_TRACE
    assert _names() == GOLDEN_LEDGER
    assert result.get("ok") is True
    assert set(headers) == {"h1"}   # the only addition: the box's name


def test_one_box_lists_one_candidate_whatever_the_class():
    for user in (60, 7, 8):
        assert box_routing.candidates({"user_id": user}) == [("h1", True)]
    assert box_routing.boxes()[0]["url"] == BOX1
    assert not box_routing.multi_box()
    assert box_routing.url() == BOX1
    assert box_routing.call_box("bx-preview-p5-x") == "h1"
    assert box_routing.call_tag("h1") == ""


def test_a_single_entry_boxes_list_is_still_one_box(monkeypatch):
    box_routing.set_routing_for_tests(dict(ONE, boxes=[
        {"id": "h1", "url": BOX1, "classes": ["owner"]}]))
    out, _h, _r = _trace(monkeypatch, SINGLE_SCRIPT, _job())
    assert [tuple(e) for e in out] == GOLDEN_TRACE


# ── two boxes ──────────────────────────────────────────────────────────────
def _post_targets(out):
    return [(e[1].split("/calls/")[0], e[1].rsplit("/", 1)[1], e[2])
            for e in out if e[0] == "POST"]


def test_owner_job_asks_its_home_box_then_overflows_to_box_one(monkeypatch):
    box_routing.set_routing_for_tests(TWO)
    out, headers, result = _trace(monkeypatch, [
        ("health",), ("refuse", 150.0), ("health",), ("done", 3.0)], _job())
    posts = _post_targets(out)
    assert posts[0][0] == BOX2 and posts[0][1].startswith("bx-h2-preview-p5-")
    assert posts[0][2] == config.BOX_ADMISSION_WAIT_S        # home: full wait
    assert posts[1][0] == BOX1 and posts[1][1].startswith("bx-preview-p5-")
    assert posts[1][2] == 30                       # overflow: a brief wait
    assert posts[0][1][len("bx-h2-"):] == posts[1][1][len("bx-"):]
    assert headers == ["h2", "h1"]
    assert result.get("ok") is True
    # The hop is immediate (no backoff) and follows the shard-hop order:
    # the refused identity's ledger row is closed, the lease re-checked,
    # and only then is the new identity recorded.
    sleeps = [e[1] for e in out if e[0] == "sleep"]
    assert len(sleeps) == 1 and sleeps[0] < 0.5
    names = _names()
    first_close = names.index("finish_remote_execution")
    assert names[:first_close + 1] == ["record_remote_execution",
                                       "finish_remote_execution"]
    assert names[first_close + 1] == "lease_is_current"
    assert names[first_close + 2] == "record_remote_execution"
    recorded = [args for name, args in _Ledger.events
                if name == "record_remote_execution"]
    closed = [args for name, args in _Ledger.events
              if name == "finish_remote_execution"]
    assert closed[0][2] == "cancelled" and closed[0][5] == recorded[0][3]
    assert recorded[0][3] != recorded[1][3]          # a fresh identity


def test_customer_job_asks_box_one_first(monkeypatch):
    box_routing.set_routing_for_tests(TWO)
    out, headers, _r = _trace(monkeypatch, [
        ("health",), ("refuse", 150.0), ("health",), ("done", 1.0)],
        _job(user=7))
    posts = _post_targets(out)
    assert [p[0] for p in posts] == [BOX1, BOX2]
    assert posts[0][2] == config.BOX_ADMISSION_WAIT_S and posts[1][2] == 30
    assert out[1][3] == "customer"
    assert headers == ["h1", "h2"]


def test_a_round_of_refusals_backs_off_only_when_it_wraps(monkeypatch):
    box_routing.set_routing_for_tests(TWO)
    out, _h, _r = _trace(monkeypatch, [
        ("health",), ("refuse", 0.2), ("health",), ("refuse", 0.2),
        ("health",), ("refuse", 0.2), ("health",), ("done", 1.0)], _job())
    posts = _post_targets(out)
    assert [p[0] for p in posts] == [BOX2, BOX1, BOX2, BOX1]
    sleeps = [e[1] for e in out if e[0] == "sleep"]
    assert sleeps[0] < 0.5                  # hop h2 -> h1 at once
    assert sleeps[1] >= 1.5                 # both refused fast: back off
    assert sleeps[2] < 0.5                  # hop again


def test_an_unreachable_home_box_hops_without_launching_there(monkeypatch):
    box_routing.set_routing_for_tests(TWO)
    out, _h, _r = _trace(monkeypatch, [
        ("health_down",), ("health",), ("done", 1.0)], _job())
    assert _post_targets(out) == [(BOX1, _post_targets(out)[0][1], 30)]
    assert ("GET", f"{BOX2}/health") in out


def test_an_ambiguous_launch_never_moves_to_the_other_box(monkeypatch):
    box_routing.set_routing_for_tests(TWO)
    out, _h, result = _trace(monkeypatch, [
        ("health",), ("lost", 1.0),
        ("status", {"status": "running"}),
        ("status", {"status": "done", "envelope": dict(DONE)})], _job())
    assert [p[0] for p in _post_targets(out)] == [BOX2]
    status_reads = [e[1] for e in out if e[0] == "GET"
                    and "/calls/" in e[1]]
    assert status_reads and all(u.startswith(BOX2) for u in status_reads)
    assert result.get("ok") is True


def test_a_real_failure_is_not_a_reason_to_move(monkeypatch):
    box_routing.set_routing_for_tests(TWO)
    with pytest.raises(remote.RemoteExecutorError):
        _trace(monkeypatch, [("health",), ("fail", 5.0)], _job())


def test_a_box_answering_as_another_id_gets_nothing(monkeypatch):
    box_routing.set_routing_for_tests(TWO)
    out, _h, _r = _trace(monkeypatch, [
        ("health",), ("health",), ("done", 1.0)], _job(),
        health_id={BOX2: "h1", BOX1: "h1"})
    assert [p[0] for p in _post_targets(out)] == [BOX1]


def test_every_box_refusing_until_the_window_ends_returns_to_the_queue(
        monkeypatch):
    box_routing.set_routing_for_tests(TWO)
    monkeypatch.setattr(config, "BOX_INCLAIM_WAIT_S", 200.0)
    with pytest.raises(remote.BoxWaiting):
        _trace(monkeypatch, [("health",), ("refuse", 150.0),
                             ("health",), ("refuse", 30.0),
                             ("health",), ("refuse", 150.0)], _job())


def test_owner_pin_runs_on_that_box_only(monkeypatch):
    box_routing.set_routing_for_tests(dict(TWO, boxes=[
        {"id": "h1", "url": BOX1},
        {"id": "h2", "url": BOX2, "classes": [], "accepts": []}]))
    assert box_routing.candidates(_job()) == [("h1", True)]
    assert box_routing.candidates(_job(box_pin="h2")) == [("h2", True)]
    # A customer cannot pin; an unknown pin is ignored.
    assert box_routing.candidates(_job(user=7, box_pin="h2")) == [
        ("h1", True)]
    assert box_routing.candidates(_job(box_pin="h9")) == [("h1", True)]
    out, _h, _r = _trace(monkeypatch, [
        ("health",), ("refuse", 150.0), ("health",), ("done", 1.0)],
        _job(box_pin="h2"))
    assert [p[0] for p in _post_targets(out)] == [BOX2, BOX2]


def test_a_box_with_no_classes_takes_no_new_work_but_stays_reachable():
    box_routing.set_routing_for_tests(dict(TWO, boxes=[
        {"id": "h1", "url": BOX1},
        {"id": "h2", "url": BOX2, "classes": [], "accepts": []}]))
    for user in (60, 7, 8):
        assert box_routing.candidates({"user_id": user}) == [("h1", True)]
    assert remote._call_url("bx-h2-preview-p5-abc") == BOX2
    box_routing.set_routing_for_tests(dict(TWO, boxes=[
        {"id": "h1", "url": BOX1}]))
    assert remote._call_url("bx-h2-preview-p5-abc") == ""   # never box 1


def test_calls_name_their_box_in_every_request(monkeypatch):
    box_routing.set_routing_for_tests(TWO)
    seen = []

    def get(url, headers=None, timeout=None):
        seen.append((url, (headers or {}).get(remote.BOX_ID_HEADER)))
        return _Response({"status": "running"})

    monkeypatch.setattr(remote.requests, "get", get)
    remote._cloudflare_status("bx-h2-preview-p5-abc", "interactive")
    remote._cloudflare_status("bx-preview-p5-abc", "interactive")
    remote._cloudflare_status("cf-preview-p5-abc", "interactive")
    assert seen == [
        (f"{BOX2}/calls/interactive/bx-h2-preview-p5-abc", "h2"),
        (f"{BOX1}/calls/interactive/bx-preview-p5-abc", "h1"),
        ("https://cloudflare.example/calls/interactive/cf-preview-p5-abc",
         None)]


def test_lost_read_relaunch_stays_on_its_box(monkeypatch):
    box_routing.set_routing_for_tests(TWO)
    lost = {"error": "Box lost the executor during this mcp_tool (exit). "
            "No result was recorded; it is safe to retry.",
            "retryable": True, "failure": {
                "kind": "transient_infrastructure", "retryable": True,
                "max_attempts": 1}}
    clock = {"t": 1000.0}
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(remote.time, "sleep", lambda s: None)
    posts = []

    def get(url, headers=None, timeout=None):
        return _Response(_health("h2" if url.startswith(BOX2) else None))

    def post(url, json=None, headers=None, timeout=None):
        posts.append(url)
        return _Response(lost if len(posts) == 1 else DONE)

    monkeypatch.setattr(remote.requests, "get", get)
    monkeypatch.setattr(remote.requests, "post", post)
    job = _job(jtype="mcp_tool", tool="look_at", mutation=False)
    remote._run_remote(job)
    assert [u.split("/calls/")[0] for u in posts] == [BOX2, BOX2]
    assert "/bx-h2-alt1-mcp_tool-p5-" in posts[1]


def test_children_inside_a_slot_never_leave_their_box(monkeypatch):
    monkeypatch.setenv("BOX_ROUTE_ALL", "1")
    monkeypatch.setenv("BOX_EXECUTOR_URL", "http://172.30.0.1:8090")
    box_routing.set_routing_for_tests(TWO)        # not consulted in a slot
    child = {"id": None, "type": "frames", "project_id": 5, "user_id": 60,
             "attempts": 0, "payload": {}}
    assert box_routing.candidates(child) == [("h1", True)]
    out, headers, _r = _trace(monkeypatch, [
        ("health",), ("refuse", 10.0), ("health",), ("done", 1.0)], child,
        health_id={"http://172.30.0.1:8090": "h2"})
    assert {p[0] for p in _post_targets(out)} == {"http://172.30.0.1:8090"}
    assert all(p[1].startswith("bx-frames-") for p in _post_targets(out))
    assert headers == [None, None]       # the local admission, whoever it is


def test_routing_document_is_validated():
    box_routing.set_routing_for_tests(dict(TWO, boxes=[
        {"url": BOX1},                                   # first: h1
        {"id": "H2", "url": BOX2 + "/", "classes": ["owner", "bogus"]},
        {"id": "h2", "url": "https://dup.example"},        # duplicate id
        {"id": "x9", "url": "https://bad.example"},        # bad id
        {"id": "h3"},                                      # no url
        "junk"]))
    listed = box_routing.boxes()
    assert [(b["id"], b["url"]) for b in listed] == [("h1", BOX1),
                                                    ("h2", BOX2)]
    assert listed[1]["classes"] == frozenset({"owner"})
    assert listed[1]["accepts"] == frozenset(box_routing.CLASSES)
    assert box_routing.url() == BOX1
    assert box_routing.box_url("h2") == BOX2
    assert box_routing.call_tag("h2") == "h2-"
    assert box_routing.call_box("bx-h2-mcp-p1-x") == "h2"
    assert box_routing.call_box("bx-mcp-p1-x") == "h1"


def test_the_add_box_documents_route_as_designed():
    """box/add_box.py writes what box_routing reads: smoke-only first, then
    h2 the factory's home and h1 customers first plus factory overflow."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "..", "box", "add_box.py")
    spec = importlib.util.spec_from_file_location("add_box_tool", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    smoke = tool.with_box(ONE, "h2", BOX2, "smoke")
    box_routing.set_routing_for_tests(smoke)
    for user in (60, 7, 8):
        assert box_routing.candidates({"user_id": user}) == [("h1", True)]
    assert box_routing.candidates(_job(box_pin="h2")) == [("h2", True)]
    home = tool.with_box(smoke, "h2", "", "home")
    box_routing.set_routing_for_tests(home)
    assert box_routing.candidates({"user_id": 60}) == [("h2", True),
                                                       ("h1", False)]
    assert box_routing.candidates({"user_id": 7}) == [("h1", True),
                                                      ("h2", False)]
    assert box_routing.candidates({"user_id": 8}) == [("h1", True),
                                                      ("h2", False)]
    assert box_routing.overflow_wait_s() == 30
    assert box_routing.enabled() and box_routing.url() == BOX1
    off = tool.with_box(home, "h2", "", "off")
    box_routing.set_routing_for_tests(off)
    for user in (60, 7, 8):
        assert box_routing.candidates({"user_id": user}) == [("h1", True)]
    assert remote._call_url("bx-h2-preview-p5-abc") == BOX2
