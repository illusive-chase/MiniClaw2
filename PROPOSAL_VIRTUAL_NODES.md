# Proposal: Virtual nodes, categories, and the filesystem projection

Status: design settled, not yet landed.
Companion to `PHILOSOPHY.md` (commitments) and `IMPLEMENTATION_STATUS.md`
(what is built).

This proposal eliminates `STATUS.md` and `PLAN.md` as maintained
files. The planspace becomes a DAG of nodes (executed and virtual).
At each launch, the framework materializes the active lane into a
real filesystem subtree the agent reads and writes via native tools.
A required `preview.json` per node closes the agentic loop and
carries the orientation the next agent needs.

The proposal also introduces an orthogonal **category** axis on
nodes (planning / regular / review) that determines what each agent
is allowed to write at reap, and folds the previous "gate" node
kind into the `review` category with two subtypes (`agentic_review`,
`human_interact_review`).

This is the bridge between the updated `PHILOSOPHY.md` §§4, 6.1,
6.3, 7, 8, 9, 10 and the code, which still operates the old
STATUS/PLAN harness and the legacy passive-gate path.


## 1. The pain we are fixing

After concierge bootstrap finishes filling STATUS slots, the user
stares at a single empty phantom and the framework goes silent. They
must invent the first prompt unaided, decide whether to plan or to
implement, and re-decide that for every subsequent node. PHILOSOPHY
§3 promises the framework absorbs setup cost; §5 promises the user
does not micromanage nodes inside a planspace. Both promises break
the moment bootstrap returns control.

Real workflows are progressive and sometimes bootstrap (set up the
toolchain, discover I need shadcn, then implement the auth UI). A
single static `goal` cannot describe this; neither can the current
`goal` + `current_state` snapshot, which is also static, just hidden
behind two slots.

A second pain: the gate ontology has accumulated four parallel
representations (passive checkpoint node + interim preview + review
guidance + final preview + acceptance state + verdict source) for
what is essentially "an agent reviewed something, possibly with
human input." The wire envelopes, runner state, and frontend
rendering all carry the cost of that fan-out.


## 2. Diagnosis

The burden lives at the node-launch boundary. STATUS is the wrong
place to fight it: STATUS is a snapshot, while the user is asking
"what next?" The answer to "what next?" is a *plan* — concrete
enough to click. Today we surface the plan as a markdown checklist
(PLAN.md), which PHILOSOPHY §4 says should not exist. Any concept
worth surfacing should be a node or an edge.

So: dissolve STATUS and PLAN into the graph. Executed nodes are the
"what we've done." Virtual nodes are the "what we plan to do." Both
are nodes; both live on the canvas; both are previews in the
materialized filesystem the agent reads.

For gates: dissolve the passive-checkpoint path into the regular
agent path. A reviewer is just an agent with `category=review`. A
human-interact review is the same agent, with a brief and a prose
input file the user fills in before the agent runs.


## 3. The change

### 3.1 Virtual nodes

A virtual node is a `Node` with `state = "virtual"` (a new variant
in `NodeState`, ordered before `queued`). Same shape as a real node
— same persistence, same panel, same edges — but unrun.

Fields specific to virtual:

- `prompt_draft` — the prompt that will launch if promoted.
- `category` — `planning` | `regular` | `review`.
- `subtype` — `agentic_review` | `human_interact_review`, required
  when `category == "review"`.
- `brief` — `{check_what, expected, abnormal}`, all three required
  when `category == "review"`.
- `scheduled_deps` — node ids (virtual or executed) that must reach
  a terminal state before promotion is allowed.
- `proposed_by` — `"user" | "concierge" | "node:<id>"`. Immutable
  after creation; tile renders a small provenance badge.
- `obsolete_reason` — non-null only after obsoletion.

**No `declared_loads`, no `declared_produces`.** Reads and writes
are observed via the transcript at reap; pre-declaration is dead
ceremony.

