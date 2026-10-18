# 05. Data and Audit

Everything is stored in one SQLite database at `${RS_DATA_DIR}/audit.db`, opened in WAL mode with `PRAGMA foreign_keys=ON`. The `data/` directory is sensitive: gitignored, mode `700`, never mounted to the agent.

## 1. Schema (`backend/db/schema.sql`)

```sql
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
  id                TEXT UNIQUE NOT NULL,          -- evt_<12 hex>
  session_id        TEXT NOT NULL REFERENCES sessions(id),
  ts                TEXT NOT NULL,                 -- ISO-8601 UTC
  connector         TEXT NOT NULL,                 -- filesystem | command | database | network | system
  action            TEXT NOT NULL,                 -- tool name or system action
  target            TEXT NOT NULL,                 -- relative path, command, table, host
  task_context      TEXT,
  risk_tier         INTEGER NOT NULL CHECK (risk_tier BETWEEN 0 AND 5),
  scores_json       TEXT NOT NULL,                 -- {pattern, location, content, behaviour, combined}
  decision          TEXT NOT NULL CHECK (decision IN ('ALLOW','MASK','HOLD','BLOCK')),
  final_outcome     TEXT NOT NULL CHECK (final_outcome IN
                      ('executed','executed_masked','blocked','denied','timeout','terminated','error')),
  approval          TEXT NOT NULL CHECK (approval IN
                      ('auto','approved','approved_masked','denied','timeout')),
  reason_codes_json TEXT NOT NULL,                 -- ["PROTECTED_PATH", ...]
  reason_text       TEXT NOT NULL,                 -- plain-language sentence
  detections_json   TEXT NOT NULL,                 -- [{kind, category, confidence, preview}] masked previews only
  flags_json        TEXT NOT NULL DEFAULT '[]',    -- e.g. ["score_elevated","unknown_placeholder"]
  content_sha256    TEXT,                          -- hash of raw content
  delta_sha256      TEXT,                          -- hash of sanitized content
  latency_ms        REAL,                          -- pipeline time, excludes human wait and execution
  prev_hash         TEXT NOT NULL,
  hash              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);

CREATE TABLE IF NOT EXISTS approvals (
  id            TEXT PRIMARY KEY,                  -- apr_<12 hex>
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
  resolved_by   TEXT,                              -- console | web | timeout
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
  scope         TEXT NOT NULL,                     -- relative path, or "db:<table>", or "global"
  placeholder   TEXT NOT NULL,                     -- [MASKED_SECRET_01], [EMAIL_1], [MASKED_LOGIC_BLOCK_01]
  kind          TEXT NOT NULL,
  original      TEXT NOT NULL,
  original_indent TEXT,                            -- for code blocks
  base_sha256   TEXT,                              -- file hash when masked
  created_at    TEXT NOT NULL,
  UNIQUE (session_id, scope, placeholder)
);

CREATE TABLE IF NOT EXISTS snapshots (
  id            TEXT PRIMARY KEY,                  -- snp_<12 hex>
  event_id      TEXT REFERENCES events(id),
  session_id    TEXT NOT NULL REFERENCES sessions(id),
  kind          TEXT NOT NULL CHECK (kind IN ('file','database','workspace')),
  target        TEXT NOT NULL,
  snapshot_path TEXT NOT NULL,                     -- under data/snapshots/
  existed       INTEGER NOT NULL,                  -- 0 if the file did not exist (rollback deletes it)
  created_at    TEXT NOT NULL,
  rolled_back_at TEXT
);
```

Notes:

- `events` is written **once**, after the action completes. A pending hold exists only in `approvals` and as a WebSocket message. This keeps the chain append-only with no updates.
- `event_id` on `approvals` and `snapshots` is set after the event row exists. Create the snapshot before the action, then link it in the same transaction as the event insert.
- No raw secret or PII appears in `events`. Detections carry masked previews only.

## 2. Hash chain (`backend/core/audit.py`)

