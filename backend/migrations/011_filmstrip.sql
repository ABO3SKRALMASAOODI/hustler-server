-- Round 51 — the timeline filmstrip.
--
-- One new job type. The strip itself lives in object storage under a key
-- derived from the source sha (worker/filmstrip.py), NOT in `assets`, for the
-- same reason the round-39 cleaned sources very nearly did not: assets.kind
-- has its own CHECK constraint, and coupling a cosmetic artifact to a second
-- schema change is how a feature ends up half-deployed. Nothing reads a row
-- for it, so there is nothing to add.
--
-- Everything degrades if this is NOT applied: the backend's enqueue fails, the
-- endpoint reports the strip unavailable, and the studio timeline draws the
-- blocks it has always drawn. No user-visible error, no broken page.
--
-- Apply on Render:  psql $DATABASE_URL -f backend/migrations/011_filmstrip.sql

ALTER TABLE video_jobs DROP CONSTRAINT IF EXISTS video_jobs_type_check;
ALTER TABLE video_jobs ADD CONSTRAINT video_jobs_type_check
  CHECK (type IN ('index', 'preview', 'final', 'agent_turn', 'mcp_tool',
                  'filmstrip'));
