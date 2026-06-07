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


## 3. The graph is the state

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


## 4. Three-layer scope hierarchy

| Scope | What it holds | How it's maintained |
|---|---|---|
| **project** | Plan-free reference: principles, philosophy, current-state-of-the-world facts. `CONTEXT.md` is the textual form. | Hand-edited by user; not auto-derived from planspace activity. |
| **planspace** | A single direction's STATUS, accumulated decisions, open questions. | Updated by every node's agent-facing state delta. |
| **node** | One step in one planspace. | Reads planspace state on launch, writes a delta on completion. |

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


## 5. Domain model

Three primary objects. Everything else is a view over these.

### 5.1 Node

A node is one provider-backed session, one passive human checkpoint,
or one programmatic state mutation.

Three kinds:

- **agent** — runs a provider with options assembled from project
  settings, context bundle, and per-node overrides. A normal agent
  node starts a fresh provider session/thread; timeline adjacency
  means only filesystem ordering, never conversation continuation.
  Inline human gates (permission, ask-user, plan-approval) put the
  node into `waiting`; resolving an inline gate continues the same
  session and returns the node to `running`.
- **gate** — a *passive human checkpoint with no provider call*. The
  runner short-circuits straight to `awaiting_review`. The gate
  renders a brief (markdown) and collects a free-form judgment from
  the user (see §8).
- **op** — a non-agent, fast, always-immediate state transition that
  appears on the timeline so the project's full mutation history is
  visible. MVP is `commit` (auto-appended after agent/gate done) and
  `fork-project`.

One state machine:

```
queued → running [↔ waiting via inline gate] → done | error | cancelled
                                            ↘
                                    awaiting_review → done   (gate only)
```

`waiting` is a substate of `running` during a session — a node may
oscillate `running ↔ waiting` many times. `awaiting_review` is only
reached by gate nodes after the (skipped) session ends. Op nodes go
`queued → running → done | error` without intermediate states.

### 5.2 Project

A workspace is `(folder + ordered nodes)`. One worktree per project;
nodes run **one at a time** on it.

Within a project the timeline is strictly ordered. Concurrency comes
from forks (new projects with their own worktree), not from
intra-project parallelism. This is what makes FS state coherent — a
node's `commit_after` is the only state another node will start from.

Each node records `commit_before` and `commit_after`, so the timeline
can show a project-state diff per node.

### 5.3 Edges (derived, not stored)

Edges are read off node/project fields, not modelled as separate
records.

| Edge | Derived from | Means |
|---|---|---|
| **timeline** | `(project_id, sequence)` | FS-state dependency between adjacent nodes |
| **resume** | `parent_node_id` | Explicit provider conversation continuation |
| **loads** (context) | snapshotted context bundle sources | Acausal carryover; evaluated once at consumer creation |
| **produces** | artifact path on the producing node | "This agent wrote this file" |
| **reviews** | gate sourced from upstream brief | Gate inspects an upstream artifact |
| **fork** | `parent_project_id + parent_commit` | A new project rooted at a worktree of another's snapshot |


## 6. ContextSpace

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
  - `planspace` — a single direction's STATUS, PLAN, accumulated
    decisions.
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


## 7. Outputs as planspace state updates

> **Every node output is an agent-facing planspace state update.**
> When a node also needs user review, it additionally produces a
> transient user-facing review-guidance packet, opens a gate, and the
> user's free-form judgment is merged back into the agent-facing state
> update before that update is committed to the planspace.

This is the unifying claim about outputs. Two consequences:

- **There is no enum of "output kinds."** Every node produces the same
  kind of thing: a planspace state update. Per-node `result.md` /
  `result.json` siloed files are not first-class — they are an artifact
  of an earlier design and the implementation status tracks their
  retirement.
- **"No output" is not a category.** Even pure exploration accrues
  *something* — a new finding, a new open question, an explicit
  `out_of_scope` note. If a run truly advances nothing, that run should
  not exist.

### 7.1 Two purposes of context-out

A node produces context-out for one of two consumers:

- **Agent-facing (vertical, in-scope).** A state update the next node
  in this planspace reads. It accrues. It does not need to be
  self-contained — the next agent has access to the same planspace
  state. Writing style: matter-of-fact, optimized for another agent's
  quick scan.
- **User-facing (horizontal, out-of-scope).** A self-contained handoff
  packet for when the agent reaches a decision boundary it cannot
  cross alone. The recipient (a user) has no access to the planspace's
  running state and must be brought up to speed by the packet itself.
  Writing style: plain language, verify-steps explicit, assume nothing.

A single node can produce both, simultaneously. They have **different
lifetimes**: agent-facing state is durable; user-facing packets are
transient and consumed by the gate that follows.

### 7.2 STATUS.md form

Planspace STATUS.md is a two-part document: YAML frontmatter holding
structured slots, plus a free markdown body.

Slots:

- `goal` — one-sentence statement of what this direction is trying to
  achieve.
- `current_state` — short paragraph of where this planspace stands today.
- `open_questions` — append-only list with stable IDs (`Q1`, `Q2`, …).
- `decisions` — append-only list with stable IDs (`D1`, `D2`, …).
- `out_of_scope` — explicit non-goals.

The literal value `unknown` is a first-class slot value — "what we
don't know yet" is structured, not absent.

