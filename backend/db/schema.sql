CREATE TABLE IF NOT EXISTS sessions (
  id            TEXT PRIMARY KEY,
  name          TEXT,
  profile       TEXT NOT NULL,
  task          TEXT,
  status        TEXT NOT NULL CHECK (status IN ('active','locked','ended')),
  locked_reason TEXT,
  agent_pid     INTEGER,
  started_at    TEXT NOT NULL,
  ended_at      TEXT
);

-- Append-only, hash-chained. One row per completed action.
CREATE TABLE IF NOT EXISTS events (
  seq               INTEGER PRIMARY KEY AUTOINCREMENT,
  id                TEXT UNIQUE NOT NULL,
  session_id        TEXT NOT NULL REFERENCES sessions(id),
  ts                TEXT NOT NULL,
  connector         TEXT NOT NULL,
  action            TEXT NOT NULL,
  target            TEXT NOT NULL,
  task_context      TEXT,
  risk_tier         INTEGER NOT NULL CHECK (risk_tier BETWEEN 0 AND 5),
  scores_json       TEXT NOT NULL,
  decision          TEXT NOT NULL CHECK (decision IN ('ALLOW','MASK','HOLD','BLOCK')),
  final_outcome     TEXT NOT NULL CHECK (final_outcome IN
                      ('executed','executed_masked','blocked','denied','timeout','terminated','error')),
  approval          TEXT NOT NULL CHECK (approval IN
                      ('auto','approved','approved_masked','denied','timeout')),
  reason_codes_json TEXT NOT NULL,
  reason_text       TEXT NOT NULL,
  detections_json   TEXT NOT NULL,
  flags_json        TEXT NOT NULL DEFAULT '[]',
  content_sha256    TEXT,
  delta_sha256      TEXT,
  latency_ms        REAL,
  prev_hash         TEXT NOT NULL,
  hash              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);

CREATE TABLE IF NOT EXISTS approvals (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES sessions(id),
  connector     TEXT NOT NULL,
  action        TEXT NOT NULL,
  target        TEXT NOT NULL,
  summary       TEXT NOT NULL,
  tier          INTEGER NOT NULL,
  reason_codes_json TEXT NOT NULL,
  reason_text   TEXT NOT NULL,
  scores_json   TEXT NOT NULL,
  options_json  TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('pending','approved','approved_masked','denied','timeout','cancelled')),
  resolved_by   TEXT,
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  resolved_at   TEXT,
  event_id      TEXT REFERENCES events(id)
);

-- Raw vs sanitized content for the dashboard. NOT part of the hash chain. Purgeable.
CREATE TABLE IF NOT EXISTS deltas (
  event_id      TEXT PRIMARY KEY REFERENCES events(id),
  raw_text      TEXT NOT NULL,
  sanitized_text TEXT NOT NULL,
  changes_json  TEXT NOT NULL
);

-- Placeholder to original values, per session. Used for restore-on-write.
CREATE TABLE IF NOT EXISTS mask_map (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id    TEXT NOT NULL REFERENCES sessions(id),
  scope         TEXT NOT NULL,
  placeholder   TEXT NOT NULL,
  kind          TEXT NOT NULL,
  original      TEXT NOT NULL,
  original_indent TEXT,
  base_sha256   TEXT,
  created_at    TEXT NOT NULL,
  UNIQUE (session_id, scope, placeholder)
);

CREATE TABLE IF NOT EXISTS snapshots (
  id            TEXT PRIMARY KEY,
  event_id      TEXT REFERENCES events(id),
  session_id    TEXT NOT NULL REFERENCES sessions(id),
  kind          TEXT NOT NULL CHECK (kind IN ('file','database','workspace')),
  target        TEXT NOT NULL,
  snapshot_path TEXT NOT NULL,
  existed       INTEGER NOT NULL,
  created_at    TEXT NOT NULL,
  rolled_back_at TEXT
);

CREATE TABLE IF NOT EXISTS chain_head (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  seq INTEGER NOT NULL,
  hash TEXT NOT NULL
);
