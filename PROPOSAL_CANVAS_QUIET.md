# The Quiet Canvas — Edge Weight, the Vertical Trunk, and the Library Dock (2026-07)

A frontend design proposal in six parts, argued from `PHILOSOPHY.md`.
It has one thesis:

> **At rest the canvas shows the plan and the filesystem state. Every
> derived relation — loads, produces, epoch membership — and every
> unbound library entry is available on demand, not at rest.**

This is the visual counterpart of §8.4's anti-self-poisoning
discipline, and the first installment of the "doctrine of forgetting"
that `PROPOSAL_GRAPH_UIUX.md` §2.1 named as missing. Nothing is
deleted; weight is ranked. Today every edge and every tile renders at
equal weight, and enumerating the entire user-wide library as dimmed
nodes puts things on the canvas that are not project state at all.


## 1. Current state

**Edges all render at rest.** `buildGraph` emits six families — `dep`,
`resume`, `commit` (trunk plus epoch source/sink), `loads`, `produces`,
and the error-terminal `timeline` edge. Only `loads` and `produces` are
visibility-gated, via `decorateEdges` in `Canvas.tsx:848`, which zeroes
their opacity unless an endpoint is hovered or selected. Epoch
source/sink edges (`commit-source:*`, `commit-sink:*`) share the
`commit` type with trunk edges and so are always visible, tangled with
the trunk along the same axis.

**The home node fabricates dependencies.** §6.3 defines `dep` as a
virtual's `scheduled_deps`. `layout.ts:568` synthesizes
`dep:root->{node}` for every node whose `scheduled_deps` is empty —
an edge with no domain relation behind it, drawn on exactly the nodes
that are *defined* by having none.

**The commit trunk is horizontal, and its length leaks into lane
layout.** Hubs are placed at `commitStartX + index * 112` on a shared
`y` (`layout.ts:309`). `trunkExtent` (`layout.ts:269`) is folded into
each lane's default `x` (`layout.ts:279`), so on any lane without a
saved layout hint, every new commit shifts the lane rightward.

**The whole library renders as canvas nodes.** `layout.ts:774-810`
enumerates every user-wide principle and every skill into the context
aggregate, producing a tile per entry with `dimmed: true` when no live
node has loaded it. Pre-attachment (`pending_extra_principles`,
`pending_extra_skills`) surfaces only as a count badge on that tile.
Clicking a shelf tile is the sole path to principle/skill details and
deletion (`ContextNodePanel.tsx:68-71`).

Two hazards shape the design:

1. **Hover must not rewrite the React Flow node array.**
   `Canvas.tsx:288-293` documents that a node-list rewrite on
   mouseenter makes React Flow's pointer hit-test churn enough to lose
   its grip on the element under the cursor, producing visible cursor
   flicker between pane-grab and node-pointer. Any hover highlight has
   to reach tiles without touching that array.
2. **Removing the home node orphans two things** that currently hang
   off it: the `+N external commits` badge, which renders on the
   trunk edge and for the oldest commit *is* the `root → C₀` edge
   (`layout.ts:316`, `CommitEdge.tsx:40-51`); and the empty-repo case,
   which today renders `root → ghost` with the ghost's parent falling
   back to `"root"` (`layout.ts:333`).


## 2. Decisions

Settled in design discussion; recorded here with rationale.

### 2.1 Four edge weights, not one

Always visible: **dep** (§6.3's primary edge), **commit trunk** (FS
state), **resume**, and the **error-terminal** edge. Hover- or
selection-gated and dashed: **loads**, **produces**, and **epoch
source/sink**.

Resume stays at rest deliberately. It is low-cardinality, so it costs
almost no clutter, and it encodes a hard causal fact — this session
continues that one rather than starting fresh — that §10.2 chose to
make *implicit* in provenance. The tile's `↻ <id>` badge makes the
fact recoverable without the edge, so hiding it would be defensible;
keeping it is the cheaper correctness.

