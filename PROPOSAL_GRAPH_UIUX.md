# The Graph UI/UX — Position and Open Directions (2026-07)

A design essay on the high-level graph-based UI/UX, argued from
`PHILOSOPHY.md`. Companion to `PROPOSAL_DESIGN_REVIEW.md` (which covers
code-level findings); this document covers where the product surface
should go next. Every direction proposed here extends an existing
philosophical commitment rather than adding a new concept — the test
any proposal must pass, given §4's rule that what cannot be a node or
an edge probably should not be surfaced.


## 1. What the design fundamentally is

The deepest idea in `PHILOSOPHY.md` is not "show a graph" — workflow
tools (n8n, LangGraph Studio) do that. It is that **the plan and the
history are the same data structure at different tenses.** Virtual
nodes are future tense, the running node is present tense, executed
previews are past tense — one lane holds all three, and executing the
plan is nodes changing tense in place.

Chat UIs collapse intent, execution, and pending decisions into one
scroll where they erase each other. Workflow builders separate
"definition" from "run history" into different screens. MiniClaw2's
tense-unification is genuinely novel, and it is what makes the
composer-as-virtual-node (§10.2) and verdict-as-mutations (§9.3)
coherent rather than gimmicky.

The second load-bearing idea is attention economics: §1 says a node
matters because it is *blocked on you*. The canvas is an attention
allocator wearing a diagram costume.

Once those two ideas are named, both the latent tensions and the
future directions fall out of them.


## 2. Where the philosophy is under-specified

### 2.1 The graph has no doctrine of forgetting

§8.4 carefully protects the *LLM projection* from accumulating noise,
and §8.3 insists a coherent summary is "produced on demand — never
maintained." The *visual* projection has no equivalent discipline.
"The graph is the state" is total: state accretes forever, and a
three-month lane becomes an archaeology site where the five tiles that
matter are buried under two hundred that don't. A chat at least
scrolls old turns away; the canvas keeps everything at equal visual
weight. This is the same self-poisoning failure mode §8.4 warns
about — at the retina instead of the context window.

### 2.2 Attention is pull, not push

The philosophy says gates are the interesting events, but the canvas
only reveals a gate if the user is already looking at that project.
The real unit of supervision is "what, across everything I have
delegated, is blocked on me right now?" — and no surface answers it.
The philosophy *implies an inbox*; the UI only built canvases.

### 2.3 The visual grammar promises concurrency the substrate forbids

A dep DAG with branches reads as "these run in parallel," but
execution is strictly serial per project (§6.2, correctly, for FS
coherence). Nothing on the canvas communicates the actual
serialization order. Forks are the sanctioned resolution of this
tension, and forks are the one major ontology piece never built — so
today the tension is resolved by the user's confusion.

### 2.4 Plan mutations are invisible as events

§9.3's best idea — the reviewer's verdict *is* its graph mutations —
has no reading surface. After a planning or review node runs, the
virtual subgraph is silently different; the user must diff the plan in
their head. For a product whose thesis is "outputs are graph
mutations," the mutation itself is strangely not a first-class,
reviewable thing.


## 3. Open directions

Ranked at the end; each direction names the commitment it extends.

### A. The attention inbox as the true top-level surface

*Extends §1 (gates are the interesting events).*

A cross-project queue of human gates: awaiting-input reviews, pending
ask-user questions, lanes gone idle with unpromoted ready virtuals.
Each entry is a doorway that drops the user onto the canvas, zoomed to
the tile. This does not compete with the graph — it completes §1. The
projects landing page is the natural host; today it lists projects by
name, which is inventory, not attention.

This is arguably the highest-leverage UX investment available: it is
what makes supervising five delegated directions feel different from
tabbing between five chat windows.

### B. Plan-diff as a reviewable artifact

*Extends §8 (outputs are graph mutations) and §9.3 (verdict =
mutations).*

When a node's reap creates, rewrites, or obsoletes virtuals, render
the mutation as a diff — "this review added two steps, rewrote one
prompt, obsoleted the deploy step" — as an expandable annotation on
the node that caused it (`proposed_by` provenance already exists).

