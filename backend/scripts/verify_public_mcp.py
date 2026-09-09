#!/usr/bin/env python3
"""Verify the public MCP discovery and authentication boundary.

This probe uses no account, token, or customer data. It certifies the surface
an MCP client must traverse before login: the server card, both standard OAuth
metadata aliases, and the unauthenticated JSON-RPC challenge.
"""

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


SERVER_NAME = "io.valmera/video-editor"
REQUIRED_SESSION_TOOLS = {
    "create_project",
    "download_url",
    "index_status",
    "list_projects",
    "open_project",
    "open_short",
    "project_state",
    "shorts_status",
    "upload_finish",
    "upload_start",
    "wait_for_job",
    "watch_video",
}


class VerificationError(RuntimeError):
    """A public MCP release invariant was not satisfied."""


def _require(condition, message):
    if not condition:
        raise VerificationError(message)


def _request_json(url, method="GET", payload=None, timeout_s=30):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers=({"Content-Type": "application/json"}
                 if payload is not None else {}),
    )
    try:
        try:
            response = urlopen(request, timeout=timeout_s)
        except HTTPError as exc:
            response = exc
        raw = response.read()
        status = response.status
        headers = {key.lower(): value for key, value in response.headers.items()}
    except (OSError, URLError) as exc:
        raise VerificationError(f"request failed for {url}: {exc}") from exc
    finally:
        if "response" in locals():
            response.close()
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(
            f"expected JSON from {url}, received {len(raw)} bytes") from exc
    return status, headers, body


