#!/usr/bin/env python3
"""Valmera render box: secrets intake from GitHub Actions (no shared secret).

The executor needs the same runtime secrets the Cloudflare lanes get from
GitHub (database, storage, model keys). They live only in GitHub's secret
store, so the box receives them from a workflow
(.github/workflows/box-provision.yml) instead of from a person:

    POST /provision/secrets      Authorization: Bearer <GitHub OIDC token>
    body {"env": {"NAME": "value", ...}}

The bearer is a GitHub Actions OIDC ID token. It is accepted only when its
RS256 signature verifies against GitHub's published keys and its claims name
THIS repository, its main branch, the box-provision workflow file on main,
and the audience ``valmera-box``. Nothing else can mint one: a fork's token
names the fork, and a pull request from a fork gets no token at all. Each
token is accepted once (its ``jti``), within its own few-minute lifetime.

Only listed names are written, to ENV_PATH (mode 0600, atomic rename). No
value is ever logged or returned: the answer lists names and value lengths.
A changed file marks ``env-changed`` so boxctl.py recreates the slot
containers (drained, one by one) with the new environment; admission re-reads
REMOTE_EXECUTOR_SECRET itself.

Listens on 127.0.0.1 only; Caddy routes /provision/* here over TLS.
"""

import base64
import hashlib
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ENV_PATH = os.getenv("BOX_ENV_FILE", "/etc/valmera/executor.env")
STATE_DIR = os.getenv("BOX_DATA_DIR", "/var/lib/valmera")
CHANGED_FLAG = os.path.join(STATE_DIR, "env-changed")
LOG_PATH = os.getenv("BOX_PROVISION_LOG", "/var/log/valmera/provision.log")
LISTEN = (os.getenv("BOX_PROVISION_HOST", "127.0.0.1"),
          int(os.getenv("BOX_PROVISION_PORT", "8091")))

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
AUDIENCE = os.getenv("BOX_OIDC_AUDIENCE", "valmera-box")
REPOSITORY = os.getenv("BOX_OIDC_REPOSITORY",
                       "ABO3SKRALMASAOODI/hustler-server")
REF = "refs/heads/main"
WORKFLOW_PATH = ".github/workflows/box-provision.yml"
EVENTS = {"push", "workflow_dispatch", "schedule"}

# The names the executor may receive. Everything else in a body is ignored.
ALLOWED = (
    "REMOTE_EXECUTOR_SECRET", "DATABASE_URL", "S3_ENDPOINT",
    "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET", "S3_REGION",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "VISION_API_KEY", "VISION_BASE_URL",
    "VISION_MODEL", "IMAGE_API_KEY", "IMAGE_BASE_URL", "IMAGE_GEN_MODEL",
    "FAL_KEY", "DEEPGRAM_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY",
    "ANTHROPIC_API_KEY",
    # The replacement object store while media moves off R2 (optional;
    # worker/storage_routing.py). Absent = the executor uses S3_* alone.
    "S3_NEXT_ENDPOINT", "S3_NEXT_ACCESS_KEY_ID", "S3_NEXT_SECRET_ACCESS_KEY",
    "S3_NEXT_BUCKET", "S3_NEXT_REGION",
    # YouTube from the box (E1, 30 Oct 2026), both optional and absent by
    # default. YTDLP_PROXY: owner decision (b), a residential/ISP proxy that
    # yt-dlp uses for YouTube pages only (worker/url_media, song_find).
    # CLOUDFLARE_EXECUTOR_URL: owner decision (c); inert in box slots
    # (slot.env keeps CLOUDFLARE_EXECUTOR_ENABLED=0) unless app_kv
    # box_routing.youtube_egress == "cloudflare" (worker/remote.py).
    "YTDLP_PROXY", "CLOUDFLARE_EXECUTOR_URL",
)
# Without these the executor cannot run a single job; refuse a partial set.
REQUIRED = ("REMOTE_EXECUTOR_SECRET", "DATABASE_URL", "S3_ENDPOINT",
            "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET")

