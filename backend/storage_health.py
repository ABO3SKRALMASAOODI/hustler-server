"""Measured health of each object store, and the automatic failover.

THIS FILE EXISTS TWICE, byte for byte: backend/storage_health.py and
worker/storage_health.py (the web service and the executors are separate
images). backend/tests/test_storage_health.py fails when they differ.

12 Nov 2026: Cloudflare R2 answered every call with ``NotEntitled`` from
16:36 UTC. Every upload failed for over an hour while the next store (B2) sat
configured on every process, and /healthz said storage was fine, because it
reported configuration and never asked a store anything. So:

* PROBE. One background thread per process (never on a request path) asks
  every configured store something every STORAGE_PROBE_EVERY_S (60 s): a GET
  of a key that does not exist under the reserved ``_probe/`` prefix, which a
  healthy store answers NoSuchKey and a broken account answers NotEntitled,
  AccessDenied, InvalidAccessKeyId, NoSuchBucket or a timeout. Every
  STORAGE_PROBE_WRITE_EVERY-th probe (5: every 5 minutes), and every probe
  while it is failing, the store the live mode writes to gets a real round
  trip instead: put, head and delete of a tiny object. ``status()`` is what
  the health routes report: ``stores.{legacy,next}.{ok,err,at,...}`` and
  ``write_ok`` for the store the live mode writes to.
* ALARM. backend/ops_loop/gaps.py files a P0 when the write store has failed
  FAILS_FOR_ALARM probes in a row.
* FAILOVER. ``Failover`` runs in ONE process, the dispatcher. When the write
  store fails at the account level (FAILS_FOR_ALARM probes in a row) or times
  out TIMEOUTS_FOR_FAILOVER probes in a row, it puts the other store through
  every call an upload makes (put, head, multipart create and abort, a
  presigned PUT over HTTP, delete) and, when that passes, moves app_kv
  ``storage_routing`` to ``storage_routing.failover_mode``: R2 writes go to
  ``next``, next-store writes go to ``rollback``. Both stores stay readable in
  either mode. The row says why (``by: auto-failover``, ``note``), one admin
  alert is queued, and the intake files the P0. It never flips back, and it
  never flips a row it wrote itself: returning is a human's or a Fixer's
  decision (storage_migrate.py mode ...).

Error codes are a provider's answer, not a customer's words: matching them is
infrastructure, like is_missing() in storage_routing.py.
"""

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import storage_routing as routing

PROBE_PREFIX = "_probe/"
ABSENT_KEY = PROBE_PREFIX + "absent"
# A store refusing the account or the key: nothing will work until a person
# acts on the provider's side, so the other store must take the writes.
ACCOUNT_CODES = frozenset((
    "NotEntitled", "AccessDenied", "InvalidAccessKeyId", "NoSuchBucket",
    "SignatureDoesNotMatch", "AllAccessDisabled", "AccountProblem",
    "InvalidToken", "ExpiredToken", "403"))
MISSING_CODES = frozenset(("NoSuchKey", "404", "NotFound"))
FAILS_FOR_ALARM = 2
TIMEOUTS_FOR_FAILOVER = 5
FULL_PROBE_GAP_S = 300.0