Error-terminal edges stay because §1 says the interesting events are
the ones that need you. Hiding the line to a failure marker inverts
the product's own attention model.

### 2.2 The commit trunk rotates to a vertical column

Enter-top / exit-bottom only reads correctly on a vertical axis; with
the current horizontal row it turns every trunk edge into an S-curve.
Three reasons beyond the geometry:

- §4 names GitLens, JetBrains' git lane, and VS Code's graph view as
  the analogy. All three are vertical.
- It decouples the axes. Trunk grows in `y` at fixed `x`; lanes keep
  their `x` for the life of the project, fixing the rightward drift
  that `trunkExtent` causes today.
- It frees the horizontal axis entirely for within-lane progression,
  which is the axis the user reads most.

**Oldest at top, ghost at bottom.** This inverts the GitLens
convention (newest-first) on purpose: appending downward preserves the
append-don't-reflow strategy documented at `layout.ts:229`, whereas
newest-at-top would push the entire column down on every commit. It
also matches within-lane time direction — later is further from the
origin — and the direction lanes themselves accrue.

Accepted cost: on a long history the ghost drifts far below the active
lanes. The header's workspace status already selects the ghost and
opens the commit composer directly (`App.tsx:1856`), so the ghost does
not need to be visually adjacent to current work.

### 2.3 Epoch links are the payment for the diagonals

Rotating the trunk turns commit↔agent edges into long diagonals across
the lane stack. §2.1 makes them invisible at rest, so the two
decisions pay for each other: the trunk gets the clean axis, and the
crossings only appear when the user asks for them by hovering.

### 2.4 The commit hub answers "what ran against this state?"

Hovering a hub highlights its epoch's agent tiles exactly as if each
were hovered — revealing their gated edges and ringing the tiles.
Membership is `commit_before == sha`, the same set that draws the
source/sink edges (`layout.ts:357-363`). The relation is reciprocal:
hovering an agent rings its epoch hub. This is what a derived view over
Git should do — answer its question without a click.

### 2.5 The home node becomes a header button

