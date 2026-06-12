# Proposal: Virtual nodes and the filesystem projection

Status: draft, not landed.
Companion to `PHILOSOPHY.md` (commitments) and `IMPLEMENTATION_STATUS.md`
(what is built).

This proposal eliminates `STATUS.md` and `PLAN.md` as maintained files.
The planspace becomes a graph of nodes (executed and virtual). At every
launch, the framework projects the relevant slice of that graph into a
synthesized filesystem for the agent to read. A required `preview.json`
per node closes the agentic loop and carries the orientation the next
agent needs.

This is the bridge between the updated `PHILOSOPHY.md` §§4, 6.1, 7, 8
and the code, which still operates the old STATUS/PLAN harness.


## 1. The pain we are fixing

After concierge bootstrap finishes filling STATUS slots, the user
stares at a single empty phantom and the framework goes silent. They
must invent the first prompt unaided, decide whether to plan or to
implement, and re-decide that for every subsequent node. PHILOSOPHY §3
promises the framework absorbs setup cost; §5 promises the user does
not micromanage nodes inside a planspace. Both promises break the
moment bootstrap returns control.

The user named the deeper tension: real workflows are progressive and
sometimes bootstrap (set up the toolchain, discover I need shadcn,
then implement the auth UI). A single static `goal` cannot describe
this; neither can the current `goal` + `current_state` snapshot, which
is also static, just hidden behind two slots.


## 2. Diagnosis

The burden lives at the node-launch boundary. STATUS is the wrong
place to fight it: STATUS is a snapshot, while the user is asking
"what next?" The answer to "what next?" is a *plan* — concrete enough
to click. Today we surface the plan as a markdown checklist (PLAN.md),
which PHILOSOPHY §4 says should not exist. Any concept worth
surfacing should be a node or an edge.

So: dissolve STATUS and PLAN into the graph. Executed nodes are the
"what we've done." Virtual nodes are the "what we plan to do." Both
are nodes; both live on the canvas; both are written to the same
filesystem the agent reads.


## 3. The change

### 3.1 Virtual nodes

A virtual node is a `Node` in `state = "virtual"` (a new variant in
`NodeState`, ordered before `queued`). Same shape as a real node —
same persistence, same panel, same edges — but unrun.

Fields specific to virtual:

- `prompt_draft` — the prompt that will launch if promoted.
- `declared_loads` — refs the launch will read. Each is `{kind:
  "node", id}` / `{kind: "file", path}` / `{kind: "lane", id, path}`.
- `declared_produces` — `{path, kind: "file" | "directory"}` items.
- `scheduled_deps` — node ids (virtual or executed) that must reach a
  terminal state before promotion is allowed.
- `proposed_by` — `"user" | "concierge" | "node:<id>"`. Immutable
  after creation; tile renders a small provenance badge.

Promotion: the user clicks the tile; a small popover confirms the
prompt; the node transitions to `queued` in place (same id). The
dashed outline becomes solid; the loads/produces edges remain.

Obsoletion: setting the field `obsolete_reason` flips the tile to a
greyed-out state. Obsoleted virtuals stay visible briefly, then
collapse behind a "+N obsolete" affordance on the lane.

Mutation rights: anyone may edit any virtual node at any time. There
are no review gates on plan changes. Provenance is the audit; the
canvas is the review. No edit history is kept (last-writer-wins);
this is a deliberate non-goal — see §8.

### 3.2 The preview contract

Every executed node writes `nodes/<id>/preview.json` before reaching
terminal state. The framework enforces this in the runner: if the
agent finishes without a valid preview, the runner re-prompts inline
with the schema and lets the agent close the loop. Only after a valid
write does the node transition to `done` / `error` / `cancelled`.

**Executed schema:**

```json
{
  "id": "N48",
  "kind": "agent",
  "state": "done",
  "acceptance": "unreviewed",
  "ran_at": "2026-06-12T14:22Z",
  "lane": "auth-flow",
  "loaded": ["N45", "N47", "frontend/auth/"],
  "motivation": "Wire the signup form to the API endpoint.",
  "summary": "Built /api/signup integration; one happy-path unit test passes; rate-limiting deferred.",
  "produces": ["frontend/auth/Signup.tsx", "frontend/auth/__tests__/Signup.test.tsx"],
  "next_implications": "Server-side validation still missing; password-reset flow not started."
}
```

**Virtual schema:**

```json
{
  "id": "V14",
  "kind": "agent",
  "state": "virtual",
  "proposed_by": "node:N48",
  "lane": "auth-flow",
  "motivation": "Users will need password reset before launch.",
  "prompt_draft": "Implement /forgot-password flow ...",
  "declared_loads": ["frontend/auth/", "N48"],
  "declared_produces": ["frontend/auth/Reset.tsx"],
  "scheduled_deps": []
}
```

