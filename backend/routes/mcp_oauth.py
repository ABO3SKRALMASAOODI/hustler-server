"""OAuth 2.1 authorization server for the MCP surface (round 49b).

WHY THIS EXISTS. Claude Code can hold a static bearer token in a header;
**claude.ai cannot** — its custom-connector UI has no header field. You give it
a URL, it calls the endpoint, gets a 401 pointing at this server's metadata,
**registers itself** (RFC 7591 dynamic client registration), opens a browser
login, and comes back with an authorization code. Without every one of those
pieces the connector simply cannot be added. So this file is not decoration on
top of routes/mcp.py — it is the entire difference between "works in my
terminal" and "works in the Claude app".

WHAT IT IMPLEMENTS
    /.well-known/oauth-protected-resource[/mcp]   RFC 9728 — where to get a token
    /.well-known/oauth-authorization-server[/mcp] RFC 8414 — this server's endpoints
    POST /mcp/oauth/register                      RFC 7591 — the client enrols itself
    GET/POST /mcp/oauth/authorize                 login + consent, issues a code
    POST /mcp/oauth/token                         code -> access + refresh, and rotation
    POST /mcp/oauth/revoke                        RFC 7009

PKCE (S256) is REQUIRED, not optional: the clients here are public — they hold
no secret — so the code interception defence has to come from the challenge.
Every credential (code, access token, refresh token) is stored as sha256 only.

OPEN REGISTRATION IS SAFE HERE, and it has to be open — the client registers
before any human is involved. Registering grants nothing: authorization still
needs a real password login AND an email on MCP_ALLOWED_EMAILS, which today is
one address. A stranger who registers a client gets a login screen that will
never say yes to them.

WHAT THIS DELIBERATELY IS NOT: a general-purpose IdP. It issues tokens for one
resource (the MCP endpoint) and one scope. Anything else it is asked for is
refused rather than approximated.
"""

import hashlib
import json
import os
import secrets
import time
from base64 import urlsafe_b64encode
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, request, jsonify, redirect, Response
from werkzeug.security import check_password_hash

from routes.video import vdb

mcp_oauth_bp = Blueprint("mcp_oauth", __name__)

SCOPE = "valmera.edit"
ACCESS_TTL_S = int(os.getenv("MCP_ACCESS_TTL_S", str(8 * 3600)))
REFRESH_TTL_S = int(os.getenv("MCP_REFRESH_TTL_S", str(90 * 24 * 3600)))
CODE_TTL_S = 300


def base_url():
    """This server's public origin. It is the OAuth `issuer`, so it has to be
    byte-identical everywhere it appears or a strict client rejects the
    metadata it just fetched."""
    return os.getenv("BACKEND_URL",
                     "https://entrepreneur-bot-backend.onrender.com").rstrip("/")


def resource_url():
    return f"{base_url()}/mcp"


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _allowed_emails():
    # Read through routes.mcp so there is ONE allowlist, not two that can
    # disagree about who may connect.
    from routes.mcp import ALLOWED_EMAILS
    return ALLOWED_EMAILS


# ------------------------------------------------------------------ #
#  Discovery                                                           #
# ------------------------------------------------------------------ #

