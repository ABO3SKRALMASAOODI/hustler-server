-- 033 (optional, owner decision D6): label the 3–7 Oct 2026 signups (browser
-- journey linked, source capture not yet live) with an ESTIMATE taken from
-- the browser's first recorded visit referrer. Marked
-- tracking = 'estimated_from_referrer' and always shown "(estimated)".
-- Requires 031 (tracking column). Replay-safe: touches only rows that still
-- have no attribution and no tracking value. Expected: about 37 rows
-- (Google 17, no referrer 13, ChatGPT 2, Bing 2, Gemini 1, Yandex 1,
-- Instagram 1), computed read-only before release.
BEGIN;
SET LOCAL lock_timeout = '2s';
UPDATE website_signups ws
   SET tracking = 'estimated_from_referrer',
       attribution = jsonb_build_object('first', x.t, 'last', x.t)
  FROM (
    SELECT s.user_id, jsonb_build_object(
             'source',  COALESCE(NULLIF(fv.referrer, ''), 'direct'),
             'medium',  CASE WHEN COALESCE(fv.referrer, '') = '' THEN 'none' ELSE 'estimated' END,
             'campaign', '', 'content', '', 'code', '',
             'at', (extract(epoch FROM fv.visited_at) * 1000)::bigint) AS t
      FROM website_signups s JOIN users u ON u.id = s.user_id
      CROSS JOIN LATERAL (SELECT p.referrer, p.visited_at FROM page_visits p
                           WHERE p.device_id = s.device_id AND p.analytics_id IS NOT NULL
                             AND p.visited_at <= u.created_at + interval '5 minutes'
                           ORDER BY p.visited_at LIMIT 1) fv
     WHERE s.attribution IS NULL AND s.tracking IS NULL
       AND s.device_id IS NOT NULL
       AND fv.referrer IS DISTINCT FROM 'valmera.io'
       AND fv.referrer IS DISTINCT FROM 'www.valmera.io'
  ) x
 WHERE ws.user_id = x.user_id AND ws.attribution IS NULL AND ws.tracking IS NULL;
COMMIT;
