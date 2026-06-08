# Planspace & Project — Next-Step Redesign

Companion to `PHILOSOPHY.md` (the destination) and
`IMPLEMENTATION_STATUS.md` (the ledger). This document is a
**proposal for the next round of changes**; it is not a
contract until landed work has been ticked back into the
status ledger.


## 1. Why this round exists

Today's surface treats planspace as a second-class artifact:

1. **Planspace creation is a one-shot.** `POST
   /sessions/{sid}/contextspace/bootstrap` creates exactly one
   planspace plug + one binding and then never offers itself
   again. There is no way to add a second direction inside a
   project.
2. **STATUS.md / PLAN.md previews are stub text.** The tiles
   are rendered as generic `ContextNode`s; `ContextNodePanel`
   tries to lift the file body out of the agent's bundle
   text and almost always fails, falling back to *"File
   contents not embedded in this run's context — read them
   from disk."* The user never sees the actual document.
3. **Tile copy is uninformative.** The default description
   *"Context file pulled into the agent's working context."*
   leaves the user unable to tell what the file does, when it
   is updated, or whether they should edit it.
4. **`CONTEXT.md` is invisible until an agent loads it.**
   There is no UI to initialize it, refresh it, or read it.
5. **Agent panel buries the result.** "What this run
   changed" only shows `applied / proposed` counts; the
   actual STATUS delta is not visible. Tool activity stays
   fully expanded after the node finishes, which inverts the
   user's priorities (the philosophy says they only care to
   plan level).
6. **The planspace ↔ project relationship is implicit.**
   Nothing on the canvas tells the user that a planspace
   belongs to a project, that a project may have several,
   or which one a new run will write into.


## 2. Principles this round commits to

- **`PHILOSOPHY.md` §3 (Setup is concierge, not
  schema-entry).** Planspace bootstrap is an agent node that
  reads the user's natural-language motivation and writes
  the structured first STATUS.md. CONTEXT init / refresh run
  from preset prompts the user never sees.
- **`PHILOSOPHY.md` §5 (Project owns planspaces; user
  controls direction granularity).** Creating a planspace is
  always a project-rooted action. Visibility of each lane is
  a per-project view preference, persisted on the project.
- **`PHILOSOPHY.md` §4 (Banned vocabulary on primary
  surfaces).** New UI copy stays in the "direction / project
  memory / notebook" register; the word *planspace* lives
  only in Inspect.
- **`PHILOSOPHY.md` §8 (Every node output is a planspace
  state update).** "What this run changed" must visualize
  the actual state delta, not a counter. Activity is
  secondary and collapses once the node is done.
- **`PHILOSOPHY.md` §7 (CONTEXT.md is plan-free, not
  auto-derived from planspaces).** CONTEXT init/refresh are
  user-initiated only and never read planspace state.


## 3. UX changes

### 3.1 Project root menu

Selecting the project root opens `ProjectPanel`. The panel
gains a single new section, **"Project actions"**, with
three buttons:

| Action | Visible when | Effect |
|---|---|---|
| **+ New direction** | always | Opens the bootstrap composer (§3.2). |
| **Initialize project notes** | `CONTEXT.md` missing | Runs the CONTEXT bootstrap task (§3.5). |
| **Refresh project notes** | `CONTEXT.md` present | Runs the CONTEXT refresh task (§3.5). |

The legacy *"Set up project memory"* button is removed; its
job is split between **+ New direction** (creates the first
planspace) and the existing implicit binding bootstrap (now
folded into the "+ New direction" code path).

The active-direction picker (today's planspace list) moves
to its own section just below, **"Directions"**, with one
row per planspace: name, color swatch, *active* badge if
applicable, and a visibility eye-toggle (§3.4).

### 3.2 "+ New direction" — concierge bootstrap

Clicking *+ New direction* spawns a new agent node parented
to the project root, in a new (empty) planspace. The
composer is minimal:

- One **textarea** asking *"What direction are you taking?
  A paragraph is fine."*
- One **needs-review** toggle (off by default — bootstrap is
  rarely worth a gate on top).

On submit, the backend:

1. Creates the planspace plug (`bootstrap_planspace_only`,
   §4.1) with empty STATUS.md slots.
2. Adds it to the project's existing binding (or creates a
   binding on first run) and activates it.
