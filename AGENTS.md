# AGENTS.md: Instructions for the AI agent building RepoShield AI

You are building **RepoShield AI**: a gateway between AI agents and the systems they touch (files, shell, SQLite, network). It enforces policy, masks sensitive data, asks a human to approve risky actions, and logs everything to a hash-chained ledger shown in a live dashboard.

This file is binding. Read it fully before doing anything, then read the docs in the order below.

## Read order

1. `README.md`
2. `docs/01-PRODUCT.md`
3. `docs/02-ARCHITECTURE.md`
4. `docs/03-POLICY-AND-SCORING.md`
5. `docs/04-CONNECTORS-AND-API.md`
6. `docs/05-DATA-AND-AUDIT.md`
7. `docs/06-MASKING.md`
8. `docs/07-DASHBOARD.md`
9. `docs/08-DEMO-AND-EVAL.md`
10. `docs/09-BUILD-PLAN.md`

Build **only the phase you are asked to build**. `docs/09-BUILD-PLAN.md` defines phases and acceptance criteria. Do not start the next phase until the current one passes its acceptance criteria.

## Stack

| Area | Choice |
|---|---|
| Language | Python 3.11+ (backend), TypeScript (frontend) |
| API and gateway | FastAPI, uvicorn, pydantic v2 |
| MCP | Official `mcp` Python SDK, using FastMCP, streamable HTTP transport |
| Policy | PyYAML plus pydantic models |
| SQL analysis | `sqlglot` (SQLite dialect) |
| HTTP client | `httpx` |
| Content model | `scikit-learn` (TF-IDF plus logistic regression), `joblib` |
| Watchdog | `psutil` |
| Storage | SQLite (stdlib `sqlite3`, WAL mode) |
| Tests and lint | `pytest`, `pytest-asyncio`, `ruff` |
| Frontend | Node 20+, Vite, React 18, TypeScript, Tailwind CSS, Zustand, Cytoscape.js |

Do not add a dependency that is not listed here without recording the reason in `docs/DECISIONS.md`. Code masking uses Python's stdlib `ast`, not tree-sitter.

## Commands

The `Makefile` must provide: `setup`, `seed`, `train-model`, `run`, `demo`, `eval`, `test`, `lint`, `docker-up`, `verify-isolation`, `tamper-demo`. Keep them working at the end of every phase.

## Security invariants (never violate)

1. **Two listeners.** The MCP listener (port 8001) serves only `/mcp`. The dashboard listener (port 8000) serves only `/api/*` and `/ws`. No approval, session or audit route may ever be reachable through the MCP listener.
2. **Token on everything human-side.** Every `/api/*` route (GET included) and every WebSocket connection requires the approver token. Compare in constant time.
3. **Policy and data are unreachable to the agent.** The `.aishield` file and the `data/` directory (audit DB, snapshots, mask map) are outside the workspace jail. No tool may read, write, list or reference them.
4. **Strictest decision wins.** Final decision is the maximum of rule verdict, tier default, score decision and masking floor, ordered ALLOW < MASK < HOLD < BLOCK.
5. **Fail closed.** Any exception inside the pipeline produces BLOCK with reason `INTERNAL_ERROR`, a logged event, and a generic message to the agent.
6. **Secrets never reach the agent.** Detected high-confidence secrets are always masked, including after human approval.
7. **No raw sensitive data in logs.** The `events` table, application logs and stdout contain only masked previews, types, counts and SHA-256 hashes. Raw content lives only in the `deltas` and `mask_map` tables.
8. **No policy leakage to the agent.** Blocked and denied responses use a generic message plus an `event_id`. Never name the matched rule or threshold.
9. **Workspace jail.** Resolve every path with `Path.resolve()` and require it to be inside the workspace. Reject escapes and symlinks that leave the workspace.
10. **No shell.** The command gateway never uses `shell=True` and never passes agent input to a shell.
11. **Synthetic data only.** All fixtures, seed data, secrets and customer rows are fake. Fake secrets are generated at seed time, not committed.
12. **One writer for the chain.** Audit inserts happen under a single lock so the hash chain stays linear.

## Engineering rules

- Type-hint everything. Use pydantic models for all data crossing a module boundary.
- Use `async` for connectors, the pipeline, approvals and the API. Run blocking work (sqlite, file I/O, subprocess, model inference) in `asyncio.to_thread`.
- Keep modules small and single-purpose. Match the file layout in `docs/02-ARCHITECTURE.md` exactly. Do not invent new top-level directories.
- Reason codes, decisions, tiers and event fields use the exact names in the docs. Define them once (`backend/core/reasons.py`, `backend/core/models.py`) and import them everywhere.
- Every user-visible explanation comes from `backend/core/explain.py` templates. No hand-written strings in the pipeline.
- Configuration comes from environment variables listed in `.env.example`. No hard-coded paths or ports.
- Write clear, direct docstrings. No filler comments.

## Testing rules

- Every module ships with tests in the same phase. `make test` must pass before a phase is complete.
- Unit-test detectors with positive and negative cases. Unit-test maskers with round trips (`restore(mask(x)) == x`).
- The worked scoring examples in `docs/03-POLICY-AND-SCORING.md` are parametrized tests. The numbers must match within 0.01.
- Integration tests drive the real MCP server with the SDK client, not mocks.
- Never weaken a test or edit the evaluation dataset to make a number look better.

## When the docs are unclear

Choose the simplest option that satisfies the requirement IDs in `docs/01-PRODUCT.md`, then add one entry to `docs/DECISIONS.md` (date, decision, reason). Do not stop to ask unless two documents contradict each other on a security invariant.

For MCP SDK details (mounting, session id access, lifespan handling), check the documentation of the installed SDK version. Do not rely on memory of the API.

## Definition of done (every phase)

- All acceptance criteria for the phase in `docs/09-BUILD-PLAN.md` pass.
- `make test` and `make lint` are green.
- No invariant above is violated.
- `docs/DECISIONS.md` lists any deviation.
- A short summary lists what was built, what was skipped, and how to run it.

## Do not build

- Canary traps, watermarking, zero-width-character signatures or injected decoy code.
- Any LLM call inside the gateway's decision path. Decisions are offline and deterministic.
- Authentication systems beyond the approver token.
- Multi-tenant features, cloud deployment, or a policy editor UI.
- Connectors beyond filesystem, command, SQLite and network, unless a phase explicitly says so.
