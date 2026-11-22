"""The footage ladder asks YouTube on both address families (20 Nov, Alexandr Wang m05: this Mac's IPv6 range was
walled while its IPv4 address opened every refused source)."""
from types import SimpleNamespace

import pytest

import config
import ytaccess
from footage import fetch

URL = "https://www.youtube.com/watch?v=abcdefghijk"
BOT = "ERROR: [youtube] abcdefghijk: Sign in to confirm you're not a bot. This helps protect our community."


@pytest.fixture
def ladder(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "YTDLP_PROXY", "", raising=False)
    monkeypatch.setattr(config, "YTDLP_PROXY_FALLBACK", [], raising=False)
    monkeypatch.setattr(ytaccess, "extraction_strategies", lambda have_cookies=False: [{"client": "tv", "name": "tv"}])
    monkeypatch.setattr(ytaccess, "pot_args", lambda: [])
    monkeypatch.setenv("FOOTAGE_PACE_DIR", str(tmp_path))
    monkeypatch.setenv("FOOTAGE_PACE_S", "0")
    monkeypatch.setattr(fetch, "_FAMILY", {})
    return tmp_path


def test_each_direct_rung_is_asked_again_on_ipv4_at_once(ladder):
    labels = [label for _, label in fetch._rungs(URL)]
    assert labels == ["default", "default+ipv4", "tv", "tv+ipv4"]
    assert all("--force-ipv4" in extra for extra, label in fetch._rungs(URL) if label.endswith("+ipv4"))


def test_ipv4_opens_what_the_walled_family_could_not_and_the_run_leads_with_it(ladder, monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        ok = "--force-ipv4" in cmd
        return SimpleNamespace(returncode=0 if ok else 1, stdout="{}" if ok else "", stderr="" if ok else BOT)
    monkeypatch.setattr(fetch.subprocess, "run", run)
    out, rung = fetch.run_ladder(URL, ["-J"])
    assert rung == "default+ipv4" and len(calls) == 2
    assert (ladder / fetch.FAMILY_FILE).read_text() == "4"                 # shared with every process of the run
    calls.clear()
    out, rung = fetch.run_ladder(URL, ["-J"])
    assert rung == "default+ipv4" and len(calls) == 1                         # the next fetch leads with IPv4
    assert [label for _, label in fetch._rungs(URL)][:2] == ["default+ipv4", "tv+ipv4"]


def test_a_proxy_route_keeps_its_own_rungs(ladder, monkeypatch):
    monkeypatch.setattr(config, "YTDLP_PROXY", "http://proxy.example:1", raising=False)
    labels = [label for _, label in fetch._rungs(URL)]
    assert labels == ["default+proxy", "tv+proxy"]


def test_an_open_default_family_is_unchanged(ladder, monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")
    monkeypatch.setattr(fetch.subprocess, "run", run)
    assert fetch.run_ladder(URL, ["-J"])[1] == "default"
    assert len(calls) == 1 and "--force-ipv4" not in calls[0]
    assert not (ladder / fetch.FAMILY_FILE).exists()