@mcp_oauth_bp.route("/.well-known/oauth-protected-resource")
@mcp_oauth_bp.route("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata():
    """RFC 9728. The client reads this off the 401 to learn which
    authorization server guards the MCP endpoint. Both paths are served
    because clients differ on whether the resource path is appended."""
    return jsonify({
        "resource": resource_url(),
        "authorization_servers": [base_url()],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Valmera Video Editor",
        "resource_documentation": "https://valmera.io/docs",
    })


@mcp_oauth_bp.route("/.well-known/oauth-authorization-server")
@mcp_oauth_bp.route("/.well-known/oauth-authorization-server/mcp")
@mcp_oauth_bp.route("/.well-known/openid-configuration")
def authorization_server_metadata():
    """RFC 8414. token_endpoint_auth_methods_supported is ["none"] on purpose:
    these are public clients, which is exactly why PKCE is mandatory below."""
    b = base_url()
    return jsonify({
        "issuer": b,
        "authorization_endpoint": f"{b}/mcp/oauth/authorize",
        "token_endpoint": f"{b}/mcp/oauth/token",
        "registration_endpoint": f"{b}/mcp/oauth/register",
        "revocation_endpoint": f"{b}/mcp/oauth/revoke",
        "scopes_supported": [SCOPE],
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "service_documentation": f"{b}/mcp",
    })


# ------------------------------------------------------------------ #
#  Dynamic client registration (RFC 7591)                              #
# ------------------------------------------------------------------ #

def _valid_redirect(uri):
    """https only, no fragment, and a real host. localhost over http is
    allowed because desktop MCP clients loop back to it."""
    try:
        u = urlsplit(uri)
    except ValueError:
        return False
    if u.fragment or not u.netloc:
        return False
    if u.scheme == "https":
        return True
    return u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1")


@mcp_oauth_bp.route("/mcp/oauth/register", methods=["POST"])
def register_client():
    body = request.get_json(silent=True) or {}
    uris = body.get("redirect_uris") or []
    if not isinstance(uris, list) or not uris:
        return jsonify({"error": "invalid_redirect_uri",
                        "error_description": "redirect_uris is required"}), 400
    uris = [u for u in uris if isinstance(u, str)][:10]
    bad = [u for u in uris if not _valid_redirect(u)]
    if bad or not uris:
        return jsonify({"error": "invalid_redirect_uri",
                        "error_description": f"unusable redirect_uri: "
                                             f"{(bad or ['none'])[0]}"}), 400
    client_id = "mcpc_" + secrets.token_urlsafe(24)
    name = (body.get("client_name") or "Unnamed client")[:120]
    with vdb() as conn:
        conn.cursor().execute(
            """INSERT INTO mcp_oauth_clients (client_id, client_name,
                                              redirect_uris, metadata)
               VALUES (%s, %s, %s, %s)""",
            (client_id, name, json.dumps(uris), json.dumps(body)[:8000]))
    return jsonify({
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "client_name": name,
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        # No secret is issued, and none is needed: a client that cannot keep
        # one is more honest as a public client with PKCE than as a
        # "confidential" one whose secret ships inside an app.
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
    }), 201


def _client(client_id):
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM mcp_oauth_clients WHERE client_id = %s",
                    (client_id,))
        return cur.fetchone()


