"""box/yt_meter.py: the YouTube fallback's traffic meter and prepaid budget.

Run locally: python -m pytest box/tests
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import yt_meter as ym  # noqa: E402

NOW = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)
GW = ("geo.iproyal.com", 12321)
PRIMARY = ("185.113.182.198", 12323)


class FakeNft:
    """nft stand-in: one table with two counters, driven by the test."""

    def __init__(self, table=None, refuse=False):
        self.table = table
        self.refuse = refuse
        self.scripts = []
        self.calls = []

    def __call__(self, args, stdin=None, timeout=20):
        self.calls.append(args)
        if args[:2] == ["-j", "list"]:
            if self.table is None:
                return subprocess.CompletedProcess(args, 1, "", "No such")
            items = [{"metainfo": {"version": "1.0.9"}},
                     {"table": {"family": "inet", "name": ym.TABLE}}]
            items += [{"counter": {"family": "inet", "table": ym.TABLE,
                                   "name": n, "packets": 1,
                                   "bytes": self.table[k]}}
                      for n, k in ((ym.TX, "tx"), (ym.RX, "rx"))]
            items.append({"counter": {"table": "other", "name": ym.TX,
                                      "bytes": 999}})
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"nftables": items}), "")
        if args[:2] == ["-f", "-"]:
            if self.refuse:
                return subprocess.CompletedProcess(args, 1, "",
                                                   "Error: syntax error")
            self.scripts.append(stdin)
            self.table = {"tx": 0, "rx": 0}
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:1] == ["delete"]:
            self.table = None
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)


# ── endpoints: host:port only, never credentials ────────────────────────
def test_credentials_never_survive_parsing():
    assert ym.host_port("http://user:secret@geo.iproyal.com:12321") == GW
    assert ym.host_port("geo.iproyal.com:12321") == GW
    assert ym.host_port("http://GEO.iproyal.com:12321/") == GW
    for bad in ("", None, "http://geo.iproyal.com", "http://:80",
                "http://h:notaport"):
        assert ym.host_port(bad) is None
    eps = ym.parse_list("http://a:b@1.2.3.4:8000, geo.iproyal.com:12321,"
                        "http://1.2.3.4:8000,")
    assert eps == [("1.2.3.4", 8000), GW]
    assert "secret" not in json.dumps(ym.new_baseline(
        1, NOW, endpoints_=[ym.host_port("http://u:secret@h.example:1")]))


def test_endpoints_come_from_the_secret_and_the_budget_file(tmp_path):
    env = tmp_path / "executor.env"
    env.write_text("YTDLP_PROXY=http://185.113.182.198:12323\n"
                   "YTDLP_PROXY_FALLBACK=http://geo.iproyal.com:12321\n")
    assert ym.endpoints(str(env)) == [GW]
    assert ym.endpoints(str(env), {"endpoints": ["9.9.9.9:1",
                                                 "geo.iproyal.com:12321"]}) \
        == [GW, ("9.9.9.9", 1)]
    env.write_text("YTDLP_PROXY=http://185.113.182.198:12323\n")
    assert ym.endpoints(str(env)) == []
    assert ym.endpoints(str(tmp_path / "missing.env")) == []
    assert ym.env_value("YTDLP_PROXY", str(env)) == \
        "http://185.113.182.198:12323"


# ── the counting-only ruleset ───────────────────────────────────────────
def test_the_table_only_counts():
    script, sha = ym.ruleset([GW], PRIMARY, ["185.113.182.198"])
    table = script.split("delete table inet valmera_ytmeter\n", 1)[1]
    for word in (" drop", " reject", " accept\n", " jump", " goto", " nat",
                 " log", " queue", "masquerade", "redirect", " dnat",
                 " snat", " mark set"):
        assert word not in table.replace("policy accept;", ""), word
    assert table.count("policy accept;") == 3
    assert 'tcp dport 12321 counter name "fb_tx"' in table
    assert 'tcp sport 12321 counter name "fb_rx"' in table
    # one transaction that replaces the table (no partial state)
    assert script.startswith("table inet valmera_ytmeter\n"
                             "delete table inet valmera_ytmeter\n")
    assert ym.ruleset([GW], PRIMARY, ["185.113.182.198"])[1] == sha
    assert ym.ruleset([("geo.iproyal.com", 12322)])[1] != sha


def test_the_primary_is_never_counted_as_the_fallback():
    # a gateway on the primary's own port: the primary's address is excluded
    tx, rx = ym.rule_matches([("isp.example", 12323)], PRIMARY,
                             ["185.113.182.198"])
    assert tx[0] == "ip daddr != { 185.113.182.198 } tcp dport 12323"
    assert rx[0] == "ip saddr != { 185.113.182.198 } tcp sport 12323"
    assert tx[1] == "meta nfproto ipv6 tcp dport 12323"
    # a static fallback IP is matched by address and port
    tx, rx = ym.rule_matches([("185.113.182.199", 12323)], PRIMARY,
                             ["185.113.182.198"])
    assert tx == ["ip daddr 185.113.182.199 tcp dport 12323"]
    assert rx == ["ip saddr 185.113.182.199 tcp sport 12323"]
    # residential on its own port: the whole port
    assert ym.rule_matches([GW], PRIMARY, ["185.113.182.198"]) == (
        ["tcp dport 12321"], ["tcp sport 12321"])


def test_a_port_counted_whole_is_not_counted_twice():
    tx, _ = ym.rule_matches([GW, ("185.193.157.60", 12321),
                             ("2001:db8::1", 8080)])
    assert tx == ["tcp dport 12321", "ip6 daddr 2001:db8::1 tcp dport 8080"]


# ── accounting ───────────────────────────────────────────────────────────
def test_readings_add_up_per_day_and_survive_a_reset():
    fs, d = ym.take({}, {"tx": 100, "rx": 900}, NOW)
    assert d == 1000 and fs["bytes_by_day"] == {"2026-10-30": 1000}
    fs, d = ym.take(fs, {"tx": 150, "rx": 1850}, NOW + timedelta(hours=1))
    assert d == 1000 and fs["total_bytes"] == 2000
    # reboot / rebuild: counters start again from zero
    fs, d = ym.take(fs, {"tx": 10, "rx": 40}, NOW + timedelta(hours=11))
    assert d == 50 and fs["bytes_by_day"] == {"2026-10-31": 50,
                                              "2026-10-30": 2000}
    fs, d = ym.take(fs, None, NOW + timedelta(hours=12))
    assert d == 0 and fs["last"] == {"tx": 0, "rx": 0}
    assert fs["total_bytes"] == 2050
    many = {"bytes_by_day": {f"2026-09-{i:02d}": 1 for i in range(1, 31)}}
    fs, _ = ym.take(many, {"tx": 1, "rx": 0}, NOW)
    assert len(fs["bytes_by_day"]) == ym.KEEP_DAYS
    assert "2026-10-30" in fs["bytes_by_day"]


def test_the_budget_goes_low_under_the_floor():
    b = ym.new_baseline(1.35, NOW)
    v = ym.budget_view(b)
    assert v["set"] and v["remaining_est_gb"] == 1.35 and not v["low"]
    assert v["floor_gb"] == 0.3
    b = ym.spend(b, 1_000_000_000)
    v = ym.budget_view(b)
    assert v["used_gb"] == 1.0 and v["remaining_est_gb"] == 0.35
    assert not v["low"]
    v = ym.budget_view(ym.spend(b, 60_000_000))
    assert v["remaining_est_gb"] == 0.29 and v["low"]
    assert ym.budget_view(None) == {"set": False, "low": False}
    assert ym.spend(None, 5) is None
    # a new baseline keeps the floor and the endpoints unless given
    old = {"floor_gb": 0.5, "endpoints": ["geo.iproyal.com:12321"]}
    nb = ym.new_baseline(10, NOW, old=old)
    assert nb["floor_gb"] == 0.5 and nb["used_bytes"] == 0
    assert nb["endpoints"] == ["geo.iproyal.com:12321"]
    assert ym.new_baseline(10, NOW, 0.2, ["x:1"], old)["floor_gb"] == 0.2


def test_a_look_builds_the_table_reads_it_and_spends_the_budget():
    nft = FakeNft()
    budget = ym.new_baseline(1.35, NOW)
    fs, budget, d = ym.look({}, budget, [GW], PRIMARY, NOW, run=nft,
                            resolve=lambda h: ["185.113.182.198"])
    assert d == 0 and len(nft.scripts) == 1 and "error" not in fs
    assert fs["endpoints"] == ["geo.iproyal.com:12321"] and fs["rules"]
    nft.table = {"tx": 4_000_000, "rx": 96_000_000}
    fs, budget, d = ym.look(fs, budget, [GW], PRIMARY, NOW, run=nft,
                            resolve=lambda h: ["185.113.182.198"])
    assert d == 100_000_000 and budget["used_bytes"] == 100_000_000
    assert len(nft.scripts) == 1          # unchanged rules: not rebuilt
    # the endpoints change: the bytes so far are kept, the table rebuilt
    nft.table = {"tx": 5_000_000, "rx": 96_000_000}
    eps = [GW, ("geo.iproyal.com", 12322)]
    fs, budget, d = ym.look(fs, budget, eps, PRIMARY, NOW, run=nft,
                            resolve=lambda h: [])
    assert d == 1_000_000 and len(nft.scripts) == 2
    assert fs["last"] == {"tx": 0, "rx": 0}
    assert fs["total_bytes"] == 101_000_000
    # the table vanished (reboot): recreated at the next look
    nft.table = None
    fs, budget, d = ym.look(fs, budget, eps, PRIMARY, NOW, run=nft,
                            resolve=lambda h: [])
    assert d == 0 and len(nft.scripts) == 3 and nft.table == {"tx": 0,
                                                               "rx": 0}
    # no fallback any more: the table is removed
    nft.table = {"tx": 7, "rx": 3}
    fs, budget, d = ym.look(fs, budget, [], PRIMARY, NOW, run=nft)
    assert d == 10 and nft.table is None and "rules" not in fs
    # and a box with no fallback never creates one
    nft2 = FakeNft()
    ym.look({}, None, [], None, NOW, run=nft2)
    assert nft2.scripts == []


def test_a_meter_failure_is_reported_not_raised():
    nft = FakeNft(refuse=True)
    fs, budget, d = ym.look({}, None, [GW], None, NOW, run=nft)
    assert "nft refused" in fs["error"] and d == 0

    def missing(args, stdin=None, timeout=20):
        raise FileNotFoundError("nft")
    fs, _, _ = ym.look({}, None, [GW], None, NOW, run=missing)
    assert fs["error"].startswith("FileNotFoundError")
    fs, _, _ = ym.look(fs, None, [GW], None, NOW, run=FakeNft())
    assert "error" not in fs


def test_the_budget_file_round_trips(tmp_path):
    path = str(tmp_path / "b.json")
    assert ym.load_budget(path) is None
    ym.save_budget(path, None)
    assert not os.path.exists(path)
    b = ym.new_baseline(1.35, NOW)
    ym.save_budget(path, b)
    assert ym.load_budget(path) == b
    (tmp_path / "bad.json").write_text("{nope")
    assert ym.load_budget(str(tmp_path / "bad.json")) is None
