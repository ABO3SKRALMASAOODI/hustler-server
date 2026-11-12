"""An upload that carries on after its page is gone (12 Nov 2026, #883, #401).

A closed tab, a phone that kills a hidden page, a laptop lid: the page stops
sending and nothing on our side can reach it. 13 customers' uploads stopped
because the page went away, 39 more fell silent. The only web API that sends
bytes after the page is gone is Background Fetch (Chromium: desktop Chrome,
Edge, Android). It cannot send a request that needs a CORS preflight, so it
can neither PUT to storage nor send an Authorization header. It can send a
plain POST.

So a multipart transfer is handed a token when it is presigned: it names one
user, one project, one storage key and one upload id, and nothing else. With
it, a plain POST may write one part of that upload (relay-part) and finish
it once every part is in storage (handoff-complete). It is not a login: it
has no subject, and it carries an audience a login token check refuses.

Every check here reads storage or the token, never what the customer typed.
"""
import os
import time

import jwt
from flask import current_app

AUDIENCE = "valmera-upload-handoff"
TTL_S = 7 * 24 * 3600
BEAT_EVERY_PARTS = 4


def enabled():
    return os.getenv("UPLOAD_HANDOFF", "1") != "0"


def max_bytes():
    """Bytes a handed-off transfer may still have to send through our servers.

    Background Fetch posts to the API, which writes each part to storage, so
    the remainder costs our bandwidth once. Bounded so one abandoned 50 GB
    file cannot become a bill; above it the transfer waits for the page, as
    before."""
    try:
        return int(os.getenv("UPLOAD_HANDOFF_MAX_BYTES", str(4 * 1024 ** 3)))
    except ValueError:
        return 4 * 1024 ** 3


def mint(user_id, project_id, key, upload_id, kind, nbytes, part_size):
    if not (enabled() and upload_id and key and part_size):
        return None
    now = int(time.time())
    claims = {"aud": AUDIENCE, "u": int(user_id), "p": int(project_id),
              "k": str(key), "x": str(upload_id), "t": str(kind or "original"),
              "n": int(nbytes), "s": int(part_size), "iat": now,
              "exp": now + TTL_S}
    secret = current_app.config.get("SECRET_KEY")
    if not isinstance(secret, str) or not secret:
        return None
    try:
        return jwt.encode(claims, secret, algorithm="HS256")
    except Exception:  # noqa: BLE001 — a handoff is a bonus, never a refusal
        return None


def read(token, project_id, key=None, upload_id=None):
    """The claims of a valid token for this project (and key and upload id,
    when given), or None."""
    if not token or not enabled():
        return None
    try:
        c = jwt.decode(token, current_app.config["SECRET_KEY"],
                       algorithms=["HS256"], audience=AUDIENCE)
    except Exception:  # noqa: BLE001 — any bad token is simply no token
        return None
    if int(c.get("p") or 0) != int(project_id):
        return None
    if key is not None and c.get("k") != key:
        return None
    if upload_id is not None and c.get("x") != upload_id:
        return None
    return c


def attach(out, user_id, project_id, kind, nbytes):
    """Add the token to a presign or resume answer that is a multipart
    transfer with an open upload id."""
    if out.get("mode") != "multipart" or not out.get("upload_id"):
        return out
    token = mint(user_id, project_id, out.get("storage_key"),
                 out.get("upload_id"), kind, nbytes, out.get("part_size"))
    if token:
        out["handoff_token"] = token
        out["handoff_max_bytes"] = max_bytes()
        out["handoff_base"] = public_base()
    return out


def public_base():
    """Where a page's browser reaches this API directly (not through the site's
    proxy, which a background transfer cannot use and whose bodies are capped).
    UPLOAD_HANDOFF_BASE when set; else this request's own host, over https
    unless it is this machine (a local stack)."""
    from flask import request
    base = (os.getenv("UPLOAD_HANDOFF_BASE") or "").strip().rstrip("/")
    if base:
        return base
    host = request.host or ""
    local = host.split(":")[0] in ("127.0.0.1", "localhost", "[::1]")
    return f"{request.scheme if local else 'https'}://{host}"


def stored_parts(storage, claims):
    """[{part_number, etag}] when storage holds every part of the upload at
    its exact size, else (None, how many are there)."""
    n, size = int(claims["n"]), int(claims["s"])
    n_parts = (n + size - 1) // size
    have = {}
    for p in storage.list_parts(claims["k"], claims["x"]):
        num = int(p["part_number"])
        if 1 <= num <= n_parts and int(p.get("size") or 0) == \
                storage.expected_part_bytes(n, size, num):
            have[num] = {"part_number": num, "etag": p["etag"]}
    if len(have) != n_parts:
        return None, len(have)
    return [have[i] for i in range(1, n_parts + 1)], n_parts