def _env_num(name, default, lo):
    try:
        return max(lo, float(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def error_code(exc):
    try:
        return str(exc.response["Error"]["Code"] or "")
    except (AttributeError, KeyError, TypeError):
        return ""


def classify(exc):
    """(code, kind): kind is "missing" (the store answered: no such key),
    "account" (the store refuses this account, key or bucket), "timeout" (no
    answer in time, or no connection at all) or "other"."""
    code = error_code(exc)
    if code in MISSING_CODES:
        return code, "missing"
    if code in ACCOUNT_CODES:
        return code, "account"
    name = type(exc).__name__
    if code:
        return code, "other"
    if isinstance(exc, (socket.timeout, TimeoutError, ConnectionError)) or \
            "Timeout" in name or name in ("EndpointConnectionError",
                                          "ConnectionClosedError",
                                          "ConnectionError", "URLError"):
        return name, "timeout"
    return name, "other"


def _short(exc):
    return f"{type(exc).__name__}: {str(exc)[:200]}"


_CLIENT_LOCK = threading.Lock()


def _default_client(cfg, timeout_s=10.0):
    """A client on a session of its own, made under a lock: boto3's shared
    default session is not safe to build clients from concurrently, and the
    request threads build theirs from it."""
    import boto3
    from botocore.config import Config
    with _CLIENT_LOCK:
        return boto3.session.Session().client(
            "s3", endpoint_url=cfg["endpoint"],
            aws_access_key_id=cfg["access_key_id"],
            aws_secret_access_key=cfg["secret_access_key"],
            region_name=cfg["region"],
            config=Config(**routing.botocore_config_kwargs(
                cfg, connect_timeout=5.0, read_timeout=timeout_s,
                retries={"max_attempts": 1, "mode": "standard"},
                max_pool_connections=4)))


class Prober:
    """The measured state of every configured store, refreshed in the
    background. ``env`` is the mapping store_config reads (storage_creds.ENV,
    which holds the sealed next-store keys); ``mode`` returns the mode this
    process runs."""

    def __init__(self, env=None, mode=None, client_for=None, every_s=None,
                 write_every=None, clock=None, log=None, instance=None):
        self._env = os.environ if env is None else env
        self._mode = mode or (lambda: routing.DEFAULT_MODE)
        self._client_for = client_for or _default_client
        self.every_s = float(every_s if every_s is not None else
                             _env_num("STORAGE_PROBE_EVERY_S", 60, 10))
        self.write_every = int(write_every if write_every is not None else
                               _env_num("STORAGE_PROBE_WRITE_EVERY", 5, 1))
        self._clock = clock or time.time
        self._log = log or (lambda m: print(f"[storage] {m}", flush=True))
        self._instance = instance or (
            f"{socket.gethostname()[:40]}-{os.getpid()}")
        self._lock = threading.Lock()
        self._results = {}
        self._clients = {}
        self._n = 0
        self._thread = None
        self._pid = None
        self._listeners = []
        self._passes = 0
        self._pass_started = None
        # The first pass waits this long after start(): a web worker starts
        # the probe while it is still booting, and nothing is gained by
        # building clients in the same seconds as every request thread.
        self.first_delay_s = _env_num("STORAGE_PROBE_FIRST_DELAY_S", 10, 0)

    # -- what is configured ---------------------------------------------
    def configured(self):
        out = []
        for name in routing.STORES:
            try:
                if routing.store_config(name, self._env) is not None:
                    out.append(name)
            except Exception:  # noqa: BLE001 — a probe never raises
                pass
        return out

    def mode(self):
        try:
            return self._mode() or routing.DEFAULT_MODE
        except Exception:  # noqa: BLE001
            return routing.DEFAULT_MODE

    def write_store(self):
        return routing.write_store(self.mode())

    def client(self, name):
        cfg = routing.store_config(name, self._env)
        if cfg is None:
            raise RuntimeError(f"store {name} is not configured")
        key = (name, cfg["endpoint"], cfg["access_key_id"],
               hash(cfg["secret_access_key"]), cfg["bucket"])
        c = self._clients.get(key)
        if c is None:
            c = self._client_for(cfg)
            self._clients = {k: v for k, v in self._clients.items()
                             if k[0] != name}
            self._clients[key] = c
        return c, cfg["bucket"], cfg["host"]

    # -- probing ----------------------------------------------------------
    def probe_once(self):
        """Probe every configured store once. Returns status()."""
        self._n += 1
        self._pass_started = self._clock()
        try:
            write = self.write_store()
            for name in self.configured():
                prev = self._results.get(name) or {}
                # Every write_every-th pass, while it fails, and until it
                # has had one round trip (a process's first pass may run
                # before it has read the mode or the next store's keys).
                do_write = name == write and (
                    (self._n - 1) % self.write_every == 0
                    or prev.get("ok") is False
                    or not prev.get("last_write_ok_at"))
                self._probe(name, do_write)
        finally:
            self._pass_started = None
            self._passes += 1
        for fn in list(self._listeners):
            try:
                fn(self)
            except Exception as e:  # noqa: BLE001 — a listener never stops it
                self._log(f"probe listener failed: {_short(e)}")
        return self.status()

    def _probe(self, name, write):
        t0 = self._clock()
        res = {"op": "write" if write else "read"}
        try:
            c, bucket, host = self.client(name)
            res["host"] = host
            if write:
                key = PROBE_PREFIX + self._instance
                c.put_object(Bucket=bucket, Key=key, Body=b"ok",
                             ContentType="text/plain")
                c.head_object(Bucket=bucket, Key=key)
                c.delete_object(Bucket=bucket, Key=key)
            else:
                try:
                    body = c.get_object(Bucket=bucket, Key=ABSENT_KEY)["Body"]
                    body.close()
                except Exception as e:  # noqa: BLE001
                    if classify(e)[1] != "missing":
                        raise
            res.update(ok=True, err=None, code=None, kind=None)
        except Exception as e:  # noqa: BLE001 — every failure is a result
            code, kind = classify(e)
            res.update(ok=False, err=_short(e), code=code, kind=kind)
        now = self._clock()
        res["at"] = _iso(now)
        res["ms"] = int((now - t0) * 1000)
        with self._lock:
            prev = self._results.get(name) or {}
            if res["ok"]:
                res.update(fails=0, timeouts=0, last_ok_at=res["at"],
                           since=None)
            else:
                res["fails"] = int(prev.get("fails") or 0) + 1
                res["timeouts"] = (int(prev.get("timeouts") or 0) + 1
                                   if res["kind"] == "timeout" else 0)
                res["last_ok_at"] = prev.get("last_ok_at")
                res["since"] = prev.get("since") or res["at"]
            res["last_write_ok_at"] = (
                res["at"] if write and res["ok"]
                else prev.get("last_write_ok_at"))
            if prev.get("ok") is not res["ok"]:
                self._log(f"store {name} probe "
                          + ("ok" if res["ok"] else
                             f"FAILED ({res['code']}): {res['err']}"))
            self._results[name] = res
        return res

    def result(self, name):
        with self._lock:
            r = self._results.get(name)
            return dict(r) if r else None

    def status(self):
        """For health routes: hosts and error codes, never a key; never
        raises."""
        try:
            write = self.write_store()
            with self._lock:
                stores = {k: dict(v) for k, v in self._results.items()}
            w = stores.get(write)
            out = {"stores": stores, "write_store": write,
                   "write_ok": (w or {}).get("ok"),
                   "probe_every_s": self.every_s,
                   "probing": self.running(), "passes": self._passes,
                   "pid": os.getpid()}
            started = self._pass_started
            if started is not None and \
                    self._clock() - started > max(45.0, self.every_s):
                # A pass that never ends: say where it is stuck.
                out["stalled_s"] = int(self._clock() - started)
                out["stack"] = self._stack()
            return out
        except Exception as e:  # noqa: BLE001
            return {"stores": {}, "write_ok": None,
                    "probe_error": _short(e)}

    def _stack(self):
        try:
            frame = sys._current_frames().get(self._thread.ident)
            out = []
            while frame is not None and len(out) < 12:
                code = frame.f_code
                out.append(f"{os.path.basename(code.co_filename)}:"
                           f"{frame.f_lineno} {code.co_name}")
                frame = frame.f_back
            return out
        except Exception:  # noqa: BLE001
            return None

    # -- the background thread -------------------------------------------
    def add_listener(self, fn):
        self._listeners.append(fn)

    def running(self):
        return bool(self._thread and self._thread.is_alive()
                    and self._pid == os.getpid())

    def start(self):
        """Idempotent per process (a fork gets its own thread)."""
        with self._lock:
            if self.running():
                return False
            self._pid = os.getpid()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="storage-health-probe")
            self._thread.start()
            return True

    def _loop(self):
        time.sleep(self.first_delay_s)
        while True:
            try:
                self.probe_once()
            except Exception as e:  # noqa: BLE001
                self._log(f"store probe error: {_short(e)}")
            time.sleep(self.every_s)


# ── the full probe a failover target must pass ─────────────────────────────

def _http(method, url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def full_probe(client, bucket, http=_http, clock=time.time):
    """(ok, checks): every call an upload makes, on one store, under
    ``_probe/``, removed afterwards. checks is [(name, ok, note)]."""
    stem = f"{PROBE_PREFIX}failover-{int(clock())}"
    checks = []

    def check(name, fn):
        try:
            checks.append((name, True, fn() or ""))
            return True
        except Exception as e:  # noqa: BLE001
            checks.append((name, False, _short(e)))
            return False

    small = stem + "/small.txt"

    def put_head():
        client.put_object(Bucket=bucket, Key=small, Body=b"valmera-probe",
                          ContentType="text/plain")
        n = int(client.head_object(Bucket=bucket, Key=small)["ContentLength"])
        assert n == len(b"valmera-probe"), n
        return f"{n} bytes"

    def multipart():
        key = stem + "/multi.bin"
        up = client.create_multipart_upload(Bucket=bucket, Key=key,
                                            ContentType="video/mp4")
        client.abort_multipart_upload(Bucket=bucket, Key=key,
                                      UploadId=up["UploadId"])
        return "create + abort"

    put_key = stem + "/presigned.bin"

    def presigned_put():
        url = client.generate_presigned_url(
            "put_object", Params={"Bucket": bucket, "Key": put_key,
                                  "ContentType": "video/mp4"},
            ExpiresIn=600)
        status = http("PUT", url, data=b"x" * 64,
                      headers={"Content-Type": "video/mp4"})
        assert status == 200, f"HTTP {status}"
        n = int(client.head_object(Bucket=bucket, Key=put_key)
                ["ContentLength"])
        assert n == 64, n
        return "HTTP 200"

    def delete():
        for key in (small, put_key):
            client.delete_object(Bucket=bucket, Key=key)
        return "deleted"

    ok = check("put/head", put_head)
    ok = check("multipart create/abort", multipart) and ok
    ok = check("presigned PUT", presigned_put) and ok
    ok = check("delete", delete) and ok
    return ok, checks


# ── the failover (one writer: the dispatcher) ──────────────────────────────

def parse_row(raw):
    if raw is None:
        return {}
    text = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def failover_reason(result):
    """Why a write store's latest results call for a failover, or None."""
    if not result or result.get("ok") is not False:
        return None
    if result.get("kind") == "account" and \
            int(result.get("fails") or 0) >= FAILS_FOR_ALARM:
        return (f"account-level error {result.get('code')} on "
                f"{int(result['fails'])} probes in a row")
    if int(result.get("timeouts") or 0) >= TIMEOUTS_FOR_FAILOVER:
        return f"no answer on {int(result['timeouts'])} probes in a row"
    return None


class Failover:
    """Watches a Prober and flips app_kv storage_routing once, when the
    write store fails and the other store passes a full probe.

    ``read_row()`` returns the raw row (or None); ``write_row(old_raw, new)``
    writes it only if it still holds ``old_raw`` and returns True when it
    wrote; ``alert(subject, html)`` queues one admin alert."""

    def __init__(self, prober, read_row, write_row, alert=None,
                 probe_store=None, clock=None, log=None,
                 gap_s=FULL_PROBE_GAP_S):
        self.prober = prober
        self._read = read_row
        self._write = write_row
        self._alert = alert
        self._probe_store = probe_store or self._full_probe
        self._clock = clock or time.time
        self._log = log or (lambda m: print(f"[storage] {m}", flush=True))
        self._gap = float(gap_s)
        self._last_try = None
        self._said = set()
        self.last = None

    def _full_probe(self, name):
        c, bucket, _host = self.prober.client(name)
        return full_probe(c, bucket, clock=self._clock)

    def _once(self, key, msg):
        if key not in self._said:
            self._said.add(key)
            self._log(msg)

    def __call__(self, prober=None):
        self.last = self.step()
        return self.last

    def step(self):
        mode = self.prober.mode()
        live = routing.write_store(mode)
        res = self.prober.result(live)
        why = failover_reason(res)
        if not why:
            self._said.discard("held")
            return None
        target = routing.failover_mode(mode)
        other = routing.NEXT if live == routing.LEGACY else routing.LEGACY
        if target is None:
            self._once("no-target", f"write store {live} failing ({why}); "
                       f"mode {mode} has no automatic failover")
            return {"action": "none", "why": why, "mode": mode}
        if other not in self.prober.configured():
            self._once("no-other", f"write store {live} failing ({why}); "
                       f"store {other} is not configured here")
            return {"action": "none", "why": why, "mode": mode}
        raw = self._read()
        row = parse_row(raw)
        current = routing.parse_mode(raw) or routing.DEFAULT_MODE
        if row.get("by") == routing.AUTO_BY:
            self._once("held", f"write store {live} failing ({why}) after an "
                       "automatic failover: never flipped again "
                       "automatically; a human or a Fixer decides")
            return {"action": "held", "why": why, "mode": mode}
        if current != mode:
            return {"action": "none", "why": "the row moved", "mode": mode}
        now = self._clock()
        if self._last_try is not None and now - self._last_try < self._gap:
            return {"action": "wait", "why": why, "mode": mode}
        self._last_try = now
        ok, checks = self._probe_store(other)
        if not ok:
            bad = "; ".join(f"{n}: {t}" for n, o, t in checks if not o)
            self._log(f"write store {live} failing ({why}) but {other} failed "
                      f"its full probe too: {bad[:300]}")
            return {"action": "other_failed", "why": why, "mode": mode,
                    "checks": checks}
        host = (res or {}).get("host") or live
        note = (f"automatic failover: {live} store ({host}) {why}: "
                f"{(res or {}).get('err') or ''}"[:400]
                + f"; {other} passed put, multipart, presigned PUT and "
                "delete. Never undone automatically: a human or a Fixer "
                "returns it (storage_migrate.py mode).")
        value = json.dumps({"mode": target, "previous": mode,
                            "set_at": _iso(now), "by": routing.AUTO_BY,
                            "failed_store": live,
                            "error_code": (res or {}).get("code"),
                            "note": note}, sort_keys=True)
        if not self._write(raw, value):
            return {"action": "none", "why": "the row moved", "mode": mode}
        self._log(f"FAILOVER {mode} -> {target}: {note}")
        if self._alert is not None:
            try:
                self._alert(
                    f"Storage failed over automatically: {mode} -> {target} "
                    f"({live} store {(res or {}).get('code') or 'down'})",
                    f"<p>The {live} object store ({host}) failed: {why}."
                    f"</p><p>Error: <code>{(res or {}).get('err') or ''}"
                    f"</code></p><p>The {other} store passed every upload "
                    f"call, so app_kv <b>storage_routing</b> moved from "
                    f"<b>{mode}</b> to <b>{target}</b>: new uploads and "
                    f"writes go to {other}; objects on both stores stay "
                    f"readable where the stores answer.</p><p>This is never "
                    f"undone automatically. Return with storage_migrate.py "
                    f"mode ... once {live} works again.</p>")
            except Exception as e:  # noqa: BLE001 — the flip stands
                self._log(f"failover alert not queued: {_short(e)}")
        return {"action": "flipped", "from": mode, "to": target, "why": why,
                "note": note}


# ── SQL for the dispatcher's failover (DB-API, %s placeholders) ─────────────

def _lock(cur):
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('storage_failover'))")


def read_routing_row(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key = %s",
                    (routing.KV_KEY,))
        row = cur.fetchone()
    if row is None:
        return None
    return row["value"] if isinstance(row, dict) else row[0]


def write_routing_row(conn, old_raw, value):
    """Compare and set: True when the row still held ``old_raw`` and now
    holds ``value``."""
    with conn.cursor() as cur:
        _lock(cur)
        if old_raw is None:
            cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                           VALUES (%s, %s, NOW())
                           ON CONFLICT (key) DO NOTHING""",
                        (routing.KV_KEY, value))
        else:
            cur.execute("""UPDATE app_kv SET value = %s, updated_at = NOW()
                            WHERE key = %s AND value = %s""",
                        (value, routing.KV_KEY, old_raw))
        return cur.rowcount == 1


def queue_admin_alert(conn, dedupe_key, subject, html):
    """One admin alert (trial_alerts: the backend's alert scheduler mails the
    admin address, never a customer)."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.trial_alerts') AS t")
        row = cur.fetchone()
        if not (row["t"] if isinstance(row, dict) else row[0]):
            return False
        cur.execute("""INSERT INTO trial_alerts (dedupe_key, kind, subject,
                                                 html_content)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (dedupe_key) DO NOTHING""",
                    (str(dedupe_key)[:200], "storage_failover",
                     str(subject)[:300], str(html)[:20000]))
        return cur.rowcount > 0
