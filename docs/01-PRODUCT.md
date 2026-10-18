# 01. Product

## Problem

AI agents now work on real files and real business systems. People in every field use them: engineers, analysts, students, managers. Most of these users cannot judge what an agent is doing. An agent can read secrets, expose customer data, run a destructive command or send data to an outside server, often after one click of "Accept all". A prompt injection hidden in a README, email or web page can steer an agent into doing exactly that.

Two gaps follow:

- **Control (PS 2.1):** nothing sits between the agent and the systems it acts on to monitor, approve, block and undo.
- **Privacy (PS 2.2):** keyword filters fail because sensitivity depends on context. The same card number is harmless in a test fixture and a breach in a customer table.

## Solution

RepoShield is a gateway between any MCP-capable agent and the systems it touches. It enforces policy outside the agent's own runtime, so "Accept all" modes and injected instructions cannot switch it off. It applies deterministic rules, four-signal sensitivity scoring and human approval, masks what must be hidden, and keeps a tamper-evident record.

## Users and profiles

| User | Typical agent work | What matters most | Profile |
|---|---|---|---|
| Developer or engineer | Coding agents on local repos | Secrets, `.env`, proprietary logic, destructive git and shell | `developer` |
| Business or operations team | Agents over databases and records | Customer PII, financial data, bulk deletes, outbound data | `business` |
| Student or researcher | Agents over notes, drafts, datasets | Unpublished work, personal IDs, accidental deletion | `student` |
| Regulated teams | Agents over sensitive records | Everything sensitive held or blocked | `strict` |
| Non-technical manager | Oversees agents others run | Plain-language view, one-click approve or stop | any (Simple view) |

Usability principles: start from a profile, explain every decision in one plain sentence, and give non-technical reviewers a Simple view without scores or hashes.

## Requirements

Priority: **M** = must (core demo), **S** = should, **C** = could.

### Control (PS 2.1)

| ID | Requirement | Pri |
|---|---|---|
| C1 | All agent actions pass through the gateway. In protected mode there is no other path to data. | M |
| C2 | Four decisions (ALLOW, MASK, HOLD, BLOCK) with strictest-wins resolution. | M |
| C3 | Every action gets a risk tier from 0 to 5. | M |
| C4 | HOLD freezes the call until a human resolves it. Timeout means deny. | M |
| C5 | Two approval channels: gateway console and web dashboard. First answer wins. | M |
| C6 | Kill-switch locks the session after repeated blocked attempts or runaway behaviour score. | M |
| C7 | Snapshot before reversible writes and approved destructive actions. Undo from the dashboard. | M |
| C8 | Egress gate: host allowlist, SSRF protection, body scanning for secrets. | M |
| C9 | Command gateway: allowlist-first, no shell, path-argument analysis. | M |
| C10 | SQLite connector with statement classification and protected tables. | M |
| C11 | Process watchdog that flags attempts to bypass the gateway. | S |

### Privacy (PS 2.2)

| ID | Requirement | Pri |
|---|---|---|
| P1 | Sensitivity from four signals: pattern, location, content model, session behaviour. | M |
| P2 | High-confidence secrets are always masked and never returned to the agent. | M |
| P3 | Structure-preserving masking for text, Python code (AST) and database tables. | M |
| P4 | Restore-on-write: masked placeholders are replaced by originals when the agent writes back. | M |
| P5 | Benign-context handling (test card numbers, example emails, placeholder keys). | M |
| P6 | Offline content model (TF-IDF plus logistic regression) behind a stable interface. | M |
| P7 | Task alignment: requests outside the declared task scope raise the risk score. | M |
| P8 | Session behaviour score from a sliding window. | M |

### Audit and monitoring

| ID | Requirement | Pri |
|---|---|---|
| A1 | Hash-chained event ledger with a verify endpoint and a Verify Log button. | M |
| A2 | Live knowledge graph of what the agent has touched. | M |
| A3 | Sanitization delta view: raw requested content against what the agent received. | M |
| A4 | Intervention log with timestamp, target, scores, decision, approval state, reason. | M |
| A5 | Plain-language reason for every event. | M |
| A6 | Metrics strip: counts per decision, average gateway latency. | S |

### Usability and platform

| ID | Requirement | Pri |
|---|---|---|
| U1 | Four profiles with documented defaults. | M |
| U2 | Simple and Detail views. | M |
| U3 | Approve, Approve with masking, Stop. | M |
| S1 | Runs on one machine with `make run`. Decisions need no network. | M |
| S2 | Docker sandbox with an isolation check script. | M |
| S3 | Evaluation harness with metrics and a keyword-only baseline. | M |
| S4 | Scripted prompt-injection demo with assertions. | M |

## Non-goals

- Replacing OS-level security or defending against an agent that escapes the sandbox.
- Guaranteeing masked code compiles or runs.
- Proving that a model provider trained on leaked data. Canary traps and watermarking are excluded because invisible characters are routinely stripped, injected decoy code can corrupt a codebase, and the evidential value is weak.
- A policy editor UI, multi-user accounts or cloud hosting.

## Success criteria

1. The scripted attack in `docs/08-DEMO-AND-EVAL.md` runs end to end with all assertions passing.
2. The dashboard shows each step live: red node on a blocked read, approval card on a held query, delta view with masked values, kill-switch lock.
3. Verify Log passes on the real ledger and fails on a tampered copy.
4. Undo restores a deleted directory.
5. `make eval` reports detection rate, false-positive rate, context accuracy, injection resistance and latency overhead, next to the keyword baseline.
6. A judge can read any event and understand it without technical knowledge.

## Mapping to the problem statements

| Deliverable | Delivered by |
|---|---|
| 2.1 Monitor and control agent actions | Gateway, sandbox, risk tiers, approvals (C1 to C5) |
| 2.1 Detect or block harmful or unauthorised actions | Rules, command and SQL classification, egress gate, kill-switch (C6 to C10) |
| 2.1 Audit or monitoring interface | Graph, feed, log, verify (A1, A2, A4, A5) |
| 2.1 "Across business systems" | Connector model: files, shell, SQLite database, network |
| 2.2 Context-based sensitivity | Four-signal scoring, benign context, task alignment (P1, P5, P7, P8) |
| 2.2 Redact, restrict or prevent exposure | Masking, restore-on-write, egress scanning (P2 to P4, C8) |
| 2.2 Interface showing risks and action taken | Delta view and plain-language reasons (A3, A5) |