**Mode is per-planspace, not per-virtual.** The lane's manifest
carries `mode: auto | manual`, default `manual`. In auto mode, a
virtual auto-promotes when all its `scheduled_deps` are terminal.
In manual mode, promotion requires a user click. Human-interact
review virtuals always pause at promotion regardless of mode (to
collect prose).

**Promotion:** the user clicks the tile (manual) or the scheduler
auto-promotes (auto). Confirmation is the side panel's "promote"
action — no modal popover. The node transitions to `queued` in
place (same id). The dashed outline becomes solid; the dep edges
remain.

**Obsoletion:** an agent or user sets `obsolete_reason` on the
virtual. The tile flips to a greyed-out state. Obsoletion is a
terminal substate — dep children become eligible to promote (the
obsoleted parent vacuously satisfies their dep). `rm` of a tracked
preview is treated as an error; the framework re-materializes it.

**Mutation rights:**

- Anyone may edit any virtual's `prompt_draft`, `category`,
  `brief`, `scheduled_deps` while it is still virtual.
- Promoted nodes' fields are frozen (transcript captures
  launched-with values).
- Provenance (`proposed_by`) is immutable from creation.
- No edit history (last-writer-wins) — provenance is the audit,
  the canvas is the review.

### 3.2 Category enforcement

Each agent's launch system prompt includes a category section:

- **planning** — "You may write your own `preview.json`. You may
  also create new virtual previews in this lane, mutate existing
  virtuals, or obsolete them. Plan the next steps."
- **regular** — "You may write your own `preview.json` only.
  Virtual writes will be rejected at reap. Execute the work."
- **agentic_review** — "You are reviewing N48 with this brief
  [inline]. Read the upstream's preview/transcript/artifacts.
  Write your own preview. You may propose plan adjustments via
  virtual writes; if no adjustments are warranted, do not write
  any. The graph mutations you write (or do not write) are your
  verdict."
- **human_interact_review** — same as agentic_review, plus "The
  human reviewer's prose is at `human-review.md` in this node's
  graph path. Read it and synthesize their intent with your own
  assessment."

**Reap enforcement of category:**

- Regular agents writing virtual previews → hard-fail re-prompt
  (capped at three retries before framework stub).
- Planning and review agents may write any virtual changes their
  reap pass produces.

### 3.3 The preview contract

Every executed node writes `nodes/<id>/preview.json` before reaching
terminal state. The framework enforces this in the runner: if the
agent finishes without a valid preview, the runner re-prompts inline
with the schema. After three failed retries, the framework writes a
stub preview noting "preview contract abandoned" and terminates the
node as error.

**Executed schema** (strict whitelist; unknown fields rejected):

```json
{
  "id": "N48",
  "kind": "agent",
  "category": "regular",
  "state": "done",
  "ran_at": "2026-06-13T14:22Z",
  "lane": "auth-flow",
  "motivation": "Wire the signup form to the API endpoint.",
  "summary": "Built /api/signup integration; one happy-path unit test passes; rate-limiting deferred.",
  "next_implications": "Server-side validation still missing; password-reset flow not started."
}
```

**Virtual schema** (strict whitelist; unknown fields rejected):

```json
{
  "id": "V_reset",
  "kind": "agent",
  "category": "agent",
  "state": "virtual",
  "lane": "auth-flow",
  "proposed_by": "node:N48",
  "motivation": "Users will need password reset before launch.",
  "prompt_draft": "Implement /forgot-password flow ..."
}
```

**Review virtual** adds `subtype` and `brief`:

```json
{
  "id": "V_review_signup",
  "kind": "agent",
  "category": "review",
  "subtype": "human_interact_review",
  "state": "virtual",
  "lane": "auth-flow",
  "proposed_by": "node:N47",
  "motivation": "Verify the signup integration before proceeding.",
  "prompt_draft": "...",
  "scheduled_deps": ["N48"],
  "brief": {
    "check_what": "Run the unit test; verify /api/signup is wired correctly.",
    "expected": "Test passes; no hardcoded URLs; error handling on 4xx responses.",
    "abnormal": "Test mocks the API directly; or coverage drops below 70%."
  }
}
```