3. Launches an agent node whose prompt is a **preset
   concierge prompt** (the user's paragraph is interpolated
   as `<user_seed>`). The prompt instructs the agent to:
   - Extract `goal`, `current_state`, and any initial
     `open_questions` from `<user_seed>`.
   - Use the standard ask-user inline gate to fill any
     load-bearing slot it cannot infer.
   - Emit a memory-delta with `add_open_question` /
     `add_decision` / `rewrite_current_state` ops.

The first agent run therefore lands as a normal tile on the
new lane, parented to project root. Its transcript is the
concierge dialogue; its diff cards (§3.6) show STATUS being
populated for the first time.

### 3.3 Direction switching (B1 semantics)

A direction is "active" only in the **write target** sense:
the next phantom composer launched anywhere will write its
planspace-update into the active direction. The canvas
continues to show all visible lanes side-by-side.

- Clicking a lane header **activates** that direction (and
  opens its file panel, §3.4).
- The currently-active lane gets a thin solid border /
  brightened accent (today's lanes are translucent —
  activation needs a stronger signal).
- The phantom composer, when spawned from empty space or
  from project root, drops on the active lane's row at the
  end of that lane (not at the global timeline cursor).
- When spawned from a finished node, it inherits that
  node's lane regardless of which is active (existing
  behavior — resume is local to its planspace).

### 3.4 Lane visibility

Each planspace lane can be **hidden** independently per
project. Hidden lanes:

- Disappear from the canvas (no lane row, no nodes, no
  cross-lane chips referring to them).
- Remain in `Project → Directions` with a *(hidden)* badge;
  unhiding restores them.
- Are still bound to the project; their STATUS still feeds
  agents that load them via the cross-lane `+ load from
  another direction` picker.

Visibility is **per-project view state**, persisted on the
`Project` record as `planspace_view: { <pid>: { hidden:
bool } }` (§4.4). This lives next to `layout_hints` because
it is a viewing preference, not a domain fact, but it
**must** be a real `Project` field rather than a
frontend-only blob so the choice survives reloads on every
client.

### 3.5 CONTEXT init / refresh

Both run from **preset prompts the user never sees**.
Neither produces a timeline node, neither writes to
`events.jsonl`, and neither shows up in the canvas. Per
`PHILOSOPHY.md` §3, CONTEXT is a quick-reference handbook
— a refresh that drifts a little is harmless because the
next agent always rereads the repo anyway.

- **Initialize.** Backend runs a one-shot LLM call with the
  *context-bootstrap* preset prompt, given the project
  root's structure (top-level tree + a few key file
  headers). The output is written to
  `<project_root>/CONTEXT.md`.
- **Refresh.** Same shape, *context-refresh* preset prompt,
  given the existing CONTEXT plus a diff of repo headers
  since the last refresh.

Both are async background tasks (§4.5). The home node panel
shows a tiny spinner + *"Refreshing project notes…"* while
in flight; on completion the file panel for `CONTEXT.md`
auto-updates. Failure surfaces as a toast in the panel.

### 3.6 Node detail — diff over transcript

The agent panel's body is reordered and re-weighted:

```
┌────────────────────────────────┐
│ [headline + state pill]        │
├────────────────────────────────┤
│ ▾ What this run changed        │   ← always expanded
│   • + Q3: "Is the streaming…"  │     when planspace-update
│   • ~ current_state            │     exists
│   • + D2: "Pick…"              │
│   ▸ raw STATUS diff (collapsed)│
├────────────────────────────────┤
│ ▸ Activity (12 calls, 412 ev.) │   ← collapsed when state
│   …                            │     ∈ {done, cancelled}
├────────────────────────────────┤
│ ▸ Thinking (3 blocks)          │   ← collapsed (today)
├────────────────────────────────┤
│ ▸ Inspect                      │
└────────────────────────────────┘
```

Concrete rules:

- **"What this run changed" renders op cards** (D3 form):
  one card per memory-delta op, color-coded by op type
  (`add_open_question`, `add_decision`, `rewrite_current_state`,
  `add_out_of_scope`, `append_observation`, …). Click to
  expand the full text of the op.
