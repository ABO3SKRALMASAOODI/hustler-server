-- Round 49b — OAuth 2.1 for the MCP surface, so claude.ai can add Valmera as
-- a custom connector.
--
-- Claude Code can carry a static bearer token in a header; claude.ai cannot.
-- Its connector UI has no header field — it discovers an authorization server
-- from the 401, registers itself dynamically, and runs a browser login. That
-- is the whole reason these four tables exist.
--
-- The GRANT is the durable thing (this user let this client edit their video)
-- and tokens hang off it, so refresh-token rotation never loses which project
-- the connector had open.

-- Dynamically registered clients (RFC 7591). Registration is open by
-- necessity — the client registers itself before any human is involved — and
-- that is safe because registering grants NOTHING: every authorization still
-- requires a real login AND an email on the MCP allowlist.
CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
    client_id     TEXT PRIMARY KEY,
    client_name   TEXT,
    redirect_uris JSONB NOT NULL,
    metadata      JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per (user, client) consent. Owns the active-project pointer.
CREATE TABLE IF NOT EXISTS mcp_oauth_grants (
    id                SERIAL PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id         TEXT NOT NULL,
    scope             TEXT,
    active_project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    calls             INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at      TIMESTAMPTZ,
    revoked_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_mcp_grants_user ON mcp_oauth_grants(user_id);

-- Authorization codes: single use, short lived, PKCE-bound. Only the hash is
-- stored, like every other credential here.
CREATE TABLE IF NOT EXISTS mcp_oauth_codes (
    code_sha256    CHAR(64) PRIMARY KEY,
    grant_id       INTEGER NOT NULL REFERENCES mcp_oauth_grants(id) ON DELETE CASCADE,
    client_id      TEXT NOT NULL,
    redirect_uri   TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    resource       TEXT,
    expires_at     TIMESTAMPTZ NOT NULL,
    used_at        TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Access + refresh tokens for a grant.
CREATE TABLE IF NOT EXISTS mcp_oauth_tokens (
    id           SERIAL PRIMARY KEY,
    grant_id     INTEGER NOT NULL REFERENCES mcp_oauth_grants(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    token_sha256 CHAR(64) NOT NULL UNIQUE,
    expires_at   TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_tokens_grant
    ON mcp_oauth_tokens(grant_id, kind);
