# 09. Build Plan

Ten phases, built in order. Each phase has acceptance criteria and a prompt you paste to the building agent. Do not start a phase until the previous one passes.

Every prompt assumes the agent has read `AGENTS.md`. Each one repeats that instruction so it still works in a fresh session.

| Phase | Builds | Priority |
|---|---|---|
| 0 | Scaffold, config, schema, seed scripts | Must |
| 1 | Policy engine | Must |
| 2 | Detectors, maskers, restore-on-write | Must |
| 3 | Scoring, behaviour, content model, decisions | Must |
| 4 | Core: audit, sessions, approvals, snapshots, pipeline | Must |
| 5 | Connectors and the MCP server | Must |
| 6 | Dashboard API and WebSocket | Must |
| 7 | Frontend | Must |
| 8 | Demo and evaluation | Must |
| 9 | Docker sandbox, watchdog, polish | Must (sandbox), Should (watchdog) |

If time runs short, cut in this order: watchdog, content-model polish, SQL literal restore, Simple/Detail toggle. Never cut the pipeline, approvals, audit chain, masking, the demo or the evaluation.

---

## Phase 0: Scaffold

**Builds:** repository skeleton, tooling, config, DB schema, seed scripts.

**Acceptance criteria**

- Directory layout matches `docs/02-ARCHITECTURE.md` section 8, with module stubs and docstrings.
- `Makefile` has every target listed in `AGENTS.md`.
- `backend/config.py` loads all variables in `docs/02-ARCHITECTURE.md` section 10 with defaults, using pydantic settings.
- `backend/db/schema.sql` matches `docs/05-DATA-AND-AUDIT.md` including `chain_head`.
- `scripts/seed_workspace.py` and `scripts/seed_db.py` produce exactly the workspace and database in `docs/08-DEMO-AND-EVAL.md` part A.
- `.env.example`, `.gitignore` (workspace contents, `data/`, `.env`), `pyproject.toml` or `requirements.txt` exist.
- `docs/DECISIONS.md` exists with a header.
- `make setup && make seed && make test` succeed, with at least one real test (seed produces 50 customers, three with card numbers in `notes`).

**Prompt**

```text
Read AGENTS.md and then docs/01-PRODUCT.md, docs/02-ARCHITECTURE.md, docs/05-DATA-AND-AUDIT.md and
docs/08-DEMO-AND-EVAL.md part A.

Build Phase 0 from docs/09-BUILD-PLAN.md only: the repository skeleton exactly as in
docs/02-ARCHITECTURE.md section 8 (module stubs with one-line docstrings, no logic yet), Makefile targets,
backend/config.py, backend/db/schema.sql, .env.example, .gitignore, dependency file, docs/DECISIONS.md,
scripts/seed_workspace.py and scripts/seed_db.py (synthetic data only; fake secrets generated at seed time).

Add pytest tests proving the seed output. Run make setup, make seed and make test and fix failures.
When done, summarise what you built, anything skipped, and how to run it.
```

---

## Phase 1: Policy engine

**Builds:** `backend/policy/*`.

**Acceptance criteria**

- The canonical `.aishield` in `docs/03-POLICY-AND-SCORING.md` section 6 loads without error. Save it to the repo root.
- Pydantic models validate every section. Weights that do not sum to 1.0, unknown profile names and unknown keys produce a clear startup error.
- Profile defaults from the profile table are implemented. Explicit values override defaults. `RS_PROFILE` overrides the file's profile.
- `matcher.py` passes these cases: `**/.env*` matches `.env`, `config/.env` and `a/b/.env.local` but not `env.txt`; `src/auth/**` matches `src/auth/login.py` and `src/auth/x/y.py` but not `src/authx/a.py`; `**/*.db` matches `business.db` and `data/x.db`.
- Command patterns match on tokenised, single-space-joined commands.
- Mode presets provide thresholds. `scoring.thresholds` overrides them.

**Prompt**