Cancelled and errored runs get a framework-written stub preview
explaining the failure; the agent did not get the chance.

### 3.4 The LLM projection as a real filesystem

At every agent launch, the framework **materializes** a real subtree
under `.miniclaw2/graph/lanes/<active-lane>/`:

```
.miniclaw2/graph/lanes/<active-lane>/
  nodes/<nid>/
    preview.json                   # every node in this lane
    transcript.json                # executed nodes only
    artifacts/                     # executed nodes only (symlink ok)
    human-review.md                # only for human-interact reviews
```

**Active lane only.** Other lanes in the project are not
materialized. Cross-lane coordination is the user's job (switching
active lane via the lane header).

These are real files. The agent reads them with the native `Read`
tool — no inlining into the system prompt, no new tools, no path
trickery. The system prompt teaches the convention:

> The active lane lives at `.miniclaw2/graph/lanes/<lane>/`. Each
> node has a `preview.json`; executed ones additionally have
> `transcript.json` and `artifacts/`. Read what you need to.

Materialization mechanism: copy on launch, walk-diff at reap. The
durable node store under `projects/<pid>/nodes/<nid>/` is the source
of truth; the materialized subtree is a per-launch working copy.

CONTEXT.md is **not** materialized into the graph subtree; it stays
injected via the provider's system prompt mechanism (Claude
`system_prompt.append`, Codex prepended to `turn/start`). Today's
behavior is preserved. The canvas renders CONTEXT loads edges to
every executed tile as a visual reminder that every node had access
to the handbook.

### 3.5 The agent write-back protocol

The agent uses native `Write` (and `Edit`) against the materialized
subtree. There is no envelope, no MCP tool, no `graph_writes` JSON.
Three operations expressed as ordinary file writes:

- **Own preview.** The executed node writes
  `.miniclaw2/graph/lanes/<lane>/nodes/<this-id>/preview.json` with
  the executed-schema content. Required to close the agentic loop.
- **New virtual previews** (planning / review only). The agent
  picks a human-readable slug and writes
  `.miniclaw2/graph/lanes/<lane>/nodes/<slug>/preview.json` with
  the virtual-schema content. The framework canonicalizes `<slug>`
  to a real node id at reap and rewrites any cross-references in
  the same session's writes.
- **Obsoletion / mutation of existing virtuals** (planning /
  review only). The agent rewrites an existing virtual's preview
  to set `obsolete_reason`, or to update its prompt_draft / brief
  / deps.

**Reap pass at terminal:**

1. Diff the `.miniclaw2/graph/` subtree against the pre-launch
   snapshot.
2. For each new or changed `preview.json`: validate the schema
   (strict whitelist); assign canonical ids for new virtuals;
   persist into the durable node store. New virtuals get
   `proposed_by: "node:<this-id>"` stamped.
3. **Category enforcement:** if a regular agent wrote any virtual
   previews, hard-fail and re-prompt.
4. **Cycle detection:** walk the dep DAG forward from each new or
   modified virtual; reject the whole batch if a cycle is
   introduced.
5. **Unknown slug references:** if any virtual's `scheduled_deps`
   references a slug or id that doesn't resolve, fail the reap
   and re-prompt.
6. If the agent's own `preview.json` is missing or malformed,
   re-prompt (capped at three retries; then stub).
7. **Atomicity:** all-or-nothing per session. If any validation
   step fails, no writes persist; the agent gets one re-prompt
   round; if it still fails, the session terminates with a stub
   preview.
8. Cancelled / errored runs: framework stub preview for the
   failing node. Virtual writes from the failed session are
   discarded entirely.

The runner only reaps `.miniclaw2/graph/`. Writes to other paths
(the worktree, project-local artifacts) go through existing pipelines
unaffected.

