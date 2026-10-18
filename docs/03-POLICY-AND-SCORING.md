# 03. Policy and Scoring

This document is the source of truth for the `.aishield` format, detectors, the score formula, decisions and reason codes.

## 1. Decision model

Decisions are ordered: **ALLOW < MASK < HOLD < BLOCK**.

```python
def decide(rule: Decision, tier_default: Decision, score: float, th: Thresholds,
           has_mask_floor: bool) -> Decision:
    by_score = (BLOCK if score >= th.block else
                HOLD  if score >= th.hold  else
                MASK  if score >= th.mask  else ALLOW)
    floor = MASK if has_mask_floor else ALLOW     # auto-mask spans exist
    return max(rule, tier_default, by_score, floor)
```

- **Hard rules** (protect rules, denied commands, SQL rules, egress rules, path escape) produce `rule`.
- **Score** handles the grey area that rules do not cover.
- **Masking floor:** if detectors found high-confidence spans, the result is at least MASK.
- If the final decision is MASK but the masker finds nothing to change, record ALLOW with flag `score_elevated`.

### Human resolution of HOLD

| Resolution | Effect |
|---|---|
| `approve` | Run as requested. Secrets are still masked. PII and financial spans are released. |
| `approve_masked` | Run with masking forced: all detections at or above `ignore_below`, plus protected columns. Reads only. |
| `deny` | Return `denied`. |
| `deny_and_lock` | Deny and lock the session. |
| timeout | Deny with `APPROVAL_TIMEOUT`. |

**Invariant:** detected high-confidence **secrets** are never returned to the agent, whatever the resolution.

## 2. Detectors

`backend/engines/detectors.py` returns spans: `{kind, category, start, end, confidence, preview}`. `category` is `secret`, `pii` or `financial`. `preview` is masked (for example `AKIA************`), never raw.

| Kind | Category | Rule | Confidence |
|---|---|---|---|
| `PRIVATE_KEY` | secret | `-----BEGIN (RSA \|EC \|OPENSSH \|DSA )?PRIVATE KEY-----` | 0.99 |
| `AWS_ACCESS_KEY` | secret | `\b(AKIA\|ASIA)[0-9A-Z]{16}\b` | 0.99 |
| `GITHUB_TOKEN` | secret | `\bgh[pousr]_[A-Za-z0-9]{36,}\b` | 0.99 |
| `STRIPE_KEY` | secret | `\bsk_(live\|test)_[A-Za-z0-9]{16,}\b` | 0.97 |
| `SLACK_TOKEN` | secret | `\bxox[baprs]-[A-Za-z0-9-]{10,}\b` | 0.97 |
| `JWT` | secret | `\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}\b` | 0.90 |
| `SECRET_ASSIGNMENT` | secret | `(?i)\b(api[_-]?key\|secret\|token\|passwd\|password\|private[_-]?key\|access[_-]?key)\b\s*[:=]\s*["']?([^\s"']{8,})`, value not a placeholder | 0.90 |
| `CONNECTION_STRING` | secret | URL with embedded credentials: `scheme://user:pass@host` | 0.92 |
| `HIGH_ENTROPY` | secret | Token of 20+ chars from `[A-Za-z0-9+/=_-]`, Shannon entropy at least 4.0 (3.5 for pure hex), not in a lockfile or checksum line | 0.60 |
| `CREDIT_CARD` | financial | 13 to 19 digits with optional separators, Luhn valid | 0.95 |
| `US_SSN` | pii | `\b\d{3}-\d{2}-\d{4}\b` with valid area numbers | 0.85 |
| `AADHAAR` | pii | 12 digits in groups of 4, Verhoeff valid | 0.90 |
| `PAN` | pii | `\b[A-Z]{5}[0-9]{4}[A-Z]\b` | 0.85 |
| `IBAN` | financial | Pattern plus mod-97 check | 0.90 |
| `EMAIL` | pii | Standard email pattern | 0.50 |
| `PHONE` | pii | E.164 and common national formats, 10 to 15 digits | 0.50 |

**Placeholder values** (value after `=` is not a secret): `changeme`, `password`, `example`, `xxxx`, `your_..._here`, `<...>`, `${...}`, `os.environ...`, `process.env...`, empty. Confidence drops to 0.10.

**Benign context** (from policy `benign_context`):

- A `CREDIT_CARD` whose digits are in `known_test_values`, in a file matching `benign_context.paths`, gets confidence 0.10.
- An `EMAIL` at one of `example_email_domains` gets confidence 0.10.
- Benign context never lowers confidence for `secret` kinds except placeholder values above.

