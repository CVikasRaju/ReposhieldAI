# 02. Architecture

## 1. Threat model

### Defended against

1. **Prompt injection:** hidden instructions in a README, email, web page or data row that push the agent to leak or destroy data.
2. **Over-reach:** the agent reads or touches more than the task needs.
3. **Destructive mistakes:** hallucinated or careless `rm -rf`, `DROP TABLE`, unscoped `DELETE`, force pushes.
4. **Exfiltration:** secrets or personal data sent to an external host.
5. **Runaway loops:** repeated attempts at forbidden resources.
6. **Self-approval and policy tampering:** the agent trying to approve its own request, edit `.aishield`, or alter the audit log.

### Not defended against

- An agent that escapes the sandbox through an OS-level exploit.
- Data a human has deliberately approved and released.
- What a third-party model provider does with data it legitimately received.

### Trust boundary

RepoShield enforces policy **outside the agent's application runtime**. Client-side settings such as "Accept all" cannot override it, and an injected instruction cannot change it, because the agent has no route to the policy, the approval API or the data except through the gateway.

In protected mode the agent runs in a container where:

- the workspace is **not mounted**;
- the network is **internal only**, reaching nothing but the gateway's MCP port;
- the root filesystem is read-only and all capabilities are dropped.

The gateway holds the real workspace, database and network access.

Trade-off: an agent that only knows native file and shell tools must be configured to use the gateway's MCP tools instead. Protected mode is for agents that speak MCP.

## 2. Components

```
┌───────────────────────────────────────────────────────────────────┐
│ AI agent (Claude Code, Devin, custom). Sandboxed. No direct access │
└───────────────────────────────┬───────────────────────────────────┘
                                │ MCP tool calls  (port 8001, /mcp only)
┌───────────────────────────────▼───────────────────────────────────┐
│                      RepoShield gateway process                    │
│                                                                    │
│  Pipeline: resolve session → prepare → rules → detect → score →    │
│            decide → [hold] → snapshot → execute → mask → log       │
│                                                                    │
│  Engines    detectors · content model · behaviour · risk scorer    │
│  Maskers    text · python AST · table · restore                    │
│  Core       policy · sessions · approvals · audit · snapshots      │
│                                                                    │
│  Connectors  filesystem │ command │ sqlite │ network               │
└──────┬───────────┬──────────┬──────────┬──────────────────────────┘
       │           │          │          │
   workspace/   subprocess  business.db  allowlisted hosts
                                                      
┌─────────────────────────────────────────────────────────────────────┐
│ Dashboard API + WebSocket (port 8000, token required, humans only)   │
│ React dashboard: graph · approvals · feed · log · delta              │
└─────────────────────────────────────────────────────────────────────┘
```

## 3. Process model

One Python process, one asyncio loop, **two uvicorn servers** sharing in-memory state (event bus, pending approvals, behaviour windows):

- **MCP app** on `RS_MCP_PORT` (default 8001). Exposes only the MCP endpoint.
- **API app** on `RS_API_PORT` (default 8000). Exposes `/api/*` and `/ws`.

`backend/main.py` starts both with `asyncio.gather(server_a.serve(), server_b.serve())`.

If mounting the MCP streamable-HTTP app proves unstable with the installed SDK version, the fallback is a standalone MCP server process that talks to the API process through SQLite polling for approvals. Record the choice in `docs/DECISIONS.md`.

### Session identification

A session is one agent run. Resolve the session id in this order:

1. `X-RepoShield-Session` request header, if present.
2. The MCP transport session id.
3. The string `default`.

Wrap this in `get_session_id(ctx)` with a unit test. Create the session row on first sight, using the profile and task from the policy.

## 4. Request lifecycle

Every tool call runs these steps in `backend/core/pipeline.py`:

| # | Step | Detail |
|---|---|---|
| 1 | Resolve session | If locked, return `terminated` and log once. |
| 2 | Build `ActionRequest` | Session, connector, action, target, params, task. |
| 3 | Prepare | Connector normalises the target, assigns base tier and fetches the raw content into memory. Nothing is returned yet. |
| 4 | Rules | Protect rules, command rules, SQL rules, egress rules produce a `RuleVerdict`. |
| 5 | Detect | Detectors scan content. High-confidence spans become auto-mask spans. Medium-confidence spans feed the score. |
| 6 | Score | Pattern, location, content model, behaviour combine into one score (see `03`). |
| 7 | Decide | Strictest of rule verdict, tier default, score decision, masking floor. |
| 8 | Hold | If HOLD, freeze: create approval, notify both channels, await resolution or timeout. |
| 9 | Snapshot | For tier 2 and approved tier 3 actions, snapshot first. |
| 10 | Execute and mask | Run the action. Mask results on reads. Restore placeholders on writes. |
| 11 | Log | Write the event to the hash chain. Store the delta. Update graph and behaviour counters. Run the kill-switch check. |
| 12 | Respond | Return a structured result with no policy details. |

**Fail closed.** Wrap steps 3 to 11 in a try/except. Any exception returns BLOCK with `INTERNAL_ERROR`, logs an event and sends a generic message.

## 5. Risk tiers

| Tier | Meaning | Examples | Default |
|---|---|---|---|
| 0 | Read-only, low sensitivity | list directory, read plain source, `db_schema`, allowlisted command | ALLOW |
| 1 | Read, sensitive content | file with detected secrets, query touching protected columns | MASK |
| 2 | Reversible write | `write_file`, SQL `INSERT` or `UPDATE ... WHERE` | ALLOW with snapshot |
| 3 | Destructive or irreversible | `delete_file`, `DROP`, unscoped `UPDATE`, any `DELETE`, destructive or unlisted command | HOLD |
| 4 | Outbound effect | `http_request` | ALLOW for clean GET to allowlisted host, HOLD for write methods, BLOCK otherwise |
| 5 | Forbidden | protected block, path escape, denied command, shell operators, policy tamper, kill-switch | BLOCK |

Tier is assigned from the action first, then raised by findings (tier 1 for sensitive reads, tier 5 for hard blocks).

## 6. Sandbox topology (Docker)

```yaml
services:
  gateway:
    build: { context: ., dockerfile: sandbox/Dockerfile.gateway }
    volumes:
      - ./workspace:/app/workspace
      - ./.aishield:/app/.aishield:ro
      - ./data:/app/data
    environment:
      RS_WORKSPACE: /app/workspace
      RS_POLICY_PATH: /app/.aishield
      RS_DATA_DIR: /app/data
      RS_APPROVER_TOKEN: ${RS_APPROVER_TOKEN:-}
    networks: [agent_net, host_net]
    ports: ["127.0.0.1:8000:8000"]      # dashboard: host loopback only
    expose: ["8001"]                     # MCP: agent_net only

  agent:
    build: { context: ., dockerfile: sandbox/Dockerfile.agent }
    networks: [agent_net]                # internal network: no internet, no host
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    environment:
      RS_MCP_URL: http://gateway:8001/mcp
    depends_on: [gateway]

  collector:                              # demo only: fake attacker server
    build: { context: ., dockerfile: sandbox/Dockerfile.gateway }
    command: python demo/evil_collector.py
    networks: [host_net]

networks:
  agent_net: { internal: true }
  host_net: {}
```

Notes:

- The gateway shares `agent_net` with the agent, so the agent can reach port 8000 on the network. The approver token is the control for that route. The agent container never receives the token, and all `/api/*` routes and `/ws` reject requests without it.
- The collector is reachable by the gateway but not the agent. It proves that blocking is policy, not a network accident.
- `scripts/verify_isolation.py` runs inside the agent container and checks: no `/app/workspace`, no internet, no collector, `GET gateway:8000/api/events` returns 401, `/mcp` works.

## 7. Process watchdog (secondary)

Optional (`C11`). `backend/watchdog/watchdog.py` takes an agent PID (local mode) and uses `psutil` to watch the process tree. It flags child processes that are raw shells (`sh`, `bash`, `zsh`, `cmd`, `powershell`) or network tools (`curl`, `wget`, `nc`, `ssh`), and non-gateway outbound connections. Each hit creates a `WATCHDOG_ALERT` event and can lock the session. It is a detection layer, not the enforcement layer.

## 8. Repository layout