**Live canvas updates are deferred.** v1 is reap-only: virtuals
written mid-session appear on the canvas after the session
terminates. A later revision can add a `PostToolUse` hook that
emits a websocket event on `Write` to `.miniclaw2/graph/`.

### 3.6 Reviews at runtime

A review virtual exists in the planning DAG with its `brief` and
`subtype` set. Behavior at promotion:

**Agentic review:**

1. Scheduler picks the review virtual (auto mode) or the user
   clicks (manual mode).
2. Runner materializes the active lane.
3. Reviewer agent launches. System prompt includes:
   - The category-review section (above).
   - The brief inline.
   - Pointer to the upstream node's preview / transcript /
     artifacts.
4. Agent reads, decides, writes preview.json + optional graph
   mutations.
5. Reap as normal.

**Human-interact review:**

1. Scheduler picks the review virtual (auto mode) or the user
   clicks (manual mode).
2. Node enters `awaiting_human_input` substate. The tile expands
   inline (per PHILOSOPHY §10.2); side panel mirrors the same form.
3. The brief renders read-only; a free-form textarea collects the
   user's prose.
4. User submits. Runner writes prose to
   `.miniclaw2/graph/lanes/<lane>/nodes/<this-id>/human-review.md`.
5. Node transitions to `running`. Reviewer agent launches. System
   prompt includes:
   - The category-review section.
   - The brief inline.
   - Path to `human-review.md` plus instruction to read it.
6. Agent reads (brief + upstream + human prose), synthesizes,
   writes preview.json + optional graph mutations.
7. Reap as normal.

In both subtypes, the reviewer's **verdict is its graph mutations**:
no mutations = "plan unchanged, work accepted"; mutations = "plan
shifts thus." The canvas renders a derived ✓ vs ⚙ badge on the
review tile keyed on mutation count (presentation only; not in
schema).

### 3.7 Auto-promotion and the DAG

The virtual subgraph within a lane is a DAG keyed on
`scheduled_deps`. Multiple parents are allowed; convergence virtuals
work naturally.

**Promotion scheduler** is event-driven, not polling. On every
node-terminal event:

1. Walk the active lane's virtuals.
2. For each virtual whose `scheduled_deps` are all terminal AND
   whose mode permits auto-promotion, queue for promotion.
3. Promotion order when multiple are eligible: **creation order**
   (earliest-created first), ties broken by node id.
4. Promote one at a time (the project still runs one node at a
   time on the single worktree).

In manual mode, no auto-promotion. Eligible virtuals render a
visible "ready to promote" affordance on the tile; the user clicks.

**Multi-lane projects:** only the active lane's virtuals are
considered. Other lanes' eligible virtuals stay pending until the
user switches active lane.

**Failure cascade:** errored / cancelled upstream still counts as
terminal. Dependents with a failed parent become eligible and the
agent decides what to do about the failure from prose.

**Obsoletion of a parent:** obsolete is a terminal substate.
Dependents become eligible vacuously; the agent inherits an
empty-result parent.

### 3.8 Anti-self-poisoning

Anti-self-poisoning is launch-prompt guidance for **all** preview
writes reaped from `.miniclaw2/graph/` — the writing node's own,
plus any new virtual previews. Agents are told not to commit:

- Transient errors not commited as durable findings.
- Negative tool claims not committed as durable facts.
- Single-run environment quirks not committed unless reproducible.
- Virtual workarounds whose motivation is only to plan around a
  transient failure.

The framework does **not** perform reap-time semantic filtering for
these categories. It validates schemas, category rights, dependency
references, and cycles; it does not rewrite, strip, or reject preview
content because of anti-self-poisoning guidance.


## 4. What this replaces

- **`STATUS.md` as a maintained file.** Removed from the planspace
  plug layout. The `goal`, `current_state`, `open_questions`,
  `decisions`, `out_of_scope` slots are deleted. Their content
  lives in node previews going forward.
