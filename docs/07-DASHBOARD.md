# 07. Dashboard

The dashboard is the human control surface and the judges' view. It must make sense to a non-technical person in ten seconds.

## 1. Stack

Vite, React 18, TypeScript, Tailwind CSS, Zustand for state, Cytoscape.js for the graph. The delta view is custom (it already has change offsets from the API), so no diff library is needed.

Dev server on port 5173. API base URL from `VITE_API_URL` (default `http://localhost:8000`).

## 2. Authentication

The gateway prints `http://localhost:5173/#token=<token>`. On load, read the token from `location.hash`, keep it in memory only (never `localStorage`), then clear the hash with `history.replaceState`. If there is no token, show a screen asking for it. Send it as `X-Approver-Token` on every request and as the first WebSocket message. On a 401 or close code 4401, show "Invalid or expired token".

## 3. Layout

One page, dark theme, no routing.

```
┌─────────────────────────────────────────────────────────────────────┐
│ Header: RepoShield · session selector · profile badge · status ·     │
│         [Simple | Detail] · [Verify Log] · [Lock/Unlock]            │
├──────────────────────────────────────────────┬──────────────────────┤
│                                              │ Approvals (pending)  │
│  Knowledge Graph                             │  card · card         │
│  "What the AI thinks it knows"               ├──────────────────────┤
│                                              │ Metrics strip        │
├──────────────────────────────────────────────┼──────────────────────┤
│ Activity feed (plain language)               │ Delta view (selected │
│                                              │ event)               │
├──────────────────────────────────────────────┴──────────────────────┤
│ Intervention log table                                              │
└─────────────────────────────────────────────────────────────────────┘
```

On narrow screens, stack the panels in this order: approvals, graph, feed, delta, log.

## 4. Components

### Header

- **Session selector** lists sessions. Default is the most recent.
- **Status badge:** `ACTIVE` (green), `LOCKED` (red), `ENDED` (grey).
- **Profile badge** shows the profile and mode from `GET /api/policy`.
- **Simple / Detail toggle.** Simple hides scores, tiers, hashes and reason codes. Detail shows all.
- **Verify Log** calls `GET /api/log/verify`. Show a green "Log intact, N entries checked" or a red "Tampering detected at entry #N". Keep the result visible for 10 seconds.
- **Lock / Unlock** calls the session endpoints.

### Approval cards

Each pending approval is a card, newest on top, with a visible countdown to `expires_at`.

Content:

- One-line plain-language reason (`reason_text`).
- What the agent wants: connector icon, `summary` in monospace (truncate at 200 characters, expandable).
- Detail view adds tier and the four signal scores.
- Buttons: **Approve**, **Approve with masking** (only if listed in `options`), **Stop**. `Stop` sends `deny_and_lock`. A smaller text link **Deny only** sends `deny`.
- Disable all buttons after a click and show a spinner until `approval.resolved` arrives.

When an approval arrives, play a short visual pulse on the card and the page title shows `(1) RepoShield`.

### Knowledge graph

Cytoscape with a compound layout (directories as parents), built from `GET /api/graph`, updated by `graph.node` and `event.created`.

| State | Colour | Extra |
|---|---|---|
| `unvisited` | Grey | |
| `accessed` | Green | |
| `masked` | Amber | |
| `held` | Blue | Slow pulse |
| `blocked` | Red | Fast pulse and a lock icon |

Never rely on colour alone: every state also has an icon and appears in the legend with its name.

Behaviour:

- Node types: directory, file, database, table, external host. Directories collapse and expand on double-click. Start collapsed to depth 1, auto-expand directories that contain non-grey nodes.
- Click a node: filter the log to that node's events.
- Layout: `cose` or `breadthfirst`, rerun only when nodes are added.
- A caption under the graph: "Green: the agent has seen it. Grey: it has not."

### Activity feed

A reverse-chronological list of `reason_text` sentences with a time, a decision icon and the decision label (Allowed, Hidden parts, Paused, Blocked). New items slide in. Clicking an item selects the event for the delta view and highlights it in the log.

### Delta view

Shown for the selected event. Fetches `GET /api/events/{id}/delta`. Two panes side by side:

- **Left: "What the agent asked for"** raw content.
- **Right: "What the agent received"** sanitized content.

Highlight each change in both panes using `changes` offsets. Colour by `kind` category (secret red, personal data amber, financial purple, code logic blue). A legend below lists categories with counts. Each highlight has a tooltip with the plain-language reason.

**Privacy of the screen itself:** the left pane is blurred by default with a "Click to reveal" overlay, because it holds raw sensitive data and the dashboard may be projected. Reveal state resets when another event is selected.

If there is no delta (for example, a blocked read), show "Nothing was sent to the agent."

Cap rendering at 200 KB. Beyond that, show the first 200 KB with a notice.

### Intervention log table

Columns:

| Column | Notes |
|---|---|
| Time | Local time, seconds |
| Connector | Icon plus name |
| Target | Truncate in the middle for long paths |
| Tier | Detail view only |
| Score | Detail view only, two decimals |
| Decision | Coloured chip with text |
| Approval | auto, approved, approved with masking, denied, timeout |
| Reason | `reason_text` (Simple view shows this as the main column) |
| Hash | Detail view only: first 8 characters of `hash` |
| Actions | **Undo** if the event has an un-rolled-back snapshot |

Features: filter chips by decision, text filter on target, row click selects the event, auto-scroll to newest unless the user has scrolled up. **Undo** asks for confirmation ("This restores the previous state. Changes made after this action may also be lost for database snapshots.") then calls rollback.

### Metrics strip

Counts of Allowed, Masked, Paused, Blocked for the selected session, plus mean pipeline latency in ms. From `GET /api/metrics/summary`, refreshed on each `event.created`.

## 5. State and data flow

Zustand store slices: `auth`, `sessions`, `events`, `approvals`, `graph`, `selectedEventId`, `filters`, `view` (simple or detail), `verifyResult`.

Startup:

1. Authenticate.
2. `GET /api/sessions`, `GET /api/events`, `GET /api/graph`, `GET /api/approvals?status=pending`, `GET /api/policy`.
3. Open the WebSocket. Apply incoming messages to the store.

**Reconnect:** exponential backoff from 1 s to 15 s. On reconnect, refetch events after the last known `seq`, pending approvals and the graph. Show a "Reconnecting" banner while disconnected.

## 6. Quality bar

- No console errors during the demo run.
- Works at 1280 by 720 (projector) and on a laptop screen.
- Contrast ratios meet WCAG AA on the dark theme.
- Every interactive element is keyboard reachable. Approval buttons have visible focus rings.
- Empty states: "No activity yet. Connect an agent to the gateway." for the log, "Nothing waiting for you." for approvals.
- Error states: failed API call shows an inline message with a Retry button, not a blank panel.
- Types for all API payloads live in `frontend/src/types.ts`, matching `04-CONNECTORS-AND-API.md`.

## 7. Suggested structure

```
frontend/src/
├── main.tsx
├── App.tsx
├── api.ts                 # fetch wrapper with token, WebSocket client
├── store.ts               # Zustand
├── types.ts
├── components/
│   ├── Header.tsx
│   ├── ApprovalCard.tsx
│   ├── KnowledgeGraph.tsx
│   ├── ActivityFeed.tsx
│   ├── DeltaView.tsx
│   ├── InterventionLog.tsx
│   ├── MetricsStrip.tsx
│   └── TokenGate.tsx
└── lib/format.ts          # time, truncation, decision labels and icons
```