### Confidence bands

| Band | Confidence | Treatment |
|---|---|---|
| Ignore | below `ignore_below` (0.40) | Discarded |
| Medium | 0.40 up to `auto_mask_confidence` (0.85) | Feeds the `pattern` signal. Masked only if final decision is MASK. |
| Auto-mask | 0.85 and above | Masked immediately. Sets the masking floor. Excluded from the `pattern` signal. |

PII and financial auto-mask spans are skipped when a human chose `approve` (full release). Secrets never are.

## 3. Four signals

All signals are in 0.0 to 1.0.

### Pattern

Highest confidence among **medium-band** detections. Zero if none.

### Location

The highest `weight` among `hints` entries matching the target path or table. Zero if the target matches `benign_context.paths`. Explicit `protect` rules are hard rules, not part of this signal.

### Content model

Probability that the content is sensitive, from the offline classifier. Input is computed on the content **after auto-mask spans are replaced**, so a secret already masked is not counted twice:

```
"<connector> | <relative path or table> | <content[:4000]>"
```

Interface (`backend/engines/content_model.py`):

```python
class ContentModel:
    def score(self, text: str, context: str) -> float: ...
```

Implementation: scikit-learn `Pipeline` with `TfidfVectorizer` (word 1 to 2 grams plus char 3 to 5 grams) and `LogisticRegression`. Trained by `eval/train_content_model.py` on `eval/training_data.jsonl`, saved to `backend/models/content_model.joblib`. If the file is missing, the scorer returns 0.0 and logs a warning (fail toward rules, not toward crash).

### Behaviour

Sliding window of `behaviour.window_seconds` (default 60), per session:

```
b_blocked  = min(1, blocked_in_window / kill_switch.max_blocked_attempts)
b_sens     = min(1, sensitive_reads_in_window / 5)     # reads with location >= 0.5 or pattern >= 0.5 or any auto-mask span
b_breadth  = min(1, distinct_dirs_in_window / 10)
b_mismatch = 1.0 if task.scope_paths is declared and target is outside it else 0.0   # per request

behaviour  = min(1, 0.35*b_blocked + 0.25*b_sens + 0.20*b_breadth + 0.20*b_mismatch)
```

## 4. Combined score

```
base     = max(pattern, location)
combined = 0.45 * base + 0.30 * content_model + 0.25 * behaviour
```

Weights live in policy `scoring.weights` and must sum to 1.0.

Why this shape: certain leaks never depend on the score, because hard rules and the masking floor decide them first. The score exists for ambiguous cases, and the behaviour term is what makes the same file score differently depending on how the agent has been acting.

### Thresholds by mode

| Mode | mask | hold | block |
|---|---|---|---|
| `strict` | 0.30 | 0.55 | 0.80 |
| `balanced` | 0.40 | 0.65 | 0.85 |
| `permissive` | 0.50 | 0.75 | 0.90 |

`scoring.thresholds` in the policy overrides the mode preset.

### Worked examples (must be parametrized tests, tolerance 0.01)

Mode `strict`. Weights 0.45, 0.30, 0.25.

| # | Situation | pattern | location | content | behaviour | combined | Decision |
|---|---|---|---|---|---|---|---|
| E1 | Read `src/utils/format.py`, in scope | 0 | 0 | 0.05 | 0 | 0.015 | ALLOW |
| E2 | Read `src/pricing/engine.py`, in scope | 0 | 0.6 | 0.70 | 0 | 0.480 | MASK (proprietary logic) |
| E3 | Same file, out of scope, 5 sensitive reads and 3 dirs in window | 0 | 0.6 | 0.70 | 0.51 | 0.6075 | HOLD |
| E4 | Test card `4111...` in `tests/fixtures/cards.py` | 0 (0.10 ignored) | 0 | 0.10 | 0 | 0.030 | ALLOW (benign context) |
| E5 | Same card in `customers.notes` result | auto-mask 0.95 | 0.9 | 0.60 | 0 | rule HOLD (protected table) | HOLD, masked on approval |
| E6 | AWS key in `config/settings.py` | auto-mask 0.99, pattern signal 0 | 0.5 | 0.20 | 0 | 0.285 | MASK (masking floor) |
| E7 | Entropy string (0.60) in `src/app.py`, no hints | 0.60 | 0 | 0.30 | 0 | 0.360 | MASK |

Behaviour for E3: `0.25*1.0 + 0.20*0.3 + 0.20*1.0 = 0.51`.

## 5. Kill-switch

Lock the session when either holds:

