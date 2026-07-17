# MiniClaw2 Philosophy

This document is the source of truth for *what MiniClaw2 is trying to
be*. It describes the destination, not the distance to it.
`IMPLEMENTATION_STATUS.md` is the companion ledger of how far the
current code has travelled.

When this document and the code disagree, the document is the position
to argue from — the code is the position to fix toward. Where past
proposals were not adopted, they are not preserved here.


## 1. What MiniClaw2 is for

MiniClaw2 is a **graph IDE for human-supervised LLM workflows**. It is
not a nicer chat UI for one vendor.

Three commitments follow from that framing:

- **The atom of computation is a session, not a turn.** A session is
  the smallest unit a researcher is willing to delegate before checking
  in. Turn-level granularity makes the user a co-pilot of the model;
  session-level granularity makes the model an assistant the user
  reviews.
- **The interesting events are human gates, not model output.** A node
  matters because it is *blocked waiting on you* or because it produced
  something you need to validate. Streaming text matters less than
  decision boundaries.
- **The substrate is heterogeneous.** Filesystem state flows linearly,
  conversation state can fork or resume, configuration/memory can be
  inherited acausally. Each edge type is distinct — the graph makes
  those distinctions readable instead of collapsing them.


## 2. Investigation-free interface

A user of MiniClaw2 is never asked to investigate internals to decide
whether the system worked. They are given (a) what to do and (b) what
they should see; success is "the observed effect matches the
description." They may know nothing about the node graph, the event
log, the state machine, the gate kinds, or the provider adapters. The
framework's job is to be **inspectable when something fails**, not to
be **required reading when things work**.

Two surfaces inherit from this principle:

- **Product UX.** A user supervising work interacts with node tiles,
  gate prompts, and produced artifacts. They never need to read
  `events.jsonl`, distinguish inline gates from checkpoint gates, or
  reason about commit-op rewrites in order to know whether their work
  landed. The Inspect drawer is *available* for diagnosis, not
  *required* for usage.
- **Validation.** A demo passes when the produced artifact behaves as
  its brief promised. Internal correctness — that a gate routed to the
  right runner, that auto-commit rewrote `commit_after`, that
  reconnect-replay reconstructed the right stream — is *implied* by
  the visible outcome. If an internal path is broken, the user-visible
  artifact will be broken too, and that is the signal to ground on.


## 3. Setup is concierge, not schema-entry

A user of MiniClaw2 should never be asked to learn the system's
schemas in order to start using it. Forms — "fill in a goal, a
current_state, a list of open questions" — assume the user already
knows what the framework needs and how each slot should read. That
assumption is wrong on first contact and stays wrong every time the
schema grows.

The default for any setup-shaped flow is therefore an **agent acting
as a concierge**, not a form acting as a gatekeeper. The user speaks
their intent in natural language; the agent owns the schema, drafts
sensible defaults, and reaches back through the standard ask-user
inline gate when a load-bearing slot is missing.

Two concrete commitments follow:

- **Planspace initialization is an agent node.** The user provides a
  paragraph of motivation. The agent (category=planning) drops a
  starter graph of three to five virtual nodes onto the lane — rough
  motivations, prompt drafts, reasonable dependencies. The user sees
  concrete next steps the moment bootstrap finishes; they may edit,
  delete, or add. If the seed leaves a load-bearing question
  unanswered, the agent asks rather than guessing.
- **Project CONTEXT initialization and refresh run from preset
  prompts.** The user does not author the prompt; the framework
  holds it. CONTEXT is a quick-reference handbook, not a contract —
  the agent always rereads the repo on the next run, so light-touch
  refresh is enough.

The decision remains the user's — *"is there a meaningful new
direction?"* is still answered by the user clicking *new direction*.
The *structuring* of that decision into schema is the agent's job.
The user is in command; the agent is the typist.

Forms stay valid for direct-manipulation actions the user already
understands (dragging tiles, clicking lanes, resolving gates, typing
a follow-up prompt). The concierge model applies to flows where the
user cannot reasonably be expected to know what the system needs.

