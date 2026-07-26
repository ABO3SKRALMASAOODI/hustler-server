"""The MCP surface's protocol contract, end to end, with Postgres faked.

Two clients have to work and they authenticate completely differently:

  * Claude Code  — static bearer token in a header.
  * claude.ai    — no header field exists, so it MUST discover an
                   authorization server from the 401, register itself, run a
                   browser login and exchange a code. Break any one of those
                   links and the connector cannot be added at all, while
                   Claude Code keeps working and hides it.

So the OAuth dance is tested as one continuous flow, in the exact order
claude.ai performs it, plus the ways it should refuse.

    cd backend && python -m pytest tests -q
"""

import base64
import contextlib
import hashlib
import json
import os
import re
import sys
from urllib.parse import urlsplit, parse_qs

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
os.environ.setdefault("BACKEND_URL", "https://api.example.com")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402

import routes.mcp as mcpmod  # noqa: E402
import routes.mcp_oauth as oauth  # noqa: E402

EMAIL = "thevalmera@gmail.com"
PASSWORD = "correct horse battery staple"
STATIC_TOKEN = "vlm_mcp_static"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"

CATALOG = {
    "tools": [{"type": "function", "function": {
        "name": "get_transcript",
        "description": "Read the transcript.",
        "parameters": {"type": "object",
                       "properties": {"start": {"type": "number"}}}}}],
    "system_prompt": "DOCTRINE.",
    "capabilities": "CAPABILITIES — ...",
}

DB = {}


def _reset():
    DB.clear()
    # The static-token client starts with project 3 open, which is the normal
    # state for an editing call; the one test that needs no open project
    # clears it explicitly.
    DB.update(clients={}, grants={}, codes={}, tokens={}, seq=0,
              static_project=3, expired_codes=set())


def _next_id():
    DB["seq"] += 1
    return DB["seq"]


class FakeCur:
    """Recognises the handful of statements these two modules issue. Anything
    unmatched returns no rows, which surfaces as a clean failure rather than a
    silent pass."""

    rowcount = 1

    def __init__(self):
        self.rows = []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        p = params or ()
        if s.startswith("INSERT INTO mcp_oauth_clients"):
            DB["clients"][p[0]] = {"client_id": p[0], "client_name": p[1],
                                   "redirect_uris": json.loads(p[2])}
        elif "FROM mcp_oauth_clients" in s:
            c = DB["clients"].get(p[0])
            self.rows = [c] if c else []
        elif "FROM users WHERE LOWER(email)" in s:
            self.rows = ([{"id": 60, "email": EMAIL,
                           "password": generate_password_hash(PASSWORD),
                           "is_verified": 1}] if p[0] == EMAIL else [])
        elif s.startswith("INSERT INTO mcp_oauth_grants"):
            gid = _next_id()
            DB["grants"][gid] = {"id": gid, "user_id": p[0], "client_id": p[1],
                                 "revoked_at": None, "active_project_id": None}
            self.rows = [{"id": gid}]
        elif s.startswith("INSERT INTO mcp_oauth_codes"):
            DB["codes"][p[0]] = {"code_sha256": p[0], "grant_id": p[1],
                                 "client_id": p[2], "redirect_uri": p[3],
                                 "code_challenge": p[4], "resource": p[5],
                                 "used_at": None, "expires_at": p[0]}
        elif "FROM mcp_oauth_codes WHERE code_sha256" in s:
            row = DB["codes"].get(p[0])
            self.rows = [row] if row else []
        elif s.startswith("SELECT NOW() >"):
            self.rows = [{"expired": p[0] in DB["expired_codes"]}]
        elif s.startswith("UPDATE mcp_oauth_codes SET used_at"):
            DB["codes"][p[0]]["used_at"] = "now"
        elif s.startswith("INSERT INTO mcp_oauth_tokens"):
            for gid, kind, sha in ((p[0], "access", p[1]),
                                   (p[3], "refresh", p[4])):
                DB["tokens"][sha] = {"id": _next_id(), "grant_id": gid,
                                     "kind": kind, "revoked_at": None,
                                     "expired": False}
        elif "FROM mcp_oauth_tokens t JOIN mcp_oauth_grants g" in s:
            t = DB["tokens"].get(p[0])
            want = "refresh" if "kind = 'refresh'" in s else "access"
            if t and t["kind"] == want:
                g = DB["grants"][t["grant_id"]]
                self.rows = [{"id": t["id"], "token_id": t["id"],
                              "grant_id": g["id"], "user_id": g["user_id"],
                              "revoked_at": t["revoked_at"],
                              "expired": t["expired"],
                              "grant_revoked": g["revoked_at"],
                              "active_project_id": g["active_project_id"],
                              "email": EMAIL}]
        elif s.startswith("UPDATE mcp_oauth_tokens SET revoked_at") \
                and "id = %s" in s:
            for t in DB["tokens"].values():
                if t["id"] == p[0]:
                    t["revoked_at"] = "now"
        elif s.startswith("UPDATE mcp_oauth_tokens SET revoked_at"):
            for t in DB["tokens"].values():
                if t["grant_id"] == p[0]:
                    t["revoked_at"] = "now"
        elif s.startswith("UPDATE mcp_oauth_grants SET revoked_at"):
            DB["grants"][p[0]]["revoked_at"] = "now"
        elif s.startswith("UPDATE mcp_oauth_grants SET active_project_id"):
            DB["grants"][p[1]]["active_project_id"] = p[0]
        elif "FROM mcp_tokens t JOIN users" in s:
            want = hashlib.sha256(STATIC_TOKEN.encode()).hexdigest()
            self.rows = ([{"id": 1, "user_id": 60, "email": EMAIL,
                           "revoked_at": None,
                           "active_project_id": DB["static_project"]}]
                         if p[0] == want else [])
        elif s.startswith("UPDATE mcp_tokens SET active_project_id"):
            DB["static_project"] = p[0]
        elif "FROM mcp_catalog" in s:
            self.rows = [{"json": CATALOG}]
        elif "type = 'agent_turn'" in s:
            self.rows = []
        elif s.startswith("INSERT INTO video_jobs"):
            self.rows = [{"id": 5}]
        elif "FROM video_jobs WHERE id" in s:
            self.rows = [{"id": 5, "type": "mcp_tool", "state": "done",
                          "progress": 100, "error": None, "payload": {},
                          "project_id": 3,
                          "result": {"text": "12 sentences."}}]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def cursor(self):
        return FakeCur()


