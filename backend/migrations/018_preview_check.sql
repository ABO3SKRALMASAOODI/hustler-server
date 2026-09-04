-- Short changed-section proof reels are queue jobs, but deliberately not
-- ordinary previews: their short asset must never replace the Studio player.
BEGIN;

ALTER TABLE video_jobs DROP CONSTRAINT IF EXISTS video_jobs_type_check;
ALTER TABLE video_jobs ADD CONSTRAINT video_jobs_type_check
    CHECK (type::text = ANY (ARRAY[
        'index'::character varying, 'preview'::character varying,
        'preview_check'::character varying, 'final'::character varying,
        'agent_turn'::character varying, 'mcp_tool'::character varying,
        'filmstrip'::character varying, 'shorts_plan'::character varying
    ]::text[]));

COMMIT;