```text
Read AGENTS.md and docs/03-POLICY-AND-SCORING.md (sections 1, 6 and the profile table).

Build Phase 1 from docs/09-BUILD-PLAN.md only: backend/policy/models.py, loader.py, profiles.py and
matcher.py, and write the canonical demo policy to .aishield at the repo root exactly as given.

Implement glob semantics exactly as specified ("**/" matches zero or more directories). Provide a
Policy object with helper methods: protect_rule_for_path(rel, op), protect_rule_for_table(name, op),
hint_weight(rel_or_table), is_benign_path(rel), thresholds(), command_verdict(normalised_command),
egress_allows(host).

Write thorough tests, including the matcher cases listed in the Phase 1 acceptance criteria, profile
merging, override order and invalid-policy errors. Run make test and make lint until green.
Summarise what you built, anything skipped, and how to run it.
```

---

## Phase 2: Detectors, maskers, restore-on-write

**Builds:** `engines/detectors.py`, `text_masker.py`, `code_masker.py`, `table_masker.py`, `privacy_masker.py`, `restore.py`.

**Acceptance criteria**

- Every detector in `03` section 2 has positive and negative tests, including Luhn, Verhoeff, mod-97, placeholder values and benign context.
- `previews` never contain raw values.
- Masking and restore satisfy every property in `docs/06-MASKING.md` section 7.
- `changes` offsets reproduce `sanitized` from `raw` (property test over many inputs).
- Code masker handles: decorators, async functions, nested functions, methods, docstrings, one-line functions, syntax errors, tabs.
- Table masker produces consistent placeholders and masks free-text card numbers.

**Prompt**

```text
Read AGENTS.md, docs/03-POLICY-AND-SCORING.md section 2 (detectors) and docs/06-MASKING.md in full.

Build Phase 2 from docs/09-BUILD-PLAN.md only: backend/engines/detectors.py, text_masker.py, code_masker.py,
table_masker.py, privacy_masker.py and restore.py. Use stdlib ast only to locate line ranges in Python files;
never regenerate source with ast.unparse.

Detector spans carry kind, category, confidence and a masked preview, never the raw value. Implement the
confidence bands, placeholder values and benign-context rules exactly as written. The mask_map persistence
interface should be a small class that takes a sqlite connection so tests can use an in-memory database.

Write tests for every property listed in docs/06-MASKING.md section 7, plus detector positives and
negatives. Run make test and make lint until green. Summarise what you built, anything skipped, and how to
run it.
```

---

## Phase 3: Scoring and decisions

**Builds:** `engines/behaviour.py`, `content_model.py`, `risk_scorer.py`, `core/decision.py`, `eval/train_content_model.py`, `eval/training_data.jsonl`.

**Acceptance criteria**

- `risk_scorer.py` returns the four signals and the combined score using the formula and weights from policy.
- Worked examples E1 to E7 in `docs/03-POLICY-AND-SCORING.md` are parametrized tests within 0.01.
- `decide()` implements strictest-wins, including the masking floor.
- Behaviour tracker computes the sliding-window terms exactly as specified and exposes `blocked_in_window` for the kill-switch.
- `make train-model` trains on at least 400 synthetic examples, saves the model and prints its held-out accuracy. If the model file is missing, scoring returns 0.0 for the content signal and logs a warning.
- Content-model inference averages under 20 ms on the sample content.

**Prompt**

```text
Read AGENTS.md and docs/03-POLICY-AND-SCORING.md in full, and docs/08-DEMO-AND-EVAL.md part C (content
model training section).

Build Phase 3 from docs/09-BUILD-PLAN.md only: backend/engines/behaviour.py, content_model.py,
risk_scorer.py, backend/core/decision.py, eval/train_content_model.py and eval/training_data.jsonl
(at least 400 synthetic labelled examples across credentials, personal data, financial records,
proprietary logic, and benign code, docs and fixtures; do not copy items from eval/dataset.jsonl).

Implement the formulas exactly. Turn the worked examples E1 to E7 into parametrized tests that assert the
numbers within 0.01. Add tests for decide(), the masking floor and behaviour windows (use an injectable clock).
Run make train-model, make test and make lint until green. Summarise what you built, anything skipped, and
how to run it.
```

