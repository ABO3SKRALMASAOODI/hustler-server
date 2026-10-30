"""One background reader per process for one app_kv value (30 Oct 2026).

THIS FILE EXISTS TWICE, byte for byte: backend/kv_feed.py and
worker/kv_feed.py (separate images). The tests fail when they differ.

Why it exists: storage_routing.Router reads the ``storage_routing`` mode at
most every 30 s, and it used to read it INLINE, inside whichever storage call
or health probe found the cached value stale. Once a process can see the
next store's keys (storage_creds.py) that read is live: about one 0.35 s
database connection per 30 s per process, and in a database outage the
caller waits on it. The box probes every slot's /health with a 3 s timeout
and checks readiness before every /run, so a hung connect made healthy slots
look down; a render's download or an upload's presign could wait the same way.

A Feed takes the read off every caller's path:

* ONE daemon thread per process reads the value every ``ttl_s`` and keeps the
  last good one. ``get()`` returns it at once and never touches the database.
  The thread starts on ``start()`` or the first ``get()``, and again in a
  forked child (a thread does not survive a fork).
* Before the first good read, ``get()`` returns the value an earlier process
  in this container persisted to a small local cache file, when that file is
  fresh (``cache_max_age_s``), and otherwise raises NotReadYet so the caller
  keeps its own default (the router's is 'legacy').
* A failed read keeps the last good value and backs off: 5, 10, 20, 40, then
  every 60 s. The first good read after an outage replaces the value, so a
  change made meanwhile arrives within one backoff step of the recovery.
* ``enabled()`` false (say, no next store configured): nothing is read; the
  thread looks again every second for its first two minutes (keys from the
  sealed row arrive about a second after boot), then every ``ttl_s``, and at
  once when a caller asks for the value.
* ``accept(raw)`` false (a row that names no mode): the database answered,
  the last good value stays, and that is said once instead of on every call.
* The thread holds only a weak reference to its Feed: a Feed that is stopped
  or dropped (tests build many) ends its thread.
* Logs and ``status()`` carry exception TYPE names and counts, never a value
  or a database message, because health routes repeat them.
"""

import json
import os
import stat
import tempfile
import threading
import time
import weakref

_NOTHING = object()
CACHE_VERSION = 1
CACHE_MAX_BYTES = 4096
CACHE_REWRITE_S = 600.0      # an unchanged value refreshes the file's age
FAST_IDLE_S = 1.0
FAST_IDLE_WINDOW_S = 120.0


class NotReadYet(Exception):
    """No value has been read yet and no fresh cache file holds one."""


def cache_path(env_name, filename, env=None):
    """The cache file: ``$env_name`` when set ('off' or '0' disables it),
    else ``filename`` in the system temp directory (per container on the
    box and Render; it survives a process restart, not a new container)."""
    env = os.environ if env is None else env
    raw = env.get(env_name)
    if raw is not None:
        raw = raw.strip()
        if raw.lower() in ("", "0", "off", "none", "false"):
            return None
        return raw
    return os.path.join(tempfile.gettempdir(), filename)


def _signal(stop, wake):
    stop.set()
    wake.set()


def _run(ref, stop, wake):
    """The refresher thread. Holds the Feed only while it ticks."""
    while not stop.is_set():
        feed = ref()
        if feed is None:
            return
        try:
            delay = feed._tick()
        except Exception:  # noqa: BLE001 — the refresher never dies
            delay = 5.0
        feed = None
        wake.wait(max(0.01, float(delay)))
        wake.clear()


