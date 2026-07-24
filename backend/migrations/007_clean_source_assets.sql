-- ============================================================================
-- Round-39: repainted ("cleaned") sources — additive, safe to re-run.
--   psql "$DATABASE_URL" -f backend/migrations/007_clean_source_assets.sql
--
-- erase_burned_text / erase_region repaint burned-in captions, watermarks or
-- an object OUT of the pixels and write a cleaned copy of the source, plus a
-- cleaned proxy for previews. The EDL's `source_clean` points at those objects
-- and the renderer reads them; these two asset kinds exist so the copies are
-- visible and auditable in the admin (they are full-size video objects, and
-- their storage should never be invisible).
--
-- Not applying this does NOT break the feature: the worker records these rows
-- best-effort and logs when it cannot, because the repaint is already in
-- storage and the EDL — not the asset row — is what the render follows. What
-- you lose until it runs is the admin listing for them.
-- ============================================================================

ALTER TABLE assets DROP CONSTRAINT IF EXISTS assets_kind_check;
ALTER TABLE assets ADD CONSTRAINT assets_kind_check
  CHECK (kind IN ('original','proxy','audio','thumb','sheet','render',
                  'music','image_ref','video_clip',
                  'clean_source','clean_proxy'));