§4's "anything the user can reason about exists as a node" governs
concepts *within* the graph. The project is the container of the
graph, not a participant in it; rendering the container as a node
inside itself is a category error, and the node's only structural role
today is to anchor edges that either shouldn't exist (§2.1's
`dep:root->`) or have a better anchor (the trunk's first hub).

The home glyph moves to the header and **replaces `+ New direction`**,
whose action is already the first control inside `ProjectPanel`
(`ProjectPanel.tsx:261-268`). One fewer top-level control, one extra
click on the most common action, no capability lost. Keeping the glyph
as the button's icon preserves the vocabulary §10.1 established for
it.

### 2.6 A library entry becomes a node only when bound

An unbound principle is not project state, so it is not a node. A
dimmed shelf tile asserts "this exists in the world," which is
precisely not what §4 says the canvas is for. The rule:

> A principle or skill tile exists iff it is bound to at least one
> visible node — either **observed** (`bundle.sources`, `skill_audit`)
> or **declared** (`pending_extra_principles` /
> `pending_extra_skills` on a virtual).

Declared bindings draw a dashed loads edge; observed-and-used ones
draw solid. `LoadsEdge` already dashes available-but-unused skills, so
dashed reads uniformly as "will load or did not use," solid as
"actually consumed."

**Superseded in implementation.** This collided with §2.1, which claims
dashing for the whole gated class. Two meanings on one channel is the
worse outcome, so §2.1 wins the axis: *every* loads edge is dashed, and
consumption moves to the dash **pattern** — tight (`5 3`) for consumed,
sparse (`2 4`) for declared or available-but-unused. Solid is reserved
for at-rest structure. The `ld:*` edge data drops `dashed` and keeps
`relation: "used" | "available" | "declared"` as the single source for
both the pattern and any future encoding.

The library itself moves into one dock with three sections —
Templates, Principles, Skills. "Library" is already the codebase's
word for this collection (`PROPOSAL_LIBRARIAN.md` §2.2), the aside is
already a mode-swapping panel, and one renamed button beats a third
toggle in an already-crowded header.

### 2.7 Drop-on-pane stays a no-op

Dragging a library entry onto empty canvas, a running agent, or an op
tile remains silently ignored, as today. Materializing a new virtual
from a pane drop would extend §10.2's "tap empty canvas → a virtual
appears at the click position" attractively, but it is a product
addition beyond this proposal's scope (§7).


## 3. Design

### 3.1 Edge families and gating

Split the single `commit` edge type in two, so gating is a predicate on
type rather than a data flag:

- `commitTrunk` — hub to hub, and hub to ghost. Solid, always visible,
  carries the `+N` external-commit label.
- `commitLink` — `commit-source:*` and `commit-sink:*`. Dashed,
  gated. Enters an agent tile at its top (`epochIn`) and leaves at its
  bottom (`epochOut`): the diagonals cross the lane stack from a
  vertical trunk, so reusing the horizontal dep/resume anchors would
  make them read as plan edges.

Both carry the arrowhead from `defaultEdgeOptions`. A custom edge
component does not inherit it — it has to forward `props.markerEnd` to
`BaseEdge` — and direction is the content of both families: older →
newer for the trunk, state-read → run → state-written for a link.

`decorateEdges` (`Canvas.tsx:848`) gates on
`type ∈ {loads, produces, commitLink}`, and its endpoint test widens
from a scalar `hoveredNodeId` to the hover group of §3.3. Both types
share one implementation module; only registration and defaults
differ.

`dep:root->*` edges are removed outright. A node with no
`scheduled_deps` and no continuation source draws no incoming edge —
lane membership carries direction, the tile carries state, and the
epoch link (on hover) carries which filesystem state it read.

### 3.2 Trunk geometry

`CommitNode` handles become `target: Top` / `source: Bottom`. Hub
placement becomes `{ x: LANE.trunkX, y: LANE.trunkStartY + index *
LANE.trunkStep }`, with the ghost appended one step past the last
hub. `layoutHints["commit:<sha>"]` keeps overriding per hub.

`trunkExtent` is deleted. Lane default `x` becomes a constant
(`LANE.rootX + LANE.trunkGutter`), and `initialFreeCursorX` loses its
`trunkExtent` term. Lane vertical stacking is unchanged.

The oldest hub renders `external_count_before` as a small chip on the
circle instead of on a trunk edge, since it no longer has an inbound
edge. Interior hubs keep the edge label. Empty repo: the ghost renders
alone, with no trunk and no parent — the honest picture of a project
with no history.

`CommitNode`'s `head`/`live`/`ghost`/dirty-count rendering, selection
routing, and `GitCommitPanel` are untouched.

### 3.3 The hover group

`buildGraph` already computes `epochMembers`; export the projection so
the canvas can resolve groups without recomputing:

```ts
epochMembersByCommitSha: Record<string, string[]>
commitHubIdByNodeId: Record<string, string>
```

`onNodeMouseEnter` resolves a group instead of an id: a commit hub
yields itself plus its epoch members; an agent yields itself plus its
epoch hub; everything else yields itself. The group has **two
consumers with different mechanisms**:

- **Edges** read it from React state, as they do today — the edge
  array already rewrites on hover in its own effect
  (`Canvas.tsx:365`).
- **Tiles** read it from a module-level hover store —
  `canvas/hoverStore.ts` with `subscribe` / `getSnapshot` /
  `setHoverGroup`, consumed in `AgentNode` and `CommitNode` via
  `useSyncExternalStore`. This is the existing
  `setAgentNodeContext` pattern and it is mandatory here: publishing
  hover through node `data` would rewrite the node array and
  reintroduce the cursor flicker documented at `Canvas.tsx:288-293`.

Group highlight on a tile is the same ring the tile already shows on
its own hover — no new visual vocabulary, so §10.1's three axes stay
untouched.

### 3.4 Library binding and tiles

`buildGraph` drops both enumeration loops (`layout.ts:774-810`) and
instead builds context aggregates from bindings:

- **Observed** — unchanged: `bundle.sources` plus the `skill_audit`
  pass that records `loadedBy` / `usedBy`.
- **Declared** — for each visible virtual, each id in
  `pending_extra_principles` / `pending_extra_skills` resolves against
  the `principles` / `skills` arguments to a path and title, and
  contributes a `declaredBy` binding.

`principles` and `skills` remain `buildGraph` arguments, now only for
path/title resolution of bound entries. `ContextNodeData.dimmed` and
`attachedCount` are deleted along with the shelf: no tile means not
bound, and the count moves to the dock card. Declared bindings emit
`ld:*` edges with `relation: "declared"` (see §2.6's supersession).
Loads target the agent tile's top `loads` handle; op tiles carry no
such handle, so their loads keep the default left/right anchors —
naming a handle the node does not have makes React Flow drop the edge.

Placement is unchanged — bound principle/skill tiles continue to land
in the floating loaded-context stripe (`LANE.contextLaneY`), which no
longer collides with anything now that the trunk occupies a left
column. Per-lane placement of bound entries is a natural follow-on
(§7).

### 3.5 The library dock

`TemplateLibraryDock` generalizes to `LibraryDock` with three
collapsible sections. `panelState.mode` renames `"templates"` →
`"library"` (with a `readPanelState` fallback so a persisted
`"templates"` still opens the dock); the header button renames to
**Library**.

Cards keep the existing drag contract: `application/x-miniclaw-template`,
`application/x-miniclaw-principle`, `application/x-miniclaw-skill`, all
three already handled by `Canvas.onCanvasDrop`. Principle and skill
drops still attach only to non-obsolete virtual tiles.

Two capabilities must move with the shelf or they are lost:

- **Inspect and delete.** Each principle/skill card expands in place
  to a compact form of `PrincipleDetails` / `SkillDetails` —
  description, files, provenance, attached count, delete — so browsing
  and deleting stay inside the dock rather than swapping the panel out
  from under the list. A card also offers "open full", which sets
  `selection = {kind: "context", identityKey, path, scope:
  "contextspace", sourceKind, plugId}` and switches to details mode;
  `SidePanel`'s existing context branch resolves the entry by
  `plugId` (`SidePanel.tsx:433-440`) and needs no change.
- **Librarian feedback.** A finished `library_edit` node currently
  makes a new dimmed tile appear on the canvas, which is the §2
  "observed effect matches the description" signal. The dock's
  existing refresh token is already bumped on library-node terminal
  states; it must additionally surface the new entry — section
  auto-expanded, entry marked new — so authoring still has a visible
  outcome.

### 3.6 Header and project selection

The home glyph button sets `selection = {kind: "projectRoot"}`,
clears the inspected node, bumps `newDirectionRequestVersion`, and
opens the panel — the existing `+ New direction` handler
(`App.tsx:1883-1896`) with a glyph instead of a label. Selection kind
`projectRoot` and `ProjectPanel` are unchanged; only the canvas node
disappears. `selectedCanvasNodeId` returns `null` for `projectRoot`
instead of `"root"` (`App.tsx:2261`), since no canvas node carries
that id. `ProjectRootNode.tsx` and its `NODE_TYPES` entry are deleted;
stale `layoutHints["root"]` entries are inert.


## 4. Philosophy amendments

Two edits to `PHILOSOPHY.md` §10.1, which otherwise disagrees with
this design and, per the document's own preamble, would be the
position to argue from:

- Remove `home glyph (project root)` from the shape-encodes-kind list.
  The project is no longer a node.
- Add a fourth axis: **weight encodes at-rest relevance.** The plan
  DAG, the commit trunk, resume, and failure edges render at rest;
  derived relations render on hover or selection. State the rule
  positively — the canvas at rest is plan plus filesystem state.

The library rule of §2.6 needs no amendment: it is §4's corollary
("if a concept cannot be a node or an edge, it is probably not worth
surfacing") applied in the direction the code had been violating.


## 5. Touch points

| Area | File(s) |
|---|---|
| Edge types, gating, hover group | `frontend/src/canvas/Canvas.tsx` |
| Trunk geometry, dep-root removal, binding-driven tiles | `frontend/src/canvas/layout.ts` |
| Trunk vs link edge split | `frontend/src/canvas/edges/CommitEdge.tsx` |
| Handles, `+N` chip, hover ring | `frontend/src/canvas/nodes/CommitNode.tsx` |
| Hover ring | `frontend/src/canvas/nodes/AgentNode.tsx` |
| Shelf affordances removed (`dimmed`, drag handle, badge) | `frontend/src/canvas/nodes/ContextNode.tsx` |
| Hover store (new) | `frontend/src/canvas/hoverStore.ts` |
| Dock with three sections, inline details/delete | `frontend/src/components/LibraryDock.tsx` (from `TemplateLibraryDock.tsx`) |
| Header button, panel mode rename, selection id | `frontend/src/App.tsx` |
| Deleted | `frontend/src/canvas/nodes/ProjectRootNode.tsx` |
| Docs | `PHILOSOPHY.md` §10.1, `IMPLEMENTATION_STATUS.md` |

No backend change. No wire, schema, or store change: every relation
involved is already derived at render time, and
`pending_extra_principles` / `pending_extra_skills` /
`settings_snapshot.skill_audit` already carry the bindings §3.4 reads.


## 6. Testing

- **Edges at rest:** a graph with all six families renders dep,
  trunk, resume, and error edges at full opacity and loads, produces,
  and epoch links at zero; hovering an endpoint reveals only that
  node's gated edges.
- **No fabricated deps:** a node with empty `scheduled_deps` emits no
  incoming `dep` edge, and no edge references a `root` source.
- **Trunk:** hubs are collinear in `x` and monotonically increasing in
  `y`, oldest first, ghost last; adding a commit changes no lane's
  default `x`; the oldest hub carries the `+N` chip and interior edges
  carry the label; an empty repo renders the ghost alone.
- **Hover group:** hovering a hub rings exactly its `commit_before`
  members and reveals their epoch links; hovering a member rings its
  hub; the node array is not rewritten (assert referential stability
  of the nodes prop across a hover).
- **Library binding:** an unbound principle produces no canvas tile; a
  principle in a virtual's `pending_extra_principles` produces a tile
  whose loads edge is `relation: "declared"`; a loaded principle
  produces one that is `"used"`; an unused skill stays `"available"`;
  every loads edge anchors source→`loads`, target→`loads` except on op
  tiles; hiding a lane removes tiles bound only within it.
- **Dock:** all three sections list and drag with their existing MIME
  types; a principle/skill card expands to details and deletes; a
  finished `library_edit` node surfaces its new entry; a persisted
  `"templates"` panel mode still opens the dock.
- **Header:** the home button opens `ProjectPanel` with the direction
  composer requested; `projectRoot` selection highlights no canvas
  node.


## 7. Out of scope

- **Pane-drop materialization** — dragging a library entry onto empty
  canvas to create a pre-attached virtual (§2.7). Natural follow-on;
  the drop handler already distinguishes pane from tile.
- **Per-lane placement of bound context tiles** — bound principles and
  skills stay in the shared stripe rather than moving beside the node
  that binds them.
- **Trunk level-of-detail** — collapsing long stretches of history into
  a single capsule, which is `PROPOSAL_GRAPH_UIUX.md` direction C
  applied to the trunk rather than to lanes.
- **Drawing edges by gesture**, artifact-forward tiles, and the
  attention inbox — unchanged directions in
  `PROPOSAL_GRAPH_UIUX.md` §3.