class Feed:
    """``fetch()`` returns the raw value (None when the row is absent) and
    may raise or hang; only the refresher thread ever calls it."""

    def __init__(self, fetch, name, ttl_s=30.0, backoff_s=5.0,
                 backoff_max_s=60.0, enabled=None, accept=None,
                 cache_path=None, cache_max_age_s=86400.0, cache_as=None,
                 clock=None, wall=None, log=None):
        self._fetch = fetch
        self.name = str(name)
        self._ttl = max(0.01, float(ttl_s))
        self._backoff = max(0.01, float(backoff_s))
        self._backoff_max = max(self._backoff, float(backoff_max_s))
        self._enabled = enabled
        self._accept = accept
        self._cache_path = cache_path or None
        self._cache_max_age = float(cache_max_age_s)
        self._cache_as = cache_as
        self._clock = clock or time.monotonic
        self._wall = wall or time.time
        self._log = log or (lambda msg: print(f"[storage] {msg}",
                                              flush=True))
        self._latest = None         # (value,) after the first good read
        self._read_at = None        # clock() of the last good read
        self._boot = _NOTHING       # the cache file's value, read once
        self._boot_loaded = False
        self._boot_age = None
        self._failures = 0
        self._error = None          # exception type of the last failed read
        self._rejected = False
        self._idle = False
        self._woken_at = None
        self._started_at = None
        self._cached = (_NOTHING, None)   # (value, wall) last written
        self._cache_err = None
        self._closed = False
        self._reads = 0
        self._sync_init()
        # A fork copies a lock the refresher may hold, but not the thread:
        # the child starts clean and lazily runs its own refresher.
        if hasattr(os, "register_at_fork"):
            ref = weakref.ref(self)
            os.register_at_fork(
                after_in_child=lambda: ref() is not None and
                ref()._sync_init())

    def _sync_init(self):
        self._pid = os.getpid()
        self._start_lock = threading.Lock()
        self._read_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        weakref.finalize(self, _signal, self._stop, self._wake)

    # -- the thread ----------------------------------------------------------
    def start(self):
        """Start the refresher (idempotent; again after a fork). Never
        raises; False when the Feed is stopped or no thread could start."""
        try:
            if self._closed:
                return False
            if self._pid != os.getpid():
                self._sync_init()
            t = self._thread
            if t is not None and t.is_alive():
                return True
            with self._start_lock:
                t = self._thread
                if t is not None and t.is_alive():
                    return True
                if self._closed:
                    return False
                t = threading.Thread(
                    target=_run,
                    args=(weakref.ref(self), self._stop, self._wake),
                    name=f"kv-feed:{self.name}", daemon=True)
                if self._started_at is None:
                    self._started_at = self._clock()
                t.start()
                self._thread = t
                return True
        except Exception:  # noqa: BLE001 — no thread: callers keep defaults
            return False

    def started(self):
        t = self._thread
        return t is not None and t.is_alive() and self._pid == os.getpid()

    def stop(self, timeout=2.0):
        """End the refresher; True when it has ended."""
        self._closed = True
        _signal(self._stop, self._wake)
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)
        return t is None or not t.is_alive()

    def wake(self):
        """Read now (the thread does; this returns at once)."""
        self._wake.set()

    def _tick(self):
        """One refresher step; the seconds until the next one."""
        if self._enabled is not None:
            try:
                on = bool(self._enabled())
            except Exception:  # noqa: BLE001
                on = False
            if not on:
                self._idle = True
                young = self._started_at is not None and \
                    self._clock() - self._started_at < FAST_IDLE_WINDOW_S
                if self._latest is None and young:
                    return min(self._ttl, FAST_IDLE_S)
                return self._ttl
        self._idle = False
        if self._read():
            return self._ttl
        return min(self._backoff_max,
                   self._backoff * (2 ** min(self._failures - 1, 16)))

    def _read(self):
        """One read; True when the database answered."""
        with self._read_lock:
            self._reads += 1
            try:
                raw = self._fetch()
            except Exception as exc:  # noqa: BLE001 — keep the last good
                self._failures += 1
                self._error = type(exc).__name__
                if self._failures == 1:
                    self._say(f"{self.name} unreadable ({self._error}); "
                              f"keeping {self._keeping()}, retrying with "
                              f"backoff")
                return False
            failed = self._failures
            self._failures = 0
            self._error = None
            self._read_at = self._clock()
            try:
                ok = self._accept is None or bool(self._accept(raw))
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                if not self._rejected:
                    self._say(f"{self.name} row names no usable value; "
                              f"keeping {self._keeping()}")
                self._rejected = True
                return True
            self._rejected = False
            self._latest = (raw,)
            if failed:
                self._say(f"{self.name} readable again after {failed} "
                          f"failed read(s)")
            self._write_cache(raw)
            return True

    def refresh_now(self):
        """Read synchronously in the calling thread (tools and tests)."""
        return self._read()

    # -- the value -----------------------------------------------------------
    def get(self):
        """The last good value, at once. Before the first good read: the
        fresh cache file's value, else NotReadYet. Never touches the
        database and never waits."""
        if not self.started():
            self.start()
        elif self._idle and self._latest is None:
            now = self._clock()
            if self._woken_at is None or now - self._woken_at >= 1.0:
                self._woken_at = now
                self._wake.set()
        latest = self._latest
        if latest is not None:
            return latest[0]
        boot = self._boot_value()
        if boot is not _NOTHING:
            return boot
        why = f" ({self._error})" if self._error else ""
        raise NotReadYet(f"{self.name} not read yet{why}")

    def _keeping(self):
        if self._latest is not None:
            return "the last good value"
        if self._boot_value() is not _NOTHING:
            return "the cached value"
        return "the default"

    def status(self):
        """Source, age and failures: names and numbers only."""
        out = {"source": "db" if self._latest is not None else
               "cache" if self._boot_value() is not _NOTHING else "default"}
        if self._read_at is not None:
            out["age_s"] = int(max(0, self._clock() - self._read_at))
        elif self._boot_age is not None and self._latest is None:
            out["age_s"] = int(max(0, self._boot_age))
        if self._failures:
            out["failures"] = self._failures
        if self._error:
            out["error"] = self._error
        if self._rejected:
            out["rejected"] = True
        if self._idle:
            out["idle"] = True
        return out

    # -- the cache file ------------------------------------------------------
    def _boot_value(self):
        if not self._boot_loaded:
            self._boot = self._load_cache()
            self._boot_loaded = True
        return self._boot

    def _load_cache(self):
        path = self._cache_path
        if not path:
            return _NOTHING
        try:
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode) or st.st_size > CACHE_MAX_BYTES:
                return _NOTHING
            if hasattr(os, "getuid") and st.st_uid != os.getuid():
                return _NOTHING
            with open(path, encoding="utf-8") as f:
                data = json.loads(f.read(CACHE_MAX_BYTES + 1))
            if not isinstance(data, dict) or \
                    data.get("v") != CACHE_VERSION or \
                    data.get("name") != self.name:
                return _NOTHING
            age = self._wall() - float(data.get("at"))
            if age > self._cache_max_age or age < -300:
                return _NOTHING
            value = data.get("value")
            if value is not None and not isinstance(value, str):
                return _NOTHING
            if self._accept is not None and not self._accept(value):
                return _NOTHING
            self._boot_age = age
            return value
        except Exception:  # noqa: BLE001 — no cache: the caller's default
            return _NOTHING

    def _write_cache(self, raw):
        path = self._cache_path
        if not path:
            return
        try:
            value = self._cache_as(raw) if self._cache_as else raw
        except Exception:  # noqa: BLE001
            return
        if value is not None and not isinstance(value, str):
            return
        now = self._wall()
        last_value, last_at = self._cached
        if last_value == value and last_at is not None and \
                now - last_at < CACHE_REWRITE_S:
            return
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".kv-feed-",
                                       dir=os.path.dirname(path) or ".")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"v": CACHE_VERSION, "name": self.name,
                           "value": value, "at": now}, f)
            os.replace(tmp, path)
            tmp = None
            self._cached = (value, now)
            self._cache_err = None
        except Exception as exc:  # noqa: BLE001 — a cache is only a hint
            if self._cache_err != type(exc).__name__:
                self._cache_err = type(exc).__name__
                self._say(f"{self.name} cache not written "
                          f"({self._cache_err})")
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _say(self, msg):
        try:
            self._log(msg)
        except Exception:  # noqa: BLE001
            pass
