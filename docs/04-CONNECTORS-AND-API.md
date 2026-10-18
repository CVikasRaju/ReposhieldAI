# 04. Connectors and API

Two surfaces: the **agent-facing MCP tools** (port 8001) and the **human-facing dashboard API** (port 8000).

---

## Part A. MCP tools (agent-facing)

### Common result shape

Every tool returns:

```json
{
  "status": "ok | masked | blocked | denied | terminated | error",
  "event_id": "evt_3f9a1c2b7d4e",
  "content": "...",
  "notice": "..."
}
```

| Status | Meaning |
|---|---|
| `ok` | Executed as requested. |
| `masked` | Executed, but sensitive parts were replaced with placeholders. `notice` explains. |
| `blocked` | Refused by policy. `content` is absent. |
| `denied` | A human denied it, or the approval timed out. |
| `terminated` | The session is locked. |
| `error` | The action failed for a non-security reason (file not found, syntax error). |

Blocked and denied messages are generic: `"This action is not permitted by the workspace security policy."` Never name a rule, path pattern or threshold.

Masked reads include this notice: `"Some content was replaced with placeholders such as [MASKED_SECRET_01]. If you write this file back, the original values are restored automatically."`

### Tools

| Tool | Parameters | Connector | Notes |
|---|---|---|---|
| `list_directory` | `path` | filesystem | Hides entries whose read rule is `block` when `limits.hide_protected_in_listing` is true. |
| `read_file` | `path` | filesystem | Text only. Binary returns `UNSUPPORTED_BINARY`. Over `max_read_bytes` returns `TOO_LARGE`. |
| `write_file` | `path`, `content` | filesystem | Restore-on-write applies. Snapshot first. |
| `delete_file` | `path` | filesystem | Tier 3. |
| `run_command` | `command` (string) | command | No shell. See below. |
| `db_schema` | none | database | Tables and columns. Protected columns are listed but flagged `protected`. |
| `db_query` | `sql` | database | One statement only. |
| `http_request` | `method`, `url`, `headers?`, `body?` | network | See below. |

Out of scope for the MVP: `search_files`, directory move or copy tools.

---

### Filesystem connector

**Path jail.**

```python
root = workspace.resolve()
p = (root / user_path).resolve(strict=False)
if not p.is_relative_to(root): block PATH_ESCAPE
```

- Reject absolute paths that resolve outside the root and any path through a symlink that leaves it.
- Names `.aishield` and `data` get `POLICY_TAMPER` at any depth, even if they somehow appear in the workspace.
- Policy match uses the workspace-relative path.

**Read flow.** Read text (UTF-8, fall back to `latin-1` with a flag), run detectors, score, decide. If MASK, pass through `privacy_masker`. Store the placeholder map and `base_sha256` in `mask_map`.

**Write flow.** Read the current file (if any) for `base_sha256`. Run restore-on-write (see `06-MASKING.md`). Scan the proposed content with detectors for the audit record. Snapshot, then write atomically (temp file plus `os.replace`).

**Delete flow.** Tier 3. Snapshot, then delete. Directories are not deletable through `delete_file`.

---

### Command connector

Input is one string. Steps:

