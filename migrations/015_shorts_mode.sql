-- Shorts mode (round 99). A generated short is a CHILD PROJECT of the long
-- video it was cut from: it shares the parent's original + proxy by storage
-- key (prefix-based deletion keeps that safe — deleting a child only wipes
-- objects under the CHILD's prefix), reuses the sha-keyed index row outright,
-- and gets its own EDL lineage so the full editor works on it unchanged.
--
--   projects.kind             'edit' (default) | 'shorts' (a parent that runs
--                             the clip pipeline) | 'short' (a generated clip)
--   projects.parent_project_id children die with their parent (CASCADE)
--   projects.meta             parent: {"shorts": {status, clips, style_profile,
--                             ...}}; child: {"clip": {hook, score, start, end}}
--
-- video_jobs gains type 'shorts_plan' — the orchestrator job that analyzes the
-- optional reference clip, picks the moments, creates the children, seeds
-- their EDLs and fans out their final renders.

BEGIN;

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS parent_project_id integer
        REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS kind varchar(12) NOT NULL DEFAULT 'edit';
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS meta jsonb NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_projects_parent
    ON projects (parent_project_id) WHERE parent_project_id IS NOT NULL;

ALTER TABLE video_jobs DROP CONSTRAINT video_jobs_type_check;
ALTER TABLE video_jobs ADD CONSTRAINT video_jobs_type_check
    CHECK (type::text = ANY (ARRAY[
        'index'::character varying, 'preview'::character varying,
        'final'::character varying, 'agent_turn'::character varying,
        'mcp_tool'::character varying, 'filmstrip'::character varying,
        'shorts_plan'::character varying]::text[]));

COMMIT;
