"""The deployment MCP probe fails closed on public-contract drift."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import verify_public_mcp as probe  # noqa: E402


BASE = "https://api.example.com"


def _responses(*, tool_count=14, challenge=True):
    session_tools = sorted(probe.REQUIRED_SESSION_TOOLS)
    card = {
        "name": probe.SERVER_NAME,
        "version": "0.1.0",
        "toolCount": tool_count,
        "toolGroups": {"inspect": ["get_edl"], "edit": ["set_frame"]},
        "sessionTools": session_tools,
        "remotes": [{"type": "streamable-http", "url": BASE + "/mcp"}],
        "authentication": {
            "type": "oauth2",
            "metadata": BASE + "/.well-known/oauth-authorization-server",
        },
    }
    protected = {
        "resource": BASE + "/mcp",
        "authorization_servers": [BASE],
    }
    authorization = {
        "issuer": BASE,
        "authorization_endpoint": BASE + "/mcp/oauth/authorize",
        "token_endpoint": BASE + "/mcp/oauth/token",
        "registration_endpoint": BASE + "/mcp/oauth/register",
        "revocation_endpoint": BASE + "/mcp/oauth/revoke",
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }
    challenge_header = (
        'Bearer realm="valmera", resource_metadata="'
        + BASE + '/.well-known/oauth-protected-resource"'
        if challenge else 'Bearer realm="valmera"')

    def request_json(url, method="GET", payload=None, timeout_s=30):
        del timeout_s
        if url.endswith("/.well-known/mcp/server-card.json"):
            return 200, {}, card
        if "/.well-known/oauth-protected-resource" in url:
            return 200, {}, protected
        if "/.well-known/oauth-authorization-server" in url:
            return 200, {}, authorization
        assert url == BASE + "/mcp"
        assert method == "POST"
        assert payload["method"] == "tools/list"
        return 401, {"www-authenticate": challenge_header}, {
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32001, "message": "authentication required"},
        }

    return request_json


def test_public_mcp_probe_certifies_discovery_and_authentication():
    report = probe.verify_public_mcp(BASE, request_json=_responses())

    assert report == {
        "status": "ok",
        "name": probe.SERVER_NAME,
        "version": "0.1.0",
        "tool_count": 14,
        "session_tool_count": 12,
        "oauth_metadata_aliases": 4,
        "unauthenticated_status": 401,
    }


def test_public_mcp_probe_rejects_catalog_count_drift():
    with pytest.raises(probe.VerificationError, match="toolCount disagrees"):
        probe.verify_public_mcp(BASE, request_json=_responses(tool_count=99))


def test_public_mcp_probe_requires_resource_metadata_challenge():
    with pytest.raises(probe.VerificationError,
                       match="does not point to protected-resource"):
        probe.verify_public_mcp(BASE, request_json=_responses(challenge=False))