```python
GENESIS = "0" * 64

def canonical(row: dict) -> bytes:
    body = {k: v for k, v in row.items() if k not in ("seq", "hash")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

def compute_hash(prev_hash: str, row: dict) -> str:
    return hashlib.sha256(prev_hash.encode("ascii") + canonical(row)).hexdigest()
```

**Append** under one `asyncio.Lock` and one SQLite transaction:

1. Read the last row's `hash` (or `GENESIS` if the table is empty).
2. Set `row["prev_hash"]` to it.
3. Set `row["hash"] = compute_hash(prev_hash, row)`.
4. Insert. Commit.

**Verify** (`verify_chain(db_path) -> {ok, checked, first_bad_seq, reason}`):

1. Iterate rows by `seq` ascending.
2. For each row: `prev_hash` must equal the previous row's `hash` (or `GENESIS`), and `compute_hash(prev_hash, row)` must equal the stored `hash`.
3. Stop at the first failure and report its `seq` and the reason (`prev_hash_mismatch` or `hash_mismatch`).
4. Detect truncation of the tail by also storing the latest `(seq, hash)` in a small `chain_head` table updated in the same transaction, and comparing it at the end. Report `head_mismatch` if it differs.

```sql
CREATE TABLE IF NOT EXISTS chain_head (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  seq INTEGER NOT NULL,
  hash TEXT NOT NULL
);
```

Limit to state honestly: a hash chain in a local file detects accidental or casual tampering. An attacker with full write access can rewrite everything consistently. Optional hardening (stretch): periodically print or export the head hash to a place the agent and gateway cannot write.

`scripts/tamper_demo.py` copies `audit.db` to a temp file, changes one field in a middle row, runs `verify_chain` on the copy, and prints the failure. It never touches the real ledger.

## 3. Snapshots and rollback (`backend/core/snapshots.py`)

| Kind | When | Snapshot | Rollback |
|---|---|---|---|
| `file` | Before `write_file` (existing or new) and before `delete_file` | Copy of the file to `data/snapshots/<event_id>/<relpath>`. `existed=0` if the file did not exist. | Restore the copy, or delete the file if `existed=0`. |
| `database` | Before any approved or allowed SQL write | SQLite backup API copy of the database | Replace the database with the snapshot. This also discards later changes. Say so in the UI. |
| `workspace` | Before an approved tier 3 command | Full directory copy (max 50 MB) | Replace the workspace contents with the copy. |

Rules:

- Create the snapshot **before** execution and **after** approval. If the snapshot fails, do not execute: BLOCK `INTERNAL_ERROR`.
- `POST /api/events/{id}/rollback` finds snapshots for the event, restores them, sets `rolled_back_at`, writes a new event `system / rollback` (so the ledger records the undo), and publishes `snapshot.rolled_back`.
- Rollback is a human action. It is allowed even when the session is locked.
- A snapshot can be rolled back once.
- Retention: keep snapshots for the life of the demo. Add a `make clean-data` target that purges `data/`.

## 4. Deltas and mask map

- `deltas.changes_json` is a list of `{raw_start, raw_end, san_start, san_end, kind, reason_code, placeholder}`. Offsets are Python string indices.
- Invariant for tests: applying all changes to `raw_text` in order reproduces `sanitized_text`.
- `deltas` is excluded from the chain. Its integrity is covered indirectly by `events.delta_sha256`, which the Verify Log button can also check.
- `mask_map` rows are scoped per session. A new session starts with an empty map.

## 5. Event construction rules

- `id`: `evt_` plus 12 hex characters from `uuid4`.
- `ts`: UTC, ISO-8601 with `Z`.
- `target`: workspace-relative path, normalised command string, table name or host. For commands, truncate to 300 characters and run detectors to mask secrets in the string.
- `latency_ms`: time from step 1 to step 7 of the lifecycle plus masking, excluding human wait and connector execution.
- `reason_text`: from `explain.py`.
- `detections_json`: `kind`, `category`, `confidence`, `preview` only.
