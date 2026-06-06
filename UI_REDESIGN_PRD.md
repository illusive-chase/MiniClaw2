# UI/UX redesign PRD — graph-driven MiniClaw2

This document superseded the old frontend surfaces (`Chat / Context /
Tests` switcher, `+ Node` modal, six-tab `NodeDetail`,
`ContextSpacePanel`). It does **not** change the backend domain model,
the on-disk store, or the WebSocket protocol. Everything here is a
frontend reskin + interaction overhaul over the same APIs.

**Implementation status.** The first implementation slice is now in the
codebase: React Flow canvas, project-root node, agent/gate/op nodes,
artifact nodes, context nodes, loads/produces/reviews/resume edges,
phantom composer, projects landing page, Tests modal, and polymorphic
`SidePanel` are implemented. Still pending from this PRD: op-as-edge
chevrons, inline gate expansion directly inside the canvas tile,
schema-aware review forms, persisted backend `layout_hints`, removing
all schema words from primary surfaces, phantom future scenario steps,
and remaining header/menu polish from §11.

Read this top-to-bottom — the *derivation* (§1, §2) is load-bearing.
Future contributors should be able to evaluate new UI proposals
against the principles here, not against the surface designs alone.


## 1. Background — what was wrong with the old UI

Two distinct failures motivate this redesign.

### 1.1 The UI is confusing because it exposes the schema

The old frontend treated the backend ontology as user-facing
vocabulary. Concretely:

- The launch surface was `NodeLaunchModal`, a form with three controls:
  a prompt textarea, a "Resume from" `<select>`, and an "Output
  contract" `<select>` whose options are the literal enum values
  `freeform` / `summary` / `interface` / `review_brief`.
- `NodeDetail` shipped six tabs (`summary | transcript | diff | events |
  settings | gate`). The `summary` tab itself is a `<dl>` of internal
  fields: Provider, Acceptance, Verdict, Bundle, Provider session,
  Provider turn, plus two collapsible `<details>` for "System context
  (NNNN chars)" and "Output contract (NNNN chars)".
- The `Context` top-level view (`ContextSpacePanel`) rendered the
  ContextSpace storage schema verbatim — a list of *Bindings*, each
  containing *Plugs* of kinds *planspace / skill / global*, with a
  resolved-binding `<select>` and an active-planspace `<select>`.
  None of these words have meaning to anyone who has not read
  `backend/miniclaw2/`.

A user is forced to learn the implementation before extracting any
value. This violates DESIGN §1.1 ("investigation-free interface")
directly.

### 1.2 The UX is form-filling because actions live in modals

Every meaningful action is gated by a modal or a form:

- Launching a node = open modal, fill three controls, click Launch.
- Resuming a node = pick the source in a dropdown inside the same
  modal.
- Reviewing a gate = pick `write-json` / `no-op` radio, type a path,
  paste JSON in a textarea, optional notes, Submit.
- Switching context = two `<select>` dropdowns above a schema panel.

The main canvas has *no input box at all* — the "Chat" surface is
read-only. The user spends their attention authoring strings inside
detached modals rather than acting on the thing they are looking at.


## 2. Core principles — the derivation

These are the principles the redesign is grounded in. They came out
of a back-and-forth and are the part to preserve verbatim.

### 2.1 Two analogies that bound the solution

- **Git IDE extensions** (GitLens, the JetBrains git lane, the VS Code
  graph view): the underlying object (a commit graph) is genuinely
  complex, but the surface is a *direct-manipulation diagram* — lanes,
  hunks, hover-blame. The user reads the diagram the way they read the
  domain. Translating this to MiniClaw2: when the underlying object is
  a node graph, the surface should *also* be a node graph, not a list
  of tabs over the same data.
- **Mind-map apps** (mindnode, miro): adding a node never opens a
  modal. Hovering an existing node reveals a transparent next-node
  hint *in the graph itself*; clicking it creates the new node *at the
  position the hint occupied*. Authoring is direct, not deferred.
  Translating: the composer is not a docked bar; it is a phantom node
  that materializes where the next node will appear.