This principle is symmetric with §2: when something works, the user
does not have to read internals; when something is being set up, the
user does not have to learn schemas. The framework absorbs both
costs.


## 4. The graph is the state

The principle is **direct manipulation**, borrowed from two analogies:

- Git IDE extensions (GitLens, JetBrains' git lane, VS Code's graph
  view): the underlying object is a commit graph; the surface is *also*
  a commit graph. The user reads the diagram the way they read the
  domain.
- Mind-map apps (mindnode, miro): adding a node never opens a modal.
  Hovering an existing node reveals a transparent next-node hint *in
  the graph itself*; clicking it creates the new node *at the position
  the hint occupied*.

Stated:

> Anything the user can reason about (a run, an artifact, a piece of
> context, a review handoff) exists as a node. Anything the user can
> reason about relationally (this resumed from that, this loaded that
> file, this produced that artifact) exists as an edge. The side panel
> inspects the selected node. The canvas does everything else.

Corollaries:

- **If a concept cannot be a node or an edge, it is probably not worth
  surfacing.** It belongs in an Inspect drawer or nowhere at all.
- **A surface that requires reading a tab label has already failed.**
  Selection drives polymorphism — the side panel's shape is a function
  of the selected node's kind.
- **No modals in steady-state UI.** Modals are reserved for exceptional
  flows (creating a project, confirming destruction). Authoring a run is
  done on the canvas.

**Words that should not appear on primary surfaces.** `node`, `kind`,
`output contract`, `binding`, `plug`, `planspace` (as a noun the user
reads), `bundle`, `provider session`, `provider turn`, `verdict`,
`acceptance`. These are accurate at the schema level and live inside
the Inspect drawer. On the canvas the user reads tiles, hexagons,
documents, and lanes — not vocabulary.


## 5. Three-layer scope hierarchy

| Scope | What it holds | How it's maintained |
|---|---|---|
| **project** | Plan-free reference: principles, philosophy, current-state-of-the-world facts. `CONTEXT.md` is the textual form. | Hand-edited by user; not auto-derived from planspace activity. |
| **planspace** | An ordered collection of executed and virtual nodes belonging to one direction. | Updated by adding, mutating, or obsoleting node previews. |
| **node** | One step in one planspace. | Reads the lane's projected filesystem on launch; writes its own preview on completion (and may write new virtual previews). |

Two consequences:

- **Planspace does not auto-deliver to project.** Project `CONTEXT.md`
  remains a plan-free reference for any agent reading the code; the
  user is the only mechanism by which planspace outcomes ever
  influence project-level CONTEXT.
- **User control granularity stops at planspace boundaries.** The user
  decides "is there a meaningful new direction?" (create planspace) and
  "has this direction completed its goal?" (close planspace). The user
  does not micromanage nodes within a planspace; that is the agent's
  job.


## 6. Domain model

Three primary objects. Everything else is a view over these.

### 6.1 Node

A node is one provider-backed session or one programmatic state
mutation. Two axes: **kind** (how it executes) and **category**
(its semantic role in the plan).

**Kind:**

- **agent** — runs a provider with options assembled from project
  settings, CONTEXT.md, and per-node overrides. A normal agent
  node starts a fresh provider session; timeline adjacency means only
  filesystem ordering, never conversation continuation. Inline human
  gates (permission, ask-user, plan-approval) pause the session;
  resolving the gate continues the same session.
- **op** — a non-agent, fast, always-immediate state transition that
  appears on the timeline so the project's full mutation history is
  visible. Commit ops may be framework-injected or manually requested;
  pull ops run `git pull --rebase`. Successful Git ops fold into derived
  commit hubs, while failed ops remain selectable tiles.

There is no `gate` kind. The gate concept is preserved as a category
(see below) — a virtual review node, agentic or human-interact.

**Category** (orthogonal to kind, applies to agent nodes):

- **planning** — may write its own preview AND propose, mutate, or
  obsolete virtual nodes in the same lane.
- **regular** — may only write its own preview. Virtual writes by
  regular nodes are rejected at reap (re-prompted). Regular nodes
  execute work, not plan.
