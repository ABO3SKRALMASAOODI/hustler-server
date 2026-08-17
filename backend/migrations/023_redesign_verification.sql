-- Durable quality evidence for every immutable EDL version.
CREATE TABLE IF NOT EXISTS change_manifests (
  project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  edl_version INTEGER NOT NULL,
  manifest    JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (project_id, edl_version),
  FOREIGN KEY (project_id, edl_version)
    REFERENCES edls(project_id, version) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS verification_records (
  project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  edl_version INTEGER NOT NULL,
  status      TEXT NOT NULL CHECK (status IN
                  ('pending','repair_required','passed','justified')),
  record      JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (project_id, edl_version),
  FOREIGN KEY (project_id, edl_version)
    REFERENCES edls(project_id, version) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_verification_records_status
  ON verification_records(status, updated_at DESC);