Cancelled and errored runs get a framework-written stub preview from
the runner explaining the failure; the agent did not get the chance.
These appear in the projection with `state: "error"` / `"cancelled"`
so the next agent knows what was attempted and how it failed.

The required fields (`motivation`, `summary`/`prompt_draft`,
`produces`/`declared_produces`) are validated server-side. Missing
fields trigger the re-prompt loop.

### 3.3 The LLM projection as a real filesystem

At every agent launch, the framework **materializes** a real subtree
under the working directory, rooted at the lane:

```
graph/
  lanes/<lane-id>/
    nodes/<nid>/preview.json     # every node on the active lane
    nodes/<nid>/preview.json     # cross-lane nodes only via this node's declared_loads
```

These are real files on disk. The agent reads them with the native
`Read` tool — no inlining into the system prompt, no new tools, no
new path scheme. The system prompt teaches the convention ("recent
work and the current plan live under `graph/lanes/...`"); the
agent's existing filesystem fluency does the rest.

`transcript.json` and `artifacts/` live at the same paths but are
**not** pre-materialized. The agent reaches for them on demand when
it needs depth. The shallow level is cheap; the deep level is
on-demand.

Scope: the active lane is fully materialized. Cross-lane previews
appear only when this node's `declared_loads` references them. This
is predictable and matches the user's mental model of "I only need
to know about other lanes I asked for." A long-running lane that
hits its token budget will be addressed when that becomes
measurable; not pre-optimized.

The materialized subtree is the surface the agent reads *and writes*
— see §3.4.

### 3.4 The agent write-back protocol

The agent uses native `Write` (and `Edit`) against the materialized
subtree. There is **no envelope, no MCP tool, no `graph_writes` JSON**
— the filesystem the agent reads is the same filesystem it writes to.

Three operations expressed as ordinary file writes:

- **Own preview.** The executed node writes
  `graph/lanes/<lane-id>/nodes/<this-id>/preview.json` with the
  executed-schema content. Required to close the agentic loop.
- **New virtual previews.** The agent picks a human-readable slug and
  writes `graph/lanes/<lane-id>/nodes/<slug>/preview.json` with the
  virtual-schema content. Slug is convention-only — the framework
  canonicalizes `<slug>` to a real node id (`V20`, etc.) at reap time
  and rewrites any cross-references in the same session's writes.
- **Obsoletion.** The agent rewrites an existing virtual's preview
  to set an `obsolete_reason` field. Deletion via `rm` is not the
  obsoletion mechanism — keeping the file preserves provenance and
  disambiguates "agent obsoleted this" from "agent failed to write."

**Reap pass at terminal:**

1. The runner diffs the `graph/` subtree against a snapshot taken
   before the agent ran.
2. For each new or changed `preview.json`: validate the schema
   server-side; run the anti-self-poisoning filter; assign canonical
   ids for new virtuals; persist into the durable node store. New
   virtual previews get `proposed_by: "node:<this-id>"` stamped by
   the runner.
3. If the agent's own `preview.json` is missing or malformed, the
   runner re-prompts inline (existing loop) and lets the agent close
   the contract before terminal transition.
4. Cancelled / errored runs: the framework writes a stub preview for
   the failing node. Any virtual-preview writes the agent made before
   the failure are discarded — failed runs do not promote durable
   plan additions (matches the rule that failed nodes do not promote
   memory deltas).

The runner only reaps `graph/`. Writes to other paths (the working
worktree, project-local artifacts) are normal file writes and go
through the existing pipelines unaffected.

**Live canvas updates are deferred.** v1 is reap-only: a virtual the
agent writes mid-session appears on the canvas after the session
terminates. The cost is a brief delay between proposal and
visualization; the benefit is no `PostToolUse` hook plumbing in v1.
A later revision can add a hook that emits a websocket event on
`Write` to a `graph/` path if the delay proves disruptive.

### 3.5 Anti-self-poisoning

The existing filter contract applies to *all* preview writes reaped
from `graph/` — the writing node's own, plus any new virtual
previews. Same categories: no transient errors as durable findings,
no negative tool claims, no single-run environment quirks. A
sentence is added specifically about not encoding session noise as
future plan steps: "if the failure mode is transient, do not propose
a virtual node to work around it."

The filter runs at reap, not at write time. The agent is briefed in
the launch system prompt about what categories not to commit; if it
slips, the filter rewrites or strips the content at commit. The
user-visible behavior matches the current contract, and the agent
need not learn a new tool or block on a synchronous filter call.


## 4. What this replaces

- **`STATUS.md` as a maintained file.** Removed from the planspace
  plug layout. The `goal`, `current_state`, `open_questions`,
  `decisions`, `out_of_scope` slots are deleted. Their content lives
  in node previews going forward; they emerge from reading recent
  previews rather than being maintained as separate state.
- **`PLAN.md` as a maintained file.** Removed entirely. The plan is
  the virtual-node subgraph; PLAN never had a source of truth other
  than STATUS anyway.
- **The `planspace-update` artifact contract.** Replaced by the
  preview contract. Same enforcement pattern (re-prompt on missing
  artifact), narrower schema (just preview + optional new virtual
  previews).
- **`backend/miniclaw2/planspace_state.py`.** Replaced by a
  `preview` module that parses, validates, renders, and applies
  preview writes. The old STATUS update operations (`append_observation`,
  `rewrite_current_state`, `add_open_question`, `add_decision`,
  `add_out_of_scope`) are removed; agents write previews instead.
- **The `goal` slot.** Already absent under this design; the lane
  label plus the earliest preview's `motivation` carries that
  meaning.
- **`prompts/concierge_bootstrap.md`.** Rewritten. Instead of filling
  STATUS slots, the concierge emits three to five `add virtual node`
  graph writes with rough prompts and dependencies, plus its own
  setup preview summarizing what the direction is for.


## 5. What stays the same

- The `Node` / `Project` / `HumanGate` ontology and the executed-node
  state machine (apart from prepending `virtual` to it).
- Three node kinds only: `agent`, `gate`, `op`. No `question`,
  `decision`, or `scope_line` kinds — those are things a node
  *describes in prose* inside its preview.
- Type-A vs Type-B gates, passive checkpoint gates, the merge
  template.
- `<project_root>/CONTEXT.md` as the codebase-facing handbook — its
  role is unchanged.
- ContextSpace plug layout, with the planspace plug's contents
  redefined from STATUS/PLAN files to a node collection.
- Anti-self-poisoning enforcement at write time.
- Memory-delta inbox semantics (auto-apply on accepted-or-unreviewed
  terminal nodes; no apply on failed / cancelled / rejected).


## 6. Disk and storage

Project storage already mirrors the proposed shape under
`projects/<pid>/nodes/<nid>/`. The deltas:

```
projects/<pid>/nodes/<nid>/
  node.json            # existing — full Node fields, including state=virtual
  events.jsonl         # existing — present for executed runs, absent for virtual
  gates.jsonl          # existing
  preview.json         # NEW — required for executed nodes; declarative for virtual
  transcript.json      # NEW — derived from events.jsonl, or just expose events.jsonl
  artifacts/...        # existing under .miniclaw2/outputs/<nid>/
```

ContextSpace per-lane storage drops STATUS.md / PLAN.md:

```
contextspace/plugs/planspaces/<id>/
  manifest.yaml        # existing
  events.jsonl         # existing
  inbox/...            # existing — receives preview-shaped memory deltas
```

Cross-project cross-lane reads work the same way they do today
(snapshotted with hashes), but the materialized payload is
`preview.json` rather than STATUS.md.

**Per-launch materialization.** Before each agent runs, the runner
copies the relevant slice of the durable store into the working
directory's `graph/` subtree. This materialized subtree is the
read/write surface for the session and is reaped at terminal. The
durable node store (under `projects/<pid>/nodes/<nid>/`) is the
source of truth; the materialized subtree is a per-launch working
copy.


## 7. Migration

Existing planspaces with STATUS.md / PLAN.md / accumulated body get a
one-time migration:

1. Concierge-style agent reads the existing STATUS frontmatter and
   body, plus the recent node history.
2. It writes back-dated `preview.json` files for the existing
   executed nodes, synthesizing `motivation` / `summary` /
   `next_implications` from whatever signal it can find (body
   timestamps tied to node ids, summaries from `node.json`,
   commit messages).
3. Open questions and decisions from STATUS become content inside
   the most recent preview's `next_implications` field.
4. STATUS.md and PLAN.md are deleted from disk.
5. Out-of-scope items become a single appended note on the lane's
   first preview.

This is a best-effort migration. We accept that some prose nuance
will be lost; the user can edit a preview by hand to restore it. The
old files are kept in a one-time `legacy/` subdirectory for thirty
days then removed.


## 8. Non-goals

- **A `phase` / `milestone` schema.** Virtual nodes with dependencies
  are expressive enough; introducing a second hierarchy re-creates
  the schema-entry burden §3 rejects.
- **Schema-validated declared loads.** The loader is permissive —
  paths that no longer resolve at launch degrade to a runtime warning
  in the agent's projection; the run proceeds.
- **A separate `/replan` slash.** Replanning is just the finishing
  agent emitting virtual-previews as part of its graph writes. The
  user can still create a deliberate "rethink the plan" virtual node
  by typing that into a phantom.
- **Acceptance gates on virtual-node mutations.** Mutation is direct;
  provenance is the audit. Adding a per-update review gate would
  break the "anyone, anytime" flow.
- **Preview edit history.** Last-writer-wins. If we discover the
  audit gap matters in practice, a `preview.history.jsonl` is a
  trivial addition.
- **Question / decision / scope_line node kinds.** Explicitly
  rejected. These concepts express themselves in preview prose; the
  LLM is fluent enough to synthesize "what are the open questions?"
  from reading recent previews live.
- **A synthesized `current_state` paragraph.** No projector
  summarization at launch time. The next agent reads recent previews
  in order and orients itself; no intermediate summary node, no
  pre-launch LLM call.
- **Auto STATUS export.** No durable on-disk projection. If the user
  wants a textual export for sharing, an explicit Export action
  renders the projection to a chosen path on demand.
- **An MCP tool for virtual creation.** Native `Write` against the
  materialized subtree is the interface. No `create_virtual`,
  `obsolete_virtual`, or `update_preview` tool ever ships.
- **A JSON terminal-output envelope.** Earlier drafts of this proposal
  sketched a `graph_writes` array as the agent's terminal output.
  Rejected — it duplicates what the agent already does with `Write`,
  imposes a schema-versioned wire format that has to evolve in lock
  step with the on-disk shape, and costs tokens in the contract
  itself.
- **Live `PostToolUse` canvas updates in v1.** Reap-only first.
  Virtuals appear on the canvas after the session terminates. A later
  revision can add a websocket-emitting hook if the delay is
  disruptive.
- **Deletion via `rm` as the obsoletion mechanism.** Obsoletion
  rewrites the preview to set `obsolete_reason`. The file persists;
  provenance is preserved. `rm` of a tracked preview at reap is
  treated as an error (the runner logs and re-materializes on the
  next launch).


## 9. Open questions

These are items I would not commit to in v1 without further
discussion:

- **Q1.** When an executed agent obsoletes a downstream virtual,
  should that show as a visible "+1 obsolete" pill on the lane
  immediately, or fold in only after the user has navigated past it?
- **Q2.** Should the user be able to fork a virtual into alternate-
  path siblings ("explore both shadcn and custom") within a single
  lane, or does that always become a project fork? Intersects with
  the existing fork ontology.
- **Q3.** Cross-lane `declared_loads`: do we surface them as the
  current dashed `loads` edges, or as a single "↗ loaded:" chip on
  the tile? Today's behavior is the chip plus the auto-hidden edge;
  the proposal does not change it, but a cleanup pass may be wanted.
- **Q4.** What is the exact concierge prompt that produces three to
  five starter virtual nodes from a paragraph of motivation? The
  field schema is fixed; the prompt that writes good motivations and
  good dependencies is craftsmanship — likely needs iteration on
  real seeds.
- **Q5.** When the preview re-prompt loop fires (agent finished
  without a valid preview), should it bound the number of retries
  before giving up and writing a stub from the framework? A
  pathological agent could loop forever.
- **Q6.** Slug→canonical-id assignment happens at reap. Within a
  single session an agent may write virtual A whose `declared_loads`
  references virtual B by slug — the framework must rewrite the
  reference to B's canonical id at reap. What if the agent references
  a slug that doesn't exist (typo, or B was never written)?
  Probably: leave the unresolved slug visible in the projection and
  warn the next agent ("`declared_loads` refers to unknown node X —
  typo, or never created?"). Confirm.


## 10. Why we keep these constraints

- **Anyone, anytime mutation of virtuals.** Plans drift. Review gates
  on every agent-proposed plan change would make replanning
  expensive and reintroduce the schema-entry burden.
- **Filesystem as the interface, native tools as the protocol.** The
  agent uses the same `Read` and `Write` it uses everywhere else.
  No envelope means no schema migration when the wire format would
  have changed; no new tool means no per-provider tool registration;
  no in-band protocol means no token tax on the contract itself. One
  channel, one anti-self-poisoning pass, one persistence story: "your
  preview writes are the file writes you make under `graph/`."
- **Declared loads/produces as fields, edges as the render.** §4
  again: if dataflow is worth knowing, it is worth being a graph
  edge. Free-text hints would have to be re-parsed every time the
  canvas redraws.
- **No `goal`, no `current_state`, no STATUS schema.** A single
  sentence cannot capture progressive or bootstrap workflows. The
  user told us this directly. Refusing to add a richer goal schema
  is consistent with §3 (no schema-entry). Letting the constellation
  of previews carry that meaning is consistent with §4 (the graph is
  the state).
- **Filesystem projection, not document projection.** The LLM is
  fluent with filesystems and with markdown both. Filesystem makes
  the "cheap level injected, deep level on-demand" split natural and
  matches the on-disk storage shape 1-to-1, so there is no synthesis
  step between truth and projection.
- **Required preview to close the loop.** Without enforcement, agents
  will sometimes skip the write under length pressure. With
  enforcement, the next agent always has at least the three-sentence
  orientation it needs. The cost is one schema check at terminal
  transition.
