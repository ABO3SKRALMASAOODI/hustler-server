-- The owner's render box is an execution provider (worker/box_routing.py,
-- box/admission.py). Its calls use the same durable ownership ledger as
-- Cloudflare and Modal, under provider 'box'.
--
-- NOT VALID + VALIDATE: the brief ACCESS EXCLUSIVE lock covers only the
-- catalog change; the row scan runs under SHARE UPDATE EXCLUSIVE and blocks
-- no queue write. Safe to re-run.
SET lock_timeout = '5s';

ALTER TABLE remote_executions
  DROP CONSTRAINT IF EXISTS remote_executions_provider_check;
ALTER TABLE remote_executions
  ADD CONSTRAINT remote_executions_provider_check
  CHECK (provider IN ('modal', 'cloudflare', 'cloud_run', 'box')) NOT VALID;
ALTER TABLE remote_executions
  VALIDATE CONSTRAINT remote_executions_provider_check;
