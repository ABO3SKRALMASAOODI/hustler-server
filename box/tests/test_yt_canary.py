"""box/yt_canary.py: YouTube through the box's proxy, watched daily.

Run locally: python -m pytest box/tests
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import yt_canary as yc  # noqa: E402

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def _at(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def _slot(name, lane, **kw):
    s = {"name": name, "lane": lane, "ready": True, "busy": False,
         "retiring": False, "quick": False, "status": "ok"}
    s.update(kw)
    return s


# ── when a probe runs ────────────────────────────────────────────────────
def test_a_healthy_route_is_probed_once_a_day():
    state = {"runs": [{"at": _at(60), "ok": True}], "last_ok_at": _at(60)}
    assert yc.due(state, NOW, {})[0] is False
    state = {"runs": [{"at": _at(23 * 60 + 1), "ok": True}],
             "last_ok_at": _at(23 * 60 + 1)}
    assert yc.due(state, NOW, {}) == (True, "daily")


def test_a_failure_is_retried_within_the_hour_not_the_next_day():
    state = {"runs": [{"at": _at(30), "ok": False}],
             "last_ok_at": _at(25 * 60)}
    assert yc.due(state, NOW, {})[0] is False
    state["runs"][-1]["at"] = _at(51)
    assert yc.due(state, NOW, {}) == (True, "retry after a failure")


def test_first_run_and_force_always_probe():
    assert yc.due({}, NOW, {}) == (True, "first run")
    state = {"runs": [{"at": _at(1), "ok": True}], "last_ok_at": _at(1)}
    assert yc.due(state, NOW, {}, force=True) == (True, "forced")


def test_a_skipped_run_is_retried_and_never_counts_as_a_failure():
    state = yc.record({"consecutive_failures": 1},
                      {"at": _at(0), "skipped": True, "ok": None}, NOW)
    assert state["consecutive_failures"] == 1
    assert "alert" not in state
    assert yc.due(state, NOW + timedelta(minutes=51), {})[0] is True


# ── the streak that pages the owner ─────────────────────────────────────
def test_two_failures_in_a_row_raise_the_alert_and_a_pass_clears_it():
    s = yc.record({}, {"at": _at(60), "ok": False, "why": "bot wall"}, NOW)
    assert s["consecutive_failures"] == 1 and s["alert"] is False
    s = yc.record(s, {"at": _at(0), "ok": False, "why": "bot wall"}, NOW)
    assert s["consecutive_failures"] == 2 and s["alert"] is True
    assert s["last_why"] == "bot wall"
    s = yc.record(s, {"at": _at(-60), "ok": True}, NOW)
    assert s["consecutive_failures"] == 0 and s["alert"] is False
    assert s["last_ok_at"] == _at(-60)


def test_the_run_history_is_bounded():
    s = {}
    for i in range(yc.KEEP_RUNS + 5):
        s = yc.record(s, {"at": _at(-i), "ok": True}, NOW)
    assert len(s["runs"]) == yc.KEEP_RUNS


# ── the verdict ──────────────────────────────────────────────────────────
def test_one_removed_video_does_not_fail_the_route():
    ans = {"probes": [
        {"url": "https://www.youtube.com/shorts/x", "ok": False,
         "download_ok": False, "why": "error: Video unavailable"},
        {"url": "https://www.youtube.com/watch?v=y", "ok": True,
         "download_ok": True, "strategy": "web_embedded"}]}
    ok, why, best = yc.judge(ans)
    assert ok is True and why == "" and best["strategy"] == "web_embedded"


def test_extraction_alone_is_not_a_pass():
    ans = {"probes": [{"url": "u1", "ok": True, "download_ok": False,
                       "download_why": "access_blocked: HTTP Error 403"}]}
    ok, why, _ = yc.judge(ans)
    assert ok is False and "403" in why


def test_a_wall_on_every_video_fails_with_the_reasons():
    ans = {"probes": [
        {"url": "https://www.youtube.com/watch?v=a", "ok": False,
         "why": "access_blocked: Sign in to confirm you're not a bot"},
        {"url": "https://www.youtube.com/watch?v=b", "ok": False,
         "why": "access_blocked: Sign in to confirm you're not a bot"}]}
    ok, why, best = yc.judge(ans)
    assert ok is False and why.count("not a bot") == 2
    assert best["url"].endswith("v=a")
    assert yc.judge({})[:2] == (False, "no probe ran")


def test_the_probe_row_keeps_the_shape_the_worker_and_gate_read():
    doc = yc.probe_doc({"ok": True, "download_ok": True, "url": "u"},
                       {"code": "537b38442a24"}, "185.113.182.198:12323",
                       NOW)
    # worker ytaccess.provider_youtube_ok + round2 stage_gate youtube_gate
    assert doc["ok"] is True and doc["download_ok"] is True
    assert doc["provider"] == "box" and doc["code"] == "537b38442a24"
    assert doc["source"] == "yt_canary"
    bad = yc.probe_doc({}, None, None, NOW)
    assert bad["ok"] is False and bad["download_ok"] is False


# ── where it probes from ────────────────────────────────────────────────
def test_an_idle_mcp_slot_is_preferred_highest_number_first():
    state = {"slot_detail": [
        _slot("interactive1", "interactive"), _slot("batch3", "batch"),
        _slot("mcp2", "mcp"), _slot("mcp12", "mcp", busy=True),
        _slot("mcp11", "mcp"), _slot("mcp9", "mcp")]}
    assert yc.pick_slot(state) == "vbox-mcp11"


def test_busy_retiring_quick_unready_or_unrolled_slots_are_never_used():
    state = {"slot_detail": [
        _slot("mcp1", "mcp", busy=True), _slot("mcp2", "mcp", retiring=True),
        _slot("look1", "interactive", quick=True),
        _slot("mcp3", "mcp", ready=False), _slot("mcp4", "mcp"),
        _slot("batch1", "batch")]}
    assert yc.pick_slot(state, lambda n: n != "vbox-mcp4") == "vbox-batch1"
    assert yc.pick_slot(state, lambda n: False) is None
    assert yc.pick_slot(None) is None


# ── what it reports, and never ──────────────────────────────────────────
def test_the_proxy_is_reported_as_host_port_only(tmp_path):
    env = tmp_path / "executor.env"
    env.write_text("DATABASE_URL=postgres://x\n"
                   "YTDLP_PROXY=http://user:secret@10.0.0.1:12323\n")
    assert yc.proxy_host(str(env)) == "10.0.0.1:12323"
    env.write_text("YTDLP_PROXY=http://185.113.182.198:12323\n")
    assert yc.proxy_host(str(env)) == "185.113.182.198:12323"
    env.write_text("YTDLP_PROXY=\n")
    assert yc.proxy_host(str(env)) is None
    env.write_text("OTHER=1\n")
    assert yc.proxy_host(str(env)) is None


def test_bytes_per_day_split_what_went_through_the_box():
    rows = [
        {"day": "2026-10-30", "provider": "box", "n": 2, "bytes": 300},
        {"day": "2026-10-30", "provider": "worker", "n": 1, "bytes": 50},
        {"day": "2026-10-30", "provider": "cloudflare", "n": 4, "bytes": 900},
        {"day": "2026-10-29", "provider": "cloudflare", "n": 1, "bytes": 7}]
    out = yc.bytes_by_day(rows)
    assert out["2026-10-30"] == {
        "assets": 7, "bytes": 1250, "proxy_bytes_est": 350,
        "by_provider": {"box": 300, "worker": 50, "cloudflare": 900}}
    assert out["2026-10-29"]["proxy_bytes_est"] == 0
    assert list(yc.bytes_by_day(rows, days=1)) == ["2026-10-30"]


def test_no_proxy_is_a_failure_not_a_skip(tmp_path, monkeypatch):
    env = tmp_path / "executor.env"
    env.write_text("DATABASE_URL=postgres://x\n")
    written = {}
    monkeypatch.setattr(yc.hr, "ENV_PATH", str(env))
    monkeypatch.setattr(yc.hr, "CONFIG_PATH", str(tmp_path / "box.json"))
    monkeypatch.setattr(yc.hr, "maintenance_until", lambda: None)
    monkeypatch.setattr(yc.hr, "write",
                        lambda k, d: written.__setitem__(k, d))
    monkeypatch.setattr(yc, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(yc, "read_bytes", lambda: {})
    assert yc.main(["yt_canary.py"]) == 0
    doc = written["youtube_canary"]
    assert doc["consecutive_failures"] == 1 and doc["ok"] is False
    assert "YTDLP_PROXY" in doc["last_why"]
    assert written["ytdlp_probe_box"]["ok"] is False
    # The local state survives for the next hourly look.
    assert yc.load_state()["consecutive_failures"] == 1


def test_a_due_run_probes_the_chosen_slot_and_writes_both_rows(
        tmp_path, monkeypatch):
    env = tmp_path / "executor.env"
    env.write_text("YTDLP_PROXY=http://185.113.182.198:12323\n")
    written, calls = {}, []
    monkeypatch.setattr(yc.hr, "ENV_PATH", str(env))
    monkeypatch.setattr(yc.hr, "CONFIG_PATH", str(tmp_path / "box.json"))
    monkeypatch.setattr(yc.hr, "maintenance_until", lambda: None)
    monkeypatch.setattr(yc.hr, "admission_state", lambda cfg: {
        "slot_detail": [_slot("mcp4", "mcp")]})
    monkeypatch.setattr(yc, "container_has_proxy", lambda n: True)
    monkeypatch.setattr(yc, "slots_missing_proxy", lambda: [])
    monkeypatch.setattr(yc.hr, "write",
                        lambda k, d: written.__setitem__(k, d))
    monkeypatch.setattr(yc, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(yc, "read_bytes", lambda: {"2026-10-30": {}})

    def fake_probes(slot, urls):
        calls.append((slot, urls))
        return {"code": "537b38442a24", "probes": [
            {"url": urls[0], "ok": True, "download_ok": True,
             "strategy": "default"}]}
    monkeypatch.setattr(yc, "run_probes", fake_probes)
    assert yc.main(["yt_canary.py"]) == 0
    assert calls == [("vbox-mcp4", list(yc.DEFAULT_URLS))]
    assert written["youtube_canary"]["ok"] is True
    assert written["youtube_canary"]["proxy"] == "185.113.182.198:12323"
    assert written["youtube_canary"]["bytes_by_day"] == {"2026-10-30": {}}
    assert written["ytdlp_probe_box"]["download_ok"] is True
    # Not due again within the day.
    written.clear()
    assert yc.main(["yt_canary.py"]) == 0
    assert written == {}


def test_a_second_box_reports_under_its_own_keys():
    assert yc.keys_for({}) == ("youtube_canary", "ytdlp_probe_box")
    assert yc.keys_for({"box_id": "h2"}) == ("youtube_canary:h2",
                                             "ytdlp_probe_box:h2")


def test_slots_still_on_the_old_env_are_listed():
    names = ["vbox-mcp2", "vbox-bquick1", "vbox-batch3"]
    old = {"vbox-bquick1", "vbox-batch3"}
    assert yc.slots_missing_proxy(names, lambda n: n not in old) == [
        "vbox-batch3", "vbox-bquick1"]
    assert yc.slots_missing_proxy(names, lambda n: True) == []


def test_the_reason_keeps_the_gate_not_the_update_noise():
    long = ("access_blocked: WARNING: You are using an outdated version. "
            "To suppress this warning, add --no-update to your command. | "
            "WARNING: [youtube] No title found in player responses | ERROR: "
            "[youtube] abc: Sign in to confirm you're not a bot")
    out = yc._kind_and_tail(long, n=60)
    assert out.startswith("access_blocked: ...") and out.endswith("not a bot")
    assert yc._kind_and_tail("error: HTTP Error 404") == "error: HTTP Error 404"
    assert yc._kind_and_tail(None) == "?"