@contextlib.contextmanager
def fake_vdb():
    yield FakeConn()


@pytest.fixture(autouse=True)
def stub_db(monkeypatch):
    _reset()
    monkeypatch.setattr(mcpmod, "vdb", fake_vdb)
    monkeypatch.setattr(oauth, "vdb", fake_vdb)
    monkeypatch.setattr(mcpmod, "_project_for_user",
                        lambda cur, pid, uid: {"id": pid, "title": "P"})
    monkeypatch.setattr(mcpmod, "_enqueue",
                        lambda cur, pid, uid, t, payload: 5)
    mcpmod._catalog_cache.update(at=0.0, json=None)


@pytest.fixture
def client():
    from app import create_app
    return create_app().test_client()


def rpc(client, method, token=None, params=None, accept=None, _id=1):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if accept:
        h["Accept"] = accept
    return client.post("/mcp", headers=h, data=json.dumps(
        {"jsonrpc": "2.0", "id": _id, "method": method,
         "params": params or {}}))


def text_of(resp):
    return resp.get_json()["result"]["content"][0]["text"]


# ── the handshake ────────────────────────────────────────────────────

def test_initialize_carries_the_editing_doctrine(client):
    b = rpc(client, "initialize", STATIC_TOKEN,
            {"protocolVersion": "2025-06-18"}).get_json()
    assert b["result"]["protocolVersion"] == "2025-06-18"
    assert "DOCTRINE." in b["result"]["instructions"]
    assert "ONE ACTIVE PROJECT" in b["result"]["instructions"]


def test_unknown_protocol_version_falls_back_to_ours(client):
    b = rpc(client, "initialize", STATIC_TOKEN,
            {"protocolVersion": "1999-01-01"}).get_json()
    assert b["result"]["protocolVersion"] == mcpmod.DEFAULT_PROTOCOL


def test_notification_gets_no_reply(client):
    r = client.post("/mcp", headers={"Authorization": f"Bearer {STATIC_TOKEN}",
                                     "Content-Type": "application/json"},
                    data=json.dumps({"jsonrpc": "2.0",
                                     "method": "notifications/initialized"}))
    assert r.status_code == 202 and not r.data


def test_tools_list_is_session_tools_plus_the_worker_registry(client):
    tools = rpc(client, "tools/list", STATIC_TOKEN).get_json()["result"]["tools"]
    names = [t["name"] for t in tools]
    assert "open_project" in names and "get_transcript" in names
    editor = [t for t in tools if t["name"] == "get_transcript"][0]
    # The schema must arrive exactly as the worker published it — a rewrite
    # here is how the two tool surfaces would start to differ.
    assert editor["inputSchema"] == \
        CATALOG["tools"][0]["function"]["parameters"]


