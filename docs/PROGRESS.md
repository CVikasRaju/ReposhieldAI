# Progress

Status of every phase in `docs/09-BUILD-PLAN.md`. Each entry lists what was
built, the command that proves it and the real result. Nothing here is
estimated: every number comes from a command run on this repository.

Statuses: **complete** (acceptance criteria met), **partial** (built, an
explicitly optional or unverified part remains), **blocked** (could not be run).

Environment used for the evidence below: Windows 11, Python 3.12.10 inside
`.venv`, Node 24, GNU make invoked as `mingw32-make` (no `make` on this shell's
PATH), Docker 29.5.2 with Docker Compose v5.1.4.

---

## Phase 0: Scaffold — complete

Repository layout matches `docs/02` section 8, configuration comes from the
`RS_*` variables in `.env.example`, and the Makefile provides every documented
target plus `frontend`, `dashboard` and `clean-data`.

Evidence: `make setup` on a fresh clone finished with exit 0 and installed the
pinned requirements; `make lint` exits 0; `make test` runs the whole suite.

Skipped: nothing.

## Phase 1: Policy engine — complete

`.aishield` loading with profile merge, glob matching with `**` semantics,
protect rules for paths and tables, command/egress lists and thresholds.

Evidence: `make test` includes `backend/tests/test_policy.py` (green), and the
integration gateway loads the real `.aishield` in every MCP test.

Skipped: nothing.

## Phase 2: Detectors, maskers, restore-on-write — complete

Regex, checksum and entropy detectors; text, Python-AST and table maskers; the
placeholder map; restore-on-write for files and SQL literals.

Evidence: `make test` includes `test_detectors.py`, `test_masking.py` and
`test_seed.py`; `make eval` reports **20/20** edit-after-mask trials restored
all untouched masked blocks byte-identically.

Skipped: nothing.

## Phase 3: Scoring and decisions — complete

Four-signal scoring, thresholds, strictest-wins decisions and the offline
content model trained on synthetic data.

Evidence: `test_scoring.py` and `test_content_model.py` pass inside
`make test`; the worked examples from `docs/03` are parametrized tests in that
suite. Training is deterministic: two consecutive `make train-model` runs
produced the same `backend/models/content_model.joblib` SHA-256
(`5bc21ded4b0a7a4d…`).

Skipped: nothing.

## Phase 4: Core pipeline — complete

Prepare → rules → detect → score → decide → hold → snapshot → execute → mask →
log, with fail-closed handling and the hash-chained ledger.

Evidence: `test_pipeline.py` and `test_audit.py` pass; `make demo --assert`
verifies the chain with `GET /api/log/verify` (`{"ok": true, "checked": 29}` in
the final run) and `make tamper-demo` detects tampering on a copy while the
real ledger's SHA-256 is unchanged.

Skipped: nothing.

## Phase 5: Connectors and MCP server — complete

Filesystem, command, SQLite and network connectors behind the MCP tools, with
two separate listeners (MCP `/mcp` only on 8001, dashboard routes only on 8000).

Evidence: `test_mcp.py` drives the real server with the SDK client;
`demo/run_demo.py --auto-approve --assert` passes **29/29** checks including
blocked `.env` reads, masked customer rows and the kill switch; all five
`eval/attacks/attack_*.py` report `RESULT: stopped`.

Skipped: nothing.

## Phase 6: Dashboard API and WebSocket — complete

Sessions, events, deltas, graph, approvals, rollback, log verification, policy,
metrics, and the token-gated `/ws` with `hello` plus live event messages.

Evidence: `test_api.py` passes; a headless-Chrome run against a live gateway
showed a held query arriving as an approval card without a reload
(`before cards: 0` → `live cards: 1`, title `(1) RepoShield`), `Verify Log`
showing "Log intact, 14 entries checked", no console errors, no token in
`localStorage`, and the hash cleared from the URL.

Skipped: nothing.

## Phase 7: Frontend — complete

Token gate, header with session/profile/status and Simple/Detail toggle,
approval cards with countdowns, five-state Cytoscape graph with icons and a
legend, plain-language feed, blurred raw vs sanitized delta view, filtered
intervention log with undo, and the metrics strip.