- **A "view raw STATUS diff" `<details>` block** below the
  op cards renders a unified text diff of STATUS.md
  before/after, sourced from a per-node snapshot (§4.3).
- **Activity collapses by default** when the node is in
  `done` or `cancelled`. `running / waiting / awaiting_review
  / error` stay expanded — the user needs them for live
  debugging.
- **Staged-for-review nodes** (interim delta parked behind
  a gate) show the op cards greyed out with a *"Pending
  gate resolution"* badge, plus a link to the downstream
  gate node. The cards re-render with the gate's free-form
  merge text when the gate resolves.
- **Nodes with no planspace update** (errors, opted-out)
  fall back to the existing last-assistant-text rendering.
  The Activity section in that case stays expanded so the
  user can investigate.

### 3.7 STATUS.md / PLAN.md / CONTEXT.md preview

The current `ContextNodePanel` keeps handling generic
context files. A new sibling panel — `PlanspaceFilePanel`
— handles tiles whose `source.kind ∈ {planspace,
project-root}` and reads file content from disk (§4.2)
instead of trying to extract it from bundle text.

Each preview header shows:

- **What this file is**, in one sentence:
  - `STATUS.md` — *"Notebook of decisions and open
    questions for this direction. Updated automatically
    after each run."*
  - `PLAN.md` — *"A read-only checklist derived from
    STATUS — open questions become checkboxes, decisions
    appear as completed items."*
  - `CONTEXT.md` — *"Plan-free project handbook. Loaded at
    the start of every run. Hand-edited; refresh from the
    project menu."*
- **Last updated**, sourced from:
  - STATUS / PLAN — the latest node whose memory-delta
    touched the file (link jumps to that node).
  - CONTEXT — file mtime + *"refreshed via project
    menu"* / *"hand-edited"* hint inferred from whether
    the last write came through `/context/refresh`.

The body renders the file as markdown. For STATUS, the
existing slot-aware editor moves into this panel as a
collapsible *"Edit slots"* section directly under the
markdown preview.

The redundant lane-header → `PlanspacePanel` route is
preserved (it is the same component) so users who learned
the existing path are not broken.


## 4. Backend changes

### 4.1 Split bootstrap

Today `bootstrap_project_contextspace` couples three
actions (write contextspace root + create planspace +
create binding) into one. Split into:

- `ensure_contextspace_root(store_root)` — idempotently
  writes `contextspace.yaml` and `README.md`.
- `ensure_project_binding(project, store_root,
  binding_slug=None)` — returns the existing binding for
  the project if one exists, otherwise creates a new
  binding YAML with no `active_planspace_id`.
- `add_planspace_to_binding(binding_id, *, title,
  planspace_slug=None, store_root) -> planspace_id` —
  creates an **empty** planspace plug (manifest +
  empty-slot STATUS.md + derived PLAN.md + empty
  SKILLS.md + events.jsonl). The STATUS body explicitly
  reads *"This direction is being initialized…"* so a
  half-bootstrapped state is visible.

The existing public function stays as a thin facade for
back-compat in tests.

New REST endpoint:

```
POST /sessions/{sid}/planspaces
body: { user_seed: string, needs_review?: bool }
→ { planspace_id, binding_id, node_id }
```

Server-side, this:

1. Calls the three helpers above.
2. Sets `active_planspace_id` on the binding to the new
   planspace.
3. Launches an agent node with `planspace_id=<new>`,
   parented to project root, prompt =
   `concierge_bootstrap_prompt(user_seed=...)`,
   `requires_review=needs_review`.
4. Returns the new ids and node id so the frontend can
   focus it.

### 4.2 File-read endpoint

Single read endpoint with a path whitelist:

```
GET /sessions/{sid}/files?role=status&planspace_id=...
GET /sessions/{sid}/files?role=plan&planspace_id=...
GET /sessions/{sid}/files?role=context
→ { text, mtime, last_writer: { kind: "node"|"context-refresh"|"hand", node_id?: ... } }
```

Implementation:

- `role=status`: read
  `contextspace/plugs/planspaces/<slug>/STATUS.md`.
- `role=plan`: read the sibling `PLAN.md`. (PLAN is derived;
  the file on disk is the source of truth for display.)
- `role=context`: read `<project_root>/CONTEXT.md`.
- `last_writer` for STATUS/PLAN is derived from the
  planspace `events.jsonl` tail; for CONTEXT it is read
  from a sidecar `<project_root>/.miniclaw2/context.meta.json`
  written by the refresh task (§4.5).

This replaces ad-hoc bundle-text scraping in
`ContextNodePanel` for the planspace/CONTEXT cases.

### 4.3 Per-node STATUS snapshot

Before applying a memory-delta to STATUS, the inbox
processor captures the existing STATUS text. After the
apply succeeds, it writes both to
`projects/<pid>/nodes/<nid>/status-delta.json`:

```json
{
  "planspace_id": "planspaces.abc",
  "before": "...full STATUS.md text...",
  "after":  "...full STATUS.md text...",
  "ops":    [ ... copy of applied memory-delta ops ... ],
  "applied_at": 1234567890
}
```

The frontend reads this through a new endpoint
`GET /sessions/{sid}/nodes/{nid}/status-delta` and uses it
to render the op cards (§3.6) and the raw diff.

Nodes that did not modify STATUS (errors, opted-out, no
active planspace) simply have no `status-delta.json` —
absent means "no planspace impact".

### 4.4 `Project.planspace_view`

Add to `domain.Project`:

```python
planspace_view: dict[str, PlanspaceViewPref] = {}
# PlanspaceViewPref: { hidden: bool }
```

Persisted in `project.json` next to `layout_hints`. The
existing `PATCH /sessions/{sid}/layout-hints` endpoint
gains a sibling:

```
PATCH /sessions/{sid}/planspace-view
body: { planspaces: { <pid>: { hidden: bool } } }
```

The graph builder reads `planspace_view` and drops hidden
lanes from `buildGraph`.

### 4.5 CONTEXT refresh as out-of-band task

A new background-task runner (`backend/miniclaw2/context_refresh.py`)
holds the preset prompts and runs them as **one-shot
provider calls outside the NodeRunner state machine**:

- `POST /sessions/{sid}/context/init` — runs only if
  `CONTEXT.md` does not exist.
- `POST /sessions/{sid}/context/refresh` — runs only if it
  does.

Both:

1. Build a small repo digest (top-level tree, a handful of
   key file headers).
2. Issue a single provider call with the preset prompt.
3. Write the result to `<project_root>/CONTEXT.md` (atomic
   tmp+rename).
4. Update `<project_root>/.miniclaw2/context.meta.json`
   with `{ updated_at, source: "init"|"refresh" }`.

Concurrent calls return `409`. In-flight state is exposed
on `GET /sessions/{sid}/contextspace`:

```json
"context_refresh": {
  "running": true,
  "started_at": 1234567890
}
```

The frontend polls or listens to a `context_refresh`
WebSocket envelope (project-level WS already exists) to
update the spinner.

Per philosophy §3 these tasks are deliberately **not**
nodes — they are setup actions, not delegated work the
user reviews.

### 4.6 Vocabulary

The `concierge_bootstrap_prompt`, `context_init_prompt`,
and `context_refresh_prompt` live in
`backend/miniclaw2/prompts/` as plain `.md` files loaded at
startup. Treating them as data (not code strings) makes
iterating on the wording reviewable without recompiling.


## 5. Frontend changes

### 5.1 New / changed panels

- **`ProjectPanel.tsx`**: section reordering (§3.1);
  remove `onBootstrapContextSpace`; add `onNewDirection`,
  `onContextInit`, `onContextRefresh`, `onToggleLaneVisibility`.
  *"Active project memory"* renamed to *"Directions"*.
- **`PlanspaceFilePanel.tsx`** (new): file preview +
  collapsible slot editor (§3.7). Fetches via
  `/sessions/.../files?role=...`. Replaces
  `ContextNodePanel` for `kind ∈ {planspace, project-root}`
  tiles.