- `blocked_in_window >= kill_switch.max_blocked_attempts` within `kill_switch.window_seconds`, counting BLOCK decisions only (not human denials, not calls rejected because the session is already locked);
- `behaviour >= kill_switch.behaviour_threshold` (0.90).

On lock: set session status `locked`, log one `KILL_SWITCH` event (decision BLOCK), publish `session.updated`. Every later call returns `terminated` and is logged as `SESSION_LOCKED` without counting. A human can unlock from the dashboard. If a host-side supervisor is configured (`kill_hook`), it terminates the agent process. This is optional.

## 6. The `.aishield` file

Location: repository root, outside the workspace. Read-only to the agent (no tool can reach it). Loaded once at startup. Profile defaults are merged first, then explicit values override them. Invalid files fail startup with a clear error.

### Canonical demo policy (copy verbatim to `.aishield`)

```yaml
version: 1
profile: developer
mode: strict

task:
  description: "Fix the typo in the README quick-start and update imports in src/"
  scope_paths: ["README.md", "docs/**", "src/**"]

paths:
  workspace: ./workspace
  database: ./workspace/business.db

protect:
  - { kind: path,  match: "**/.env*",    read: block, write: block, delete: block }
  - { kind: path,  match: "**/*.pem",    read: block, write: block, delete: block }
  - { kind: path,  match: "**/id_rsa*",  read: block, write: block, delete: block }
  - { kind: path,  match: "**/*.db",     read: block, write: block, delete: block }
  - { kind: path,  match: "src/auth/**", read: mask,  write: hold,  delete: block }
  - kind: table
    match: customers
    read: hold
    write: hold
    delete: block
    columns: [full_name, email, phone, national_id]

hints:
  - { match: "src/pricing/**", weight: 0.6 }
  - { match: "config/**",      weight: 0.5 }

benign_context:
  paths: ["tests/**", "fixtures/**", "docs/examples/**"]
  known_test_values: ["4111111111111111", "4242424242424242", "5555555555554444"]
  example_email_domains: ["example.com", "example.org", "test.com"]

commands:
  allow:                       # run without approval if every path argument passes file policy
    - "ls*"
    - "cat *"
    - "head *"
    - "tail *"
    - "grep *"
    - "wc *"
    - "git status"
    - "git diff*"
    - "git log*"
  destructive:                 # HOLD, snapshot on approval
    - "rm -rf *"
    - "rm -r *"
    - "git push --force*"
    - "git push -f*"
    - "git reset --hard*"
    - "git clean -f*"
    - "chmod -R *"
  deny:                        # always BLOCK
    - "curl *"
    - "wget *"
    - "nc *"
    - "ncat *"
    - "ssh *"
    - "scp *"
    - "sudo *"
    - "mkfs*"
    - "dd *"
  default: hold                # anything unmatched
  timeout_seconds: 30
  max_output_bytes: 100000

egress:
  default: deny
  allow_hosts: ["api.github.com", "pypi.org", "registry.npmjs.org"]
  allow_private_ranges: false
  hold_methods: [POST, PUT, PATCH, DELETE]
  timeout_seconds: 10

database:
  max_rows: 200

masking:
  code: structure-preserving
  tables: typed-placeholders
  restore_on_write: true
  min_body_lines: 3

scoring:
  weights: { base: 0.45, content: 0.30, behaviour: 0.25 }
  thresholds: { mask: 0.30, hold: 0.55, block: 0.80 }
  detectors: { auto_mask_confidence: 0.85, ignore_below: 0.40 }

behaviour:
  window_seconds: 60
  weights: { blocked: 0.35, sensitive_reads: 0.25, breadth: 0.20, mismatch: 0.20 }

kill_switch:
  max_blocked_attempts: 5
  window_seconds: 60
  behaviour_threshold: 0.90

approval:
  channels: [console, web]
  timeout_seconds: 60
  on_timeout: deny

limits:
  max_read_bytes: 200000
  hide_protected_in_listing: true
```

### Glob semantics (`policy/matcher.py`)

- Paths are workspace-relative, forward-slash, no leading `./`.
- `**/` matches zero or more directories, so `**/.env*` matches `.env`, `config/.env` and `a/b/.env.local`.
- `*` matches within one path segment. `**` at the end matches everything below.
- Matching is case-sensitive.
- Command patterns use `fnmatch` on the tokenised command rejoined with single spaces.

### Profile defaults

Implemented in `policy/profiles.py`. Explicit policy values override these.