### 2.2 The principle, stated

> **The graph *is* the state.** Anything the user can reason about
> (a run, an artifact, a piece of context, a review handoff) exists
> as a node. Anything the user can reason about relationally ("this
> resumed from that", "this loaded that file", "this produced that
> artifact") exists as an edge. The side panel inspects the selected
> node. The canvas does everything else.

Corollaries:

- **If a concept cannot be a node or an edge, it is probably not
  worth surfacing.** It belongs in an `Inspect▸` drawer or nowhere
  at all.
- **A surface that requires reading a tab label to understand what it
  shows has already failed.** Selection drives polymorphism — the
  side panel's shape is a function of the selected node's kind.
- **No modal in the steady-state UI.** Modals are reserved for
  exceptional flows (creating a project, confirming destruction).
  Authoring a run is done in the canvas.

### 2.3 Two-axis visual grammar

To make a populated graph scan-readable, we separate two orthogonal
signals:

- **Shape encodes *kind*** (what the thing is ontologically). Stable
  across state changes.
- **Color encodes *state*** (how it is going). Reuses the existing
  `state-*` theme tokens (`state-running`, `state-waiting`,
  `state-done`, `state-error`, `state-review`).

A user scans the canvas and reads both axes at once — "two
hexagons, one amber, one green" decodes as "two review gates, one
waiting, one approved." Hovering any node yields a one-line plain
language explanation of the kind, so the legend never has to be
memorized.


## 3. Visual grammar — the specifics

### 3.1 Node shapes

| Kind | Shape | Rationale |
|---|---|---|
| **Agent run** | Rounded rect, "tile" body, ~200x110 | The primary work unit; needs room for prompt preview |
| **Artifact** (`result.md`, `result.json`, `brief.md`, review-response JSON) | Document card with folded corner | Reads as "a file" universally |
| **Context** (planspace, binding, injected `CONTEXT.md`, loaded file) | Layered/stacked card | Reads as "something attached / inherited" |
| **Gate** (passive review checkpoint) | Hexagon | Distinct from rounded agent tiles; reads as "stop and decide" |
| **Op** (commit, fork) | Small chevron *on the connecting edge*, not a free-standing node | A commit is the transition between two repo states; it lives between agents, not beside them |
| **Project root** | Anchored "home" glyph in canvas corner | Origin of the graph |
| **Phantom** (composer-staging, future scenario steps) | Dashed outline of any of the above shapes | "Not yet real" |

Op-as-edge-chevron is intentional. It violates "everything is a node"
strictly, but it preserves the *experience* the principle is aiming
for (mechanical bookkeeping should not eat horizontal real estate).
Hovering the chevron yields a popover with the commit hash and diff
stats; clicking it expands an inline diff. Power users may toggle
"expand ops to full nodes" via a project setting if they want the
legacy op-node view back.

### 3.2 Edge styles

| Edge | Visual | Means |
|---|---|---|
| `timeline` | Solid spine, thicker | FS-ordering between adjacent nodes |
| `resume` | Solid arrow with `↻` mid-glyph | Provider conversation continuation |
| `produces` | Thin solid arrow | "This agent wrote this artifact" |
| `loads` (context) | **Dashed** thin line | Acausal carryover; auto-hidden unless an endpoint is hovered/selected |
| `reviews` | Solid arrow into a gate node | Gate inspects the upstream brief |
| `commits` | Chevron sitting *on* a timeline edge | Op is the transition, not a peer |

Dashed = "reference, not causal step." Standard convention in graph
IDEs (Bazel, Dagster) and reduces clutter dramatically when many
context nodes are present.


## 4. Where the "as-a-node" insight applies — the brainstorm

This is the brainstorm distilled to keep. Each item is a concrete
re-projection of an existing concept into the node-or-edge ontology.

1. **Context loadout becomes a node lane.** Each loaded file, each
   active planspace, each binding is a layered-card context node.
   They live in a thin **context lane above** the timeline, auto-
   hidden when no agent endpoint is hovered. When an agent runs,
   dashed `loads` edges from the agent to the exact files it pulled
   in light up on selection. Switching planspaces is click-on-the-
   planspace-node-to-activate, not a dropdown. The
   `ContextSpacePanel` top-level view goes away.

2. **Artifacts become first-class nodes.** `result.md`, `result.json`,
   `brief.md`, and reviewer-written JSON files are sibling nodes
   connected by `produces` arrows from their authoring agent. Two
   wins: (a) selecting the artifact opens its content directly,
   matching the user's mental model; (b) a downstream agent that
   consumes the artifact gets a visible `loads` edge into it, so
   "what flowed into this run" is on the canvas.

3. **The review brief is just an artifact.** Currently the brief is
   a string on the gate's `contract` field, surfaced only inside the
   `gate` tab. Reframe as: `agent → produces → brief.md → reviews →
   gate → produces → review.json`. The user reads the brief by
   clicking its node; the gate node only holds the response form.
   The data flow is legible without reading a tab label.

4. **Commit ops collapse onto the edge.** See §3.1. Auto-commit nodes
   stop eating timeline width but stay visible as the chevron with
   hover popover + inline diff on click.

5. **Inline gates expand the running agent tile in place.** Permission,
   ask-user, and plan-approval requests are *substates of a running
   agent*, not separate things — promoting them to peer nodes would
   be noisy. Instead, the agent tile **expands in place** on the
   canvas, rendering the question + answer chips directly where the
   user is already looking. (Confirmed: tile-expansion, not a side
   panel pending block.)

6. **Errors as terminal nodes.** When an agent errors, a small
   red-edged terminal node appears downstream with the error text.
   Retries draw a resume edge back to the parent, so "I retried
   after the auth failure" is a visible loop instead of a red
   banner inside a summary tab.

7. **Memory deltas as inbound arrows.** A run that updates a
   planspace draws an arrow from the agent back into the relevant
   context node, glyphed `+Δ`. The user sees memory accreting
   visually instead of reading "Memory delta: applied 2, proposed 5"
   in a settings panel.

8. **Phantom future scenario steps.** (Deferred — see §9.) When a
   scenario YAML is running, not-yet-instantiated steps could
   render as dashed phantoms ahead of the cursor. Solidify as they
   run. Loops and `on_state` branches show as alternate paths. Out
   of scope for v1 because of branching-display complexity.


## 5. Surface designs

### 5.1 Canvas

A **large 2D canvas** with pan/zoom (React Flow as the engine —
mature custom-node API, built-in edge routing, no constraint against
it).

Layout:

- Timeline lane runs left→right horizontally in the canvas middle.
- Context lane sits as a thin strip above the timeline.
- Artifacts cluster to the right of their producing agent.
- Project root anchored at the leftmost canvas position.
- Auto-layout uses an **append-don't-reflow** strategy so new nodes
  do not shove existing layout. Manual nudges are persisted into
  `project.json` as `layout_hints: {node_id: {x, y}}`. (Backend
  change is purely additive — a new optional field; no migration
  needed.)
- Pan/zoom state is per-project, stored client-side.

### 5.2 Composer = phantom node

The composer is not a docked bar. It is a phantom dashed-outline
node that materializes:

- **Hover a finished agent** → a dashed phantom appears to its right
  with a ghost `+`. Clicking it focuses the phantom for input.
- **Tap an empty area of the canvas** → a phantom appears at the
  click position (handles the "I want to start fresh / a new branch"
  case without forcing the user to hover an existing tile first).
- **Brand new project** → one center-anchored phantom invites the
  first run.

When the phantom is focused:

- A small intent-chip floater sits just above it: `Explore` /
  `Build & summarize` / `Hand off for review`. A `⋯` menu hides the
  rarely-used `interface (JSON)` chip — kept as top-level-accessible
  (per the user's request) but tucked under the menu so the primary
  row stays focused.
- Resume source is **implicit in which tile spawned the phantom**.
  If spawned from a finished agent's `+` handle, the new tile
  resumes from that agent. If spawned from an empty area, it starts
  fresh. No "Resume from" dropdown exists in the UI.
- A small chip above the phantom shows `↻ continuing from "Build
  calculator"` when applicable, with an `x` to clear (which converts
  the phantom into a fresh-start phantom in place).

On submit:

- The phantom solidifies into a real running tile at the same
  canvas position. No animation discontinuity. WebSocket starts
  streaming `text_delta` into the tile body.

### 5.3 Polymorphic side panel

The side panel's shape depends on the kind of selected node. This
collapses the old six-tab fixed layout.

- **Agent** → progressive single panel:
  - Headline: state pill + first-line prompt + optional `↻ continuing
    from …`.
  - **Result**: if `output_kind != freeform` and the artifact exists,
    render it (markdown / JSON preview). Otherwise the latest
    assistant text.
  - **Activity**: tool calls and transcript turns, tools collapsed by
    default.
  - **Pending**: only renders when an inline gate is open. Mirrors
    the data of the tile-expansion gate UI for users who'd rather
    answer from the side panel.
  - **Inspect▸**: one collapsed disclosure absorbing raw events,
    `settings_snapshot`, `system_context_snapshot`,
    `output_contract_snapshot`, and the context bundle source list.
    The words `binding`, `plug`, `planspace`, `bundle` live here and
    nowhere else.
- **Artifact** → the rendered file content + path + size + "produced
  by" link + "used by" links.
- **Context node** → file content + char count + a plain-language
  one-line explanation of the kind ("This is your project planspace;
  it is appended to every agent's system prompt") + a list of
  agents that loaded it.
- **Gate** → the inline response form. The brief is the upstream
  artifact node; users read it by clicking that node.
- **Op (chevron)** → typically inspected via the on-edge popover.
  If selected, shows commit hash + short diff stat.
- **Project root** → project settings (provider, auto-commit,
  scenario name), with the planspace activation reachable here too.

### 5.4 Header

Becomes thin:

- `← Projects` back-button.
- Project name + ws status.
- `Stop` (only when streaming).
- A `⋯` menu absorbing `Simulate WS drop`, theme toggle, and any
  future debug actions.

The `Chat / Context / Tests` switcher is gone:

- Chat is the side panel of the selected agent.
- Context is the lane on the canvas.
- Tests becomes a small entry on the projects landing page.


## 6. What we remove from the frontend

- Top-level `Chat / Context / Tests` switcher.
- `+ Node` button in the header.
- `NodeLaunchModal` entirely.
- `ContextSpacePanel` as a top-level view.
- Six-tab `NodeDetail`. Replaced by the polymorphic panel.
- `Settings` tab as a peer of `summary`. Folds into Inspect▸ and
  the project root side panel.

Words that should not appear on any primary surface:

- `node` (as a noun the user reads)
- `kind`
- `output contract`
- `binding`, `plug`, `planspace`, `bundle`
- `provider session`, `provider turn`
- `verdict`, `acceptance`

These all remain inside the Inspect▸ drawer and in the on-disk
representation.


## 7. Risks and non-obvious things

- **Graph clutter.** A 30-node project with full context lane,
  artifacts, and edges can get noisy. Mitigations baked into the
  design:
  - `loads` dashed edges auto-hide unless an endpoint is hovered or
    selected.
  - Context lane auto-clusters behind a "12 context files ▸"
    disclosure node beyond a threshold.
  - "Zoom to fit / zoom to selection" is a one-key action.
- **Layout stability.** Adding a new node must not shove the
  existing layout. The autolayout is incremental, not global. Manual
  drags are persisted.
- **Stable identity for context nodes.** Today, context files come
  from binding/plug paths; the visual node needs a deterministic id
  derived from the source path so it survives reloads. Use the
  existing `ContextBundle.sources[].path` + `scope` + `kind` as the
  identity tuple.
- **Artifact node materialization is the largest frontend touch.**
  The data already exists (`output_path`, `GET
  /sessions/{sid}/nodes/{nid}/artifact`). We just materialize a
  client-side node per known artifact path. No backend schema
  change.
- **React Flow performance.** With 100+ nodes and many edges,
  default React Flow can lag. Plan: virtualized edges, custom node
  memoization, debounce on the `node_updated` stream.


## 8. Decisions taken in this PRD

These were resolved in conversation and are no longer open:

- **Composer surfacing**: conditional, not docked. Hover-tile and
  empty-canvas-tap both produce a phantom.
- **Canvas style**: large 2D canvas (pan/zoom) over React Flow.
- **`interface` (JSON) output kind**: kept reachable from the
  composer's `⋯` menu, not in the primary intent-chip row.
- **Context as nodes**: yes — replaces `ContextSpacePanel` entirely.
- **Op visualization**: chevron on the connecting edge, not a peer
  node.
- **Context lane placement**: thin strip above the timeline; dashed
  `loads` edges auto-hide unless an endpoint is hovered/selected.
- **Inline gates**: tile-expansion in place on the canvas.
- **Phantom scenario steps**: deferred to a later phase.
- **Schema-aware review form generation**: deferred; the v1 review
  panel keeps the JSON textarea inline on the gate node.


## 9. Out of scope / deferred

- Phantom future scenario steps (§4 item 8). Aspirational; complex
  when scenarios branch on `on_state`. Revisit once the basic
  graph-driven surface ships.
- Schema-aware review-form generation from `# Response schema`
  blocks in briefs.
- Cross-project / fork visualization (DESIGN §2.3 `fork` edges).
  Single-project focus first.
- A backend graph layout solver. Layout stays client-side; backend
  only stores `layout_hints` as an opaque map.


## 10. Success criteria

- A first-time user can launch an agent step, watch it run, accept
  its result, and start a follow-up step **without reading a
  tooltip and without seeing the words `node`, `contract`,
  `binding`, or `planspace`**.
- A returning user moves an `agent → result → review → follow-up`
  loop using **zero modals** — composer + tile affordances + inline
  gate + canvas selection.
- An engineer debugging a failed run can reach raw events, settings
  snapshot, and the context bundle source list in **≤2 clicks** from
  the relevant node, via the Inspect▸ drawer.
- A user looking at the canvas can answer "what context did this
  run use?" without opening any panel — the dashed `loads` edges
  light up on hover/selection.


## 11. Sequencing for implementation

Original suggested order, now annotated with implementation status:

1. [✓] **React Flow canvas**, rendering the project graph over existing
   REST / WebSocket APIs.
2. [✓] **Polymorphic side panel** for agents, gates, artifacts,
   context nodes, ops, and project root.
3. [✓] **Composer-as-phantom** replacing `NodeLaunchModal`.
4. [~] **Artifacts as nodes** with `produces` edges and brief nodes.
   Result and brief artifacts are graph nodes; reviewer-written JSON is
   written to the workspace but is not yet always materialized as its
   own graph node.
5. [✓] **Context lane** with context nodes + dashed `loads` edges.
   Top-level `Context` tab removed.
6. [ ] **Inline gate tile-expansion** for permission / ask / plan.
   Current implementation renders pending inline gates in `AgentPanel`.
7. [ ] **Op chevron on edges** replacing op tiles. Current
   implementation still renders op nodes.
8. [✓] **Header cleanup** + removal of the `Chat / Context / Tests`
   switcher.
9. [ ] **Errors as terminal nodes** + memory-delta arrows. Current
   implementation shows errors in the selected agent panel and memory
   delta results in `Inspect`.