- **`PLAN.md` as a maintained file.** Removed entirely. The plan
  is the virtual-node subgraph.
- **`PlanspacePanel.tsx` STATUS viewer + slot editor.** Drop
  entirely. Clicking the lane header frames the lane on the
  canvas; lane IS the panel.
- **The `planspace-update` artifact contract.** Replaced by the
  preview contract.
- **`backend/miniclaw2/planspace_state.py`.** Removed. STATUS
  slot operations (`append_observation`, `rewrite_current_state`,
  `add_open_question`, `add_decision`, `add_out_of_scope`) are
  removed; agents write previews instead.
- **Memory-delta inbox.** Removed entirely. The reap pipeline
  replaces the inbox; previews ARE the deltas.
- **`backend/miniclaw2/contextspace.py` planspace plug code paths
  reading STATUS.md / PLAN.md / SKILLS.md.** Removed; planspace
  plug layout becomes node-collection-shaped.
- **`prompts/concierge_bootstrap.md`.** Rewritten as a planning-
  category prompt that emits 3-5 virtual previews instead of
  filling STATUS slots.
- **`NodeKind.gate` and the passive-gate code path.**
  `NodeRunner._run_passive_gate` is removed. `NodeKind = {agent,
  op}`. Reviews are agents with `category=review`.
- **`requires_review` flag on Node.** Removed. Reviews are
  proposed as virtuals by planning nodes, not implied by an
  upstream's flag.
- **`HumanGate` model (checkpoint subtype).** Shrinks to inline-
  only (`permission`, `ask_user`, `plan_approval`).
- **`AcceptanceState`, `verdict_source`, `verdict_artifact_path`,
  `verdict_thread_id`, `accepted_at`, `rejected_at`,
  `review_outcome`.** Removed. The reviewer's graph mutations are
  the verdict.
- **`declared_loads`, `declared_produces` (proposal v1 fields).**
  Never landed; rejected as legacy markdown-era ceremony.
- **`output_kind`, `output_path`, `output_contract_snapshot`.**
  Already removed per `IMPLEMENTATION_STATUS.md`.
- **`needs_review` checkbox in the phantom composer.** Replaced
  by a category picker.
- **Cross-lane `↗ loaded:` tile chips.** Drop; cross-lane content
  is not materialized.


## 5. What stays the same

- The `Node` / `Project` / `HumanGate` (inline-only) ontology and
  the executed-node state machine (apart from prepending `virtual`
  to it and adding `awaiting_human_input`).
- Inline gates within agent sessions (permission, ask_user,
  plan_approval) — substates of running, unchanged.
- `<project_root>/CONTEXT.md` as the codebase-facing handbook.
  Injected via `system_prompt.append` (Claude) / prepended to
  `turn/start` (Codex). Not materialized into the graph subtree.
- ContextSpace plug layout for `global`, `skill`, `protocol`
  plugs. Planspace plug's contents transform from STATUS/PLAN to
  a node collection.
- Anti-self-poisoning guidance in the launch prompt.
- Auto-commit op as framework-injected, outside the virtual
  pipeline. Appears as edge chevron or trailing tile.
- One node at a time per project. FS state coherence via linear
  execution; the dep DAG is planning structure only.
- Scenario / Tests modal machinery (separate path from virtuals).


## 6. Disk and storage

Project storage already mirrors most of the proposed shape under
`projects/<pid>/nodes/<nid>/`. The deltas:

```
projects/<pid>/nodes/<nid>/
  node.json            # existing — full Node fields, including
                       # state=virtual, category, subtype, brief
  events.jsonl         # existing — present for executed runs,
                       # absent for virtual
  gates.jsonl          # existing — inline gates only
  preview.json         # NEW — required for executed nodes;
                       # declarative for virtual
  human-review.md      # NEW — human-interact reviews only,
                       # copied here from materialized graph/
                       # at reap; persists for transcript replay
  artifacts/...        # existing under .miniclaw2/outputs/<nid>/
```