def verify_public_mcp(base_url, request_json=_request_json, timeout_s=30):
    """Return a bounded summary or raise ``VerificationError``."""
    base = str(base_url or "").rstrip("/")
    parsed = urlsplit(base)
    _require(parsed.scheme == "https" and parsed.netloc,
             "base URL must be an absolute HTTPS URL")

    card_url = base + "/.well-known/mcp/server-card.json"
    status, _, card = request_json(card_url, timeout_s=timeout_s)
    _require(status == 200 and isinstance(card, dict),
             "server card did not return a JSON object with HTTP 200")
    _require(card.get("name") == SERVER_NAME,
             "server card identity does not match the Valmera MCP server")
    _require(isinstance(card.get("version"), str) and card["version"],
             "server card has no version")
    server_info = card.get("serverInfo") or {}
    _require(isinstance(server_info, dict)
             and server_info.get("name") == "valmera"
             and server_info.get("version") == card["version"],
             "server card has no valid standard serverInfo")

    groups = card.get("toolGroups")
    session_tools = card.get("sessionTools")
    _require(isinstance(groups, dict) and groups,
             "server card has no editor tool groups")
    _require(isinstance(session_tools, list),
             "server card has no session tool list")
    editor_tools = []
    for names in groups.values():
        _require(isinstance(names, list)
                 and all(isinstance(name, str) and name for name in names),
                 "server card contains an invalid tool group")
        editor_tools.extend(names)
    _require(all(isinstance(name, str) and name for name in session_tools),
             "server card contains an invalid session tool name")
    published_tools = editor_tools + session_tools
    _require(len(published_tools) == len(set(published_tools)),
             "server card publishes duplicate tool names")
    _require(REQUIRED_SESSION_TOOLS.issubset(set(session_tools)),
             "server card is missing a required session tool")
    _require(card.get("toolCount") == len(published_tools),
             "server card toolCount disagrees with its published tool names")
    tools = card.get("tools")
    _require(isinstance(tools, list),
             "server card has no standard tool definitions")
    schema_names = []
    for tool in tools:
        _require(isinstance(tool, dict), "invalid standard tool definition")
        schema = tool.get("inputSchema") or {}
        _require(isinstance(tool.get("name"), str) and tool["name"]
                 and isinstance(tool.get("description"), str)
                 and tool["description"]
                 and isinstance(schema, dict) and schema.get("type") == "object"
                 and isinstance(schema.get("properties"), dict),
                 "invalid standard tool definition")
        schema_names.append(tool["name"])
    _require(sorted(schema_names) == sorted(published_tools),
             "standard tool definitions disagree with published tool names")
    _require(not {"export_final", "edit_shorts", "load_tools"}.intersection(schema_names),
             "server card exposes an internal or denied tool")
    _require(card.get("resources") == [] and card.get("prompts") == [],
             "server card advertises unsupported resources or prompts")

    mcp_url = base + "/mcp"
    remotes = card.get("remotes") or []
    _require(any(remote.get("type") == "streamable-http"
                 and remote.get("url") == mcp_url
                 for remote in remotes if isinstance(remote, dict)),
             "server card does not publish the canonical streamable HTTP URL")

    protected_url = base + "/.well-known/oauth-protected-resource"
    protected_alias = protected_url + "/mcp"
    auth_url = base + "/.well-known/oauth-authorization-server"
    auth_alias = auth_url + "/mcp"
    protected_status, _, protected = request_json(
        protected_url, timeout_s=timeout_s)
    protected_alias_status, _, protected_via_alias = request_json(
        protected_alias, timeout_s=timeout_s)
    auth_status, _, authorization = request_json(
        auth_url, timeout_s=timeout_s)
    auth_alias_status, _, authorization_via_alias = request_json(
        auth_alias, timeout_s=timeout_s)
    _require(protected_status == protected_alias_status == 200,
             "protected-resource metadata aliases are not both healthy")
    _require(auth_status == auth_alias_status == 200,
             "authorization-server metadata aliases are not both healthy")
    _require(protected == protected_via_alias,
             "protected-resource metadata aliases disagree")
    _require(authorization == authorization_via_alias,
             "authorization-server metadata aliases disagree")
    _require(protected.get("resource") == mcp_url,
             "protected-resource metadata names the wrong MCP resource")
    _require(protected.get("authorization_servers") == [base],
             "protected-resource metadata names the wrong authorization server")
    _require(authorization.get("issuer") == base,
             "authorization-server issuer does not match the service origin")
    expected_endpoints = {
        "authorization_endpoint": base + "/mcp/oauth/authorize",
        "token_endpoint": base + "/mcp/oauth/token",
        "registration_endpoint": base + "/mcp/oauth/register",
        "revocation_endpoint": base + "/mcp/oauth/revoke",
    }
    for key, expected in expected_endpoints.items():
        _require(authorization.get(key) == expected,
                 f"authorization metadata has the wrong {key}")
    _require("S256" in authorization.get(
        "code_challenge_methods_supported", []),
        "authorization metadata does not require PKCE S256")
    _require("none" in authorization.get(
        "token_endpoint_auth_methods_supported", []),
        "authorization metadata does not support public MCP clients")
    authentication = card.get("authentication") or {}
    _require(authentication.get("type") == "oauth2"
             and authentication.get("metadata") == auth_url,
             "server card OAuth metadata pointer is inconsistent")
    _require(authentication.get("required") is True
             and authentication.get("schemes") == ["oauth2"],
             "server card does not declare required OAuth authentication")

    rpc_status, rpc_headers, rpc = request_json(
        mcp_url,
        method="POST",
        payload={"jsonrpc": "2.0", "id": 1,
                 "method": "tools/list", "params": {}},
        timeout_s=timeout_s,
    )
    challenge = rpc_headers.get("www-authenticate", "")
    _require(rpc_status == 401,
             "unauthenticated tools/list did not return HTTP 401")
    _require(challenge.lower().startswith("bearer "),
             "unauthenticated tools/list has no Bearer challenge")
    _require(f'resource_metadata="{protected_url}"' in challenge,
             "Bearer challenge does not point to protected-resource metadata")
    _require(isinstance(rpc, dict)
             and (rpc.get("error") or {}).get("code") == -32001,
             "unauthenticated tools/list has the wrong JSON-RPC error")

    return {
        "status": "ok",
        "name": card["name"],
        "version": card["version"],
        "tool_count": card["toolCount"],
        "session_tool_count": len(session_tools),
        "oauth_metadata_aliases": 4,
        "unauthenticated_status": rpc_status,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True,
                        help="HTTPS origin hosting the MCP endpoint")
    parser.add_argument("--timeout", type=int, default=30,
                        help="per-request timeout in seconds (default: 30)")
    args = parser.parse_args()
    try:
        report = verify_public_mcp(
            args.base_url, timeout_s=max(1, min(120, args.timeout)))
    except VerificationError as exc:
        print(f"PUBLIC MCP VERIFICATION FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