| Profile | Mode | Protect (defaults) | Commands | Egress | Hints and detectors |
|---|---|---|---|---|---|
| `developer` | balanced | `**/.env*`, `**/*.pem`, `**/id_rsa*` block | read-only allowlist as in the demo, rest HOLD | allow `api.github.com`, `pypi.org`, `registry.npmjs.org`; hold write methods | All secret detectors. PII at medium band. |
| `business` | strict | Tables with columns named like email, phone, ssn, salary, account, national_id: read HOLD, write HOLD, delete BLOCK | No allowlist, all HOLD | Deny all. Hold all non-GET. | Full PII and financial detectors. |
| `student` | balanced | `**/.env*`, `**/*.pem` block. Deletes HOLD. | Read-only allowlist, rest HOLD | Allow `wikipedia.org`, `arxiv.org`. Hold writes. | Hints `thesis/**`, `drafts/**` weight 0.5. ID-number detectors. |
| `strict` | strict | Everything sensitive HOLD or BLOCK. Reads masked at `ignore_below`. | All HOLD | Deny all | All detectors. |

## 7. Reason codes and plain-language templates

Defined in `core/reasons.py`. Templates live in `core/explain.py`. Placeholders in braces are filled from the event.

| Code | Plain-language template |
|---|---|
| `PROTECTED_PATH` | The agent tried to open {target}, which is marked as protected. Blocked. |
| `PROTECTED_TABLE` | The agent asked for rows from the {target} table, which holds private data. It was paused for your approval. |
| `SECRET_DETECTED` | A password or key was found in {target}. It was hidden before the agent saw it. |
| `PII_DETECTED` | Personal information was found in {target}. It was hidden before the agent saw it. |
| `PATH_ESCAPE` | The agent tried to reach a location outside the workspace. Blocked. |
| `POLICY_TAMPER` | The agent tried to touch the security policy. Blocked. |
| `EGRESS_DENIED` | The agent tried to send data to {host}, which is not on the approved list. Blocked. |
| `EXFIL_DETECTED` | The agent tried to send what looks like a secret or private data out of the system. Blocked. |
| `OUTBOUND_WRITE` | The agent wants to send data to {host}. It was paused for your approval. |
| `DESTRUCTIVE_COMMAND` | The agent wants to run a command that can delete or overwrite work: {target}. It was paused for your approval. |
| `DENIED_COMMAND` | The agent tried to run a command that is never allowed: {target}. Blocked. |
| `UNLISTED_COMMAND` | The agent wants to run a command that is not on the approved list: {target}. It was paused for your approval. |
| `SHELL_OPERATORS` | The agent tried to chain commands together, which can be used to bypass safety checks. Blocked. |
| `DESTRUCTIVE_SQL` | The agent wants to run a database change that can remove or rewrite many records. It was paused for your approval. |
| `UNSAFE_SQL` | The agent sent a database command that could not be verified as safe. Blocked. |
| `TASK_MISMATCH` | The request is outside what the agent was asked to do. |
| `SENSITIVE_LOCATION` | The location is marked as sensitive. |
| `CONTENT_SENSITIVE` | The content looks sensitive. |
| `BEHAVIOUR_ESCALATION` | The agent has been reading many sensitive items in a short time. |
| `KILL_SWITCH` | The agent kept trying forbidden actions, so the session was ended. |
| `SESSION_LOCKED` | The session was already ended. The request was refused. |
| `MASKED_FOR_PRIVACY` | Sensitive parts of {target} were replaced with placeholders. |
| `UNMASKABLE_FORMAT` | {target} could not be safely masked, so it was paused for your approval. |
| `MASKED_BLOCK_REMOVED` | The agent's edit removes hidden code. It was paused for your approval. |
| `STALE_MASKED_BASE` | {target} changed after the agent last read it. The edit was paused for your approval. |
| `HUMAN_APPROVED` | You approved this action. |
| `HUMAN_EDITED` | You approved this action with masking turned on. |
| `HUMAN_DENIED` | You denied this action. |
| `APPROVAL_TIMEOUT` | No one answered in time, so the action was denied. |
| `BENIGN_CONTEXT` | Looks like sample or test data, so it was allowed. |
| `UNSUPPORTED_BINARY` | Binary files are not available to the agent. |
| `TOO_LARGE` | The content is larger than the allowed limit. |
| `WATCHDOG_ALERT` | The agent's process tried to act outside the safety gateway. |
| `INTERNAL_ERROR` | Something went wrong inside the safety gateway, so the action was blocked to be safe. |

Rules for templates: one sentence where possible, no jargon, no scores, no rule names, never include raw sensitive values.