### 7.3 PLAN.md is derived

PLAN.md is not separately maintained. It is generated from STATUS.md:
open questions and undischarged decisions become checkbox items;
out-of-scope items appear in a closing "Not addressing" section.

The user edits STATUS; PLAN follows. Drift between "STATUS says
decided, PLAN still asks" cannot happen.

### 7.4 Anti-self-poisoning

Durable planspace state must not absorb session noise. Categories that
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
decisions made, open questions discovered, things explicitly ruled out
of scope. Enforcement is a fixed pre-commit prompt template injected
before every planspace state write.


## 8. Gates as state transformers

### 8.1 Two flavors

| | Inline gate | Passive checkpoint gate |
|---|---|---|
| Provider call | Inside a running agent's session | None — gate has no provider call |
| When declared | Implicit (agent calls a question tool) | Explicit at gate-node launch, with a brief |
| When fires | Mid-session | Immediately when the gate node starts |
| Node state | `waiting` (substate during running) | `awaiting_review` (terminal-but-blocking) |
| Continuation | Resolving resumes the same session | Resolving does not wake any agent |
| UI signal | Pulsing animation on a running tile | Solid color on a finished hexagon |

### 8.2 Type-A vs Type-B

- **Type-A (objective)** — machine-checkable. Exit code zero? File
  exists? Test passes? The agent may self-judge — it is bookkeeping,
  not a verdict. Handled inline.
- **Type-B (taste / correctness)** — judgment of merit. Is this the
  right approach? Did we cover what the user actually wanted? Are
  these the right open questions to leave behind? The agent **must
  not** self-judge. It hands off.

**Gates exist only for Type-B questions.** Type-A questions never
produce gates.

### 8.3 The gate as state transformer

A gate-bearing node produces three things:

1. **Interim agent-state-update.** The would-be planspace delta in
   draft form. Lives in gate storage until the gate closes.
2. **User-facing review guidance.** Transient. Written by the agent
   to bring the user up to speed (plain language, verify steps
   explicit, self-contained).
3. **Final agent-state-update.** Produced when the gate closes. Equals
   (interim) merged with (user's free-form judgment). This is what
   commits to the planspace.

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

### 8.4 Done vs accepted

Execution completion and acceptance are separate:

- `state = done` — the runner finished without error. Bookkeeping.
- `acceptance_state ∈ {not_applicable, unreviewed, accepted, rejected, blocked}`
  — was the result accepted?
- `verdict_source ∈ {none, human, deterministic, cross_provider, same_provider_advisory}`
  — who said so?

A done-but-unreviewed node may update STATUS.md as "done but
unreviewed." It may not promote skills, protocols, or durable rules
until accepted. Rejected review gates prevent the upstream node's
durable proposals from being applied. Same-provider self-review can
be recorded as advisory but does not mark the node accepted.


## 9. Visual grammar

### 9.1 Two orthogonal axes

To make a populated graph scan-readable, two signals are kept
separate:

- **Shape encodes kind** — what the thing is ontologically. Stable
  across state changes. Tile (agent), hexagon (gate), document card
  (artifact), layered card (context), chevron-on-edge (op), home
  glyph (project root), dashed outline (phantom — not yet real).
- **Color encodes state** — how it is going. `state-running`,
  `state-waiting`, `state-done`, `state-error`, `state-review`.

A user scans the canvas and reads both axes at once: "two hexagons,
one amber, one green" decodes as "two review gates, one waiting, one
approved." A third axis — tile left-edge accent — encodes which
planspace direction the tile belongs to. Lane background and tile
accent share the planspace's hue.

Hovering any node yields a one-line plain-language explanation. The
legend never has to be memorized.

### 9.2 Composer as phantom node

The composer is not a docked bar. It is a dashed-outline node that
materializes where the next node will appear:

- Hover a finished agent → a phantom appears to its right.
- Tap empty canvas → a phantom appears at the click position.
- A brand new project → one center-anchored phantom invites the first
  run.

Resume source is **implicit in which tile spawned the phantom**: if
spawned from a finished agent, the new node resumes from that agent;
if spawned from empty space, it starts fresh. There is no "Resume
from" dropdown. The only structural decision the composer surfaces is
whether the run is gated — a single "needs review" toggle, no intent
enum.

### 9.3 Polymorphic side panel

The side panel's shape is a function of the selected node's kind. One
collapsed Inspect drawer absorbs raw events, settings snapshot,
context bundle source list, and schema-level fields. The vocabulary
banned from primary surfaces (§3) lives only in Inspect.

### 9.4 Planspace as layout, not containment

Planspaces are an implicit grouping (driven by `planspace_id`), not a
React Flow subflow. Containment fights the graph because:

- Cross-planspace `loads` edges are first-class.
- Past-commit references are inherently cross-cutting.
- Planspace membership is mutable; subflow re-parenting is expensive.
- Future alternate views (by time, by commit, by importance) require
  the underlying graph to remain flat.

Three render layers driven from `planspace_id`: a translucent lane
background, a tile left-edge accent, and a neutral top stripe for
project CONTEXT (orthogonal to the planspace palette).


## 10. Why we keep these constraints

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
- **All outputs are planspace state updates.** No siloed per-node
  result files; the next agent reads accumulated state, not a folder
  of orphans.
