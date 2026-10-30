"""The next object store's keys, sealed in one app_kv row (30 Oct 2026).

THIS FILE EXISTS TWICE, byte for byte: backend/storage_creds.py and
worker/storage_creds.py (separate images). The tests fail when they differ.

The replacement store (S3_NEXT_*, storage_routing.py) needs its keys in every
process that touches storage: the Render web service (presigned uploads and
playback), the Render dispatcher, the box executors and the Cloudflare lanes.
Nobody should have to paste them into four dashboards, so:

* Environment first. A process whose env has the four required S3_NEXT_*
  values (endpoint, access key id, secret, bucket) uses its env and never
  reads the row.
* Otherwise it reads app_kv ``storage_next_credentials`` and, when it can
  decrypt it, uses the row's S3_NEXT_* set (whole; a partial env is ignored):

      {"v": 1, "alg": "AES-256-GCM", "kdf": "HKDF-SHA256",
       "info": "valmera-storage-next-v1",
       "kek": "S3_SECRET_ACCESS_KEY", "kid": <16 hex>,
       "nonce": <b64, 12 bytes>, "ct": <b64, ciphertext + 16-byte tag>,
       "also": [{"kek": "REMOTE_EXECUTOR_SECRET", "kid", "nonce", "ct"}],
       "fields": <n>, "at": <UTC>}

  Each entry seals the same JSON object of S3_NEXT_* values under
  AES-256-GCM with key HKDF-SHA256(ikm=the named env secret, salt=zeros,
  info). ``kid`` is HKDF(secret, info + "/kid") cut to 8 bytes: it tells a
  wrong key from a damaged row without saying anything about the secret.

* The sealing secrets are ones every storage process already holds and a
  plain database reader does not. S3_SECRET_ACCESS_KEY is the legacy store's
  own secret: every process that touches storage has it, and whoever has it
  can already read and write every object, so the sealed keys (to a copy of
  the same bucket) give them nothing new. REMOTE_EXECUTOR_SECRET (dispatcher
  and executors; the web service does not have it) is a second wrap, so a
  lane whose legacy secret differs from the sealer's still opens the row.

* Cached for TTL_S (5 minutes). A row that does not decrypt is retried
  after RETRY_S, and an authentication error from the next store (a rotated
  key) re-reads the row at most every RETRY_S. A failed database read keeps
  the last good keys. Only a process's first read waits for the database;
  later refreshes run in a background thread while calls use what is cached,
  so a slow or unreachable database never stalls a storage call.

* Fail closed and quiet: no row, no sealing secret, a wrong key, a damaged
  or unknown row, or no AES implementation all leave the next store "not
  configured" exactly as before. Nothing here raises into a storage call,
  and no secret or key value is ever logged or reported (status() carries
  names and reason words only).

AES-GCM comes from ``cryptography`` (the web service, the sealing CLI) or,
where that is not installed (the worker images), from yt-dlp's pure-Python
``yt_dlp.aes`` (verified tag; a few milliseconds for this row). Sealing needs
``cryptography``.
"""

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from collections.abc import Mapping

KV_KEY = "storage_next_credentials"
VERSION = 1
ALG = "AES-256-GCM"
KDF = "HKDF-SHA256"
INFO = "valmera-storage-next-v1"
# Sealing secrets, in order of preference (the first is the row's own entry).
KEKS = ("S3_SECRET_ACCESS_KEY", "REMOTE_EXECUTOR_SECRET")
REQUIRED = ("S3_NEXT_ENDPOINT", "S3_NEXT_ACCESS_KEY_ID",
            "S3_NEXT_SECRET_ACCESS_KEY", "S3_NEXT_BUCKET")
FIELDS = REQUIRED + ("S3_NEXT_REGION", "S3_NEXT_PUBLIC_ENDPOINT",
                     "S3_NEXT_ADDRESSING_STYLE")
TTL_S = 300.0
RETRY_S = 30.0
NONCE_BYTES = 12
TAG_BYTES = 16
# S3 error codes that mean "these keys are not (or no longer) accepted".
AUTH_CODES = frozenset((
    "InvalidAccessKeyId", "SignatureDoesNotMatch", "InvalidToken",
    "ExpiredToken", "AccessDenied", "AuthorizationHeaderMalformed",
    "Unauthorized", "InvalidSecurity", "401", "403"))


