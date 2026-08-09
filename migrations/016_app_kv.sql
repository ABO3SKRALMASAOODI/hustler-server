-- app_kv (round 102): one tiny operator-reachable key/value slab.
--
-- Born of a specific constraint: the worker runs on a box whose dashboard
-- (Render) is not scriptable from where operators actually work, so state
-- that must flow between an operator and the worker needs a door both can
-- reach. The database is that door.
--
-- First tenants:
--   ytdlp_cookies : a Netscape cookie jar delivered over psql — the fifth
--                   delivery door in worker/ytaccess.py. UPDATE the row and
--                   the worker picks it up within five minutes, no restart.
--   ytdlp_probe   : the worker's boot verdict on whether YouTube serves its
--                   IP (JSON: ok, why, cookie_source, pot, code). Written by
--                   worker/ytaccess.boot_probe on every boot.
--
-- Deliberately NOT a config system: rows are opaque text, readers own their
-- parsing, and anything with schema or relations deserves a real table.

BEGIN;

CREATE TABLE IF NOT EXISTS app_kv (
    key        varchar(64) PRIMARY KEY,
    value      text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT NOW()
);

COMMIT;