---

## Phase 4: Core pipeline

**Builds:** `core/models.py`, `reasons.py`, `sessions.py`, `approvals.py`, `audit.py`, `snapshots.py`, `events.py`, `explain.py`, `pipeline.py`.

**Acceptance criteria**

- Audit chain append and verify match `docs/05-DATA-AND-AUDIT.md`, including `chain_head` and tamper detection (modify a field, delete the last row, reorder rows: all detected).
- Approvals: create, resolve from either channel, first answer wins, timeout denies, console channel does not block the event loop.
- Snapshots: file, database and workspace snapshot and rollback work, rollback is recorded as an event, a snapshot rolls back once.
- Kill-switch locks at the fifth BLOCK in the window and not on human denials or already-locked rejections.
- `explain.py` has a template for every reason code in `03` section 7, and tests confirm none interpolate raw sensitive values.
- `pipeline.py` runs the 12-step lifecycle against a **fake connector** in tests. Any exception becomes BLOCK `INTERNAL_ERROR`.
- Concurrent audit appends keep the chain linear (test with `asyncio.gather` over 100 appends).

**Prompt**

```text
Read AGENTS.md, docs/02-ARCHITECTURE.md (sections 3 to 5), docs/03-POLICY-AND-SCORING.md, and
docs/05-DATA-AND-AUDIT.md in full.

Build Phase 4 from docs/09-BUILD-PLAN.md only: backend/core/models.py, reasons.py, sessions.py, approvals.py,
audit.py, snapshots.py, events.py, explain.py and pipeline.py. Implement the 12-step request lifecycle as
specified. Use the Connector protocol from docs/02-ARCHITECTURE.md section 9 and test the pipeline with a fake
connector; do not build real connectors yet.

Fail closed: any exception becomes BLOCK with INTERNAL_ERROR and a logged event. Audit appends are serialised
under one lock. Approvals support console and web channels (the web side is a method the API will call later).

Write tests for every acceptance criterion in the Phase 4 section. Run make test and make lint until green.
Summarise what you built, anything skipped, and how to run it.
```

---

## Phase 5: Connectors and MCP server

**Builds:** `connectors/*`, `mcp_app.py`, `main.py` (two listeners; the API app can be a stub with `/api/health`).

**Acceptance criteria**

- All eight tools from `docs/04-CONNECTORS-AND-API.md` work through the real MCP server using the SDK client in tests.
- Filesystem: path escape, symlink escape, `.aishield` and `data` access, binary, oversize and listing hiding all behave as specified.
- Command: operators blocked, deny and destructive lists, default hold, path-argument analysis, no shell, minimal env, output scanning, workspace snapshot on approved destructive commands.
- Database: classification table, read-only reads, snapshot before writes, row cap, column masking, multi-statement and `ATTACH` blocked.
- Network: allowlist, SSRF ranges, payload scan, method rule.
- The MCP listener serves only `/mcp`. The API listener serves only `/api/*` and `/ws`. A test requests `/api/health` on port 8001 and `/mcp` on port 8000 and expects 404.
- A held call stays frozen until resolved through the approvals module (test with an async resolver).
- `get_session_id(ctx)` is implemented and tested.

**Prompt**