# ------------------------------------------------------------------ #
#  Authorization: login + consent                                      #
# ------------------------------------------------------------------ #

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect Valmera</title><style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;
justify-content:center;background:#0b0b0b;color:#f4f4f4;
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:24px}
.card{width:100%;max-width:420px;background:#111;border:1px solid #1e1e1e;
border-radius:16px;padding:32px}
.eyebrow{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;
letter-spacing:.18em;text-transform:uppercase;color:#8a8a8a;margin:0 0 18px}
h1{font-size:21px;line-height:1.3;margin:0 0 8px;font-weight:650}
p{color:#a0a0a0;margin:0 0 20px;font-size:14px}
.host{color:#f4f4f4}
label{display:block;font-size:12px;color:#8a8a8a;margin:14px 0 6px}
input{width:100%;padding:11px 13px;border-radius:10px;border:1px solid #262626;
background:#0b0b0b;color:#f4f4f4;font-size:15px}
input:focus{outline:none;border-color:#3a3a3a}
.row{display:flex;gap:10px;margin-top:22px}
button{flex:1;padding:12px;border-radius:10px;border:0;font-size:14px;
font-weight:600;cursor:pointer}
/* ALLOW IS FIRST IN THE MARKUP AND SECOND ON THE SCREEN, and that is not a
   style choice. Pressing Enter in a form does IMPLICIT SUBMISSION, which
   activates the FIRST submit button — so with Cancel written first, typing a
   password and hitting Enter posted action=deny. The server read that
   correctly as a refusal and bounced the app away with access_denied, and the
   connector reported "Unable to authorize app": a login that failed for
   everyone who does not reach for the mouse, blaming the wrong thing. Order
   restores the layout without restoring the trap. */
.allow{background:#f4f4f4;color:#0b0b0b;order:2}
.deny{background:transparent;color:#a0a0a0;border:1px solid #262626;order:1}
.err{background:#2a1215;border:1px solid #532228;color:#ff9aa2;padding:10px 12px;
border-radius:10px;font-size:13px;margin-bottom:16px}
.note{margin-top:20px;font-size:12px;color:#6d6d6d;line-height:1.5}
ul{margin:0 0 20px;padding-left:18px;color:#a0a0a0;font-size:13.5px}
li{margin:4px 0}
</style></head><body><div class="card">
<p class="eyebrow">Valmera · connect an app</p>
<h1><span class="host">__CLIENT__</span> wants to edit your videos</h1>
__ERROR__
<ul>
<li>Read your projects, footage and transcripts</li>
<li>Cut, caption, score and render them</li>
<li>Export finished videos</li>
</ul>
<p>Sign in to allow it. It cannot see your password, your card, or anything
outside your video projects.</p>
<form method="POST">
__HIDDEN__
<label for="email">Email</label>
<input id="email" name="email" type="email" autocomplete="username"
 value="__EMAIL__" required autofocus>
<label for="password">Password</label>
<input id="password" name="password" type="password"
 autocomplete="current-password" required>
<div class="row">
<button class="allow" name="action" value="allow" type="submit">Allow</button>
<button class="deny" name="action" value="deny" type="submit">Cancel</button>
</div>
</form>
<p class="note">Redirects to <b>__REDIRECT_HOST__</b> when you allow.
Signed up with Google? You have no password yet — set one with
&ldquo;Forgot password&rdquo; on valmera.io, then come back.</p>
</div></body></html>"""


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _page(params, client_name, error=None, email=""):
    hidden = "".join(
        f'<input type="hidden" name="{_esc(k)}" value="{_esc(v)}">'
        for k, v in params.items() if v)
    return (PAGE
            .replace("__CLIENT__", _esc(client_name))
            .replace("__HIDDEN__", hidden)
            .replace("__EMAIL__", _esc(email))
            .replace("__REDIRECT_HOST__",
                     _esc(urlsplit(params.get("redirect_uri", "")).netloc))
            .replace("__ERROR__",
                     f'<div class="err">{_esc(error)}</div>' if error else ""))


def _fail_page(message, code=400):
    """A dead end that does NOT redirect. Used only for a bad client_id or an
    unregistered redirect_uri: bouncing an error to an unvalidated URI is how
    an authorization server becomes an open redirector."""
    body = (PAGE.replace("__CLIENT__", "This app")
            .replace("__ERROR__", f'<div class="err">{_esc(message)}</div>')
            .replace("__HIDDEN__", "").replace("__EMAIL__", "")
            .replace("__REDIRECT_HOST__", "nowhere"))
    body = body[:body.index("<form")] + "</div></body></html>"
    return Response(body, status=code, mimetype="text/html")


def _redirect_err(uri, state, error, desc):
    q = {"error": error, "error_description": desc}
    if state:
        q["state"] = state
    sep = "&" if urlsplit(uri).query else "?"
    return redirect(f"{uri}{sep}{urlencode(q)}")


# Password attempts per email, per process. Brute force here has to get past
# an allowlist of one address, so this is a speed bump rather than a wall —
# but an unthrottled password form is never acceptable.
_fails = {}
MAX_FAILS = 6
FAIL_WINDOW_S = 900


def _too_many(email):
    hits = [t for t in _fails.get(email, []) if time.time() - t < FAIL_WINDOW_S]
    _fails[email] = hits
    return len(hits) >= MAX_FAILS


@mcp_oauth_bp.route("/mcp/oauth/authorize", methods=["GET", "POST"])
def authorize():
    src = request.form if request.method == "POST" else request.args
    params = {k: src.get(k, "") for k in
              ("client_id", "redirect_uri", "response_type", "scope", "state",
               "code_challenge", "code_challenge_method", "resource")}

    client = _client(params["client_id"]) if params["client_id"] else None
    if not client:
        return _fail_page("This app is not registered with Valmera. It has to "
                          "register itself before it can ask for access.")
    registered = client["redirect_uris"] or []
    if params["redirect_uri"] not in registered:
        return _fail_page("That redirect address is not one this app "
                          "registered, so Valmera will not send anything to "
                          "it.")

    uri, state = params["redirect_uri"], params["state"]
    if params["response_type"] != "code":
        return _redirect_err(uri, state, "unsupported_response_type",
                             "only the authorization code flow is supported")
    if not params["code_challenge"] or \
            params["code_challenge_method"] not in ("S256", ""):
        return _redirect_err(uri, state, "invalid_request",
                             "PKCE with S256 is required")
    if params["code_challenge_method"] == "":
        params["code_challenge_method"] = "S256"

    name = client["client_name"] or "An app"
    if request.method == "GET":
        return Response(_page(params, name), mimetype="text/html")

    action = src.get("action") or ""
    if action == "deny":
        return _redirect_err(uri, state, "access_denied",
                             "the user declined")
    if action != "allow":
        # A post carrying NO button value is not consent — but it is not a
        # refusal either, and treating the two the same is what made the
        # Enter-key bug unreadable: the user was sent back to the app with
        # "you declined" over a form they had just filled in and submitted.
        # Absent stays on our page, where the sentence can name the fix.
        return Response(_page(params, name,
                              "Press Allow to connect, or Cancel to stop.",
                              (src.get("email") or "").strip()),
                        status=400, mimetype="text/html")

    email = (src.get("email") or "").strip().lower()
    password = src.get("password") or ""
    if _too_many(email):
        return Response(_page(params, name, "Too many attempts. Wait a few "
                              "minutes and try again.", email),
                        status=429, mimetype="text/html")

    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, email, password, is_verified FROM users "
                    "WHERE LOWER(email) = %s", (email,))
        user = cur.fetchone()
    ok = bool(user) and user["is_verified"] and \
        check_password_hash(user["password"], password)
    if not ok:
        _fails.setdefault(email, []).append(time.time())
        time.sleep(0.4)
        return Response(_page(params, name, "Wrong email or password.", email),
                        status=401, mimetype="text/html")
    if email not in _allowed_emails():
        # Honest and specific: the credentials were right, the feature is not
        # open. Telling them it was the password would be a lie.
        return Response(_page(params, name,
                              "Connecting apps to Valmera is not enabled for "
                              "this account yet.", email),
                        status=403, mimetype="text/html")

    code = secrets.token_urlsafe(32)
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO mcp_oauth_grants (user_id, client_id, scope)
                       VALUES (%s, %s, %s) RETURNING id""",
                    (user["id"], client["client_id"], params["scope"] or SCOPE))
        grant_id = cur.fetchone()["id"]
        cur.execute("""INSERT INTO mcp_oauth_codes
                           (code_sha256, grant_id, client_id, redirect_uri,
                            code_challenge, resource, expires_at)
                       VALUES (%s, %s, %s, %s, %s, %s,
                               NOW() + make_interval(secs => %s))""",
                    (_sha(code), grant_id, client["client_id"], uri,
                     params["code_challenge"], params["resource"] or None,
                     CODE_TTL_S))
    q = {"code": code}
    if state:
        q["state"] = state
    sep = "&" if urlsplit(uri).query else "?"
    return redirect(f"{uri}{sep}{urlencode(q)}")


# ------------------------------------------------------------------ #
#  Token                                                               #
# ------------------------------------------------------------------ #

def _token_error(err, desc, status=400):
    return jsonify({"error": err, "error_description": desc}), status


def _issue(cur, grant_id):
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    cur.execute("""INSERT INTO mcp_oauth_tokens (grant_id, kind, token_sha256,
                                                 expires_at)
                   VALUES (%s, 'access', %s, NOW() + make_interval(secs => %s)),
                          (%s, 'refresh', %s, NOW() + make_interval(secs => %s))
                """, (grant_id, _sha(access), ACCESS_TTL_S,
                      grant_id, _sha(refresh), REFRESH_TTL_S))
    return {"access_token": access, "token_type": "Bearer",
            "expires_in": ACCESS_TTL_S, "refresh_token": refresh,
            "scope": SCOPE}


def _pkce_ok(verifier, challenge):
    if not verifier:
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return secrets.compare_digest(
        urlsafe_b64encode(digest).decode().rstrip("="), challenge)


@mcp_oauth_bp.route("/mcp/oauth/token", methods=["POST"])
def token():
    f = request.form or {}
    grant_type = f.get("grant_type") or ""

    if grant_type == "authorization_code":
        code = f.get("code") or ""
        with vdb() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT * FROM mcp_oauth_codes
                           WHERE code_sha256 = %s FOR UPDATE""", (_sha(code),))
            row = cur.fetchone()
            if not row:
                return _token_error("invalid_grant", "unknown code")
            # Replay: a code presented twice means it may have been stolen, so
            # the whole grant dies rather than the second attempt alone.
            if row["used_at"]:
                cur.execute("""UPDATE mcp_oauth_grants SET revoked_at = NOW()
                               WHERE id = %s""", (row["grant_id"],))
                cur.execute("""UPDATE mcp_oauth_tokens SET revoked_at = NOW()
                               WHERE grant_id = %s AND revoked_at IS NULL""",
                            (row["grant_id"],))
                return _token_error("invalid_grant",
                                    "this code was already used")
            cur.execute("SELECT NOW() > %s AS expired", (row["expires_at"],))
            if cur.fetchone()["expired"]:
                return _token_error("invalid_grant", "code expired")
            if f.get("client_id") and f["client_id"] != row["client_id"]:
                return _token_error("invalid_grant", "client mismatch")
            if f.get("redirect_uri") and \
                    f["redirect_uri"] != row["redirect_uri"]:
                return _token_error("invalid_grant", "redirect_uri mismatch")
            if not _pkce_ok(f.get("code_verifier"), row["code_challenge"]):
                return _token_error("invalid_grant", "PKCE verification failed")
            cur.execute("UPDATE mcp_oauth_codes SET used_at = NOW() "
                        "WHERE code_sha256 = %s", (_sha(code),))
            out = _issue(cur, row["grant_id"])
        return jsonify(out)

    if grant_type == "refresh_token":
        raw = f.get("refresh_token") or ""
        with vdb() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT t.id, t.grant_id, t.revoked_at,
                                  NOW() > t.expires_at AS expired,
                                  g.revoked_at AS grant_revoked
                           FROM mcp_oauth_tokens t
                           JOIN mcp_oauth_grants g ON g.id = t.grant_id
                           WHERE t.token_sha256 = %s AND t.kind = 'refresh'""",
                        (_sha(raw),))
            row = cur.fetchone()
            if not row or row["revoked_at"] or row["expired"] \
                    or row["grant_revoked"]:
                return _token_error("invalid_grant",
                                    "refresh token is not usable")
            # Rotation: the presented refresh token dies with the access
            # tokens it authorized, so a stolen copy is worth one use at most.
            cur.execute("UPDATE mcp_oauth_tokens SET revoked_at = NOW() "
                        "WHERE id = %s", (row["id"],))
            out = _issue(cur, row["grant_id"])
        return jsonify(out)

    return _token_error("unsupported_grant_type",
                        f"grant_type {grant_type or '(missing)'} is not "
                        "supported")


@mcp_oauth_bp.route("/mcp/oauth/revoke", methods=["POST"])
def revoke():
    raw = (request.form or {}).get("token") or ""
    if raw:
        with vdb() as conn:
            cur = conn.cursor()
            cur.execute("""UPDATE mcp_oauth_tokens SET revoked_at = NOW()
                           WHERE token_sha256 = %s AND revoked_at IS NULL
                           RETURNING grant_id, kind""", (_sha(raw),))
            row = cur.fetchone()
            # Revoking a refresh token ends the whole connection, which is what
            # a user pressing "disconnect" means by it.
            if row and row["kind"] == "refresh":
                cur.execute("""UPDATE mcp_oauth_grants SET revoked_at = NOW()
                               WHERE id = %s""", (row["grant_id"],))
    # RFC 7009: always 200, so a token that never existed is indistinguishable
    # from one that did.
    return "", 200


# ------------------------------------------------------------------ #
#  Verification (used by routes/mcp.py on every call)                  #
# ------------------------------------------------------------------ #

def verify_access_token(raw):
    """(session_dict, error). The dict mirrors what a static token yields, so
    the MCP endpoint never has to care which kind it got."""
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT t.id AS token_id, t.revoked_at,
                              NOW() > t.expires_at AS expired,
                              g.id AS grant_id, g.user_id, g.revoked_at
                                  AS grant_revoked, g.active_project_id,
                              u.email
                       FROM mcp_oauth_tokens t
                       JOIN mcp_oauth_grants g ON g.id = t.grant_id
                       JOIN users u ON u.id = g.user_id
                       WHERE t.token_sha256 = %s AND t.kind = 'access'""",
                    (_sha(raw),))
        row = cur.fetchone()
        if not row:
            return None, "unknown token"
        if row["revoked_at"] or row["grant_revoked"]:
            return None, "this connection was disconnected"
        if row["expired"]:
            # Named exactly, because the client's correct response is to
            # refresh rather than to ask the user to reconnect.
            return None, "access token expired"
        if (row["email"] or "").lower() not in _allowed_emails():
            return None, "this account is not enabled for MCP access"
        cur.execute("""UPDATE mcp_oauth_grants
                       SET last_used_at = NOW(), calls = calls + 1
                       WHERE id = %s""", (row["grant_id"],))
    return {"source": "oauth", "ref_id": row["grant_id"],
            "user_id": row["user_id"], "email": row["email"],
            "active_project_id": row["active_project_id"]}, None


def set_active_project(grant_id, project_id):
    with vdb() as conn:
        conn.cursor().execute(
            "UPDATE mcp_oauth_grants SET active_project_id = %s WHERE id = %s",
            (project_id, grant_id))
