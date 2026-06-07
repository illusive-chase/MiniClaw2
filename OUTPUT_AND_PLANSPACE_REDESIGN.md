# Output, planspace, and gate — design follow-up to UI_REDESIGN_PRD.md

This document is a follow-up to `UI_REDESIGN_PRD.md`, focused on one
specific tangle the PRD did not fully resolve: **what a node's
"output" actually is, where it goes, and how human review fits into
the production flow.** It supersedes a small set of PRD claims
(listed in §7.3) and ratifies a new ontology for outputs, planspaces,
and gates.

Read PRD first. This doc assumes its principles and vocabulary
(graph-as-state, polymorphic side panel, phantom composer,
op-as-edge-chevron). Where PRD and this doc disagree, **this doc
wins** — but only for the §7.3-listed claims; everything else in PRD
stands.

**Implementation status.** Nothing in this doc has landed yet. All
sections are forward-looking. Sequencing is in §11.

The motivating observation, in one sentence:

> The `NodeOutputKind` enum (`freeform / summary / interface /
> review_brief`) collapses three orthogonal concerns onto one axis,
> and the resulting confusion radiates into every surface that
> touches outputs.

Untangling the three concerns and pushing each to its natural home
is what this doc is about.


## 1. Background — what's currently tangled

### 1.1 `output_kind` carries three orthogonal concerns

`NodeOutputKind` (in `backend/miniclaw2/domain.py:70`) is a 4-value
enum. The four values look like a flat classification, but they are
the product of three independent yes/no questions collapsed onto one
axis:

| Question | Where it shows in the enum |
|---|---|
| Does this node produce a deliverable artifact? | `freeform` answers "no"; the other three answer "yes" |
| What format does the artifact take? | markdown (`summary` / `review_brief`) vs JSON (`interface`) |
| Is the node handed off for human review afterward? | Only `review_brief` implies "yes" |

Collapsing three axes into one enum has two consequences. First, no
value cleanly represents some natural combinations (a markdown
deliverable that needs review without the rigid "brief" framing has
no enum slot). Second, every surface that consumes `output_kind`
ends up making three different decisions from one field — artifact
rendering, contract-prompt injection, downstream gate materialization
— and the surfaces drift out of sync.

### 1.2 Outputs are isolated from planspace state

A node with `output_kind=summary` writes `result.md` under
`.miniclaw2/outputs/<node-id>/`. A node with `output_kind=interface`
writes `result.json` to the same area. These files **are not read by
any downstream node**. The mechanism that actually carries
information between agents in the same direction is the
`memory_delta` channel, which writes to the planspace's `STATUS.md`
on its own schedule. So we have two parallel "output" mechanisms
that do not know about each other:

- `output_kind` + `output_path` → per-node siloed files nobody reads
- `memory_delta` → planspace state updates that downstream nodes
  actually consume on context bundle composition

This split means `output_kind` is effectively dead infrastructure
from a state-flow perspective, despite being the most-touched field
in the launch UI.

### 1.3 `review_brief` is treated as durable, but it is transient

PRD §4.3 framed `review_brief` as just another artifact (`agent →
produces → brief.md → reviews → gate → produces → review.json`). On
reflection this framing is wrong in a specific way: **a brief and a
review response are transient handoff packets, not durable
artifacts.** The brief exists to let a user make a decision; once
made, the brief itself is consumed and discarded. What persists into
planspace state is the *synthesis* of the agent's interim work plus
the user's verdict, not the two raw files.

### 1.4 The context lane conflates project and planspace

PRD §4 item 1 placed context as a single lane above the timeline.
This treats context as a flat, project-level concern. But there is a
planspace layer that the current canvas does not surface: each
planspace is a distinct direction with its own STATUS / PLAN, and a
node lives inside exactly one planspace at launch time. Squashing
project and planspace into one lane:

- Hides the planspace boundary from the user, even though user
  control granularity (per the conversation that motivated this doc)
  is exactly at the planspace boundary.