def test_sse_preferring_client_gets_an_sse_frame(client):
    r = rpc(client, "ping", STATIC_TOKEN, accept="text/event-stream")
    assert r.mimetype == "text/event-stream"
    assert r.data.startswith(b"event: message")


def test_json_preferring_client_gets_json(client):
    r = rpc(client, "ping", STATIC_TOKEN,
            accept="application/json, text/event-stream")
    assert r.mimetype == "application/json"


# ── tool calls ───────────────────────────────────────────────────────

def test_editor_tool_returns_the_workers_own_text(client):
    r = rpc(client, "tools/call", STATIC_TOKEN,
            {"name": "get_transcript", "arguments": {}})
    assert text_of(r) == "12 sentences."
    assert r.get_json()["result"].get("isError") is not True


def test_unknown_tool_explains_the_likely_reason(client):
    r = rpc(client, "tools/call", STATIC_TOKEN,
            {"name": "nope", "arguments": {}})
    assert r.get_json()["result"]["isError"] is True
    assert "hidden rather than failing" in text_of(r)


def test_editing_without_an_open_project_says_what_to_do(client):
    DB["static_project"] = None
    assert "open_project" in text_of(rpc(
        client, "tools/call", STATIC_TOKEN,
        {"name": "get_transcript", "arguments": {}}))


def test_unknown_method_is_a_jsonrpc_error(client):
    assert rpc(client, "nonsense", STATIC_TOKEN).get_json()["error"]["code"] \
        == -32601


# ── the claude.ai connector flow, in order ───────────────────────────

def _pkce():
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def test_claude_ai_connector_flow(client):
    # 1. The 401 is the only entry point claude.ai has.
    r = rpc(client, "initialize")
    assert r.status_code == 401
    wa = r.headers.get("WWW-Authenticate", "")
    meta_url = re.search(r'resource_metadata="([^"]+)"', wa).group(1)
    assert meta_url.startswith("https://api.example.com/.well-known/")

    # 2. Discovery.
    prm = client.get(urlsplit(meta_url).path).get_json()
    assert prm["resource"] == "https://api.example.com/mcp"
    assert prm["authorization_servers"] == ["https://api.example.com"]
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code \
        == 200
    asm = client.get("/.well-known/oauth-authorization-server").get_json()
    assert asm["issuer"] == "https://api.example.com"
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["token_endpoint_auth_methods_supported"] == ["none"]

    # 3. The client enrols itself — no human involved yet.
    reg = client.post("/mcp/oauth/register",
                      json={"client_name": "Claude",
                            "redirect_uris": [CALLBACK]})
    assert reg.status_code == 201
    client_id = reg.get_json()["client_id"]

    # 4. Login + consent.
    verifier, challenge = _pkce()
    q = {"client_id": client_id, "redirect_uri": CALLBACK,
         "response_type": "code", "scope": oauth.SCOPE, "state": "xyz123",
         "code_challenge": challenge, "code_challenge_method": "S256",
         "resource": "https://api.example.com/mcp"}
    page = client.get("/mcp/oauth/authorize", query_string=q)
    assert page.status_code == 200
    assert b"wants to edit your videos" in page.data
    assert b"Claude" in page.data and b'value="xyz123"' in page.data

    r = client.post("/mcp/oauth/authorize",
                    data={**q, "action": "allow", "email": EMAIL,
                          "password": PASSWORD})
    assert r.status_code == 302 and r.headers["Location"].startswith(CALLBACK)
    qs = parse_qs(urlsplit(r.headers["Location"]).query)
    assert qs["state"] == ["xyz123"]
    code = qs["code"][0]
    grant_id = DB["codes"][hashlib.sha256(code.encode()).hexdigest()]["grant_id"]

    # 5. Code -> tokens, PKCE enforced.
    bad = client.post("/mcp/oauth/token",
                      data={"grant_type": "authorization_code", "code": code,
                            "redirect_uri": CALLBACK, "client_id": client_id,
                            "code_verifier": "not-the-verifier"})
    assert bad.status_code == 400
    assert bad.get_json()["error"] == "invalid_grant"

    tok = client.post("/mcp/oauth/token",
                      data={"grant_type": "authorization_code", "code": code,
                            "redirect_uri": CALLBACK, "client_id": client_id,
                            "code_verifier": verifier}).get_json()
    assert tok["token_type"] == "Bearer" and tok["refresh_token"]
    access = tok["access_token"]

    # 6. It edits.
    assert "DOCTRINE." in rpc(client, "initialize", access
                              ).get_json()["result"]["instructions"]
    rpc(client, "tools/call", access,
        {"name": "open_project", "arguments": {"project_id": 3}})
    assert DB["grants"][grant_id]["active_project_id"] == 3
    assert text_of(rpc(client, "tools/call", access,
                       {"name": "get_transcript", "arguments": {}})) \
        == "12 sentences."

    # 7. Refresh rotates, and the open project survives it — the whole reason
    #    that pointer lives on the grant and not on the token.
    tok2 = client.post("/mcp/oauth/token",
                       data={"grant_type": "refresh_token",
                             "refresh_token": tok["refresh_token"],
                             "client_id": client_id}).get_json()
    assert tok2["access_token"] != access
    assert DB["grants"][grant_id]["active_project_id"] == 3
    assert client.post("/mcp/oauth/token",
                       data={"grant_type": "refresh_token",
                             "refresh_token": tok["refresh_token"],
                             "client_id": client_id}).status_code == 400

    # 8. A replayed code means the code may be stolen: the grant dies.
    assert client.post("/mcp/oauth/token",
                       data={"grant_type": "authorization_code", "code": code,
                             "redirect_uri": CALLBACK, "client_id": client_id,
                             "code_verifier": verifier}).status_code == 400
    assert DB["grants"][grant_id]["revoked_at"] is not None
    assert rpc(client, "tools/list", tok2["access_token"]).status_code == 401


