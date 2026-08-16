-- 020: make per-turn credit charging a durable, transactional claim.
--
-- A job can finish twice across an executor disconnect/reaper race.  The old
-- worker debited users first and wrote job_credits afterwards, so retrying the
-- completion could debit the same model turn twice.  The worker now inserts
-- this ledger row before the balance UPDATE in the same transaction; this
-- unique index is the database fence that lets exactly one execution own it.
--
-- Existing production data currently has no duplicate video job claims.  The
-- roll-up below still makes this migration safe on a drifted environment: it
-- preserves the earliest audit timestamp and SUMs tokens/credits for VIDEO
-- claims before enforcing uniqueness. Legacy app-builder/chat job ids were
-- only eight characters and legitimately/collision-wise repeat; that retired
-- namespace is intentionally left untouched. The table lock closes the insert
-- window during the one-time repair. Re-running the file is a no-op apart from
-- the empty duplicate scan.

BEGIN;

LOCK TABLE job_credits IN SHARE ROW EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_credits
         WHERE job_id LIKE 'video:%'
         GROUP BY job_id, turn
        HAVING COUNT(DISTINCT user_id) > 1
    ) THEN
        RAISE EXCEPTION
            'job_credits contains one job/turn attributed to multiple users';
    END IF;
END
$$;

CREATE TEMP TABLE job_credit_duplicate_rollup ON COMMIT DROP AS
SELECT job_id,
       turn,
       MIN(user_id) AS user_id,
       COALESCE(SUM(tokens_used), 0) AS tokens_used,
       COALESCE(SUM(credits_used), 0) AS credits_used,
       MIN(created_at) AS created_at
  FROM job_credits
 WHERE job_id LIKE 'video:%'
 GROUP BY job_id, turn
HAVING COUNT(*) > 1;

DELETE FROM job_credits AS ledger
 USING job_credit_duplicate_rollup AS duplicate
 WHERE ledger.job_id IS NOT DISTINCT FROM duplicate.job_id
   AND ledger.turn IS NOT DISTINCT FROM duplicate.turn;

INSERT INTO job_credits
            (job_id, user_id, turn, tokens_used, credits_used, created_at)
SELECT job_id, user_id, turn, tokens_used, credits_used, created_at
  FROM job_credit_duplicate_rollup;

CREATE UNIQUE INDEX IF NOT EXISTS idx_job_credits_job_turn_unique
    ON job_credits (job_id, turn)
    WHERE job_id LIKE 'video:%';

COMMENT ON INDEX idx_job_credits_job_turn_unique IS
    'Exactly one transactional video credit-debit claim per job turn';

COMMIT;
