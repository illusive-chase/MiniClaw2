# Proposal: Project-Level Git Control

Status: accepted design (discussed 2026-07-16), not yet implemented.

Companion to `PHILOSOPHY.md` §4 (the graph is the state), §6.3 (edges
are derived, not stored) and `IMPLEMENTATION_STATUS.md`. When this
document and the code disagree, this document is the position to
argue from until it is accepted or revised.


## 1. Motivation

The substrate for git awareness already exists and is half-used:

- Every node records `commit_before` / `commit_after`
  (`backend/miniclaw2/domain.py:209-210`) — immutable facts about
  which worktree state a run started from and ended at.
- The auto-commit op (`registry.py:1661-1673`, executed in
  `runner.py:298-345`) appends a framework-injected commit after
  every agent node when `auto_commit` is on, and rewrites the parent
  agent's `commit_after` to the real head (`registry.py:801-826`).
- `git_state.py` holds the read-side helpers (`git_head`,
  `commit_all`, `node_diff`).

What is missing:

- No **status surface**. The user cannot see whether the worktree is
  clean, dirty, ahead, or behind without leaving the app.
- No **manual verbs**. There is no way to commit accumulated work,
  pull upstream changes (rebase), or push — the framework can only
  auto-commit per node.
- No **graph presence** for git history. FS ordering renders only as
  op-chevron fragments (`layout.ts:469-494`); the commit — the one
  object the user actually reasons about when thinking "did my work
  land?" — is not a node, violating §4's own rule that anything the
  user reasons about should exist as a node.

The goal: make a git-enabled project robust while representing git
state as graph elements — status in the header, verbs as buttons and
ops, history as derived commit nodes on the canvas.


## 2. The authority split: derive, don't mirror

The central design decision. The commit node is **not stored**; it is
a render-time join. Three parties each own what they are
authoritative about:

| Fact | Authority | Mutability |
|---|---|---|
| "Node X ran on top of sha A, finished at sha B" | `node.json` (`commit_before` / `commit_after`) | Immutable historical fact (already recorded today) |
| "Commit A exists, its message, order, ahead/behind" | The git repository | Git's business; changes under us |
| "The user/framework ran commit/pull at time T" | A stored op node (`op_kind: commit \| pull`) | Immutable action record |

Why mirroring is unwinnable: agents run arbitrary git commands
mid-session (Claude Code commits when asked), the user commits in a
terminal, rebases rewrite shas. Any stored mirror of git state *will*
drift. Derivation turns every drift scenario into a rendering case
instead of a corruption case:

- **Terminal commit by the user** — the next status refresh sees a
  new HEAD; a commit node appears with no op behind it, labeled from
  `git log`. A commit node never *requires* an op.
- **Agent commits mid-run** — `commit_after` captures the final HEAD
  at node finish. Intermediate commits collapse into a "+N" badge on
  the trunk edge.
- **Rebase / reset orphans a sha** — epochs are keyed on the sha
  *string*, so the graph still renders; the commit node degrades to a
  stale visual (dashed amber). When the rebase was framework-run (the
  pull op), an old→new alias map captured at rebase time re-joins
  epochs onto the rewritten commits (§8). Node records are never
  rewritten.
- **Not a git repo** — `git_head` already returns `None`; no commit
  nodes render, git controls disable.

This mirrors §6.3 exactly: edges are derived, not stored. The commit
node is to git what the context node is to `CONTEXT.md` — a derived
canvas element keyed on identity (`commit:<sha>`), not a `Node`
record. Only *actions* persist, as op nodes, per §11's "auto-commit
as a visible op, not a hidden side effect."


## 3. Terminology