class CredentialsError(Exception):
    """A row that cannot be used; ``reason`` is one word, never a value."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# ── key derivation (RFC 5869, stdlib only) ──────────────────────────────────

def hkdf_sha256(ikm, info, length=32, salt=b""):
    prk = hmac.new(salt or b"\x00" * 32, ikm, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]),
                         hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def _ikm(secret):
    return str(secret).strip().encode("utf-8")


def key_for(secret):
    return hkdf_sha256(_ikm(secret), INFO.encode("ascii"))


def key_id(secret):
    return hkdf_sha256(_ikm(secret), (INFO + "/kid").encode("ascii"), 8).hex()


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _unb64(text):
    return base64.b64decode(str(text).encode("ascii"), validate=True)


# ── AES-256-GCM ─────────────────────────────────────────────────────────────

def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except Exception:  # noqa: BLE001 — not installed: the yt-dlp fallback
        return None


def _decrypt(key, nonce, data, impl=None):
    """Plaintext bytes, or CredentialsError('damaged' | 'no_aes')."""
    if len(nonce) != NONCE_BYTES or len(data) < TAG_BYTES:
        raise CredentialsError("damaged")
    impl = impl or ("cryptography" if _aesgcm() is not None else "yt_dlp")
    if impl == "cryptography":
        AESGCM = _aesgcm()
        if AESGCM is None:
            raise CredentialsError("no_aes")
        try:
            return AESGCM(key).decrypt(nonce, data, None)
        except Exception:  # noqa: BLE001 — InvalidTag
            raise CredentialsError("damaged") from None
    try:
        from yt_dlp.aes import aes_gcm_decrypt_and_verify_bytes
    except Exception:  # noqa: BLE001
        raise CredentialsError("no_aes") from None
    try:
        return bytes(aes_gcm_decrypt_and_verify_bytes(
            data[:-TAG_BYTES], key, data[-TAG_BYTES:], nonce))
    except Exception:  # noqa: BLE001 — "Mismatching authentication tag"
        raise CredentialsError("damaged") from None


# ── the row ─────────────────────────────────────────────────────────────────

def clean_values(values):
    """The allowed S3_NEXT_* names with non-empty string values."""
    out = {}
    for name in FIELDS:
        v = (values or {}).get(name)
        if isinstance(v, str) and v.strip():
            out[name] = v.strip()
    return out


def seal(values, secrets, now=None, rand=os.urandom):
    """The app_kv row text sealing ``values`` (S3_NEXT_* names) under each
    secret in ``secrets`` ({kek name: secret}). Needs ``cryptography``."""
    plain = clean_values(values)
    missing = [n for n in REQUIRED if n not in plain]
    if missing:
        raise ValueError("missing required: " + ", ".join(missing))
    unknown = sorted(set(secrets or {}) - set(KEKS))
    if unknown:
        raise ValueError("not a sealing secret: " + ", ".join(unknown))
    AESGCM = _aesgcm()
    if AESGCM is None:
        raise RuntimeError("sealing needs the 'cryptography' package")
    text = json.dumps(plain, sort_keys=True, separators=(",", ":"))
    entries = []
    for name in KEKS:
        secret = str((secrets or {}).get(name) or "").strip()
        if not secret:
            continue
        nonce = rand(NONCE_BYTES)
        ct = AESGCM(key_for(secret)).encrypt(nonce, text.encode("utf-8"),
                                             None)
        entries.append({"kek": name, "kid": key_id(secret),
                        "nonce": _b64(nonce), "ct": _b64(ct)})
    if not entries:
        raise ValueError("no sealing secret given")
    row = {"v": VERSION, "alg": ALG, "kdf": KDF, "info": INFO}
    row.update(entries[0])
    row["also"] = entries[1:]
    row["fields"] = len(plain)
    row["at"] = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return json.dumps(row, sort_keys=True)


def open_row(raw, env, impl=None):
    """(values, kek name) from the row text, using the sealing secrets in
    ``env``; else CredentialsError with one of: missing, malformed,
    unsupported, no_secret, wrong_key, damaged, incomplete, no_aes."""
    if raw is None:
        raise CredentialsError("missing")
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        row = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:  # noqa: BLE001
        raise CredentialsError("malformed") from None
    if not isinstance(row, dict):
        raise CredentialsError("malformed")
    if (row.get("v"), row.get("alg"), row.get("kdf"), row.get("info")) != \
            (VERSION, ALG, KDF, INFO):
        raise CredentialsError("unsupported")
    also = row.get("also") if isinstance(row.get("also"), list) else []
    entries = [row] + [e for e in also if isinstance(e, dict)]
    held, reason = False, "wrong_key"
    for entry in entries:
        name = entry.get("kek")
        if name not in KEKS:
            continue
        secret = str(env.get(name) or "").strip()
        if not secret:
            continue
        held = True
        if not hmac.compare_digest(str(entry.get("kid") or ""),
                                   key_id(secret)):
            continue
        try:
            nonce, data = _unb64(entry["nonce"]), _unb64(entry["ct"])
        except Exception:  # noqa: BLE001
            reason = "damaged"
            continue
        try:
            plain = _decrypt(key_for(secret), nonce, data, impl=impl)
        except CredentialsError as exc:
            reason = exc.reason
            continue
        try:
            values = clean_values(json.loads(plain.decode("utf-8")))
        except Exception:  # noqa: BLE001
            reason = "damaged"
            continue
        if any(n not in values for n in REQUIRED):
            reason = "incomplete"
            continue
        return values, name
    raise CredentialsError(reason if held else "no_secret")


def env_complete(env):
    return all(str(env.get(n) or "").strip() for n in REQUIRED)


# ── the per-process cache ───────────────────────────────────────────────────

class Credentials:
    """The row's S3_NEXT_* values for this process, cached. ``fetch()``
    returns the raw app_kv value (None when absent) and may raise."""

    def __init__(self, fetch=None, env=None, ttl_s=None, retry_s=None,
                 clock=None, log=None, impl=None, background=True):
        self._fetch = fetch
        self._background = background
        self._env = os.environ if env is None else env
        self._ttl = float(TTL_S if ttl_s is None else ttl_s)
        self._retry = float(RETRY_S if retry_s is None else retry_s)
        self._clock = clock or time.monotonic
        self._log = log or (lambda msg: print(f"[storage] {msg}",
                                              flush=True))
        self._impl = impl
        self._lock = threading.Lock()
        self._reset()

    def _reset(self):
        self._values = None
        self._kek = None
        self._reason = "not_read"
        self._tried = False
        self._due = 0.0
        self._read_at = None
        self._invalidated_at = None
        self._said = None

    def install(self, fetch):
        """Set where the row comes from (each process has its own database
        access); forgets anything cached."""
        with self._lock:
            self._fetch = fetch
            self._reset()

    def _has_sealing_secret(self):
        return any(str(self._env.get(n) or "").strip() for n in KEKS)

    def values(self):
        """The row's S3_NEXT_* values, or None. Never raises."""
        try:
            if self._fetch is None:
                return None
            if not self._has_sealing_secret():
                self._reason = "no_secret"
                return None
            if self._clock() < self._due:
                return self._values
            if not self._tried:
                with self._lock:                # the first read waits
                    if not self._tried:
                        self._refresh()
            elif self._lock.acquire(blocking=False):
                if self._clock() < self._due:
                    self._lock.release()
                elif self._background:
                    try:
                        threading.Thread(target=self._refresh_then_release,
                                         daemon=True,
                                         name="storage-creds").start()
                    except Exception:  # noqa: BLE001
                        self._lock.release()
                else:
                    self._refresh_then_release()
            return self._values
        except Exception:  # noqa: BLE001 — never into a storage call
            return None

    def _refresh_then_release(self):
        try:
            self._refresh()
        finally:
            self._lock.release()

    def _refresh(self):
        now = self._clock()
        self._tried = True
        try:
            raw = self._fetch()
        except Exception as exc:  # noqa: BLE001 — keep the last good keys
            self._due = now + self._retry
            if self._values is None:
                self._reason = "unreadable"
            self._say(f"credentials row unreadable ({type(exc).__name__})"
                      + ("; keeping the last good keys"
                         if self._values is not None else ""))
            return
        try:
            values, kek = open_row(raw, self._env, impl=self._impl)
        except CredentialsError as exc:
            self._values, self._kek = None, None
            self._reason = exc.reason
            self._read_at = now
            self._due = now + (self._ttl if exc.reason in
                               ("missing", "no_secret") else self._retry)
            self._say(f"next store not configured from app_kv "
                      f"({exc.reason})")
            return
        self._values, self._kek = values, kek
        self._reason = "ok"
        self._read_at = now
        self._due = now + self._ttl
        self._say(f"next store keys from app_kv ({len(values)} fields, "
                  f"sealed with {kek})")

    def _say(self, msg):
        if msg != self._said:
            self._said = msg
            try:
                self._log(msg)
            except Exception:  # noqa: BLE001
                pass

    def invalidate(self):
        """The next store refused these keys: re-read the row on the next
        call, at most once every RETRY_S."""
        if self._values is None:
            return False
        now = self._clock()
        if self._invalidated_at is not None and \
                now - self._invalidated_at < self._retry:
            return False
        self._invalidated_at = now
        self._due = 0.0
        return True

    def status(self):
        """Names and reason words only, for health routes."""
        out = {"row": self._reason}
        if self._kek:
            out["kek"] = self._kek
        if self._read_at is not None:
            out["age_s"] = int(max(0, self._clock() - self._read_at))
        return out