- **review** — normally like planning and may reshape the plan via virtuals.
  Reviews carry a structured brief
  (`check_what` / `expected` / `abnormal`) written by the proposer.
  Provider-backed subtypes:
  - **agentic_review** — runs the reviewer agent against the brief
    and the upstream's preview/transcript/artifacts.
  - **human_interact_review** — at promotion, pauses in an
    `awaiting_human_input` substate to collect free-form prose from
    the user. The reviewer agent is then launched with brief +
    upstream + human prose, synthesizes a preview.
  - **code_review** — gates the uncommitted ghost before it becomes a
    commit. The provider's native reviewer inspects the working tree while
    the scheduler holds exclusive workspace access; the framework snapshots
    the reviewed diff and synthesizes a report-only preview. It does not
    materialize a lane or mutate virtuals, and its brief/focus is optional.

Questions, decisions, and out-of-scope items are not separate
categories; they are things a node may *say in prose* inside its
preview.

A node may exist in a *virtual* substate — declared but not yet run.
Virtual nodes carry a category, motivation, prompt_draft, optional
review brief, scheduled dependencies, and provenance (`proposed_by`).
There are no `declared_loads` or `declared_produces` fields; reads
and writes are observed via the transcript at reap, not
pre-declared. Virtuals are editable in place (user or agent) and
may be obsoleted without ever running. Promotion is the user
clicking the tile (manual mode) or the framework auto-promoting
once all dep parents are terminal (auto mode).

The virtual subgraph within a lane is a DAG keyed on
`scheduled_deps`, not a chain. Multiple virtuals can share parents;
convergence virtuals can have multiple parents. The DAG governs
promotion eligibility only. Promotion persists an eligible node as
`queued`; a deterministic scheduler starts queued nodes in
`(created_at, id)` order while the project has capacity.

A running session may oscillate between active execution and a
paused substate as inline gates open and close. Human-interact
review nodes additionally pass through `awaiting_human_input`
before the reviewer agent launches. Op nodes have no intermediate
states.

### 6.2 Project

A workspace is `(folder + graph of nodes)`. One worktree per project;
`Project.concurrency` is a positive integer (default `1`) that caps how
many nodes may actually run at once. Queued nodes do not occupy a slot.
Lowering the limit never cancels active work; raising it immediately
fills newly available slots.

Nodes running concurrently intentionally share the source worktree.
This enables collaborative edits but does not provide filesystem or Git
isolation: agents can observe each other's partial changes, make
conflicting edits, or race on repository-wide operations. Dependency
edges and human supervision are the coordination mechanism; projects
that require hard isolation should still use separate worktrees/forks.
Native code-review nodes are the deliberate exception: the scheduler drains
the pool to them and holds the workspace exclusively so the ghost commit is a
stable review target.

Each node records the pre- and post-state of the worktree, so the
timeline can show a project-state diff per node.

### 6.3 Edges (derived, not stored)

Edges are read off node/project fields and Git, not modelled as separate
records. Six relations matter:

- **dep** — virtual's `scheduled_deps`. The planning DAG, rendered
  as the primary edge.
- **commit** — FS state reified as derived commit hubs on the project
  baseline. The trunk joins project root, referenced commits, and an
  uncommitted ghost; source/sink edges attach each same-`commit_before`
  epoch to that trunk. Git owns commit existence and metadata, node records
  own their historical before/after SHAs, and op nodes own action history.
  Hubs and commit edges are views, never stored records.
- **resume** — explicit provider conversation continuation.
- **reviews** — derived: a review virtual's `scheduled_deps` pointing
  at the upstream node it inspects, plus `category=review`.
- **loads** — CONTEXT.md only. Every executed node implicitly loads
  project CONTEXT.md (the provider injects it); rendered as edges
  from the project's CONTEXT context node down to every tile. No
  other markdown participates in loads — STATUS.md and PLAN.md are
  retired.
- **produces** — a terminal node's explicitly published artifacts.
  Rendered from the producing node to each artifact tile.