```
reposhield-ai/
├── README.md
├── AGENTS.md
├── .aishield                       # demo policy, copied from docs/03
├── .env.example
├── .gitignore
├── Makefile
├── docker-compose.yml
├── docs/                           # 01 to 09, plus DECISIONS.md (agent-maintained)
├── workspace/                      # generated by scripts/seed_workspace.py (gitignored)
├── data/                           # audit.db, snapshots/ (gitignored)
├── scripts/
│   ├── seed_workspace.py
│   ├── seed_db.py
│   ├── tamper_demo.py
│   └── verify_isolation.py
├── sandbox/
│   ├── Dockerfile.gateway
│   └── Dockerfile.agent
├── backend/
│   ├── main.py                     # starts both servers
│   ├── config.py                   # env settings
│   ├── mcp_app.py                  # FastMCP app and tool definitions
│   ├── api_app.py                  # dashboard REST and WebSocket
│   ├── core/
│   │   ├── models.py               # pydantic models, enums
│   │   ├── reasons.py              # reason codes
│   │   ├── pipeline.py             # orchestrates the lifecycle
│   │   ├── decision.py             # strictest-wins
│   │   ├── sessions.py
│   │   ├── approvals.py            # pending approvals, console and web channels
│   │   ├── audit.py                # hash chain, verify
│   │   ├── snapshots.py            # snapshot and rollback
│   │   ├── events.py               # in-process event bus
│   │   └── explain.py              # plain-language templates
│   ├── policy/
│   │   ├── models.py               # pydantic policy schema
│   │   ├── loader.py               # YAML load, profile merge, validation
│   │   ├── profiles.py             # profile defaults
│   │   └── matcher.py              # glob matching with ** semantics
│   ├── engines/
│   │   ├── detectors.py            # regex, checksum and entropy detectors
│   │   ├── content_model.py        # offline classifier wrapper
│   │   ├── behaviour.py            # session sliding-window score
│   │   ├── risk_scorer.py          # four-signal combination
│   │   ├── privacy_masker.py       # entry point, dispatches by content type
│   │   ├── text_masker.py
│   │   ├── code_masker.py          # Python ast, line-range replacement
│   │   ├── table_masker.py
│   │   └── restore.py              # restore-on-write
│   ├── connectors/
│   │   ├── base.py                 # Connector interface
│   │   ├── filesystem.py
│   │   ├── command.py
│   │   ├── sqlite_db.py
│   │   └── network.py
│   ├── watchdog/watchdog.py
│   ├── db/schema.sql
│   ├── models/content_model.joblib # generated, committed
│   └── tests/
├── frontend/                       # Vite + React + TypeScript
├── demo/
│   ├── run_demo.py
│   ├── simulated_agent.py
│   └── evil_collector.py
└── eval/
    ├── dataset.jsonl
    ├── training_data.jsonl
    ├── train_content_model.py
    ├── run_eval.py
    ├── keyword_baseline.py
    └── results/
```

## 9. Connector interface

Every connector implements `backend/connectors/base.py`:

```python
class Connector(Protocol):
    name: str  # "filesystem" | "command" | "database" | "network"

    async def prepare(self, req: ActionRequest) -> Prepared:
        """Normalise target(s), assign base tier, fetch raw content or the proposed write.
        Must not return anything to the agent. Raises on path escape or malformed input."""

    async def execute(self, prepared: Prepared, plan: ExecutionPlan) -> ExecResult:
        """Run the action after the decision. `plan` carries mask spans, restore data,
        snapshot handle and the final decision."""
```

Adding a connector (email, spreadsheet, CRM) means implementing this interface and registering MCP tools. The pipeline, scoring, approvals, audit and dashboard need no changes.

## 10. Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `RS_WORKSPACE` | `./workspace` | Workspace jail root |
| `RS_POLICY_PATH` | `./.aishield` | Policy file |
| `RS_DATA_DIR` | `./data` | Audit DB, snapshots |
| `RS_PROFILE` | unset | Overrides the policy's profile |
| `RS_MCP_HOST` / `RS_MCP_PORT` | `127.0.0.1` / `8001` | MCP listener |
| `RS_API_HOST` / `RS_API_PORT` | `127.0.0.1` / `8000` | Dashboard listener |
| `RS_APPROVER_TOKEN` | generated | Dashboard and API token |
| `RS_LOG_LEVEL` | `INFO` | Application log level |