- **Epoch** — the set of executed work nodes sharing one
  `commit_before`: everything that ran on top of the same commit,
  i.e. everything a subsequent commit gathers up. With `auto_commit`
  on, epochs are singletons; with manual commits, several runs
  accumulate per epoch. Schema/design vocabulary only — per §4 it
  never appears on primary surfaces (user-facing phrasing: "changes
  since the last commit").
- **Commit hub** — the derived canvas node for one commit. Rendered
  uniformly: *every* commit adjacent to project activity gets a full
  hub, including auto-commit singletons.
- **Trunk** — the chain `home → C₀ → C₁ → … → ghost` along the
  project baseline (`LANE.timelineY`). The project spine *is* the git
  history.
- **Ghost commit** — a dashed hub at the trunk's end, rendered iff
  the working tree is dirty. It represents the not-yet-made commit;
  clicking it (or the header Commit button) opens the commit
  composer. Dashed is already the "not yet real" grammar (§10.1);
  this is composer-as-virtual-node (§10.2) applied to git.
- **Stale hub** — a hub whose sha no longer exists in git history
  (rebased away, reset). Dashed amber; members stay attached.
- **Alias map** — project-level `{old_sha: new_sha}` captured when
  the framework itself rebases (pull op), used by the derivation to
  re-join stale epochs onto rewritten commits.


## 4. Header controls

All live in the header-left metadata row, next to the existing
`ws {status}` indicator (`App.tsx:1658-1661`), same dot+text anatomy.

**Status pill.** `git —` (not a repo / missing path) · `git clean` ·
`git 3~` (dirty file count) · suffix ` ↑a ↓b` when an upstream is set
and ahead/behind is nonzero. Tooltip carries branch, detached state,
upstream. One backend call computes everything:
`git status --porcelain=v2 --branch` (branch, oid, upstream,
ahead/behind, and dirty entries in a single invocation). Dirty count
excludes `.miniclaw2/`, matching the `commit_all` pathspec — else the
pill reads dirty on a tree the commit op will call clean.

Update path: a new `git_status` WS event broadcast from
`_on_runner_done` on every terminal node (the same hook that spawns
the auto-commit op today), after every git op, and computed on
session load; the frontend additionally refetches on window focus to
catch terminal-side changes. Registry-emitted, `seq: 0`, ephemeral —
the existing out-of-band event channel.

**Commit button.** Follows the `+ New direction` pattern
(`App.tsx:1680-1693`): selects the ghost commit and opens the side
panel, which *is* the commit composer — dirty summary, a message
textarea prefilled from the summaries of the current epoch's nodes,
and a Commit action. No modal, no popover (§4: no modals in
steady-state UI; the side panel inspects the selected node).

**Pull (rebase) button.** Spawns the pull op (§5). Disabled while any
node is running or queued — quiescence is required (§6).

**Push button.** Direct backend action, **no node** (decided: push
mutates remote state, not the project worktree, so it earns no
timeline presence). The button carries its own in-flight spinner;
errors surface as text in the header row, since there is no node to
hold them.

All three disable when `session.read_only` or the project is not a
git repository.


## 5. Git verbs as ops

**Commit** reuses the existing commit op: `kind=OP`,
`op_kind="commit"`, `parent_node_id=None` marks it manual. The commit
message rides in `node.prompt` (unused on ops today);
`runner._run_op` uses it when non-empty, else falls back to the
current `miniclaw:node:<id>` format.

**Pull** is a new `op_kind="pull"`: `git pull --rebase`. On success
the op's `commit_after` picks up the new head and new hubs appear on
the trunk. On conflict the framework **never leaves a mid-rebase
tree**: it runs `git rebase --abort` automatically, restores the
worktree, and fails the op with the conflicting-file list in
`node.error`. Resolution is deliberately manual (decided): the user
resolves in a terminal; the next status refresh picks it up. No
concierge virtual is auto-proposed.

**Push** is not an op (§4 above).

Network verbs get a longer subprocess timeout (~120s) than the
existing 10s `_git` default; the op runner is async, so git calls
move behind `asyncio.to_thread`.

Failed git ops stay visible: op tiles in `state=error` continue to
render on the canvas (the existing `OpNode` tile + `OpPanel` with
`node.error` and diff), so a failed pull is a selectable graph
element, not a vanished action. Successful ops stop rendering as
tiles entirely — their metadata folds into the commit hub they
produced (§7).


## 6. Quiescence guard

A rebase rewriting files under a running agent on the shared worktree
is the worst available failure mode (§6.2: concurrent nodes share the
source worktree). Two layers:

- The pull endpoint rejects with 409 while the project has running or
  queued nodes; the header button disables on the same condition.
- `_schedule_queued` (`registry.py:748`) refuses to start *any* node
  while a pull op is active, closing the race where work is promoted
  mid-pull. The guard checks active runners, so it never blocks the
  pull op's own launch.

Manual commit is merely racy (it snapshots whatever is mid-flight),
consistent with today's auto-commit semantics; no guard beyond the
existing behavior.


## 7. The derived commit layer

### 7.1 Which commits render

The referenced set = every sha appearing in any node's
`commit_before` / `commit_after`, plus current HEAD, resolved through
the alias map (§8). A new backend helper `commit_graph(cwd,
referenced_shas, alias_map)` orders live shas by one
`git rev-list --topo-order HEAD` pass and returns descriptors:

```
{ sha, live: bool, message (subject), ts,
  external_count_before: int, aliases: [old_sha, ...] }
```

`external_count_before` counts unreferenced commits between
consecutive referenced ones (terminal/agent-made commits nobody ran
on top of) — rendered as a "+N" badge on the trunk edge rather than
as N hubs. Orphaned shas (`live=False`, including gc'd unknowns) are
interleaved into the order by their member nodes' timestamps, so a
stale epoch keeps its place in history.

Uniform hubs (decided): every rendered commit is a full hub. No
chevron collapse for singleton epochs.

### 7.2 Edges

A new thin/grey `commit` edge type carries three relations:

- **Trunk**: `root → C₀ → … → C_last → ghost`.
- **Out of a hub**: `Cᵢ → n` for each *source* of epoch Cᵢ — a member
  with no incoming dep/continue edge from another member of the same
  epoch.
- **Into a hub**: `n → C` for each *sink* of its epoch — a member
  with no outgoing dep/resume edge to a same-epoch member. The target
  is the earliest rendered commit at-or-after `n.commit_after` in
  commit order (using `commit_after`, not `commit_before`, correctly
  attributes nodes that themselves moved HEAD, including auto-commit
  rewrites). If none exists yet: the ghost when rendered, else no
  edge. Running nodes (`commit_after` null) draw no sink edge.

This is transitive reduction restricted to commit edges, applied per
epoch: within an epoch, dep-reachability makes the skipped edge
deducible (a dep parent's work trivially rides through its descendant
into the commit); across epochs no reduction applies. Formally: only
the sources and sinks of each epoch's dep-sub-DAG touch the hubs.

### 7.3 What retires

The `timeline`/`opChevron` FS-ordering fragments
(`layout.ts:469-494`) and standalone DONE-op tiles retire; the commit
trunk becomes the sole encoding of FS state (decided). The
`TimelineEdge` component itself stays — error-terminal edges reuse
it. `PHILOSOPHY.md` §6.3's `timeline` relation is superseded by a
`commit` relation (§10 below).

### 7.4 Placement and identity

Hubs render as 64px circles modeled on `ProjectRootNode` (sha7 mono
label, message tooltip; live solid / stale dashed-amber / HEAD brand
ring / ghost dashed-grey showing the dirty count), placed on the
project baseline in commit order, `layoutHints` key `commit:<sha>`
(`commit:ghost` for the ghost) so drags persist. The trunk's extent
feeds the free-coordinate cursor so free tiles start right of it.

### 7.5 Selection and panel

New `CanvasSelection` kind `{ kind: "commit", sha: string | null }`
(null = ghost). The side panel routes it to a `GitCommitPanel`:

- **Real commit** — sha7, live/stale/HEAD badges, full message,
  timestamp, associated op metadata when one exists (auto vs manual
  trigger, parent agent link), and the epoch member list, each row
  clickable to select that node.
- **Ghost** — the commit composer (§4). If the dirty count raced to
  zero, it renders "working tree clean" with the action disabled.


## 8. Rebase continuity: the alias map

When the framework itself rebases (pull op), it can capture the sha
rewrite exactly: record `git rev-list --reverse @{upstream}..HEAD`
before and after the rebase; equal lengths give a positional old→new
map, persisted to a project-level `git_aliases.json` (atomic write
via the store). `commit_graph` resolves referenced shas transitively
through the map, so epochs recorded against pre-rebase shas re-join
their rewritten commits.

When lengths differ (rebase dropped patches already upstream) the map
is not written; affected epochs render stale — the correct honest
fallback. External rebases (terminal) also render stale. In all
cases, node records keep their original shas: they are facts about
the past.


## 9. Wire and API surface

New event (Pydantic, `events.py`) — **must not carry a `node_id`
field**: `LiveReplayBuffer.push_live` (`replay.py:93-103`) buffers
any event whose `node_id` is a string (including `""`) until that
node's replay is marked ready, which never happens for a synthetic
id. Modeled like `NodeRemoved`, the event flows through the
node-less-ready path opened by the initial `replay_request`.

```python
class GitStatus(BaseModel):
    type: Literal["git_status"] = "git_status"
    is_repo: bool = False
    head: str | None = None
    branch: str | None = None
    detached: bool = False
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    dirty_count: int = 0
    seq: int = 0
```

Endpoints (session-scoped, existing guard patterns:
`registry.require_native` → 403, `store.assert_writable()` → 409):

- `GET  /sessions/{sid}/git` → `{ status, commits }` — read-only,
  degrades to `is_repo=false` when the root path is missing
  (non-native machines).
- `POST /sessions/{sid}/git/commit {message}` → spawns the commit op.
- `POST /sessions/{sid}/git/pull` → 409 unless quiescent; spawns the
  pull op.
- `POST /sessions/{sid}/git/push` → direct action; 409 with git
  stderr on failure; returns the fresh status.

Frontend types: `GitStatus`, `CommitDescriptor`, `GitStatusEvent`
joins the `ServerEvent` union; `api.ts` gains `getGitState`,
`gitCommit`, `gitPull`, `gitPush`.


## 10. Documentation amendments

- `PHILOSOPHY.md` §6.3 — replace the `timeline` relation with a
  **commit** relation: FS state reified as derived commit hubs on the
  project baseline; record the derive-don't-mirror authority split
  (§2 above) and that hubs/edges are views, never records.
- `PHILOSOPHY.md` §6.1 — the op description gains manual commit and
  pull alongside auto-commit.
- `PHILOSOPHY.md` §10.1 — the shape grammar gains the commit bead:
  circle-on-baseline; dashed amber = rebased away; ring = HEAD;
  dashed grey = uncommitted ghost.
- `IMPLEMENTATION_STATUS.md` — ledger entries as each stage lands.


## 11. Edge cases

- **Empty repo (no commits)** — `head=None`; only home (+ ghost when
  dirty) render; trunk is `root → ghost`.
- **Detached HEAD** — `branch=None, detached=True`; pill renders,
  tooltip explains; the HEAD hub still rings.
- **No upstream** — ahead/behind omitted from the pill; pull op fails
  with git's "no tracking information" message; push 409s likewise.
- **Commit racing dirty status** — the ghost's composer re-checks
  `dirty_count` and degrades to "working tree clean".
- **Hidden planspaces** — epoch membership follows canvas visibility;
  hubs render with visible-member counts.
- **`.miniclaw2/`-only changes** — dirty count 0; no ghost.


## 12. Staged implementation

Each stage lands independently and leaves the app fully functional.

1. **Status telemetry.** `git_status()` in `git_state.py`; `GitStatus`
   event; `_broadcast_git_status` from `_on_runner_done`
   (`asyncio.to_thread` inside a created task — the callback is
   sync); `GET /sessions/{sid}/git` (commits empty, contract stable);
   header pill + focus refetch. Tests: temp-repo suite following
   `test_git_state.py`'s `_init_repo` pattern (non-repo, empty,
   dirty, `.miniclaw2`-only, detached, ahead/behind via bare origin);
   broadcast payload asserts no `node_id` key.
2. **Verbs.** `git_pull_rebase` / `git_push` (+ timeout param on
   `_git`); `_run_op` pull branch + prompt-as-message; registry
   `spawn_git_op` / async `git_push`; scheduler quiescence guard; the
   three POST endpoints; Pull/Push header buttons with in-flight and
   error states. Tests: message plumbing, pull 409, conflict
   abort/restore, push non-fast-forward, scheduler guard.
3. **The commit layer.** `commit_graph`; derivation + reduction in
   `layout.ts` (`gitStatus`/`commits` props through `Canvas`);
   `CommitNode` + `CommitEdge`; ghost; `{kind:"commit"}` selection +
   `GitCommitPanel` + header Commit button; retire
   opChevron/timeline emission and DONE-op tiles (keep error-op
   tiles; fix `renderedWorkNodeGeometry` / free-cursor math which
   currently consult `opsWithChild`, `layout.ts:994-1045`). Tests:
   `commit_graph` ordering/orphans/external counts; `npm run build`;
   manual canvas pass.
4. **Alias map + docs.** Store read/merge for `git_aliases.json`;
   `local_only_shas`; capture in the pull branch (length-match
   guard); transitive resolution in `commit_graph`; PHILOSOPHY /
   IMPLEMENTATION_STATUS amendments. Tests: rewrite mapping,
   count-mismatch no-op, alias-resolved graph.


## 13. Out of scope

- **Branch switching / branch UI** — the pill shows the branch
  read-only. Switching mid-project forks epoch history; same bucket
  as the retired `fork` direction (§6.3 "out of scope").
- **Merge commits / non-rebase pulls** — pull is `--rebase` only,
  keeping the trunk linear.
- **Concierge conflict resolution** — considered (a proposed virtual
  "resolve the rebase conflicts" node) and deliberately deferred;
  auto-abort + manual resolution first.
- **Dirty-file listing endpoint** — the composer shows counts;
  per-file listing can ride a later iteration.
