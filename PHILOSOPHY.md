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
  paragraph of motivation. The agent drops a starter graph of three
  to five virtual nodes onto the lane — rough prompts, declared
  loads, reasonable dependencies. The user sees concrete next steps
  the moment bootstrap finishes; they may edit, delete, or add. If
  the seed leaves a load-bearing question unanswered, the agent asks
  rather than guessing.
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

A node is one provider-backed session, one passive human checkpoint,
or one programmatic state mutation.

Three kinds — and no others. Questions, decisions, and out-of-scope
items are not separate node kinds; they are things a node may *say in
prose* inside its preview.

- **agent** — runs a provider with options assembled from project
  settings, context bundle, and per-node overrides. A normal agent
  node starts a fresh provider session; timeline adjacency means only
  filesystem ordering, never conversation continuation. Inline human
  gates (permission, ask-user, plan-approval) pause the session;
  resolving the gate continues the same session.
- **gate** — a *passive human checkpoint with no provider call*. The
  runner short-circuits straight to a blocking-review terminal
  substate. The gate renders a brief (markdown) and collects a
  free-form judgment from the user (see §9).
- **op** — a non-agent, fast, always-immediate state transition that
  appears on the timeline so the project's full mutation history is
  visible.

A node may also exist in a *virtual* substate — declared but not yet
run. Virtual nodes carry a draft prompt, declared loads, declared
produces, and scheduled dependencies. They are editable in place
(user or agent) and may be obsoleted without ever running. Promotion
is the user clicking the tile (or the framework resolving a scheduled
dependency).

A running session may oscillate between active execution and a paused
substate as inline gates open and close. The blocking-review substate
is only reached by gate nodes. Op nodes have no intermediate states.

### 6.2 Project

A workspace is `(folder + ordered nodes)`. One worktree per project;
nodes run **one at a time** on it.

Within a project the timeline is strictly ordered. Concurrency comes
from forks (new projects with their own worktree), not from
intra-project parallelism. This is what makes FS state coherent — the
state another node starts from is exactly the state the previous node
left behind.

Each node records the pre- and post-state of the worktree, so the
timeline can show a project-state diff per node.

### 6.3 Edges (derived, not stored)

Edges are read off node/project fields, not modelled as separate
records. Six relations matter:

- **timeline** — FS-state dependency between adjacent nodes.
- **resume** — explicit provider conversation continuation.
- **loads** (context) — acausal carryover; evaluated once at consumer
  creation.
- **produces** — "this agent wrote this file."
- **reviews** — a gate inspects an upstream artifact.
- **fork** — a new project rooted at a worktree of another's snapshot.


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
  - `planspace` — a single direction's ordered collection of nodes
    (executed and virtual). The LLM-facing form is a synthesized
    filesystem of node previews; there is no STATUS.md or PLAN.md on
    disk.
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
valid preview, the runner re-prompts inline. A preview carries: why
the node ran, what it did and the key outcome, what it enables or
blocks downstream, the artifact paths it produced, and the refs it
actually read.

Cancelled or errored runs get a framework-written stub preview
explaining the failure; the agent did not get the chance to write
its own.

Virtual nodes carry the same shape in declarative form: motivation
explains why the node was proposed, a draft prompt replaces the
summary, declared loads/produces replace observed loads/produces,
plus provenance and scheduled dependencies.

### 8.2 Two purposes of context-out

A node produces context-out for one of two consumers:

- **Agent-facing (vertical, in-scope).** The node's own preview,
  which becomes part of the LLM projection on every subsequent
  launch in this lane. It accrues. It does not need to be
  self-contained — the next agent has access to recent previews and
  can `Read` transcripts for depth.
- **User-facing (horizontal, out-of-scope).** A self-contained
  handoff packet for when the agent reaches a decision boundary it
  cannot cross alone. Lifetime: gate-internal, discarded after the
  judgment merges into the source node's preview.

A single node can produce both. They have **different lifetimes**:
the preview is durable; the user-facing packet is transient.

### 8.3 The LLM projection

Each agent launch sees a synthesized filesystem rooted at the lane.
Recent previews on the active lane are present as readable files;
cross-lane previews appear only when this node's declared loads
reference them. Transcripts and artifacts live at predictable paths
but are not inlined — the agent reaches for them with regular `Read`
when it needs depth. The shallow level is cheap; the deep level is
on-demand.

There is no synthesized `current_state` paragraph and no STATUS or
PLAN projection. The recent previews are the current state; coherence
emerges as the next agent reads them. If a coherent paragraph is ever
needed (for sharing, for export), it is produced on demand from the
previews — never maintained as a durable artifact.

### 8.4 Anti-self-poisoning

Durable previews must not absorb session noise. Categories that
must be filtered or rewritten before commit:

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
out of scope. Enforcement happens at commit time — for the writing
node's own preview and for any new virtual previews it proposes.


## 9. Gates as state transformers

### 9.1 Two flavors

| | Inline gate | Passive checkpoint gate |
|---|---|---|
| Provider call | Inside a running agent's session | None — gate has no provider call |
| When declared | Implicit (agent calls a question tool) | Explicit at gate-node launch, with a brief |
| When fires | Mid-session | Immediately when the gate node starts |
| Node state | Paused substate of running | Blocking-review terminal substate |
| Continuation | Resolving resumes the same session | Resolving does not wake any agent |
| UI signal | Pulsing animation on a running tile | Solid color on a finished hexagon |

### 9.2 Type-A vs Type-B

- **Type-A (objective)** — machine-checkable. Exit code zero? File
  exists? Test passes? The agent may self-judge — it is bookkeeping,
  not a verdict. Handled inline.