# ── the ways authorization must refuse ───────────────────────────────

def _registered(client):
    return client.post("/mcp/oauth/register",
                       json={"client_name": "Claude",
                             "redirect_uris": [CALLBACK]}).get_json()["client_id"]


def _q(client_id, **over):
    _v, challenge = _pkce()
    q = {"client_id": client_id, "redirect_uri": CALLBACK,
         "response_type": "code", "scope": oauth.SCOPE, "state": "s",
         "code_challenge": challenge, "code_challenge_method": "S256"}
    q.update(over)
    return q


def test_plain_http_redirect_uri_cannot_register(client):
    r = client.post("/mcp/oauth/register",
                    json={"client_name": "Bad",
                          "redirect_uris": ["http://evil.example/x"]})
    assert r.status_code == 400


def test_unregistered_redirect_never_redirects(client):
    """An authorization server that bounces errors to an unvalidated URI is an
    open redirector. Both of these must dead-end on our own page."""
    cid = _registered(client)
    r = client.get("/mcp/oauth/authorize",
                   query_string=_q(cid, redirect_uri="https://evil.example/x"))
    assert r.status_code == 400 and b"not one this app" in r.data
    r = client.get("/mcp/oauth/authorize",
                   query_string=_q("mcpc_nope"))
    assert r.status_code == 400 and b"not registered" in r.data


def test_pkce_is_mandatory(client):
    r = client.get("/mcp/oauth/authorize",
                   query_string=_q(_registered(client), code_challenge=""))
    assert r.status_code == 302
    assert "error=invalid_request" in r.headers["Location"]


def test_wrong_password_issues_no_code(client):
    r = client.post("/mcp/oauth/authorize",
                    data={**_q(_registered(client)), "action": "allow",
                          "email": EMAIL, "password": "nope"})
    assert r.status_code == 401
    assert b"Wrong email or password" in r.data
    assert not DB["codes"]


def test_deny_returns_access_denied(client):
    r = client.post("/mcp/oauth/authorize",
                    data={**_q(_registered(client)), "action": "deny",
                          "email": EMAIL, "password": PASSWORD})
    assert "error=access_denied" in r.headers["Location"]
    assert not DB["codes"]


def test_right_password_off_the_allowlist_is_refused_honestly(client,
                                                              monkeypatch):
    """The credentials were correct and the feature is still closed. Saying
    'wrong password' here would be a lie."""
    monkeypatch.setattr(mcpmod, "ALLOWED_EMAILS", {"someone@else.com"})
    r = client.post("/mcp/oauth/authorize",
                    data={**_q(_registered(client)), "action": "allow",
                          "email": EMAIL, "password": PASSWORD})
    assert r.status_code == 403
    assert b"not enabled for this account" in r.data
    assert not DB["codes"]


def test_a_get_probe_also_gets_the_challenge(client):
    """Some clients probe with GET first; without the challenge there they
    never discover the authorization server."""
    r = client.get("/mcp")
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers.get("WWW-Authenticate", "")