ContextSpace per-lane storage drops STATUS.md / PLAN.md / SKILLS.md
and the memory-delta inbox:

```
contextspace/plugs/planspaces/<id>/
  manifest.yaml        # existing, gains: mode: auto|manual
                       # default: manual
  events.jsonl         # existing
```

Manifest gains a `mode` field driving auto/manual promotion.

**Per-launch materialization** copies the active lane's node store
(or symlinks where safe) into `.miniclaw2/graph/lanes/<active-lane>/`
under the worktree. The runner takes a snapshot of the subtree
state before launching; at terminal it walk-diffs to determine
reap actions.

**Tool access:** `.miniclaw2/` is inside the worktree, so the
Claude SDK's `add_dirs` / Codex's cwd allow access by default.
Verify dotted-directory access isn't blocked by default deny rules
during impl.


## 7. Migration

**Nuke and restart.** Existing planspaces with accumulated STATUS /
PLAN content are deleted at upgrade time. This is a dogfood
project; no users to migrate.

No back-dated preview synthesis, no `legacy/` directory, no
agent-driven backfill. Saves ~30% of the proposal surface and
removes a class of validation edge cases.

Concrete steps at impl time:

1. Delete `contextspace/plugs/planspaces/` for all directions.
2. Delete `Project.settings_override["active_planspace_id"]`.
3. Drop `ProjectBinding` / `PlugRef` references to planspace
   plugs.
4. Users (i.e., the developers) recreate planspaces from scratch
   via concierge bootstrap.


## 8. Non-goals

- **A `phase` / `milestone` schema.** Virtual nodes with
  dependencies are expressive enough.
- **Alternate-path sibling virtuals on the same parent.** Use
  project forks (PHILOSOPHY §6.2) for "explore both X and Y"
  workflows. Defer for v1.
- **Schema-validated declared loads / produces.** Both fields
  retire; reads and writes come from the transcript.
- **Acceptance gates on virtual-node mutations.** Mutation is
  direct; provenance is the audit.
- **Preview edit history.** Last-writer-wins.
- **Question / decision / scope_line node kinds.** Explicitly
  rejected. These concepts express themselves in preview prose.
- **A synthesized `current_state` paragraph at launch.** No
  projector summarization. The next agent reads recent previews
  in order.
- **Auto STATUS export.** No durable on-disk projection. Export
  is an explicit user action that renders on demand.
- **An MCP tool for virtual creation.** Native `Write` against
  the materialized subtree is the interface.
- **A JSON terminal-output envelope.** Rejected — the agent
  already writes files.
- **Live `PostToolUse` canvas updates in v1.** Reap-only first.
- **Deletion via `rm` as the obsoletion mechanism.** Obsoletion
  rewrites the preview to set `obsolete_reason`. `rm` of a
  tracked preview at reap is treated as an error.
- **A separate `gate` node kind.** Folded into `category=review`.
- **An accept/reject verdict enum or `acceptance_state` field.**
  Verdicts ARE graph mutations.
- **A passive-gate runtime path with no provider call.** All
  reviews launch a reviewer agent.
- **Per-virtual mode override on top of planspace default.** Mode
  is per-planspace only.
- **Cross-lane materialization.** Active lane only.
- **`CLAUDE.md` walk / `.claude/settings.json` ingestion.**
  Tracked separately in IMPLEMENTATION_STATUS §7; not part of
  this redesign.
- **Cross-provider reviewer nodes as a special case.** They are
  just agentic_review nodes whose configured provider differs
  from the project default. No new ontology.


## 9. Resolved questions

These were open in earlier proposal drafts; settled here:

- **Q1 (obsolete visibility timing):** obsoleted virtuals stay
  visible briefly, then collapse behind a "+N obsolete"
  affordance on the lane.
- **Q2 (sibling virtuals on same parent):** non-goal. Use project
  forks.
