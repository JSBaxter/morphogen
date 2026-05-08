-- Morphogen broadcast field — SQLite schema.
--
-- ISO-8601 timestamps live in TEXT columns; SPEC's TIMESTAMP type
-- maps to NUMERIC affinity in SQLite which would coerce strings,
-- so we use TEXT explicitly the way the bundled queue does.
--
-- The partial unique index `idx_morphogens_dedup` enforces
-- concentration: at most one row per (payload_hash, tags_hash, kind)
-- can be in `emitted` status with no nonce. Re-emits hit this index
-- and the application bumps `concentration` instead of inserting.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS morphogens (
  id                 TEXT PRIMARY KEY,
  source_cell        TEXT NOT NULL,
  kind               TEXT NOT NULL,
  payload            TEXT NOT NULL,        -- JSON-encoded object
  payload_hash       TEXT NOT NULL,
  tags_hash          TEXT NOT NULL,
  nonce              TEXT,
  emitted_at         TEXT NOT NULL,
  ttl_seconds        INTEGER NOT NULL,
  concentration      INTEGER NOT NULL DEFAULT 1,
  status             TEXT NOT NULL DEFAULT 'emitted',
  claimed_by         TEXT,
  claimed_at         TEXT,
  lease_expires_at   TEXT,
  fulfilled_at       TEXT,
  fulfilled_outcome  TEXT,
  fulfilled_link     TEXT
);

CREATE TABLE IF NOT EXISTS morphogen_tags (
  morphogen_id  TEXT NOT NULL REFERENCES morphogens(id),
  tag           TEXT NOT NULL,
  PRIMARY KEY (morphogen_id, tag)
);

CREATE TABLE IF NOT EXISTS morphogen_responses (
  id                TEXT PRIMARY KEY,
  morphogen_id      TEXT NOT NULL REFERENCES morphogens(id),
  responder_cell    TEXT NOT NULL,
  response_payload  TEXT NOT NULL,         -- JSON-encoded object
  link              TEXT,
  responded_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_morphogen_tags_tag
  ON morphogen_tags(tag);

CREATE INDEX IF NOT EXISTS idx_morphogens_status
  ON morphogens(status);

CREATE INDEX IF NOT EXISTS idx_morphogens_emitted
  ON morphogens(emitted_at);

CREATE INDEX IF NOT EXISTS idx_morphogens_lease
  ON morphogens(lease_expires_at)
  WHERE status = 'claimed';

CREATE UNIQUE INDEX IF NOT EXISTS idx_morphogens_dedup
  ON morphogens(payload_hash, tags_hash, kind)
  WHERE status = 'emitted' AND nonce IS NULL;
