# RepoShield AI

A control and privacy layer for AI agents. Every action an agent attempts on files, shell commands, a database or the network passes through RepoShield first. RepoShield decides to **allow**, **mask**, **hold for human approval** or **block**, and records the outcome in a tamper-evident audit log with a live dashboard.

It targets two hackathon problem statements at once:

- **PS 2.1 AI Agent Safety and Control:** monitor and control what an agent does, block harmful actions, show an audit interface.
- **PS 2.2 Context-Aware Data Privacy:** decide what is sensitive from context, not keywords, then redact or restrict it before the AI sees it.

The product is for anyone running AI agents: developers, business teams, students and researchers. Policy **profiles** (developer, business, student, strict) give each group sensible defaults, and every decision is explained in plain language.

---

## What it does

1. **Intercepts** every tool call from the agent through an MCP gateway. The agent runs in a sandbox where the gateway is the only path to data.
2. **Classifies** each action by risk tier and each piece of data by sensitivity, using four signals: patterns, location, an offline content model and session behaviour.
3. **Decides** with a strictest-wins rule: ALLOW, MASK, HOLD or BLOCK.
4. **Freezes** held calls until a human approves (terminal or web), edits the approval to "with masking", or stops it.
5. **Masks** sensitive content with structure-preserving placeholders and restores the originals when the agent writes the file back.
6. **Protects** against destructive actions with snapshots and one-click Undo, and against exfiltration with an egress gate.
7. **Records** everything in a hash-chained ledger, shown in a live dashboard with a knowledge graph, a raw-vs-sanitized diff and a plain-language feed.

---

## Documentation map

| File | Read it when you need |
|---|---|
| `AGENTS.md` | The rules the building agent must follow. Read first. |
| `docs/01-PRODUCT.md` | Problem, users, requirement IDs, scope |
| `docs/02-ARCHITECTURE.md` | Threat model, components, request lifecycle, repo layout, sandbox |
| `docs/03-POLICY-AND-SCORING.md` | `.aishield` format, profiles, detectors, score formula, decisions, reason codes |
| `docs/04-CONNECTORS-AND-API.md` | MCP tools, connector behaviour, dashboard REST and WebSocket API |
| `docs/05-DATA-AND-AUDIT.md` | SQLite schema, hash chain, snapshots, rollback |
| `docs/06-MASKING.md` | Text, Python AST and table masking, restore-on-write |
| `docs/07-DASHBOARD.md` | Frontend screens, components, realtime behaviour |
| `docs/08-DEMO-AND-EVAL.md` | Seed data, the scripted attack demo, evaluation harness and metrics |
| `docs/09-BUILD-PLAN.md` | Phases, acceptance criteria and paste-ready prompts for the building agent |

---

## Quickstart (local mode, once built)

```bash
make setup          # create venv, install backend deps
make seed           # generate workspace/ and workspace/business.db (synthetic data only)
make train-model    # train the offline content model
make run            # gateway: MCP on :8001 (agent side), dashboard API on :8000 (human side)

cd frontend && npm install && npm run dev    # dashboard on http://localhost:5173

make demo           # scripted prompt-injection attack against the gateway
make eval           # metrics against the labelled dataset and keyword baseline
make test           # unit and integration tests
```

On startup the gateway prints an **approver token** and a dashboard URL containing it. Open that URL. Every API call and WebSocket connection requires the token.

### Hardened mode (Docker sandbox)

```bash
docker compose up --build
python scripts/verify_isolation.py     # proves the agent container cannot bypass the gateway
```

### Ports

| Port | Serves | Reachable by |
|---|---|---|
| 8001 | MCP endpoint `/mcp` only | The agent |
| 8000 | Dashboard API `/api/*` and `/ws` only | Humans (localhost). Token required. |

The two ports are separate listeners. The MCP port never serves an approval route.

---

## Using this pack with an AI coding agent

1. Copy `README.md`, `AGENTS.md` and `docs/` into a new repo named `reposhield-ai`.
2. If your tool has its own rules file (`CLAUDE.md`, `.windsurfrules`, Antigravity rules), make it a single line: `Read and follow AGENTS.md.`
3. Open `docs/09-BUILD-PLAN.md`. Paste the prompt for Phase 0, let the agent finish, check the acceptance criteria, then move to the next phase.
4. Build in order. Each phase depends on the previous one.

---

## Honest scope

- Protection holds while the agent runs inside the RepoShield sandbox and reaches data only through the gateway. An agent with its own unrestricted shell can bypass any proxy, which is why the sandbox is part of the design.
- Masked code is for comprehension, not execution.
- The offline content model is small and trained on synthetic data. Its numbers are reported as measured, with a keyword-only baseline for comparison.
- Anti-training canary traps and watermarking are out of scope. See `docs/01-PRODUCT.md`.