- **Type-B (taste / correctness)** — judgment of merit. Is this the
  right approach? Did we cover what the user actually wanted? Are
  these the right open questions to leave behind? The agent **must
  not** self-judge. It hands off.

**Gates exist only for Type-B questions.** Type-A questions never
produce gates.

### 9.3 The gate as state transformer

A gate-bearing node produces three things:

1. **Interim preview.** The would-be `preview.json` in draft form.
   Lives in gate storage until the gate closes.
2. **User-facing review guidance.** Transient. Written by the agent
   to bring the user up to speed (plain language, verify steps
   explicit, self-contained).
3. **Final preview.** Produced when the gate closes. Equals
   (interim) merged with (user's free-form judgment). This is what
   commits as `nodes/<id>/preview.json`. The merge may also add or
   rewrite virtual previews in the same lane if the user's judgment
   reshapes the plan.

Only the third is durable. (1) and (2) are gate-internal and discarded
after merge.

**User judgment is free-form.** No JSON schema, no approve/reject
radio. The user writes a paragraph (or one sentence — "looks right,
ship it"). If the user wants to add concrete instructions ("also
rename X"), they include them in the same prose. The next agent reads
the merged delta as natural-language guidance.

The merge is template-driven: the final update concatenates the
interim delta verbatim, a "Review (user)" section containing the
user's judgment, and a one-line "Resulting decision" the next agent
is prompted to summarize. A future micro-agent merge can produce
cleaner synthesis once template-merge quality becomes a problem; the
ontology is forward-compatible because both options produce the same
kind of output.

### 9.4 Done vs accepted

Execution completion and acceptance are separate:

- **Done** — the runner finished without error. Bookkeeping.
- **Acceptance** — was the result accepted? (Open variants:
  not-yet-reviewed, accepted, rejected, blocked, not-applicable.)
- **Verdict source** — who said so? (Human, deterministic check,
  cross-provider reviewer, or same-provider advisory.)

A done-but-unreviewed node's preview still participates in the lane's
projection, but the node may not promote skills, protocols, or
durable rules until accepted. Rejected review gates prevent the
upstream node's durable proposals from being applied. Same-provider
self-review can be recorded as advisory but does not mark the node
accepted.


## 10. Visual grammar

### 10.1 Two orthogonal axes

To make a populated graph scan-readable, two signals are kept
separate:

- **Shape encodes kind** — what the thing is ontologically. Stable
  across state changes. Tile (agent), hexagon (gate), document card
  (artifact), layered card (context), chevron-on-edge (op), home
  glyph (project root), dashed outline (phantom — not yet real).
- **Color encodes state** — how it is going. Distinct colors for
  running, paused, done, error, and awaiting review.

A user scans the canvas and reads both axes at once: "two hexagons,
one amber, one green" decodes as "two review gates, one waiting, one
approved." A third axis — tile left-edge accent — encodes which
planspace direction the tile belongs to. Lane background and tile
accent share the planspace's hue.

Hovering any node yields a one-line plain-language explanation. The
legend never has to be memorized.

### 10.2 Composer as virtual node

The composer is not a docked bar. Every future-intent is a **virtual
node** on the canvas — a dashed-outline tile that persists, carries a
draft prompt and declared loads/produces, and is editable in place.

- Hover a finished agent → a virtual node appears to its right.
- Tap empty canvas → a virtual node appears at the click position.
- A brand new project → the concierge bootstrap drops three to five
  virtual nodes on a fresh lane.

Resume source is **implicit in which tile spawned the virtual node**:
if spawned from a finished agent, promotion resumes from that agent;
if spawned from empty space, it starts fresh. There is no "Resume
from" dropdown. The only structural decision the tile surfaces is
whether the run is gated — a single "needs review" toggle, no intent
enum.

The user edits a virtual node's draft prompt, declared loads, and
declared produces in the side panel; an agent may rewrite or obsolete
a downstream virtual node as part of its preview write. Clicking the
tile promotes it to `queued`.

### 10.3 Polymorphic side panel

The side panel's shape is a function of the selected node's kind. One
collapsed Inspect drawer absorbs raw events, settings snapshot,
context bundle source list, and schema-level fields. The vocabulary
banned from primary surfaces (§4) lives only in Inspect.

### 10.4 Planspace as layout, not containment

Planspaces are an implicit grouping (driven by membership), not a
visual container. Containment fights the graph because:

- Cross-planspace `loads` edges are first-class.
- Past-commit references are inherently cross-cutting.
- Planspace membership is mutable; container re-parenting is expensive.
- Future alternate views (by time, by commit, by importance) require
  the underlying graph to remain flat.

Three render layers driven from planspace membership: a translucent
lane background, a tile left-edge accent, and a neutral top stripe
for project CONTEXT (orthogonal to the planspace palette).


## 11. Why we keep these constraints

The non-obvious commitments, restated as one-liners:

- **One node at a time per project.** FS coherence depends on it.
  Concurrency is forks, not interleaving.
- **Sessions, not turns.** The smallest unit a researcher delegates
  without checking in.
- **One active planspace per node launch.** Avoids merging conflicting
  directions into the same context window.
- **Free-form gate judgment.** No schema migration when the product
  moves; the user's actual reasoning is preserved verbatim.
- **Auto-commit as a visible op, not a hidden side effect.** Full
  mutation history.
- **Investigation-free interface.** Demos pass on observed effect, not
  on internal correctness.
- **All outputs are graph mutations.** Every executed node writes its
  own preview; new virtual previews are how plans accrete. No
  STATUS.md, no PLAN.md, no markdown documents of intent — the graph
  is total.
- **Virtual nodes are first-class.** The plan is the upstream half
  of the timeline, not a checklist file the user has to read.