Out of scope: `fork` (cross-project worktrees) remains as a future
direction; not part of the current ontology. Agent-to-agent artifact
paths read are observed in the transcript; artifacts published to the
user are declared on the executed preview.


## 7. ContextSpace

Context is a separate graph from the project graph:

```
Project   <─ binding ─>   Plug
Node      <─ snapshot ─>  ContextBundle
Plug      <─ requires ─>  Plug
```

The important consequence: **context is not hardcoded into a project
directory.** A project can have several context plugs attached, and the
same plug can be reused across projects. The user can think of plugs as
durable resources connected to projects through editable bindings —
not as files baked into a repo.

ContextSpace lives in its own git-maintained repo, separate from any
code project. Its layout (project-level details are in
`IMPLEMENTATION_STATUS.md`):

- **Plugs** — reusable context objects. Four types:
  - `global` — user-wide behavior and conventions.
  - `skill` — reusable tool/workflow knowledge.
  - `planspace` — a single direction's DAG of nodes (executed and
    virtual). The LLM-facing form is a real filesystem materialized
    under `.miniclaw2/graph/runs/<node-id>/lanes/<active-lane>/` per launch; there
    is no STATUS.md or PLAN.md on disk.
  - `protocol` — reusable execution loop or output contract.
- **Bindings** — many-to-many connections between projects and plugs.
  One project can bind several planspaces (parallel directions); one
  plug can bind many projects.
- **Snapshots** — every node launch persists exactly which sources were
  included, with hashes and injection modes, for audit.

**At each node launch, exactly one active planspace is selected.** The
project may have multiple planspaces bound, but only one contributes
its STATUS/PLAN to the node's context. This avoids merging conflicting
directions into the same context window.

Project root `CONTEXT.md` is the one piece of context that lives in the
code repo. Its role is narrow: codebase-facing guidance for any agent
reading the repo. It must not contain current project status, active
plans, branch coordination, or transient blockers — those are
planspace state.


## 8. Outputs as graph mutations

> **Every node output is a graph mutation.** Every executed node writes
> its own preview (required to close the agentic loop) and may
> additionally write new virtual previews or rewrite or obsolete
> existing virtual previews in the same lane. When a node also needs
> user review, it additionally produces a transient user-facing
> review-guidance packet, opens a gate, and the user's free-form
> judgment is merged into the node's preview before the preview is
> committed.

This unifies what previous designs split into STATUS updates and
PLAN updates: one channel, file writes under the graph filesystem.

Two consequences:

- **There is no enum of "output kinds."** Every node produces the
  same kind of thing: a preview write, possibly accompanied by
  further graph mutations. Per-node `result.md` / `result.json`
  siloed files are not first-class.
- **"No output" is not a category.** Every node writes a preview; a
  run that truly advances nothing has nothing to put in `summary` or
  `next_implications` and so should not have run.

### 8.1 The preview contract

Every executed node writes its own preview before reaching terminal
state. The framework enforces this — if the agent ends without a
valid preview, the runner re-prompts inline (capped at three retries
before a framework stub is written). A preview carries three prose
fields plus framework-stamped metadata:

- **motivation** — why the node ran.
- **summary** — what it did and the key outcome.
- **next_implications** — what it enables or blocks downstream.

Framework-stamped meta: id, category, subtype (for review), lane,
state, ran_at. Artifact paths read are observed in the transcript;
artifacts published to the user are declared on the preview.

Review virtuals additionally carry a structured brief
(`check_what` / `expected` / `abnormal`) describing what the
reviewer must verify. Executed review nodes write the same preview
shape — the reviewer's verdict is the graph mutations it does or
does not write; there is no separate accept/reject enum.

Cancelled or errored runs get a framework-written stub preview
explaining the failure; the agent did not get the chance to write
its own.

Virtual nodes carry the same preview shape in declarative form:
motivation explains why the node was proposed, prompt_draft replaces
summary, plus category, optional brief (for reviews), scheduled
deps, proposed_by provenance, and optional obsolete_reason.

### 8.2 Two purposes of context-out

A node produces context-out for one of two consumers:

- **Agent-facing (vertical, in-scope).** The node's own preview,
  which becomes part of the LLM projection on every subsequent
  launch in this lane. It accrues. It does not need to be
  self-contained — the next agent has access to recent previews and
  can `Read` transcripts for depth.
- **User-facing (horizontal, out-of-scope).** When the user needs to
  contribute to a decision, a planning node proposes a
  human-interact review virtual carrying a brief. At promotion the
  review tile pauses in `awaiting_human_input`; the user contributes
  prose. That prose persists at the review node's `human-review.md`
  path and becomes input to the reviewer agent, which synthesizes a
  preview from brief + upstream + prose.

Both the preview and the user's prose are durable. The prose lives
at the review node's path for transcript replay; it is not folded
into other previews.

### 8.3 The LLM projection

Each agent launch sees a **real filesystem subtree** at
`.miniclaw2/graph/lanes/<active-lane>/` containing every node in
the active lane: `nodes/<id>/preview.json` for every node,
`nodes/<id>/transcript.json` and `nodes/<id>/artifacts/` for
executed ones, and (for promoted human-interact reviews)
`nodes/<id>/human-review.md`. The agent reads with the native
`Read` tool — no inlining into the system prompt, no new tools.

**Active lane only.** Cross-lane previews are not materialized.
One direction at a time, per §7. Multi-lane coordination is the
user's job (switching active lane); the agent works in isolation
within one direction.

**CONTEXT.md is injected via system prompt** (not materialized into
the graph subtree). It surfaces as a context node in the canvas
with rendered loads edges to every executed tile.

There is no synthesized `current_state` paragraph and no STATUS or
PLAN projection. The active-lane previews are the current state;
coherence emerges as the next agent reads them. If a coherent
paragraph is ever needed (for sharing, for export), it is produced
on demand from the previews — never maintained as a durable
artifact.

### 8.4 Anti-self-poisoning

Durable previews should not absorb session noise. The framework keeps
this as launch-prompt guidance, not as a programmatic reap-time filter.
Agents are instructed not to commit these as durable findings:

- **Transient errors** — "the tool returned a 500," "permission was
  denied on this single call." Facts about one session, not facts
  about the project.
- **Negative tool claims** — "the reviewer cannot evaluate this,"
  "the API does not work." If the cause is transient, these become
  load-bearing for every future agent and silently redirect future
  runs.
- **Single-run environment quirks** — "the test takes 90 seconds
  here." Worth keeping if reproducible; pollutes if one-off.

What may be written is *stable findings* — facts about the project,
decisions made, open questions discovered, things explicitly ruled
out of scope. The framework does not rewrite, strip, or reject preview
content for this reason; user review and virtual-node editing are the
correction mechanisms when a preview over-commits session noise.


## 9. Gates as virtual review nodes

The gate concept is preserved but folded into the category axis.
There is no separate `gate` node kind, no separate "passive
checkpoint" runtime path, no separate verdict enum, and no separate
acceptance state. A gate is a **virtual review node proposed by an
upstream planning node** (or, in degenerate cases, by the user
directly on the canvas).

### 9.1 Two flavors

| | Inline gate | Review virtual |
|---|---|---|
| Provider call | Inside a running agent's session | The reviewer agent runs as a normal node |
| When declared | Implicit (agent calls a question tool) | Explicit at virtual creation, with a structured brief |
| When fires | Mid-session | When the review virtual is promoted |
| Node state | Paused substate of running | Normal node lifecycle, plus `awaiting_human_input` for human_interact subtype |
| Continuation | Resolving resumes the same session | Reviewer agent runs to terminal as any other agent |
| UI signal | Pulsing animation on a running tile | Inline expansion on the review tile when awaiting human input |

Inline gates are unchanged from prior design — they are substates
of a running agent regardless of the running agent's category.

### 9.2 Type-A vs Type-B

- **Type-A (objective)** — machine-checkable. Exit code zero? File
  exists? Test passes? The agent may self-judge — it is bookkeeping,
  not a verdict. Handled inline by the executing agent.
