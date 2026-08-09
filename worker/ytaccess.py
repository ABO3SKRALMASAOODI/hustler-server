"""Keeping yt-dlp fetchable from a datacenter IP (round 102).

YouTube challenges datacenter egress with "Sign in to confirm you're not a
bot", and Render's IP is exactly that. Two independent answers ride every
yt-dlp call this repo makes (url_media, song_find, and the boot probe):

  * PO tokens — the bgutil provider plugin mints anonymous proof-of-origin
    tokens with a JS script baked into the image (see the Dockerfile's POT
    layer). No account, no state, nothing that rotates. This is the door
    that stays open by itself.

  * Operator cookies — a logged-in session jar. Effective but PERISHABLE:
    Google rotates the session tokens server-side while the exporting
    browser keeps running, and the export dies within about a day. Both
    Aug-8 jars died exactly that way — yt-dlp's own words are "account
    cookies are no longer valid", and stale_cookies() below watches for
    them so nobody debugs the plumbing when the payload is what expired.
    Export from a private window that is then CLOSED (AGENTS.md has the
    recipe) and the jar survives for weeks instead.

Cookie DELIVERY has five doors because each one is a door somebody
actually tried to use (Aug 8-9: the jar went into a dashboard text field
as an env VALUE, where the old path-only code silently ignored it):

  1. The app_kv row 'ytdlp_cookies' — delivery over psql. FIRST on
     purpose: it is the OVERRIDE door, the one an operator reaches for
     precisely because the jar already mounted on the box has gone stale
     and the dashboard is out of reach (Aug 9: the mounted 55-entry file
     was rotated-dead while a fresh valid jar existed — a file-first
     order would have kept prod on the corpse forever). When a fresh
     secret file goes up later, DELETE the row; the probe's
     cookie_source names the active door, so the shadowing can't hide.
  2. YTDLP_COOKIES_FILE naming a real file — the documented Render Secret
     File setup.
  3. YTDLP_COOKIES_FILE holding the jar CONTENT itself — pasting content
     where a path belongs must work, not silently degrade.
  4. YTDLP_COOKIES holding the content, for operators who never saw the
     _FILE convention.
  5. Any jar-shaped file in /etc/secrets — the secret file was mounted but
     the pointer env var was never set.

Every door feeds the same normalizer (dashboard fields flatten the tabs
the Netscape format requires — yt-dlp then ignores every line without a
word) and every caller gets a WRITABLE per-run copy, never a shared or
mounted file: yt-dlp writes rotated cookies back to the jar it is handed,
and /etc/secrets is read-only ([Errno 30], Aug 8).
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import config
import db

# ── the shared failure vocabulary ────────────────────────────────────────


def bot_walled(detail):
    """YouTube's datacenter-IP bot check — the one extractor failure that a
    different player client often gets past, so it is worth exactly one
    retry. Matched on the phrases yt-dlp surfaces for it."""
    d = (detail or "").lower()
    return ("sign in to confirm" in d or "not a bot" in d
            or "--cookies" in d)


def stale_cookies(detail):
    """yt-dlp's warning for a jar Google has rotated out from under us.
    This is a PAYLOAD failure, not a plumbing one — the answer is a fresh
    export, and saying so saves the next person a day of pipe-chasing."""
    return "cookies are no longer valid" in (detail or "").lower()


# ── resolution: five doors, one jar ──────────────────────────────────────

def _looks_like_jar(text):
    """A Netscape jar, however mangled: a comment header, or at least one
    line that splits into the 7+ fields of a cookie row. A filesystem PATH
    never does either, which is the whole discrimination this needs."""
    if not text or len(text) < 8:
        return False
    stripped = text.lstrip()
    if stripped.startswith("# Netscape") or stripped.startswith("# HTTP"):
        return True
    return any(len(line.split()) >= 7 for line in text.splitlines()
               if not line.lstrip().startswith("#"))


def _normalize_jar(text):
    """Restore the TABS the Netscape format requires.

    The jar reaches production by being pasted through editors and
    dashboard text fields that quietly flatten tabs to spaces — after
    which yt-dlp ignores every line WITHOUT A WORD, and a fresh, valid,
    logged-in jar behaves exactly like no jar at all (Aug 8: the wall
    survived a same-day cookie refresh, and the pasted file showed
    four-space runs where the tabs had been). A line that already has tabs
    is kept verbatim; a tabless line that splits into the 7+ whitespace-
    separated fields of a cookie row is re-joined with tabs, everything
    after field 7 staying glued as the value. Literal two-character "\\n"
    sequences become real newlines first — a single-line env paste is how
    a multi-line file comes out of some dashboards."""
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\n", "\n")
    out = []
    for raw in text.splitlines():
        raw = raw.rstrip("\r")
        if not raw.strip() or raw.lstrip().startswith("#") or "\t" in raw:
            out.append(raw)
            continue
        fields = raw.split()
        if len(fields) >= 7:
            raw = "\t".join(fields[:6]) + "\t" + " ".join(fields[6:])
        out.append(raw)
    return "\n".join(out) + "\n"


def _read_file(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _scan_secrets_dir():
    """Door 4: a jar-shaped file in the secrets mount, name unknown.
    Files whose name mentions cookies are preferred; ties break on sorted
    name so the pick is deterministic across boots."""
    d = config.YTDLP_SECRETS_DIR
    if not d or not os.path.isdir(d):
        return None
    named, shaped = [], []
    try:
        entries = sorted(os.listdir(d))
    except OSError:
        return None
    for name in entries:
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        try:
            if os.path.getsize(p) > 1 << 20:      # a jar is KBs, not MBs
                continue
        except OSError:
            continue
        content = _read_file(p)
        if content is None or not _looks_like_jar(content):
            continue
        (named if "cookie" in name.lower() else shaped).append(content)
    return (named or shaped or [None])[0]


# Door 5 caches its DB read: fetches are rare but come in bursts (a search
# then a download in the same turn), and a jar update landing within five
# minutes is prompt enough for an operator working over psql.
_KV_TTL_S = 300.0
_kv_cache = {"at": 0.0, "content": None}


def _kv_cookies():
    if not config.DATABASE_URL or not config.YTDLP_COOKIES_KV_KEY:
        return None
    now = time.monotonic()
    if now - _kv_cache["at"] < _KV_TTL_S:
        return _kv_cache["content"]
    content = None
    try:
        value = db.Db().run(db.kv_get, config.YTDLP_COOKIES_KV_KEY)
        if value and _looks_like_jar(value):
            content = value
    except Exception:
        content = None                # any DB trouble degrades to anonymous
    _kv_cache.update(at=now, content=content)
    return content


def _kv_cache_reset():
    _kv_cache.update(at=0.0, content=None)


# The boot probe's verdict, read back so callers can ROUTE on it. find_song
# uses this to lead with SoundCloud when YouTube is blocking this box —
# there is no point recommending a source the datacenter IP cannot reach.
_health_cache = {"at": 0.0, "walled": None}


def youtube_walled():
    """True when this box's last boot probe shows YouTube blocking its IP.

    Read from the app_kv 'ytdlp_probe' row (either the extraction or the
    download stage reporting a bot_wall). Self-healing: add a residential
    YTDLP_PROXY and the next boot's probe clears the wall, so ordering
    reverts on its own. Unknown, or any DB trouble, returns False — only a
    POSITIVE, observed wall changes behavior, never a guess."""
    if not config.DATABASE_URL:
        return False
    now = time.monotonic()
    if now - _health_cache["at"] < _KV_TTL_S:
        return bool(_health_cache["walled"])
    walled = False
    try:
        raw = db.Db().run(db.kv_get, "ytdlp_probe")
        if raw:
            v = json.loads(raw)
            walled = (str(v.get("why", "")).startswith("bot_wall")
                      or str(v.get("download_why", "")).startswith("bot_wall"))
    except Exception:
        walled = False
    _health_cache.update(at=now, walled=walled)
    return walled


def _health_cache_reset():
    _health_cache.update(at=0.0, walled=None)


def resolve_cookies():
    """The jar content and which door it came through, or (None, source).

    Checked on every call rather than at import: a missing secret file
    must degrade to the normal anonymous attempt, not crash every fetch —
    and a jar delivered mid-flight (psql, redeploy) must start working
    without a restart."""
    from_db = _kv_cookies()
    if from_db:
        return from_db, "db"
    spec = (config.YTDLP_COOKIES_FILE or "").strip()
    if spec:
        if os.path.isfile(spec):
            content = _read_file(spec)
            if content:
                return content, "file"
        elif _looks_like_jar(spec):
            return spec, "env-inline"
    inline = (config.YTDLP_COOKIES or "").strip()
    if inline and _looks_like_jar(inline):
        return inline, "env"
    scanned = _scan_secrets_dir()
    if scanned:
        return scanned, "secrets-scan"
    return None, "none"


def prepare_run_jar(workdir=None):
    """A normalized, WRITABLE, per-run cookie jar path, or None.

    With a workdir the jar lives (and dies) with it; without one it is a
    tempfile the CALLER must unlink. Never the source file itself — see
    the module docstring for the read-only-mount crash this rule buries."""
    content, _source = resolve_cookies()
    if not content:
        return None
    try:
        if workdir:
            path = os.path.join(workdir, "cookies_run.txt")
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        else:
            fd, path = tempfile.mkstemp(prefix="ytck_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_normalize_jar(content))
        return path
    except OSError:
        return None                   # degrade to anonymous, never crash


# ── PO tokens: the door that stays open by itself ────────────────────────

def pot_args():
    """Extractor args pointing yt-dlp's bgutil plugin at the baked token
    script, or nothing where the image layer is absent (dev machines,
    POT=0 builds) — gating on the directory keeps every environment
    honest without a config flag to forget."""
    home = config.YTDLP_POT_SERVER_HOME
    if home and os.path.isdir(home):
        return ["--extractor-args",
                f"youtubepot-bgutilscript:server_home={home}"]
    return []


# ── the boot probe: the answer to "is it working", asked from the box ────

def _probe_cmd(url, run_jar):
    cmd = [sys.executable, "-m", "yt_dlp",
           "--ignore-config",
           "--socket-timeout", "20",
           "-f", "ba/b"]
    if run_jar:
        cmd += ["--cookies", run_jar]
    if config.YTDLP_PROXY:
        cmd += ["--proxy", config.YTDLP_PROXY]
    if config.YTDLP_REMOTE_COMPONENTS:
        cmd += ["--remote-components", config.YTDLP_REMOTE_COMPONENTS]
    cmd += pot_args()
    return cmd, ["--", url]


def _tail(text, n=3, cap=300):
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return " | ".join(lines[-n:])[:cap] if lines else "no output"


def probe(url=None, timeout_s=120.0):
    """One real extraction AND one small real download from THIS box.

    Runs the exact production flag set (cookies, PO tokens, EJS solver,
    proxy) against one stable public video, in two stages, because prod
    fails at two different gates and Aug 9 proved they are independent:
    --simulate passed from the very box whose downloads still walled.

      simulate  — the bot wall at extraction + format resolution
      download  — the media-serving gate (a few hundred KB of worstaudio,
                  capped, deleted immediately)

    The probe URL can be overridden without a deploy through the app_kv
    row 'ytdlp_probe_url' — pointing the next boot at whatever video prod
    is actually failing on is the whole diagnostic loop."""
    if url is None and config.DATABASE_URL:
        try:
            url = (db.Db().run(db.kv_get, "ytdlp_probe_url") or "").strip()
        except Exception:
            url = ""
    url = url or config.YTDLP_PROBE_URL
    content, source = resolve_cookies()
    run_jar = prepare_run_jar()
    verdict = {"at": int(time.time()), "url": url,
               "cookie_source": source, "cookie_entries": 0,
               "pot": bool(pot_args()), "ok": False, "why": "",
               "download_ok": False, "download_why": ""}
    if content:
        verdict["cookie_entries"] = sum(
            1 for line in _normalize_jar(content).splitlines()
            if line.strip() and not line.startswith("#") and "\t" in line)
    base, target = _probe_cmd(url, run_jar)
    try:
        proc = subprocess.run(
            base + ["--simulate",
                    "--print", "probe_ok id=%(id)s fmt=%(format_id)s"]
            + target,
            capture_output=True, text=True, errors="replace",
            timeout=timeout_s)
        err = proc.stderr or ""
        verdict["ok"] = proc.returncode == 0 and "probe_ok" in (
            proc.stdout or "")
        if stale_cookies(err):
            verdict["stale_cookies"] = True
        if not verdict["ok"]:
            verdict["why"] = ("bot_wall" if bot_walled(err) else "error"
                              ) + ": " + _tail(err)
        if verdict["ok"]:
            with tempfile.TemporaryDirectory(prefix="ytprobe_") as d:
                proc = subprocess.run(
                    base + ["-f", "worstaudio/worst",
                            "--max-filesize", str(4 << 20),
                            "-o", os.path.join(d, "p.%(ext)s")]
                    + target,
                    capture_output=True, text=True, errors="replace",
                    timeout=timeout_s)
                err = proc.stderr or ""
                got = any(os.path.getsize(os.path.join(d, f))
                          for f in os.listdir(d))
                verdict["download_ok"] = proc.returncode == 0 and got
                if stale_cookies(err):
                    verdict["stale_cookies"] = True
                if not verdict["download_ok"]:
                    verdict["download_why"] = (
                        "bot_wall" if bot_walled(err) else "error"
                    ) + ": " + _tail(err)
    except subprocess.TimeoutExpired:
        verdict["why"] = verdict["why"] or f"timeout after {timeout_s:.0f}s"
    except Exception as e:                        # pragma: no cover
        verdict["why"] = verdict["why"] or f"probe crashed: {str(e)[:200]}"
    finally:
        if run_jar:
            try:
                os.unlink(run_jar)
            except OSError:
                pass
    return verdict


def boot_probe():
    """Run the probe once and leave the verdict where an operator can see
    it without the Render dashboard: one log line, and the app_kv row
    'ytdlp_probe' (best effort — a missing table must never hurt a boot).
    Threaded by main(); network-slow, so never on the boot path itself."""
    if config.YTDLP_BOOT_PROBE != "1":
        return
    try:
        verdict = probe()
    except Exception as e:                        # pragma: no cover
        verdict = {"ok": False, "why": f"probe wrapper: {str(e)[:200]}"}
    line = ("[ytaccess] probe ok" if verdict.get("ok")
            else f"[ytaccess] probe FAILED — {verdict.get('why', '?')}")
    dl = ("download ok" if verdict.get("download_ok")
          else f"download FAILED — {verdict.get('download_why', '?')}")
    print(f"{line}; {dl} (cookies={verdict.get('cookie_source')}"
          f"/{verdict.get('cookie_entries', 0)} entries"
          f"{', STALE' if verdict.get('stale_cookies') else ''}, "
          f"pot={'on' if verdict.get('pot') else 'off'})", flush=True)
    try:
        import version
        verdict["code"] = version.code_version()
        db.Db().run(db.kv_put, "ytdlp_probe", json.dumps(verdict))
    except Exception as e:
        print(f"[ytaccess] probe verdict not stored: {str(e)[:120]}",
              flush=True)