1. **Tokenise** with `shlex.split`. Failure returns BLOCK `SHELL_OPERATORS`.
2. **Reject operators.** If any token (or the raw string) contains `;`, `&&`, `||`, `|`, `` ` ``, `$(`, `>`, `<` or a newline, BLOCK `SHELL_OPERATORS` (tier 5).
3. **Match lists on the normalised command** (`" ".join(tokens)`), in this order:
   1. `deny` matches: BLOCK `DENIED_COMMAND` (tier 5).
   2. `destructive` matches: HOLD `DESTRUCTIVE_COMMAND` (tier 3).
   3. `allow` matches: tier 0, continue to path analysis.
   4. No match: apply `commands.default` (HOLD `UNLISTED_COMMAND`, tier 3).
4. **Path-argument analysis.** For every token that does not start with `-`, resolve it against the workspace. If it exists, or it matches any protect glob, evaluate the filesystem **read** rule for it. Any `block` makes the whole command BLOCK `PROTECTED_PATH`. Any `hold` makes it HOLD. This analysis is best effort. It cannot see paths built by an interpreter, which is why interpreters (`python`, `node`, `bash`, `sh`) are not on the default allowlist.
5. **Execute** with `subprocess.run(tokens, cwd=workspace, env=minimal_env, timeout=..., capture_output=True)`. `minimal_env` has only `PATH` and `HOME=/tmp`. Never `shell=True`.
6. **Scan output.** Run detectors on stdout and stderr and auto-mask high-confidence spans. Truncate to `max_output_bytes`.
7. **Snapshot.** For an approved tier 3 command, snapshot the workspace (full copy, refuse if over 50 MB and block with `TOO_LARGE`).

---

### Database connector (SQLite)

Uses `sqlglot` (SQLite dialect) to parse. Reads run on a read-only connection (`file:...?mode=ro`, `uri=True`). Writes run on a read-write connection after a snapshot.

**Classification**

| Statement | Tier | Decision |
|---|---|---|
| `SELECT` on unprotected tables | 0 | ALLOW |
| `SELECT` touching a protected table | rule (`read`) | HOLD in the demo policy |
| `INSERT` | 2 | ALLOW with snapshot (protected table: table `write` rule) |
| `UPDATE` with `WHERE` | 2 | ALLOW with snapshot (protected table: `write` rule) |
| `UPDATE` without `WHERE` | 3 | HOLD `DESTRUCTIVE_SQL` |
| `DELETE` | 3 | HOLD `DESTRUCTIVE_SQL` (protected table: `delete` rule, BLOCK in the demo) |
| `DROP`, `TRUNCATE`, `ALTER`, `CREATE` | 3 | HOLD `DESTRUCTIVE_SQL` |
| `ATTACH`, `PRAGMA`, `load_extension`, multiple statements, unparseable SQL | 5 | BLOCK `UNSAFE_SQL` |

**Result handling.** Cap rows at `database.max_rows`. Return `{"columns": [...], "rows": [[...]], "truncated": bool}`. Masking by `table_masker` (see `06-MASKING.md`): protected columns by name (including aliased columns resolved through the parsed select list, best effort), then detector scan of every string cell.

**Literal restore.** SQL string literals equal to a known placeholder (for example `'[EMAIL_1]'`) are replaced with the original from `mask_map` before execution, so `WHERE email = '[EMAIL_1]'` works.

**Snapshot.** Use the SQLite backup API to copy the database file to `data/snapshots/<event_id>/business.db`.

---

### Network connector

`http_request(method, url, headers?, body?)`:

1. Parse the URL. Scheme must be `http` or `https`.
2. **Host check.** Exact match or `*.suffix` against `egress.allow_hosts`. Not listed: BLOCK `EGRESS_DENIED` (tier 5).
3. **SSRF guard.** Resolve the host. Reject loopback, link-local (including `169.254.0.0/16`), private ranges and metadata addresses unless `allow_private_ranges` is true. Do this even for allowlisted names.
4. **Payload scan.** Run detectors on URL, headers and body. Any auto-mask span: BLOCK `EXFIL_DETECTED` (tier 5), even to an allowlisted host.
5. **Method rule.** Methods in `hold_methods`: HOLD `OUTBOUND_WRITE` (tier 4). `GET` and `HEAD` to an allowlisted host with a clean payload: ALLOW.
6. Execute with `httpx` (timeout, no redirects to non-allowlisted hosts, response capped at 1 MB). Return status, headers subset and truncated body.

---

## Part B. Dashboard API (human-facing, port 8000)

### Authentication

All routes and the WebSocket need the approver token:

- HTTP: header `X-Approver-Token: <token>`.
- WebSocket: first message `{"type":"auth","token":"<token>"}`; close with code 4401 on failure.
- Constant-time comparison. Return `401` with no detail on failure.
- CORS allows only the dashboard origin (`http://localhost:5173` in development).

The token comes from `RS_APPROVER_TOKEN` or is generated at startup and printed with a ready-to-open URL: `http://localhost:5173/#token=<token>`.

### Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness and version |
| GET | `/api/sessions` | List sessions |
| GET | `/api/sessions/{id}` | One session with counters |
| POST | `/api/sessions/{id}/lock` | Manual lock |
| POST | `/api/sessions/{id}/unlock` | Unlock |
| GET | `/api/events?session_id=&limit=&after_seq=&decision=` | Event rows, newest last |
| GET | `/api/events/{id}` | One event |
| GET | `/api/events/{id}/delta` | Raw vs sanitized content and change spans |
| GET | `/api/graph?session_id=` | Nodes, edges, per-node state |
| GET | `/api/approvals?status=pending` | Approvals |
| POST | `/api/approvals/{id}/resolve` | Body `{"resolution": "approve" \| "approve_masked" \| "deny" \| "deny_and_lock"}` |
| POST | `/api/events/{id}/rollback` | Undo using the snapshot |
| GET | `/api/log/verify` | Recompute the hash chain |
| GET | `/api/policy` | Sanitised, read-only view of the active policy (profile, mode, thresholds, protect rules) |
| GET | `/api/metrics/summary` | Counts per decision, mean and p95 pipeline latency |

Error body: `{"error": "<code>", "message": "<text>"}`. Resolving an approval that is not pending returns `409`. Approving with masking on a non-read action returns `400`.

### Payload examples

**Approval (pending)**

```json
{
  "id": "apr_81c0e2a49f3b",
  "session_id": "default",
  "event_hint": "db_query",
  "connector": "database",
  "target": "customers",
  "tier": 1,
  "reason_codes": ["PROTECTED_TABLE", "TASK_MISMATCH"],
  "reason_text": "The agent asked for rows from the customers table, which holds private data. It was paused for your approval.",
  "summary": "SELECT * FROM customers LIMIT 5",
  "scores": { "pattern": 0.0, "location": 0.9, "content": 0.62, "behaviour": 0.2, "combined": 0.64 },
  "options": ["approve", "approve_masked", "deny"],
  "created_at": "2026-10-18T10:42:11Z",
  "expires_at": "2026-10-18T10:43:11Z",
  "status": "pending"
}
```

`options` omits `approve_masked` for non-read actions.

**Delta**

```json
{
  "event_id": "evt_3f9a1c2b7d4e",
  "raw": "id,full_name,email\n1,Asha Rao,asha@corp.test\n",
  "sanitized": "id,full_name,email\n1,[NAME_1],[EMAIL_1]\n",
  "changes": [
    { "raw_start": 21, "raw_end": 29, "san_start": 21, "san_end": 29,
      "kind": "NAME", "reason_code": "PROTECTED_TABLE", "placeholder": "[NAME_1]" }
  ]
}
```

**Graph**

```json
{
  "nodes": [
    { "id": "src", "label": "src", "type": "dir", "parent": null, "state": "accessed", "last_ts": "..." },
    { "id": "src/auth", "type": "dir", "parent": "src", "state": "masked" },
    { "id": ".env", "type": "file", "parent": null, "state": "blocked" },
    { "id": "db:customers", "type": "table", "parent": "db:business.db", "state": "held" },
    { "id": "host:collector.local", "type": "host", "parent": null, "state": "blocked" }
  ],
  "edges": [ { "source": "src", "target": "src/auth" } ]
}
```

Node `state` values: `unvisited`, `accessed`, `masked`, `held`, `blocked`. Precedence when several apply: `blocked` > `held` > `masked` > `accessed` > `unvisited`. A node's state reflects its **most severe** event in the session.

### WebSocket events (server to dashboard)

| `type` | Payload |
|---|---|
| `event.created` | `{ event }` full event row |
| `approval.requested` | `{ approval }` |
| `approval.resolved` | `{ approval_id, resolution }` |
| `session.updated` | `{ session }` |
| `graph.node` | `{ node_id, state, last_ts }` |
| `snapshot.rolled_back` | `{ event_id }` |

On connect, after auth, the server sends `{ "type": "hello", "pending_approvals": [...] }`. The dashboard then fetches events and graph through REST to fill history.

### Console approval channel

When `console` is in `approval.channels`, the gateway process prints a banner and reads one key without blocking the event loop (`asyncio.to_thread(input)`):

```
[RepoShield] HOLD  database  SELECT * FROM customers LIMIT 5
  The agent asked for rows from the customers table, which holds private data.
  [y] approve   [m] approve with masking   [n] deny   > 
```

The first answer from console or web wins. The other channel is cancelled and shown as resolved. For non-read actions, `m` is not offered.

### Keep-alive during holds

MCP clients often time out requests. While a call is held, send progress notifications every 10 seconds if the SDK supports it (`ctx.report_progress`). Default hold timeout is 60 seconds.