- Makes "this run loaded planspace A's STATUS" visually
  indistinguishable from "this run loaded the project's CONTEXT.md,"
  even though they have very different semantics (project CONTEXT.md
  is plan-free; planspace STATUS.md is a workflow tracker).


## 2. Core principles

### 2.1 Two purposes of context-out — vertical and horizontal

A node produces context-out for one of two kinds of consumer:

- **Agent-facing** (vertical, in-scope): a state update that the
  next node in this planspace reads. It accrues. It does not need to
  be self-contained — the next agent has access to the same
  planspace state. Writing style: matter-of-fact, optimized for
  another agent's quick scan.
- **User-facing** (horizontal, out-of-scope): a self-contained
  handoff packet for when the agent reaches a decision boundary it
  cannot cross alone. The recipient (a user) has no access to the
  planspace's running state and must be brought up to speed by the
  packet itself. Writing style: plain language, verify-steps
  explicit, assume nothing.

A single node can produce both, simultaneously. But they have
**different lifetimes**: agent-facing state is durable, user-facing
packets are transient.

### 2.2 The three-layer scope hierarchy

| Scope | What it holds | How it's maintained |
|---|---|---|
| **project** | Plan-free context: principles, philosophy, current-state-of-the-world facts. CONTEXT.md is the textual form. | Hand-edited by user; not auto-derived from planspace activity. |
| **planspace** | A single direction's STATUS + PLAN + accumulated decisions. Has a global plan that nodes incrementally advance. | Updated by every node's agent-facing state delta. |
| **node** | One step in one planspace. | Reads planspace state on launch, writes a delta on completion. |

Two consequences spell themselves out:

- **Planspace does not auto-deliver to project.** Project CONTEXT
  remains a plan-free reference; planspaces do their workflow
  independently. The user is the only mechanism by which planspace
  outcomes ever influence project-level CONTEXT.
- **User control granularity stops at planspace boundaries.** The
  user decides "is there a meaningful new direction?" (create
  planspace) and "has this direction completed its goal?" (close
  planspace). The user does not micromanage nodes within a
  planspace; that's the agent's job.

### 2.3 The unifying claim about outputs

> **Every node output is an agent-facing planspace state update.**
> When a node also needs user review, it additionally produces a
> transient user-facing review-guidance packet, opens a gate, and
> the user's free-form judgment is merged back into the agent-
> facing state update before that update is committed to the
> planspace.

Corollaries:

- There is no enum of "output kinds." Every node produces the same
  kind of thing: a planspace state update.
- "No output" is not a category. What looks like a no-output
  "exploration" node still updates planspace state — it advances the
  planspace's understanding (a new finding, a new open question, an
  explicit `out_of_scope` note). If a node truly advances nothing,
  that node should not exist.
- The brief, the review guidance, and the user's raw verdict are not
  durable artifacts. They live for the duration of the gate and are
  absorbed into the final state update.
- Gates are not a separate output type. Gates are a side-channel
  that intercepts state commits when human judgment is required.

### 2.4 Type-A vs Type-B gates

Borrowed verbatim from ARIS's `acceptance-gate.md` (the closest
prior-art match for this distinction):

- **Type-A (objective)**: machine-checkable. Did the command exit
  zero? Does the file exist? Did the test pass? The agent may
  self-judge — it is bookkeeping, not a verdict.
- **Type-B (taste, correctness)**: judgment of merit. Is this the
  right approach? Did we cover what the user actually wanted? Are
  these the right open questions to leave behind? The agent **must
  not** self-judge. It must hand off.

**Gates exist for Type-B questions only.** When an agent encounters
a Type-B question it cannot route around, it produces a gate. Type-A
questions never produce gates; they're handled inline by the agent.

