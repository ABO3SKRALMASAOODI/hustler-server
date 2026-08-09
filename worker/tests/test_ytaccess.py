"""ytaccess — the five cookie doors, the normalizer, PO tokens, the probe.

The history these tests pin down (Aug 8-9): a valid, logged-in cookie jar
was delivered THREE ways that each silently degraded to anonymous — pasted
into a dashboard field that flattened the tabs the Netscape format
requires, pasted as the VALUE of the path env var, and finally rotated out
by Google while everyone debugged the plumbing. Every one of those must
now either work or say why it doesn't.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config                                                  # noqa: E402
import ytaccess                                                # noqa: E402

JAR = ("# Netscape HTTP Cookie File\n"
       ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc123\n")
FLAT_JAR = ("# Netscape HTTP Cookie File\n"
            ".youtube.com TRUE / TRUE 0 SID abc123\n")


@pytest.fixture(autouse=True)
def _all_doors_shut(monkeypatch):
    """Every test opens exactly the doors it means to test."""
    monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", "")
    monkeypatch.setattr(config, "YTDLP_COOKIES", "")
    monkeypatch.setattr(config, "YTDLP_SECRETS_DIR", "")
    monkeypatch.setattr(config, "YTDLP_COOKIES_KV_KEY", "")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    ytaccess._kv_cache_reset()
    yield
    ytaccess._kv_cache_reset()


# ── the failure vocabulary ───────────────────────────────────────────────

def test_bot_wall_and_stale_cookies_are_distinct_diagnoses():
    wall = "ERROR: Sign in to confirm you're not a bot"
    stale = ("WARNING: [youtube] The provided YouTube account cookies are "
             "no longer valid.")
    assert ytaccess.bot_walled(wall) and not ytaccess.stale_cookies(wall)
    assert ytaccess.stale_cookies(stale) and not ytaccess.bot_walled(stale)


# ── jar-or-path discrimination and the normalizer ────────────────────────

def test_a_filesystem_path_is_never_mistaken_for_jar_content():
    assert not ytaccess._looks_like_jar("/etc/secrets/yt-cookies.txt")
    assert ytaccess._looks_like_jar(JAR)
    assert ytaccess._looks_like_jar(FLAT_JAR)          # header alone is enough
    assert ytaccess._looks_like_jar(FLAT_JAR.splitlines()[1] + "\n")


def test_normalizer_restores_tabs_and_real_newlines():
    # A dashboard paste: tabs flattened to spaces, newlines to literal \n.
    mangled = FLAT_JAR.replace("\n", "\\n")
    fixed = ytaccess._normalize_jar(mangled)
    assert ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc123" in fixed
    # Already-correct lines pass through verbatim.
    assert ytaccess._normalize_jar(JAR).strip() == JAR.strip()


# ── the five doors, in order ─────────────────────────────────────────────

def test_door2_a_real_file_path_beats_env_content(monkeypatch,
                                                  tmp_path):
    p = tmp_path / "jar.txt"
    p.write_text(JAR)
    monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", str(p))
    monkeypatch.setattr(config, "YTDLP_COOKIES", FLAT_JAR)
    content, source = ytaccess.resolve_cookies()
    assert source == "file" and "SID" in content


def test_door3_jar_content_pasted_into_the_path_var_still_works(monkeypatch):
    """The Aug-9 delivery: the whole Netscape file as the env var VALUE.
    The old path-only code silently ignored it — the exact bug that kept
    'still not working' true through two days of correct-looking setup."""
    monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", FLAT_JAR)
    content, source = ytaccess.resolve_cookies()
    assert source == "env-inline" and "SID" in content


def test_door4_plain_content_var(monkeypatch):
    monkeypatch.setattr(config, "YTDLP_COOKIES", FLAT_JAR)
    assert ytaccess.resolve_cookies()[1] == "env"


def test_door5_secrets_scan_prefers_cookie_named_files(monkeypatch,
                                                       tmp_path):
    (tmp_path / "database-url").write_text("postgres://not-a-jar")
    (tmp_path / "zz-cookies.txt").write_text(JAR)
    (tmp_path / "aa-other.txt").write_text(FLAT_JAR)
    monkeypatch.setattr(config, "YTDLP_SECRETS_DIR", str(tmp_path))
    content, source = ytaccess.resolve_cookies()
    assert source == "secrets-scan"
    assert content == JAR          # the cookie-NAMED file beats sort order


def test_door1_db_row_with_ttl_cache(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgres://x")
    monkeypatch.setattr(config, "YTDLP_COOKIES_KV_KEY", "ytdlp_cookies")
    calls = {"n": 0}

    class FakeDb:
        def run(self, fn, *a, **k):
            calls["n"] += 1
            return JAR
    monkeypatch.setattr(ytaccess.db, "Db", FakeDb)
    assert ytaccess.resolve_cookies() == (JAR, "db")
    assert ytaccess.resolve_cookies() == (JAR, "db")
    assert calls["n"] == 1                     # second hit rode the cache
    ytaccess._kv_cache_reset()
    ytaccess.resolve_cookies()
    assert calls["n"] == 2


def test_the_psql_row_overrides_a_mounted_file(monkeypatch, tmp_path):
    """Aug 9, live: the box's mounted 55-entry jar was rotated-dead while
    a fresh valid jar existed, and the dashboard was out of reach. The
    psql row is the OVERRIDE door — it must beat the corpse on disk."""
    stale = tmp_path / "mounted.txt"
    stale.write_text(FLAT_JAR.replace("abc123", "rotted"))
    monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", str(stale))
    monkeypatch.setattr(config, "DATABASE_URL", "postgres://x")
    monkeypatch.setattr(config, "YTDLP_COOKIES_KV_KEY", "ytdlp_cookies")

    class FakeDb:
        def run(self, fn, *a, **k):
            return JAR
    monkeypatch.setattr(ytaccess.db, "Db", FakeDb)
    content, source = ytaccess.resolve_cookies()
    assert source == "db" and "rotted" not in content


def test_db_trouble_degrades_to_anonymous(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgres://x")
    monkeypatch.setattr(config, "YTDLP_COOKIES_KV_KEY", "ytdlp_cookies")

    class DeadDb:
        def run(self, *a, **k):
            raise RuntimeError("db is down")
    monkeypatch.setattr(ytaccess.db, "Db", DeadDb)
    assert ytaccess.resolve_cookies() == (None, "none")


# ── per-run jars ─────────────────────────────────────────────────────────

def test_run_jar_is_normalized_and_lives_in_the_workdir(monkeypatch,
                                                        tmp_path):
    monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", FLAT_JAR)
    path = ytaccess.prepare_run_jar(str(tmp_path))
    assert path == os.path.join(str(tmp_path), "cookies_run.txt")
    assert "\tSID\t" in open(path).read()      # tabs restored on the way in


def test_no_cookies_means_no_jar_not_a_crash():
    assert ytaccess.prepare_run_jar() is None


# ── PO tokens ────────────────────────────────────────────────────────────

def test_pot_args_engage_only_when_the_script_dir_exists(monkeypatch,
                                                         tmp_path):
    monkeypatch.setattr(config, "YTDLP_POT_SERVER_HOME",
                        str(tmp_path / "absent"))
    assert ytaccess.pot_args() == []
    monkeypatch.setattr(config, "YTDLP_POT_SERVER_HOME", str(tmp_path))
    args = ytaccess.pot_args()
    assert args[0] == "--extractor-args"
    assert args[1].startswith("youtubepot-bgutilscript:server_home=")


# ── the probe reads its own tea leaves correctly ─────────────────────────

def _fake_probe_run(monkeypatch, stdout="", stderr="", rc=0):
    class R:
        pass

    def run(cmd, **kw):
        r = R()
        r.stdout, r.stderr, r.returncode = stdout, stderr, rc
        return r
    monkeypatch.setattr(ytaccess.subprocess, "run", run)


def test_probe_ok(monkeypatch):
    _fake_probe_run(monkeypatch, stdout="probe_ok id=x fmt=251\n")
    v = ytaccess.probe()
    assert v["ok"] and "stale_cookies" not in v


def test_probe_names_the_bot_wall(monkeypatch):
    _fake_probe_run(monkeypatch, stderr="ERROR: Sign in to confirm you're "
                    "not a bot. Use --cookies", rc=1)
    v = ytaccess.probe()
    assert not v["ok"] and v["why"].startswith("bot_wall")


def test_probe_flags_a_rotted_jar_even_on_success(monkeypatch):
    """Cookies rejected + anonymous success = works today, walls tomorrow.
    The flag is what tells the operator to refresh BEFORE it breaks."""
    monkeypatch.setattr(config, "YTDLP_COOKIES", FLAT_JAR)
    _fake_probe_run(monkeypatch, stdout="probe_ok id=x fmt=251\n",
                    stderr="WARNING: account cookies are no longer valid")
    v = ytaccess.probe()
    assert v["ok"] and v["stale_cookies"]
    assert v["cookie_source"] == "env" and v["cookie_entries"] == 1


def test_probe_downloads_only_after_extraction_passes(monkeypatch):
    """Aug 9: --simulate passed from the very box whose downloads still
    walled — the two gates are independent and the probe must see both."""
    calls = []

    def run(cmd, **kw):
        calls.append(list(cmd))

        class R:
            stdout, stderr, returncode = "probe_ok id=x fmt=1\n", "", 0
        return R()
    monkeypatch.setattr(ytaccess.subprocess, "run", run)
    v = ytaccess.probe()
    assert len(calls) == 2
    assert "--simulate" in calls[0] and "--simulate" not in calls[1]
    assert v["ok"] and not v["download_ok"]    # the fake wrote no file
    assert v["download_why"]


def test_probe_verdict_is_json_ready(monkeypatch):
    _fake_probe_run(monkeypatch, stdout="probe_ok id=x fmt=251\n")
    json.dumps(ytaccess.probe())