Evidence: `npm run build` passes with `tsc -b` and emits a **31.79 kB** CSS
bundle (previously 0 kB). Screenshots from a live gateway are in
`docs/screenshots/` (`01-dashboard` … `08-live-approval`).

Skipped: nothing.

## Phase 8: Demo and evaluation — complete, targets partly missed

The demo is the scripted eight-step attack from `docs/08`. The evaluation is a
hand-written 224-item dataset with a keyword baseline, and every number is in
`eval/results/results.md`.

Evidence: `make demo` → 29 checks passed, 0 failed; `make eval` →
detection 0.8278, false-positive rate 0.1644, precision 0.9124, F1 0.8681,
context accuracy 0.5238 over 21 pairs, decision accuracy 0.7232, injection
resistance 5/5, latency median 11.51 ms / p95 18.68 ms over 552 calls,
restore 20/20, and 62 of 224 items decided differently from the expected rung.

Honest misses: the targets in `docs/08` are recall ≥ 0.90 (**missed**:
0.8278), false-positive rate ≤ 0.08 (**missed**: 0.1644) and context accuracy
≥ 0.85 (**missed**: 0.5238). The failure list explains each one: the content
model does not score most "confidential" prose above the mask threshold, some
password-style secrets with unusual key names are not detected, registry index
responses (pypi, npm) are masked because they contain hash strings, and
out-of-scope secret reads are held (stricter than the expected MASK).

Skipped: nothing.

## Phase 9: Docker sandbox, watchdog, polish — partial

Docker: `sandbox/Dockerfile.gateway`, a minimal non-root `Dockerfile.agent`,
and the two-network topology from `docs/02` section 6. The gateway mounts the
workspace read-write, the policy read-only and `data/` read-write; the
dashboard is published on `127.0.0.1:8000` only; the MCP port is reachable on
`agent_net` only; the agent has no approver token.

Evidence (run for real, Docker Desktop started for the test):
`docker compose up --build -d` → gateway `Up (healthy)`, agent and collector
`Up`; `docker compose exec -T agent python scripts/verify_isolation.py` →
**5/5 PASS** (workspace not mounted, no internet, no collector, `/api/events`
401 without a token, `list_directory` works); the demo run from inside the
agent container produced blocked, masked, held-then-approved and terminated
statuses exactly as documented; `data/collector.log` had **0 entries**;
`printenv RS_APPROVER_TOKEN` inside the agent was empty.

Watchdog: the optional C11 module had invalid event construction (tuple enums,
float timestamp) and swallowed the resulting error, so it could never log. It
now builds valid events and uses `psutil` for the child-process scan, and has
`backend/tests/test_watchdog.py`.

Partial because: the watchdog is not started by the gateway (it is the optional
C11 layer and applies only to local mode), so no live alert was produced.

Skipped: nothing else.

---

## Final numbers

| Check | Command | Result |
|---|---|---|
| Tests | `make test` | 239 passed |
| Lint | `make lint` | exit 0, no errors |
| Demo | `make demo` | 29 checks passed, 0 failed |
| Tamper | `make tamper-demo` | tampering detected on the copy; ledger SHA-256 unchanged |
| Attacks | `eval/attacks/attack_*.py` | 5/5 stopped |
| Eval | `make eval` | see `eval/results/results.md` (3 of 5 targets missed, reported) |
| Isolation (Docker) | `docker compose exec -T agent python scripts/verify_isolation.py` | 5/5 PASS |
| Frontend build | `npm run build` | TypeScript clean, CSS 31.79 kB |
| Fresh clone | `make setup && make seed && make train-model && make test && make lint` | all green (see README) |

## Known limitations

- The content model misses proprietary prose and several password-style
  secrets; the numbers above are reported as measured, with no threshold
  tuning after the run.
- `/api/health` requires the approver token like every other `/api` route, so
  container health checks use a TCP probe instead.
- The Docker gateway runs as root so the bind-mounted workspace and data
  directory stay writable on Linux hosts; the agent container is non-root.
- `make verify-isolation` needs Docker because the check only means something
  inside the agent container.