The current design supports gate-as-user-judged. The ontology should
remain compatible with a future "cross-agent reviewer" (a different
model family playing the user's role for routine Type-B questions)
— but that is out of scope here.

### 2.5 Anti-self-poisoning

Durable planspace state must not absorb session noise. Specific
categories of writes that must be filtered or rewritten before
commit:

- **Transient errors**: "the tool returned a 500 just now,"
  "permission was denied on this single call." These are not facts
  about the project; they are facts about one transient session.
- **Negative tool claims**: "the reviewer cannot evaluate this,"
  "the API does not work." If the cause is transient, this becomes
  load-bearing for every future agent and silently redirects future
  runs.
- **Single-run environment quirks**: "the test takes 90 seconds
  here." If reproducible, this is a finding worth keeping; if
  one-off, it pollutes.

What may be written is *stable findings* — facts about the project,
decisions made, open questions discovered, things explicitly ruled
out of scope. The harness enforces this through a fixed pre-commit
prompt template injected before every planspace state write.

This principle is borrowed from ARIS's `capture-antipatterns.md` and
is the most important non-obvious lesson from reference repos.
Without it, planspace state accumulates noise that contaminates
every subsequent agent run.


## 3. Planspace state — the structure

### 3.1 STATUS.md form: frontmatter slots + free body

Planspace STATUS.md becomes a two-part document:

```markdown
---
goal: "<one-sentence statement of what this direction is trying to achieve>"
current_state: |
  <short paragraph of where this planspace stands today>
open_questions:
  - id: Q1
    summary: "<question>"
    raised_at: 2026-06-07
    raised_by: <node-id-or-user>
decisions:
  - id: D1
    summary: "<decision>"
    decided_at: 2026-06-07
    decided_by: <node-id-or-user>
out_of_scope:
  - "<thing this planspace explicitly does not address>"
---

# Notes

<free markdown body — accumulated detail, reasoning, references>
```

Design choices:

- **Slots use `unknown` explicitly.** A slot may carry the literal
  string `unknown` when the planspace does not yet have an answer.
  This makes "what we don't know yet" first-class and readable.
  Borrowed from meta-harness's `ONBOARDING.md` pattern.
- **Stable IDs for questions and decisions.** `Q1`, `Q2`, ...,
  `D1`, `D2`, ... so other docs and node commits can refer to them.
  The ID space is per-planspace and append-only.
- **Body is free-form.** The frontmatter is the structured surface;
  the body absorbs reasoning, links, snippets. Not all state needs
  to be slot-shaped.

The exact slot list is a v1 proposal — additions are cheap (new
optional slot), removals are expensive (existing planspaces need
migration). Err on the side of fewer slots at v1 and grow.

### 3.2 PLAN.md is a derived view

`PLAN.md` is no longer a separately maintained file. It is generated
from STATUS.md whenever the latter changes, as the read-oriented
next-step view:

- Open questions in STATUS become PLAN checkboxes (unchecked).
- Decisions whose downstream work hasn't started become PLAN
  checkboxes (unchecked, marked `[from D3]` etc.).
- Out-of-scope items appear in a closing "Not addressing" section.

Because PLAN is derived, "STATUS says decided, PLAN still asks"
drift cannot happen. The user edits STATUS; PLAN follows. This is
the same pattern ARIS uses for its `index.md` and `query_pack.md`.

### 3.3 Compressed view (deferred)

ARIS-style `query_pack.md` (auto-compressed STATUS for context-
budget injection) is deferred until full-STATUS injection becomes a
measurable problem. v1 injects full STATUS.

### 3.4 The "advance planspace state" contract

Every node's pre-launch context bundle includes the current
STATUS.md (full). Every node's commit, on completion, includes a
proposed delta to STATUS.md. The delta is constrained by §2.5
filtering, and is either:

- An additive write to one or more slots (new open question, new
  decision, new out-of-scope note).
- A rewrite of `current_state` to reflect the planspace's new
  posture.
- An append to the body.

The delta is the unified successor to today's `output_kind` +
`output_path` + `memory_delta`. Those three mechanisms collapse into
one mechanism: **commit a planspace state delta**.


## 4. Gates as state transformers

### 4.1 Three artifacts in a gate-bearing node

When a node needs review:

1. **Interim agent-state-update**: the would-be planspace delta, in
   its draft form. Lives in the gate's storage until the gate
   closes.
2. **User-facing review guidance**: transient. Written by the agent
   to bring the user up to speed. Has its own writing style (see
   §2.1): plain language, explicit verify steps, self-contained.
3. **Final agent-state-update**: produced when the gate closes.
   Equals (interim) merged with (user's free-form judgment). This
   is what actually commits to planspace state.

Only the third is durable. (1) and (2) are gate-internal and
discarded after merge.

### 4.2 User judgment is free-form

No JSON schema. No "approved / rejected" radio. The user writes a
paragraph (or one sentence — "looks right, ship it"). The harness
does not parse the verdict structurally; the merged final state
update carries the verdict text verbatim in a template-defined
section.

If a user wants to add concrete instructions ("also rename X"), they
include those in the same free-form judgment. The next agent reads
the merged state file as natural-language guidance.

### 4.3 The merge

v1 uses **template-driven merge**: the harness composes the final
state update by concatenating

- the interim agent-state-update verbatim,
- a "Review (user)" section containing the user's free-form
  judgment, and
- a one-line "Resulting decision" the next launching agent is
  prompted to summarize from the above.

A future option is a small **micro-agent merge** that produces a
cleaner synthesis when the user's verdict is paragraph-scale and
overlaps the interim text. Deferred; the ontology is forward-
compatible because both options produce the same kind of output (a
final state update).

The exact template wording is a v1 proposal and should be tuned in
practice.

### 4.4 The gate node on canvas

Gates remain hexagon-shaped (PRD §3.1). Their expansion behavior
changes:

- The hexagon expands inline (PRD §4 item 5) to reveal the review
  guidance (transient, gate-internal) and a free-form textarea for
  the user's judgment.
- **There is no peer brief artifact node.** The review guidance is
  inside the hexagon, not a sibling.
- **There is no peer review-response artifact node.** The user's
  judgment is consumed by the merge, not stored as a separate
  artifact.
- The gate's downstream edge points to the next agent's tile, if
  any, with the merged state update as the carried payload.


## 5. The output ontology — summary

| Old `output_kind` | New treatment |
|---|---|
| `freeform` | Same node, but its commit must still advance planspace state — typically a new open question, finding, or out-of-scope note. "Pure exploration that updates nothing" is not a category. |
| `summary` | Same node, but the artifact target is the planspace's STATUS/body, not a `.miniclaw2/outputs/<id>/result.md` file. |
| `interface` (JSON) | Deferred. There is no live consumer of the JSON form in the project; reintroduce it when a structured-slot use case becomes concrete (e.g., a node that updates a single typed slot in STATUS). |
| `review_brief` | No longer a separate kind. It's a normal planspace-advancing node that also opens a gate; the agent additionally emits the user-facing review guidance as a gate-internal packet. |

Field-level consequences in `backend/miniclaw2/domain.py`:

- `Node.output_kind`: deprecate. The presence of an open gate (or
  its absence) carries the only remaining bit of meaning, and that
  bit is already represented by the `HumanGate` record.
- `Node.output_path`: deprecate. Output target is always the
  planspace.
- `Node.output_contract_snapshot`: deprecate at this level; the
  contract is now per-planspace (the STATUS shape) not per-node.
- `memory_delta` mechanism merges into the planspace-commit
  pipeline.

These deprecations are staged in §11.


## 6. Planspace on the canvas

### 6.1 Implicit grouping, not React Flow subflow

Planspaces are a **layout property**, not a containment relationship.
The decision against React Flow's parent-child subflow API rests on
four observations:

1. Cross-planspace `loads` edges are first-class in this project (a
   node in planspace A often pulls context from planspace B). PRD
   §3.2 already specifies these as dashed, auto-hidden, edge-routed
   normally. Containers fight that routing — clipping, special-
   cases, performance.
2. Past-commit references are inherently cross-cutting. Each node
   has `commit_before` / `commit_after`; the commit graph is its
   own axis. A containment hierarchy would force every such
   reference to be a "boundary-crossing" edge.
3. Planspace membership is mutable (a finding that "belonged" in
   planspace A may later move to planspace B). Re-parenting subflow
   children is expensive; updating a `planspace_id` field is free.
4. Future alternate views (by time, by commit, by importance)
   require the underlying graph to be flat. Subflow is a one-way
   choice.

### 6.2 Visual stack

Three render layers, all driven from `planspace_id`:

| Layer | What it is | Why |
|---|---|---|
| **Lane background** | Translucent rect (≈6–10% opacity of the planspace's hue) under each planspace's nodes, with a labelled header strip ("UI 重构 · 12 nodes · ▾"). | Conveys grouping at scan distance without trapping nodes. |
| **Tile accent** | 3–4px left edge of each agent tile in the planspace's hue. | Makes a single tile readable as "belongs to direction X" even when the lane is offscreen. |
| **Project CONTEXT stripe** | Neutral-colored thin band at the top of the canvas, not participating in the planspace palette. | Reinforces project's plan-free, scope-different status. |

Edges stay neutral. Cross-lane edges are visually obvious by
geometry (they cross the gap between lanes); they do not need a
special stroke color.

### 6.3 Cross-cutting access

| User goal | Canvas representation |
|---|---|
| "This new node should also see planspace B's STATUS" | A `+ load from another direction` chip on the phantom composer. Selecting B draws a dashed `loads` edge across lanes (default-hidden). |
| "This new node continues the conversation from past node X (in any planspace)" | Existing `↻ resume from` mechanism. Resume edge unchanged; visually obvious if the source is in a different lane. |
| "Inspect the workspace state before / after this node's run" | Inspect▸ drawer surface: `commit_before` / `commit_after` with "diff vs HEAD" navigation. Not drawn on canvas. |
| "Start a new direction by forking from a past commit" | Top-bar `+ New direction` button with an advanced "starting from commit ___" option. New planspace appears as a new lane. |

### 6.4 Planspace lifecycle

- **Create**: explicit user action only (`+ New direction`). Never
  inferred from hover-into-empty-space. Adding a node hovered off
  the side of an existing lane defaults to appending to that lane.
- **Close**: explicit user action (`Mark direction complete`) on
  the lane header. Closed planspaces collapse to a single summary
  stripe by default but remain on canvas; their STATUS.md becomes
  a read-only artifact.
- **Collapse**: lane header `▾` toggles between full nodes and a
  single summary tile (no React Flow subflow involved — the
  frontend just swaps the cluster's nodes for one summary node).

### 6.5 Color palette

Five to seven distinguishable but low-saturation hues. The palette
is **orthogonal to the `state-*` palette** — state colors mean
"how is this going" (`running`, `waiting`, `error`, ...); planspace
colors mean "which direction does this belong to." A node's tile
carries both: its body background reflects state, its left-edge
accent reflects planspace.

Color assignment is persisted in the planspace manifest (`color:
indigo` or similar), so it stays stable across reloads and shared
sessions. New planspaces draw from the palette in creation order,
with a small picker if the user wants to override.


## 7. What this doc removes or revises

### 7.1 Schema removals (backend)

- `NodeOutputKind` enum: deprecate after a migration window.
- `Node.output_kind`, `Node.output_path`,
  `Node.output_contract_snapshot`: deprecate as fields.
- `default_node_output_path`, `node_output_contract`: remove after
  deprecation window.
- The separate `memory_delta` API path merges into the unified
  planspace-commit endpoint.

### 7.2 Frontend removals

- The intent-chip row in `PhantomNode.tsx` (`Explore` / `Build &
  summarize` / `Hand off for review` / `⋯ Interface (JSON)`) is
  replaced by a single optional **"needs review"** toggle. The
  intent of the run is expressed in the prompt text; the only
  structural decision left is whether a gate follows.
- `AgentPanel.tsx`'s `showArtifact = outputKind !== "freeform"`
  branching disappears. The "Result" section always shows the most
  recent planspace state delta this node produced (or proposed, if
  gated and awaiting merge).
- The brief artifact node and review-response artifact node in
  `layout.ts` disappear. The gate hexagon expands inline for review
  guidance + user textarea.

### 7.3 Updates to UI_REDESIGN_PRD.md

This doc **supersedes** the following PRD claims. Future
contributors should treat the statements below as the new position:

- **PRD §4 item 3** ("The review brief is just an artifact"):
  revised. The brief is a transient gate-internal packet, not a
  peer artifact node.
- **PRD §4 item 7** ("Memory deltas as inbound arrows"):
  generalized. All node outputs are planspace deltas; there is no
  distinction between "memory delta" and "output" anymore. The
  inbound `+Δ` arrow becomes universal.
- **PRD §5.1** (single context lane above timeline): revised. The
  lane structure is per-planspace, with project CONTEXT living as a
  top stripe rather than as one lane among others.
- **PRD §5.2** intent-chip row: revised. Replaced by a single
  "needs review" toggle (§7.2).
- **PRD §5.3** AgentPanel "Result" rendering: revised. No longer
  keyed on `output_kind`; keyed on planspace delta existence.
- **PRD §8 deferred items** "Schema-aware review-form generation":
  cancelled (not deferred — explicitly removed from the roadmap).
  User judgment is free-form by design.

### 7.4 Words that should not appear on primary surfaces

Extending PRD §6's banned-word list:

- `output kind`, `output path`, `output contract`
- `memory delta` (use "planspace update" or "what this run
  changed")
- `review brief`, `review response` (the gate hexagon's header
  should read "Needs your call" or similar)
- `verdict`, `acceptance` (already in PRD §6)

These words remain accurate at the schema level and stay inside the
Inspect▸ drawer (PRD §6's allowance still applies).


## 8. Risks and non-obvious things

- **Unbounded STATUS.md growth.** A long-lived planspace
  accumulates open questions and decisions indefinitely.
  Mitigation: an explicit "archive resolved" action in the
  planspace's side panel that moves `D*` entries with no
  downstream open questions into a separate `archive.md`, keeping
  the active STATUS slot list crisp. Compressed view (§3.3)
  becomes mandatory once context-budget pressure shows up.
- **Anti-self-poisoning depends on prompt hygiene.** The pre-
  commit filter (§2.5) is enforced only by the injected prompt
  template. A misaligned base model could ignore it. Mitigation
  for v1: keep the template short, explicit, and append-only at
  the end of the system context (last-instructions-win bias). For
  later: consider a structural pre-commit check (e.g., heuristic
  flagging of "permission denied" / "5xx" in the proposed delta).
- **Cross-planspace `loads` discoverability.** Dashed, auto-hidden
  edges mean the user may not realize a node pulled context from
  another direction. Mitigation: show loaded-from planspace names
  as a chip on the node tile ("loaded: Parser 重写") when the load
  is cross-lane, even when the edge itself is hidden.
- **Palette exhaustion past ~7 active planspaces.** Hard-cap the
  palette and reuse hues for archived / closed planspaces;
  collapsed planspaces should display as a desaturated lane to
  communicate "out of the way."
- **Gate merge quality with paragraph-scale verdicts.** Template
  merge (§4.3) can produce awkward seams when the user writes a
  long verdict that contradicts the interim delta. Mitigation:
  monitor in practice; promote the deferred micro-agent merge if
  quality issues appear.


## 9. Decisions taken in this design

- **STATUS.md form**: structured YAML frontmatter (`goal`,
  `current_state`, `open_questions`, `decisions`, `out_of_scope`)
  plus free markdown body, with explicit `unknown` allowed in
  slots.
- **PLAN.md is derived from STATUS, not separately edited.**
- **Compressed planspace view (`query_pack`)**: deferred.
- **Anti-self-poisoning**: enforced via fixed pre-commit prompt
  template (lightweight version) for v1.
- **Planspace canvas form**: implicit grouping via `planspace_id`
  + visual lane rect + tile accent. No React Flow subflow.
- **Planspace palette**: 5–7 low-saturation hues, manifest-stable,
  orthogonal to `state-*` colors.
- **Planspace does not auto-deliver to project.** Project
  CONTEXT.md is plan-free.
- **Planspace lifecycle is explicit user action**: create and
  close are user-driven, never inferred.
- **All node outputs are planspace state updates**; no enum, no
  per-node artifact directory.
- **Gates exist only for Type-B questions**: subjective judgment
  of merit / correctness. Type-A bookkeeping stays inline.
- **Gate is a state transformer**: interim agent delta + user
  free-form judgment → merged final delta. Brief and review
  response are transient, gate-internal.
- **User judgment is free-form**, no JSON schema.
- **Gate merge is template-driven** for v1; micro-agent merge
  deferred.


## 10. Deferred / out of scope

- `interface` (JSON) output kind: hold until a concrete
  programmatic consumer exists (likely tied to typed STATUS
  slots).
- `query_pack` compressed planspace view: hold until context-
  budget pressure measurable.
- Micro-agent merge for paragraph-scale user verdicts: hold until
  quality issues with template merge appear.
- Cross-agent reviewer as a gate alternative to user: hold;
  ontology is forward-compatible.
- Structured `entities/` under planspace (ARIS-style
  papers / ideas / claims): not adopted for v1.
- Cross-project / fork-from-commit explicit visualization: carries
  the PRD §9 deferral.
- Structural pre-commit checks for planspace deltas: hold;
  prompt-template hygiene first.
- "Move node between planspaces" UI: hold; the data layer
  supports it (`planspace_id` is mutable) but the interaction is
  not designed yet.


## 11. Sequencing for implementation

A rough order — each step lands as an isolated slice with tests
before the next starts:

1. **Backend: planspace STATUS frontmatter schema.** Reader,
   writer, parsing, slot validation. PLAN.md derived generator.
2. **Backend: collapse `output_kind` / `output_path` /
   `memory_delta` into a single planspace-commit mechanism.**
   Deprecate the old fields with a brief compatibility shim.
3. **Backend: gate lifecycle revision.** Interim delta storage,
   user-facing review guidance separate from durable output,
   template merge on close.
4. **Backend: pre-commit filter prompt template** for anti-self-
   poisoning, injected by the runner.
5. **Backend: `planspace_id` surfaced on `Node`** (or derived
   from `settings_snapshot` — pick the cheaper path).
6. **Frontend: planspace lane rect + tile accent + palette**.
   Reads `planspace_id`; no graph layout change beyond
   y-coordinate clustering.
7. **Frontend: phantom composer revision** — remove intent-chip
   row, add "needs review" toggle and "load from another
   direction" chip.
8. **Frontend: gate hexagon inline expansion** with review
   guidance + free-form textarea + merge preview.
9. **Frontend: AgentPanel "Result" rewrite** — show planspace
   delta proposed / committed by this node instead of
   `output_kind`-keyed branching.
10. **Frontend: STATUS.md viewer + slot-aware editor** in the
    planspace side panel (selected by clicking the lane header).
11. **Frontend: cross-lane `loaded from:` chip** on tiles whose
    `loads` edges cross planspaces.
12. **(Deferred)** Compressed planspace view, micro-agent merge,
    fork-from-commit, archive / `out_of_scope` cleanup actions.

Slices 1–4 are backend-only and can land without UI changes;
slices 6–11 read the new backend state incrementally. Slices 5
and 6 are the smallest first-visible win — once they land, every
existing planspace gets a colored lane on the canvas, even before
the output ontology changes are user-facing.
