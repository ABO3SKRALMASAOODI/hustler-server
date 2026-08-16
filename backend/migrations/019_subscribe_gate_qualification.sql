-- 019: keep the one-free-edit qualification after project deletion.
--
-- video_jobs belongs to a project and is cascade-deleted with it. Using only
-- surviving jobs for the account-wide subscribe wall therefore let someone
-- delete the edited project and receive another free edit. This durable,
-- project-less fact is written once per account after a qualifying job
-- commits. Backfill the same predicate used by routes/video.py so existing
-- accounts do not change eligibility during rollout.

BEGIN;

-- Serialize the one-time backfill/index creation with event inserts. Without
-- this, two workers could both insert the marker in the small gap before the
-- unique index exists and make index creation fail.
LOCK TABLE client_events IN SHARE ROW EXCLUSIVE MODE;

-- The first draft of this migration used the job's absolute edl_version for
-- legacy rows.  That is not current-turn evidence: a read-only answer on a
-- project a user/earlier turn had already moved to v2 looked like a new edit.
-- Correct only migration-authored markers whose source job still exists and
-- has no positive write in its own execution window.  Missing source jobs are
-- intentionally preserved; deleting a project must not refund a known edit.
DELETE FROM client_events ce
 USING video_jobs j
 WHERE ce.kind = 'subscribe_gate_qualified'
   AND ce.detail->>'origin' = 'migration_019'
   AND ce.detail->>'source_job_id' ~ '^[0-9]+$'
   AND j.id = (ce.detail->>'source_job_id')::bigint
   AND j.type = 'agent_turn'
   AND NOT (j.result ? 'edl_changed')
   AND NOT EXISTS (
        SELECT 1 FROM edls e
         WHERE e.project_id = j.project_id
           AND e.created_by = 'agent'
           AND e.version > 1
           AND e.created_at >= j.created_at
           AND e.created_at <= j.updated_at + INTERVAL '5 seconds');

INSERT INTO client_events (user_id, project_id, kind, detail)
SELECT DISTINCT ON (j.user_id)
       j.user_id, NULL, 'subscribe_gate_qualified',
       jsonb_build_object('origin', 'migration_019',
                          'source_job_id', j.id)
FROM video_jobs j
WHERE j.state = 'done'
  AND ((j.type = 'agent_turn'
        AND j.result->>'status' = 'replied'
        AND j.result->>'outcome' IN ('fulfilled', 'partial')
        AND ((j.result ? 'edl_changed'
              AND j.result->>'edl_changed' = 'true')
             OR (NOT (j.result ? 'edl_changed')
                 AND EXISTS (
                     SELECT 1 FROM edls e
                      WHERE e.project_id = j.project_id
                        AND e.created_by = 'agent'
                        AND e.version > 1
                        AND e.created_at >= j.created_at
                        AND e.created_at <=
                            j.updated_at + INTERVAL '5 seconds'))))
       OR (j.type = 'shorts_plan'
           AND CASE
                 WHEN j.result ? 'rendered_clips'
                      AND j.result->>'rendered_clips' ~ '^[0-9]+$'
                   THEN (j.result->>'rendered_clips')::int
                 WHEN NOT (j.result ? 'rendered_clips')
                      AND j.result->>'clips' ~ '^[0-9]+$'
                   THEN (j.result->>'clips')::int
                 ELSE 0
               END > 0))
  AND NOT EXISTS (
        SELECT 1 FROM client_events ce
        WHERE ce.user_id = j.user_id
          AND ce.kind = 'subscribe_gate_qualified')
ORDER BY j.user_id, j.id;

-- A drifted/pre-index environment may already contain duplicate markers.
-- Keep the first durable fact before installing the account-level fence.
DELETE FROM client_events newer
 USING client_events older
 WHERE newer.kind = 'subscribe_gate_qualified'
   AND older.kind = 'subscribe_gate_qualified'
   AND newer.user_id = older.user_id
   AND newer.id > older.id;

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscribe_gate_qualified_user
    ON client_events (user_id)
    WHERE kind = 'subscribe_gate_qualified';

COMMIT;