```text
Read AGENTS.md, docs/02-ARCHITECTURE.md, docs/04-CONNECTORS-AND-API.md part A, docs/06-MASKING.md, and the
existing code from Phases 1 to 4.

Build Phase 5 from docs/09-BUILD-PLAN.md only: backend/connectors/base.py, filesystem.py, command.py,
sqlite_db.py, network.py, backend/mcp_app.py and backend/main.py. main.py starts two uvicorn servers in one
asyncio loop: the MCP app on RS_MCP_PORT serving only /mcp, and a minimal API app on RS_API_PORT serving only
/api/health for now.

Check the installed MCP SDK's documentation for mounting the streamable HTTP app, lifespan handling and how to
read the transport session id. Do not rely on memory of the API. Record any fallback in docs/DECISIONS.md.

Every tool call must go through backend/core/pipeline.py. Never use shell=True. Resolve all paths with the
workspace jail.

Write integration tests that drive the real MCP server with the SDK client for each tool and each acceptance
criterion in the Phase 5 section. Run make test and make lint until green. Summarise what you built, anything
skipped, and how to run it.
```

---

## Phase 6: Dashboard API and WebSocket

**Builds:** `backend/api_app.py` fully.

**Acceptance criteria**

- Every route in `docs/04-CONNECTORS-AND-API.md` part B exists with the specified payloads.
- Missing or wrong token returns 401 on every route (GET included). WebSocket requires the auth message and closes with 4401 otherwise. Comparison is constant-time.
- Resolving a pending approval over HTTP unblocks the frozen MCP call (integration test).
- `rollback` restores file, database and workspace snapshots and writes a rollback event.
- `/api/log/verify` reports ok on a clean ledger and failure on a tampered copy.
- `/api/graph` builds nodes from the workspace tree, the database tables and external hosts seen, with the state precedence from the doc.
- WebSocket emits all six event types, plus `hello` with pending approvals on connect.
- The startup banner prints the token and the dashboard URL.

**Prompt**

```text
Read AGENTS.md, docs/04-CONNECTORS-AND-API.md part B, docs/05-DATA-AND-AUDIT.md, and the existing backend code.

Build Phase 6 from docs/09-BUILD-PLAN.md only: complete backend/api_app.py with every route and WebSocket
message in docs/04-CONNECTORS-AND-API.md part B. All routes and the WebSocket require the approver token
(constant-time compare). Print the token and a ready-to-open dashboard URL at startup. CORS allows only the
dashboard origin.

Write integration tests: auth failures on every route, an approval resolved through HTTP unblocking a frozen
MCP call, rollback for each snapshot kind, verify on clean and tampered ledgers, graph state precedence, and
WebSocket events. Run make test and make lint until green. Summarise what you built, anything skipped, and
how to run it.
```

---

## Phase 7: Frontend

**Builds:** `frontend/`.

**Acceptance criteria**

- Layout, components and behaviours match `docs/07-DASHBOARD.md`.
- Token gate works (hash token, memory only, hash cleared).
- Live updates through WebSocket with reconnect and resync.
- Approval card buttons call the right resolutions and disable until `approval.resolved`.
- Graph shows all five states with icons and a legend, and collapses directories.
- Delta view highlights changes from offsets, blurs the raw pane until revealed.
- Verify Log shows green and red states. Undo asks for confirmation.
- Simple and Detail views both work.
- `npm run build` passes with no TypeScript errors. No console errors during a full demo run.

**Prompt**

```text
Read AGENTS.md, docs/07-DASHBOARD.md and docs/04-CONNECTORS-AND-API.md part B.

Build Phase 7 from docs/09-BUILD-PLAN.md only: the frontend in frontend/ with Vite, React 18, TypeScript,
Tailwind, Zustand and Cytoscape.js, following the layout, components, states and quality bar in
docs/07-DASHBOARD.md. Define all API types in frontend/src/types.ts matching the backend payloads.

Do not use localStorage for the token. Do not rely on colour alone for state. Handle empty, loading and
error states.

If the backend is running (make run), test against it by running make demo with --pause. Fix any console
errors. Run npm run build until it passes. Summarise what you built, anything skipped, and how to run it.
```

---

## Phase 8: Demo and evaluation

**Builds:** `demo/*`, `eval/dataset.jsonl`, `eval/run_eval.py`, `eval/keyword_baseline.py`, `eval/attacks/*`, `scripts/tamper_demo.py`.

**Acceptance criteria**