_jwks = {"at": 0.0, "keys": {}}
_jwks_lock = threading.Lock()
_seen_jti = {}
_seen_lock = threading.Lock()
_write_lock = threading.Lock()


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(f"[provision] {line}", flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def b64url(data):
    data = data.encode("ascii") if isinstance(data, str) else data
    return base64.urlsafe_b64decode(data + b"=" * (-len(data) % 4))


def github_keys(force=False):
    with _jwks_lock:
        if not force and _jwks["keys"] and time.time() - _jwks["at"] < 3600:
            return _jwks["keys"]
        with urllib.request.urlopen(JWKS_URL, timeout=15) as resp:
            doc = json.load(resp)
        keys = {}
        for jwk in doc.get("keys", []):
            if jwk.get("kty") != "RSA" or not jwk.get("kid"):
                continue
            n = int.from_bytes(b64url(jwk["n"]), "big")
            e = int.from_bytes(b64url(jwk["e"]), "big")
            keys[jwk["kid"]] = rsa.RSAPublicNumbers(e, n).public_key()
        _jwks.update({"at": time.time(), "keys": keys})
        return keys


def verify_token(token, now=None, keys=None):
    """The token's claims when it is a valid box-provision token; else raises
    ValueError naming the first failed check (never the token itself)."""
    now = time.time() if now is None else now
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(b64url(head_b64))
        claims = json.loads(b64url(body_b64))
        signature = b64url(sig_b64)
    except (ValueError, TypeError) as exc:
        raise ValueError("malformed token") from exc
    if header.get("alg") != "RS256":
        raise ValueError("unexpected signing algorithm")
    fetched = keys is None
    keys = github_keys() if fetched else keys
    key = keys.get(header.get("kid"))
    if key is None and fetched:
        # GitHub rotated its keys since the cached copy.
        key = github_keys(force=True).get(header.get("kid"))
    if key is None:
        raise ValueError("unknown signing key")
    try:
        key.verify(signature, f"{head_b64}.{body_b64}".encode("ascii"),
                   padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:  # noqa: BLE001 - InvalidSignature and friends
        raise ValueError("bad signature") from exc
    if claims.get("iss") != ISSUER:
        raise ValueError("wrong issuer")
    aud = claims.get("aud")
    if aud != AUDIENCE and not (isinstance(aud, list) and AUDIENCE in aud):
        raise ValueError("wrong audience")
    if not (float(claims.get("nbf", 0)) - 60 <= now
            < float(claims.get("exp", 0))):
        raise ValueError("token not valid now")
    if claims.get("repository") != REPOSITORY:
        raise ValueError("wrong repository")
    if claims.get("ref") != REF:
        raise ValueError("wrong ref")
    if claims.get("event_name") not in EVENTS:
        raise ValueError("wrong event")
    workflow_ref = str(claims.get("job_workflow_ref")
                       or claims.get("workflow_ref") or "")
    if workflow_ref != f"{REPOSITORY}/{WORKFLOW_PATH}@{REF}":
        raise ValueError("wrong workflow")
    return claims


def claim_jti(claims):
    jti = str(claims.get("jti") or "")
    if not jti:
        raise ValueError("token has no jti")
    with _seen_lock:
        now = time.time()
        for old, exp in list(_seen_jti.items()):
            if exp < now:
                del _seen_jti[old]
        if jti in _seen_jti:
            raise ValueError("token already used")
        _seen_jti[jti] = float(claims.get("exp", now + 600))


def render_env(values):
    lines = ["# Written by box/provision.py from GitHub secrets. Do not edit."]
    for name in ALLOWED:
        value = values.get(name)
        if value is None or value == "":
            continue
        value = str(value)
        if "\n" in value or "\r" in value:
            raise ValueError(f"{name} contains a newline")
        lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"


def write_env(values):
    """Atomically replace ENV_PATH; True when its content changed."""
    missing = [n for n in REQUIRED if not values.get(n)]
    if missing:
        raise ValueError("missing required names: " + ", ".join(missing))
    text = render_env(values)
    with _write_lock:
        try:
            with open(ENV_PATH, encoding="utf-8") as fh:
                old = fh.read()
        except OSError:
            old = None
        if old == text:
            return False
        tmp = f"{ENV_PATH}.tmp-{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, ENV_PATH)
        with open(CHANGED_FLAG, "w", encoding="ascii") as fh:
            fh.write(str(int(time.time())))
        return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "valmera-box-provision"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/provision/health":
            names = set()
            try:
                with open(ENV_PATH, encoding="utf-8") as fh:
                    names = {line.split("=", 1)[0] for line in fh
                             if "=" in line and not line.startswith("#")}
            except OSError:
                pass
            have = all(n in names for n in REQUIRED)
            return self.send_json(200, {"ok": True, "provisioned": have,
                                        "names": len(names)})
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/provision/secrets":
            return self.send_json(404, {"error": "not found"})
        got = self.headers.get("Authorization", "")
        token = got[7:] if got.startswith("Bearer ") else ""
        try:
            claims = verify_token(token)
            claim_jti(claims)
        except ValueError as exc:
            log(f"refused: {exc}")
            return self.send_json(401, {"error": f"unauthorized: {exc}"})
        except Exception as exc:  # noqa: BLE001 - JWKS fetch trouble
            log(f"verification unavailable: {type(exc).__name__}")
            return self.send_json(503, {"error": "verification unavailable"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 256 * 1024:
                raise ValueError("body too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            values = body.get("env") if isinstance(body, dict) else None
            if not isinstance(values, dict):
                raise ValueError("body must carry an env object")
            values = {k: v for k, v in values.items() if k in ALLOWED}
            changed = write_env(values)
        except ValueError as exc:
            log(f"rejected from run {claims.get('run_id')}: {exc}")
            return self.send_json(400, {"error": str(exc)})
        names = sorted(k for k, v in values.items() if v)
        log(f"provisioned {len(names)} names from run {claims.get('run_id')} "
            f"sha {str(claims.get('sha'))[:12]} changed={changed}")
        return self.send_json(200, {
            "ok": True, "changed": changed, "names": names,
            "lengths": {k: len(str(values[k])) for k in names}})


def main():
    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    server = ThreadingHTTPServer(LISTEN, Handler)
    server.daemon_threads = True
    log(f"listening on {LISTEN[0]}:{LISTEN[1]}")
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