Two payoffs. First, verdicts become *readable* at a glance — the ✓/⚙
badge of §9.3 is the degenerate one-bit version of this view. Second,
it opens a natural future gate: *approve the plan change* before auto
mode acts on it. As agents write more of the plan, the user's role
shifts from author to editor — and an editor needs a diff view, not a
re-read.

### C. A doctrine of graph compaction

*Extends §8.4 (anti-self-poisoning) and §8.3 (summaries on demand,
never maintained).*

Give lanes temporal level-of-detail: completed causal chains collapse
into a single "epoch" capsule — expandable, never deleted; the durable
store already keeps everything — while the active frontier (running
node, ready virtuals, pending gates) always renders at full size. The
philosophy's own on-demand-summary stance supplies the mechanism: an
epoch's collapsed label can be synthesized from its previews when
needed and never maintained as durable state.

This keeps "the graph is the state" true while making the *rendered*
graph a working set instead of an archive. It becomes existential the
first time someone runs a 200-node lane.

### D. Finish the git metaphor — the missing verbs

*Extends §4 (the git-IDE analogy) and §6.2 (concurrency = forks).*

§4 chose git IDEs as the analogy; taken seriously, it is a roadmap.
Mapping plan-space onto git verbs:

| Verb | Plan-space equivalent | Status |
|---|---|---|
| blame | `proposed_by` provenance | exists |
| revert | `obsolete_reason` | exists |
| cherry-pick | user templates (subgraph capture/stamp) | exists — and delightful |
| diff | plan-diff (direction B) | missing |
| branch / merge | forks across worktrees | named in §6.2, unbuilt |
| rebase | re-anchoring a stack of virtuals onto a different upstream after a review reshapes the plan | missing (today: manual dep editing, tile by tile) |

Forks are the big one: multi-worktree lanes stacked in one workspace
view would finally cash the check the DAG visuals write (§2.3 above).

### E. Alternate projections over the flat graph

*Extends §10.4 (planspace as layout, not containment).*

§10.4 explicitly kept the graph flat *so that* views by time, commit,
or importance stay possible — the option was purchased and never
exercised. The most valuable first projection is **cost/time**:
sessions take minutes and burn tokens, and the supervision judgment
"is this direction earning its spend?" has no surface today (token
usage exists per node but never aggregates). A "by importance" view is
essentially the inbox (A) rendered spatially.

### F. Deepen the direct-manipulation grammar

*Extends §4 (direct manipulation) and §5 (the user is the only
cross-planspace mechanism).*

Edges are meaningful objects in this ontology, but the user cannot
draw one: creating a dependency means editing a list in the side
panel. Candidate gestures:

- drag from tile to tile → create a dep edge;
- drop a tile onto a tile → propose a review of it;
- drag a preview card across lanes → the explicit "user as carrier"
  gesture for cross-direction knowledge transfer. §5 makes the user
  the only mechanism between planspaces — good doctrine, currently
  high-friction.

The mindmap analogy in §4 was about node creation; the same principle
extends to relations.

### G. Artifact-forward tiles

*Extends §2 (investigation-free interface: demos pass on observed
effect).*

The user validates *work*, not metadata. A done tile's face today is
preview prose; for many nodes the honest face is the diff stat, the
produced file, the rendered README. Letting the tile lead with the
artifact — preview one click behind — moves the canvas from "status
board about the work" toward "the work itself," which is where a graph
*IDE* should sit.


## 4. Ranking

- **A (inbox)** and **B (plan-diff)** first: philosophy and user value
  align most sharply, and both are pure views over data that already
  exists — no schema change, no new node kinds.
- **C (compaction)** before any long-lived production use; it is the
  scaling story for "the graph is the state."
- **D (forks/merge)** is the largest build but the one that makes
  "concurrency = forks" real rather than aspirational; **rebase** can
  follow once plan-diff (B) exists to make re-anchoring legible.
- **E, F, G** are incremental and can ride along with normal frontend
  work — E's cost view pairs naturally with the usage data already on
  every node; F and G are canvas-grammar polish with outsized feel.