- `make demo` runs the eight steps and every `--assert` check from `docs/08-DEMO-AND-EVAL.md` passes with `--auto-approve`.
- With `--pause` and a person clicking the dashboard, the same run works.
- `evil_collector.py` log is empty after the run.
- `make tamper-demo` shows the verification failure on a copy and leaves the real ledger intact.
- `eval/dataset.jsonl` has about 160 hand-written items across the categories, with 10 pairs.
- `make eval` writes `results.json` and `results.md` with all metrics, the keyword baseline column, a per-category breakdown and a failure list.
- Latency is measured over at least 500 calls.

**Prompt**

```text
Read AGENTS.md and docs/08-DEMO-AND-EVAL.md in full, then the existing backend.

Build Phase 8 from docs/09-BUILD-PLAN.md only: demo/simulated_agent.py, demo/run_demo.py,
demo/evil_collector.py, scripts/tamper_demo.py, eval/dataset.jsonl (about 160 hand-written items across the
categories in the doc, including 10 paired-context pairs; do not generate them from the training data),
eval/keyword_baseline.py, eval/attacks/ (five scripted injections), and eval/run_eval.py.

The demo must pass all --assert checks with --auto-approve. The eval must report every metric in the doc next
to the keyword baseline, plus per-category results and a failure list. Do not tune the dataset to improve
numbers; report what is measured.

Run make demo, make tamper-demo and make eval, and fix real bugs the runs reveal. Summarise results honestly,
including failure cases.
```

---

## Phase 9: Docker sandbox, watchdog, polish

**Builds:** `docker-compose.yml`, `sandbox/*`, `scripts/verify_isolation.py`, `backend/watchdog/watchdog.py`, final README numbers.

**Acceptance criteria**

- `docker compose up --build` starts gateway, agent and collector on the topology in `docs/02-ARCHITECTURE.md` section 6.
- `scripts/verify_isolation.py` prints PASS for all five checks from the doc.
- The simulated agent runs the demo from inside the agent container against the gateway.
- (Should) Watchdog flags a spawned `sh` or `curl` child of a test process and creates a `WATCHDOG_ALERT` event.
- README quickstart is verified end to end on a clean checkout. Real evaluation numbers replace placeholders.
- `docs/DECISIONS.md` is up to date.

**Prompt**

```text
Read AGENTS.md, docs/02-ARCHITECTURE.md (sections 1, 6 and 7) and docs/08-DEMO-AND-EVAL.md (isolation check).

Build Phase 9 from docs/09-BUILD-PLAN.md only: docker-compose.yml, sandbox/Dockerfile.gateway,
sandbox/Dockerfile.agent, scripts/verify_isolation.py and backend/watchdog/watchdog.py. Follow the compose
topology in the architecture doc: internal-only agent network, no workspace mount in the agent container,
read-only root filesystem, dropped capabilities, dashboard port published to 127.0.0.1 only.

The agent container must never receive the approver token. Run the isolation script inside the agent
container and make all five checks pass. Then run the full demo from inside the container.

Finally, verify the README quickstart on a clean checkout, fill in real evaluation numbers, and update
docs/DECISIONS.md. Summarise what you built, anything skipped, and how to run it.
```

---

## Judge-day checklist

- [ ] `make seed`, `make train-model`, `make run`, dashboard open, token URL ready.
- [ ] One full rehearsal with `--pause`. One with `--auto-approve --assert` as a safety net.
- [ ] A screen recording of a clean run as a fallback.
- [ ] `results.md` printed or on a slide, including the failure list.
- [ ] Docker mode tested on the presenting machine, with `verify_isolation.py` output ready.
- [ ] Reset between runs: `make clean-data && make seed`.
- [ ] A one-sentence answer ready for each of these questions: "What if the agent bypasses the proxy?" (sandbox and threat model), "How is this context-aware?" (four signals and the paired examples), "Why is the model small?" (rules decide first, model handles ambiguity, runs offline), "What did you not solve?" (limitations in `README.md`).