- **Q3 (cross-lane loads rendering):** non-goal. Cross-lane
  materialization is dropped; cross-lane chips drop.
- **Q4 (concierge prompt craftsmanship):** invest in this
  alongside the reap pipeline; the concierge is load-bearing UX.
- **Q5 (re-prompt retry bound):** 3 retries then framework stub.
- **Q6 (unresolved slug behavior):** fail the reap, re-prompt.


## 10. Why we keep these constraints

- **Anyone, anytime mutation of virtuals (pre-promotion).** Plans
  drift. Review gates on every agent-proposed plan change would
  make replanning expensive and reintroduce the schema-entry
  burden.
- **Filesystem as the interface, native tools as the protocol.**
  The agent uses the same `Read` and `Write` it uses everywhere
  else. No envelope means no schema migration when the wire
  format would have changed; no new tool means no per-provider
  tool registration.
- **Strict whitelist schema validation.** Unknown fields are
  rejected at reap. Forces discipline; makes the projection
  predictable for the next agent.
- **No `goal`, no `current_state`, no STATUS schema.** A single
  sentence cannot capture progressive or bootstrap workflows.
- **Filesystem projection of the active lane only.** Matches
  PHILOSOPHY §7's "one active planspace per launch." No
  cross-lane bleed.
- **Required preview to close the loop.** Without enforcement,
  agents will sometimes skip the write under length pressure.
  The cost is one schema check at terminal transition; cap at
  three retries to bound pathological loops.
- **Category-as-orthogonal-axis, not kind.** A planning agent
  and a regular agent are the same kind of thing executionally;
  what differs is what they may write. Putting the role on a
  separate axis keeps the kind axis (agent/op) small.
- **Gates are reviews are agents.** No separate node kind, no
  separate verdict enum, no separate acceptance state. Reduces
  the number of code paths, persistence shapes, and wire
  envelopes the framework has to support.
- **Free-form human prose, synthesized by an agent.** The user's
  voice survives verbatim at `human-review.md`; the agent's
  preview is the synthesis. No JSON judgment schema, no
  approve/reject radio.
- **DAG for planning, linear execution for FS coherence.** The
  DAG governs promotion eligibility only. FS state is still
  strictly linear, preserving §11's "one node at a time" rule
  without requiring fork+merge machinery.
- **Per-planspace mode (auto/manual), default manual.** Matches
  the principle of user-in-command. Auto is the opt-in
  optimization.


## 11. Implementation sequencing

Settled at the start of the design session: **horizontal
backend-first**. Order:

1. Domain types: `NodeKind = {agent, op}`, `NodeState += virtual,
   awaiting_human_input`, category / subtype / brief fields,
   remove `AcceptanceState` / `verdict_*` / `requires_review` /
   `output_*`.
2. Preview module: parse / validate / persist preview.json (both
   schemas). Strict whitelist.
3. Materialization: pre-launch copy of active lane into
   `.miniclaw2/graph/lanes/<active-lane>/`.
4. Reap pipeline: walk-diff, schema validation, category
   enforcement, cycle detection, slug canonicalization,
   atomicity.
5. Auto-promotion scheduler: on node-terminal events, walk the
   lane's DAG, promote in creation order.
6. Category-aware launch prompts: four templates (planning,
   regular, agentic_review, human_interact_review).
7. Human-interact substate in the runner; prose write-out.
8. Concierge bootstrap as planning-category agent with crafted
   prompt.
9. Remove legacy paths: passive gate runner, STATUS/PLAN
   loaders, memory-delta inbox, planspace_state.py.
10. Frontend pass: virtual tile rendering, category badges, dep
    edge layout, side panel virtual editor, drop PlanspacePanel
    STATUS UI, drop cross-lane chips, drop ArtifactNode refs,
    mode toggle in lane header.
11. Wire envelopes: add `category` to `node_started`, add
    `awaiting_human_input` to state changes, new interaction
    type for human review prose collection.
