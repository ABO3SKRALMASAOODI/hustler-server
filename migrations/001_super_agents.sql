-- ══════════════════════════════════════════════════════════════════════
-- SUPER AGENTS: Database Migration
-- Run on Render shell: psql $DATABASE_URL -f migrations/001_super_agents.sql
-- ══════════════════════════════════════════════════════════════════════

-- 1. Agent definitions
CREATE TABLE IF NOT EXISTS super_agents (
    id              SERIAL PRIMARY KEY,
    agent_id        VARCHAR(16) NOT NULL UNIQUE,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(200) NOT NULL,
    description     TEXT DEFAULT '',
    system_prompt   TEXT DEFAULT '',
    model           VARCHAR(20) DEFAULT 'V6',
    status          VARCHAR(20) DEFAULT 'draft',
    config          JSONB DEFAULT '{}',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_super_agents_user_id ON super_agents(user_id);
CREATE INDEX IF NOT EXISTS idx_super_agents_agent_id ON super_agents(agent_id);

-- 2. Agent skills/tools
CREATE TABLE IF NOT EXISTS agent_skills (
    id              SERIAL PRIMARY KEY,
    agent_id        VARCHAR(16) NOT NULL REFERENCES super_agents(agent_id) ON DELETE CASCADE,
    skill_type      VARCHAR(50) NOT NULL,
    config          JSONB DEFAULT '{}',
    enabled         BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_skills_agent_id ON agent_skills(agent_id);

-- 3. Agent schedules
CREATE TABLE IF NOT EXISTS agent_schedules (
    id              SERIAL PRIMARY KEY,
    agent_id        VARCHAR(16) NOT NULL REFERENCES super_agents(agent_id) ON DELETE CASCADE,
    name            VARCHAR(200) DEFAULT '',
    cron_expression VARCHAR(100) NOT NULL,
    task_prompt     TEXT NOT NULL,
    timezone        VARCHAR(50) DEFAULT 'UTC',
    enabled         BOOLEAN DEFAULT TRUE,
    last_run_at     TIMESTAMP,
    next_run_at     TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_schedules_agent_id ON agent_schedules(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_schedules_next_run ON agent_schedules(next_run_at) WHERE enabled = TRUE;

-- 4. Agent conversation threads
CREATE TABLE IF NOT EXISTS agent_threads (
    id              SERIAL PRIMARY KEY,
    thread_id       VARCHAR(16) NOT NULL UNIQUE,
    agent_id        VARCHAR(16) NOT NULL REFERENCES super_agents(agent_id) ON DELETE CASCADE,
    channel         VARCHAR(30) DEFAULT 'web',
    channel_id      VARCHAR(200) DEFAULT '',
    title           VARCHAR(500) DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_threads_agent_id ON agent_threads(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_threads_channel ON agent_threads(channel, channel_id);

-- 5. Agent messages
CREATE TABLE IF NOT EXISTS agent_messages (
    id              SERIAL PRIMARY KEY,
    thread_id       VARCHAR(16) NOT NULL REFERENCES agent_threads(thread_id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL,
    content         TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_thread_id ON agent_messages(thread_id);

-- 6. Agent persistent memory
CREATE TABLE IF NOT EXISTS agent_memory (
    id              SERIAL PRIMARY KEY,
    agent_id        VARCHAR(16) NOT NULL REFERENCES super_agents(agent_id) ON DELETE CASCADE,
    key             VARCHAR(300) NOT NULL,
    value           TEXT NOT NULL,
    category        VARCHAR(50) DEFAULT 'general',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(agent_id, key)
);
CREATE INDEX IF NOT EXISTS idx_agent_memory_agent_id ON agent_memory(agent_id);

-- 7. Agent execution logs
CREATE TABLE IF NOT EXISTS agent_logs (
    id              SERIAL PRIMARY KEY,
    agent_id        VARCHAR(16) NOT NULL REFERENCES super_agents(agent_id) ON DELETE CASCADE,
    trigger_type    VARCHAR(30) NOT NULL,
    trigger_source  VARCHAR(200) DEFAULT '',
    status          VARCHAR(20) DEFAULT 'running',
    input_summary   TEXT DEFAULT '',
    output_summary  TEXT DEFAULT '',
    tokens_used     INTEGER DEFAULT 0,
    credits_used    NUMERIC(10,2) DEFAULT 0,
    error           TEXT,
    started_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMP,
    duration_ms     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_id ON agent_logs(agent_id);

-- 8. Agent platform integrations
CREATE TABLE IF NOT EXISTS agent_integrations (
    id              SERIAL PRIMARY KEY,
    agent_id        VARCHAR(16) NOT NULL REFERENCES super_agents(agent_id) ON DELETE CASCADE,
    platform        VARCHAR(30) NOT NULL,
    config          JSONB DEFAULT '{}',
    webhook_secret  VARCHAR(200) DEFAULT '',
    status          VARCHAR(20) DEFAULT 'pending',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(agent_id, platform)
);
CREATE INDEX IF NOT EXISTS idx_agent_integrations_agent_id ON agent_integrations(agent_id);

-- 9. Agent credit tracking
CREATE TABLE IF NOT EXISTS agent_credits (
    id              SERIAL PRIMARY KEY,
    agent_id        VARCHAR(16) NOT NULL,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    log_id          INTEGER REFERENCES agent_logs(id),
    tokens_used     INTEGER DEFAULT 0,
    credits_used    NUMERIC(10,2) DEFAULT 0,
    model           VARCHAR(20) DEFAULT 'V6',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_credits_user_id ON agent_credits(user_id);