- **Type-B (taste / correctness)** — judgment of merit. Is this the
  right approach? Did we cover what the user actually wanted? Are
  these the right open questions to leave behind? The agent **must
  not** self-judge. A planning node proposes a review virtual; the
  reviewer agent (with or without human prose) produces the
  judgment.

Review virtuals exist for Type-B questions. Type-A questions never
produce review virtuals; the executing agent calls them inline.

### 9.3 The reviewer as state transformer

A review virtual carries:

- **Brief** — `check_what`, `expected`, `abnormal`. All three
  required. Written by the proposer at virtual creation; editable
  until promotion; frozen thereafter.
- **Subtype** — `agentic_review` or `human_interact_review`.
- **Scheduled deps** — typically including the upstream node being
  reviewed.

**At promotion:**

1. **agentic_review** — the reviewer agent launches immediately. Its
   system prompt includes the brief inline plus filesystem access to
   the upstream's preview/transcript/artifacts under the
   materialized graph subtree.
2. **human_interact_review** — the node enters
   `awaiting_human_input`. The side panel and inline tile expansion
   show the brief plus a free-form textarea. On submit, the runner
   writes the prose to
   `.miniclaw2/graph/runs/<this-id>/lanes/<lane>/nodes/<this-id>/human-review.md`
   and launches the reviewer agent. The agent's system prompt
   includes the brief verbatim and the path to `human-review.md`,
   and instructs the agent to synthesize a preview from
   brief + upstream + human prose.

The reviewer agent writes the same preview shape as any other agent
(`motivation` / `summary` / `next_implications`). **The verdict is
the graph mutations the reviewer writes** — empty mutations mean
"plan unchanged, work accepted as-is"; mutations mean "based on
review, the plan shifts thus." There is no separate
`acceptance_state`, no `verdict_source` enum, no approve/reject
schema.

The canvas may render a derived visual hint (a small ✓ or ⚙ badge
keyed on whether the reviewer wrote mutations) for scan-readability;
this is presentation only, not part of the schema.

### 9.4 Why this works

- **Symmetric ontology.** All executed nodes write the same preview
  shape. The reviewer is an agent like any other; its category
  determines what it may write, not how it writes it.
- **The user's voice survives verbatim.** Their prose lives at
  `human-review.md` for transcript replay and is the literal input
  the reviewer synthesized from.
- **No verdict schema to migrate.** When the product moves, no
  enum-to-enum conversion. The reviewer's prose and mutations carry
  whatever nuance the moment requires.
- **Plan changes are the only side effect.** A review can do no
  more than reshape the graph of virtuals downstream of it. The
  work has already happened; the reviewer cannot undo it, only
  redirect what comes next. Concretely: if the user wants to
  "reject" upstream, the reviewer's mutations propose virtuals to
  fix or replace what was produced — there is no rollback flag.


## 10. Visual grammar

### 10.1 Three orthogonal axes

To make a populated graph scan-readable, three signals are kept
separate:

- **Shape encodes kind.** Stable across state changes. Tile (agent),
  chevron-on-edge (op), layered card (context), home glyph (project
  root), circle-on-baseline (commit), dashed outline (virtual — not yet
  real). A commit ring marks HEAD, dashed amber marks a rebased-away
  commit, and dashed grey marks the uncommitted ghost. The hexagon for
  the retired `gate` kind is gone.
- **Color encodes state.** Distinct colors for running, paused,
  awaiting_human_input, done, error.
- **Badge encodes category + subtype.** 📋 planning, ⚙ regular (a
  subtle dot or no badge), 🔍 review. Review subtype refines the
  badge: 🔍🤖 agentic, 🔍👤 human-interact. A small ✓ or ⚙ post-run
  badge on review tiles indicates "no mutations" vs "plan shifted."

A user scans the canvas and reads all three axes at once: "a 🔍👤
tile in the awaiting color" decodes as "a human review waiting on
me." A fourth axis — tile left-edge accent — encodes which planspace
direction the tile belongs to. Lane background and tile accent share
the planspace's hue.