class Env(Mapping):
    """A read-only view of ``base`` (os.environ) in which the S3_NEXT_*
    names come from the credentials row whenever ``base`` lacks any of the
    four required ones. Hand it to storage_routing.store_config / Router."""

    def __init__(self, base=None, creds=None):
        self._base = os.environ if base is None else base
        self._creds = creds

    def _row(self):
        if self._creds is None or env_complete(self._base):
            return None
        return self._creds.values()

    def __getitem__(self, name):
        if isinstance(name, str) and name.startswith("S3_NEXT_"):
            row = self._row()
            if row is not None:
                return row[name]
        return self._base[name]

    def get(self, name, default=None):
        try:
            return self[name]
        except KeyError:
            return default

    def __iter__(self):
        return iter(self._base)

    def __len__(self):
        return len(self._base)

    def source(self):
        """'env', 'app_kv' or None: where the next store's keys come from."""
        if env_complete(self._base):
            return "env"
        return "app_kv" if self._row() is not None else None


CREDENTIALS = Credentials()
ENV = Env(os.environ, CREDENTIALS)


def install(fetch):
    CREDENTIALS.install(fetch)


def status():
    """{"next_source": 'env' | 'app_kv' | None, "next_credentials": {...}}"""
    try:
        source = ENV.source()
        out = {"next_source": source}
        if source != "env":
            out["next_credentials"] = CREDENTIALS.status()
        return out
    except Exception as exc:  # noqa: BLE001
        return {"next_credentials": {"row": "error",
                                     "error": type(exc).__name__}}


def note_s3_error(code, http_status=None):
    """An S3 response from the next store: an authentication refusal of
    keys that came from the row re-reads the row (rotated keys)."""
    code = str(code or "")
    if code in AUTH_CODES or http_status in (401, 403):
        if ENV.source() == "app_kv":
            return CREDENTIALS.invalidate()
    return False


def _after_call(http_response=None, parsed=None, **_kw):
    try:
        status_code = getattr(http_response, "status_code", None)
        if status_code is None or int(status_code) < 400:
            return
        err = (parsed or {}).get("Error") or {}
        note_s3_error(err.get("Code"), int(status_code))
    except Exception:  # noqa: BLE001
        pass


def watch_client(client):
    """Hook a boto3 client for the next store so an authentication refusal
    re-reads the row. Returns the client."""
    try:
        client.meta.events.register("after-call.s3", _after_call)
    except Exception:  # noqa: BLE001 — a fake client in tests
        pass
    return client