- **`AgentPanel.tsx`**: collapse Activity when terminal
  (§3.6); new `<PlanspaceDeltaCards>` component reading
  `/sessions/.../nodes/.../status-delta`; `<RawStatusDiff>`
  inside a `<details>` element below the cards.
- **`SidePanel.tsx`**: route `kind=="planspace"` selection
  and `kind=="context" && source.kind ∈ {planspace,
  project-root}` to `PlanspaceFilePanel`.

### 5.2 Composer

- **`PhantomNode.tsx` / Composer**: new "concierge"
  variant when launched as planspace bootstrap. Single
  textarea, no resume picker, no cross-lane loads picker
  (this is the first node of a new direction).
- Phantom anchoring: when the active direction changes,
  the empty-canvas phantom drops on the active lane's
  trailing column rather than the global cursor.

### 5.3 Lane visibility

- **`PlanspaceLaneNode.tsx`**: header gains an
  eye-toggle. Toggling calls
  `PATCH /sessions/{sid}/planspace-view`.
- Hidden lanes do not render; `layout.ts`'s
  `collectPlanspaceOrder` filters them.
- `Project → Directions` shows hidden lanes with a "show"
  button so the user can recover them.

### 5.4 Tile copy

`ContextNodePanel.plainLanguageDescription` is no longer
the front-line for STATUS / PLAN / CONTEXT (those go to
`PlanspaceFilePanel`). The generic copy for "Context file
pulled into the agent's working context." stays for
non-planspace / non-CONTEXT files (which are rare).

The home-node tooltip changes from *"Project · {title}"*
to *"Project · {title}. Click to manage directions and
project notes."* so the action is discoverable without
clicking.


## 6. Migration

- **Existing single-planspace projects** continue to work
  unchanged: the existing binding becomes the project's
  binding, the existing planspace becomes its only
  direction, `planspace_view` is empty (all visible).
- **`bootstrap_project_contextspace`** stays as a thin
  facade so existing tests
  (`test_planspace_status_api.py`,
  `test_contextspace_api.py`) keep passing. New code uses
  the split helpers directly.
- **Old "Set up project memory" button** is removed in the
  same change that introduces *"+ New direction"*. The
  empty-state copy is updated to *"This project has no
  directions yet. Start one to give the agent a notebook
  of plans and decisions."*
- **Nodes created before §4.3 lands** have no
  `status-delta.json`. The agent panel falls back to the
  current `applied / proposed` counter rendering for
  those, so the UI does not blank out historical nodes.


## 7. Open / deferred

- **Archive / delete planspace.** Not in this round. Hide
  via §3.4 visibility instead.
- **Cross-project planspace sharing.** Out of scope; the
  contextspace model already supports it server-side, but
  the workspace UI for forks is not built (`IMPLEMENTATION_STATUS.md`
  §9).
- **CONTEXT refresh history.** Per §3.5, refresh is fire-
  and-forget. If a user later wants to see what changed,
  they read git history of `CONTEXT.md`. We do not store
  per-refresh diffs in `.miniclaw2/`.
- **Visibility persistence in contextspace.** We keep it
  on the project per §3.4. Reconsider only if multi-client
  visibility-sync becomes a real ask.


## 8. Test plan (shape)

- **Backend.**
  - `add_planspace_to_binding` is idempotent on slug
    collision and leaves existing planspaces intact.
  - `POST /sessions/{sid}/planspaces` launches an agent
    node with the new `planspace_id` and a non-empty
    bootstrap prompt.
  - `GET /sessions/{sid}/files?role=status` returns the
    on-disk text and the latest-writer node id.
  - `status-delta.json` is written iff the memory-delta
    successfully mutates STATUS (errors / opt-outs
    produce no file).
  - `POST /sessions/{sid}/context/refresh` is serialized
    (concurrent calls return 409).
- **Frontend.**
  - "+ New direction" composer creates a planspace and
    immediately selects the bootstrap node.
  - Switching active direction moves the empty-canvas
    phantom to that lane.
  - Hiding a lane removes its nodes / chips and persists
    across reload.
  - `PlanspaceFilePanel` renders STATUS markdown and
    shows the slot editor.
  - Done agent's Activity section starts collapsed; op
    cards expand to show full op text.