Hovering any node yields a one-line plain-language explanation. The
legend never has to be memorized.

### 10.2 Composer as virtual node

The composer is not a docked bar. Every future-intent is a **virtual
node** on the canvas — a dashed-outline tile that persists, carries
a prompt_draft and category, and is editable in place.

- Hover a finished agent → a virtual node appears to its right with
  an implicit dep on the spawn tile.
- Tap empty canvas → a virtual node appears at the click position
  with no deps.
- A brand new lane → the concierge planning node runs and drops
  three to five starter virtuals.

Resume source is **implicit in which tile spawned the virtual node**:
if spawned from a finished agent, promotion resumes from that
agent's session; if spawned from empty space, it starts fresh. There
is no "Resume from" dropdown. The structural decision the tile
surfaces is its category — planning, regular, or review — and for
review virtuals the subtype and the brief
(`check_what` / `expected` / `abnormal`).

The user edits a virtual's prompt_draft, category, dep parents, and
(for reviews) brief in the side panel. An agent may write new
virtuals or rewrite / obsolete existing ones — bounded by category:
planning and review may; regular may not. Clicking the tile promotes
it to `queued` (manual mode) or it auto-promotes once all dep
parents are terminal (auto mode).

### 10.3 Polymorphic side panel

The side panel's shape is a function of the selected node's kind. One
collapsed Inspect drawer absorbs raw events, settings snapshot,
context bundle source list, and schema-level fields. The vocabulary
banned from primary surfaces (§4) lives only in Inspect.

### 10.4 Planspace as layout, not containment

Planspaces are an implicit grouping (driven by membership), not a
visual container. Containment fights the graph because:

- Past-commit references are inherently cross-cutting.
- Planspace membership is mutable; container re-parenting is
  expensive.
- Future alternate views (by time, by commit, by importance) require
  the underlying graph to remain flat.

Three render layers driven from planspace membership: a translucent
lane background, a tile left-edge accent, and a neutral top stripe
for project CONTEXT (orthogonal to the planspace palette). At any
moment exactly one planspace is `active` — the one being
materialized into the agent's filesystem projection. The active lane
carries a clear "active" badge or palette emphasis so the user knows
which direction the next agent launch will run against.


## 11. Why we keep these constraints

The non-obvious commitments, restated as one-liners:

- **Project concurrency is explicit and bounded.** Queued work starts
  deterministically up to the positive project limit. Concurrent nodes share
  the worktree, so collaboration and conflict risk are visible product
  semantics rather than hidden isolation.
- **Sessions, not turns.** The smallest unit a researcher delegates
  without checking in.
- **One active planspace per node launch.** Avoids merging
  conflicting directions into the same context window; only the
  active lane is materialized into the agent's filesystem
  projection.
- **Category enforces who may plan.** Planning and review may write
  virtuals; regular may not (hard-failed at reap). Regular agents
  focus on execution; planning and review reshape the plan.
- **Gates are reviews are agents.** No separate node kind, no
  separate verdict enum, no acceptance state. The reviewer's graph
  mutations are its verdict; the user's prose is its input.
- **Free-form human judgment.** No schema migration when the product
  moves; the user's actual reasoning is preserved verbatim at
  `human-review.md`, then synthesized by the reviewer into a normal
  preview.
- **Auto-commit as a visible op, not a hidden side effect.** Full
  mutation history. Ops are framework-injected and do not appear as
  virtuals.
- **Investigation-free interface.** Demos pass on observed effect,
  not on internal correctness.
- **All outputs are graph mutations.** Every executed node writes
  its own preview; new virtual previews are how plans accrete. No
  STATUS.md, no PLAN.md, no markdown documents of intent — the
  graph is total.
- **Virtual nodes are first-class.** The plan is the upstream half
  of the timeline, not a checklist file the user has to read. A
  lane is a DAG of virtuals and executed nodes, not a chain.
- **No declarations, only observations.** `declared_loads` and
  `declared_produces` retire — reads and writes are observed via
  the transcript at reap, not pre-declared. Planning visibility
  comes from prompt_draft and motivation prose.
