# Fix Proposal: Git-Layer Review Findings

Status: verified against the working tree at `192a6f1` on 2026-07-16.
Companion to `PROPOSAL_GIT.md` (the spec being implemented) and the two
external reviews. Every finding below was re-checked against the actual
code before being accepted; verdicts and evidence come first, the fix
plan follows.

---

## Part 1 — Verification checklist

Legend: **R1-x** = first review (6 findings), **R2-x** = second review
(10 numbered findings + smaller items). Verdict is one of CONFIRMED /
CONFIRMED-WITH-NUANCE / NOT A BUG.

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| R2-1 | `git_pull`/`git_commit` are sync `def`; spawned ops never start on an idle project | **CONFIRMED (P0)** | `app.py:621`, `app.py:638` are `def`, not `async def`. FastAPI runs them in a threadpool; `_launch_node` (`registry.py:728-731`) does `asyncio.get_running_loop()` inside `try/except RuntimeError: return None` and silently bails off-loop. The op persists as QUEUED; nothing re-runs the scheduler on an idle project; the stuck op makes `quiescent()` (`registry.py:867-871`) false, so every later pull 409s. |
| R2-2 | §6 pull guard runs only at `_schedule_queued` entry; not atomic with the endpoint check | **CONFIRMED (P0)** | Guard at `registry.py:752-757` executes once before the `while rt.has_capacity()` loop. The same pass launches the pull from `priority_node_ids`, `continue`s, and keeps launching queued nodes with the pull runner now active. The endpoint's `quiescent()` check (`app.py:643`) runs in a threadpool thread, so it is not atomic with `spawn_git_op` either. |
| R1-1 | Pull failure unconditionally `git rebase --abort`s, destroying a pre-existing user rebase | **CONFIRMED (P1)** | `git_state.py:87-98`. Pull requires *node* quiescence, not worktree quiescence — a terminal rebase mid-conflict-resolution is reachable. `git pull --rebase` refuses to start over an existing rebase; the unconditional abort then cancels the *user's* rebase and discards their resolution state. |
| R1-2 / R2-3 | `git_status` WS event never refreshes `gitCommits`; successful commit/pull leaves no canvas trace until refocus | **CONFIRMED** | Handler at `App.tsx:1351-1361` only calls `setGitStatus`. The post-POST `refreshGit()` (`App.tsx:1695`, `1714`) fires while the op is still QUEUED, capturing the pre-op graph. `layout.ts:484` hides done op tiles. Net: done tile disappears, no new hub, stale source/sink edges until window blur+refocus (`App.tsx:619`). |
| R1-3 / R2-8 | Stale shas appended after all live commits, sorted lexically; non-repo branch nondeterministic and self-aliasing | **CONFIRMED** | `git_state.py:143-146` (`ordered_refs + sorted(stale)`; the comment admits the deviation from §7.1's "interleaved by member node timestamps"). Non-repo branch at `:137` iterates the `refs` set (nondeterministic order) and its alias comprehension lacks the `old != sha` guard present at `:166`, so every sha lists itself as its own alias. |
| R2-4 | Sink-edge fallback targets `gitCommits[epochIndex+1]`, which can be an appended stale hub | **CONFIRMED** | `layout.ts:370-376`. When `commit_after === epoch` (node made no commit), the fallback fires; because stale shas are appended at the array end, the "next" element can be an unrelated rebased-away hub. Contra §7.2 "earliest rendered commit at-or-after `commit_after` in commit order; if none, ghost when rendered, else no edge". Note: the fallback to the *next live trunk commit* is itself spec-conformant (a later commit on a linear trunk does contain the work) — the bug is specifically that stale entries can occupy that slot. |
| R2-5 | Invisible done ops still counted as 140px tiles in free-cursor math | **CONFIRMED** | `layout.ts:316` excludes done ops from `opsWithChild`; `layout.ts:484` skips rendering them; but `renderedWorkNodeGeometry` (`layout.ts:1107-1116`) still returns `{opSpacing, opWidth}` for any op not in `opsWithChild` — now including invisible done ops. `initialFreeCursorX` inflates per completed lane-0 auto-commit. §12 stage 3 explicitly mandated this fix ("fix `renderedWorkNodeGeometry` / free-cursor math"). |
| R2-6 | Commit hubs never show as selected | **CONFIRMED** | `graphNodeIdForSelection` (`App.tsx:2141-2158`) has no `commit` case → returns null. The Canvas commit-click branch (`Canvas.tsx:508-510`) is the only branch that skips `pendingUserSelectionRef`, so the selection-sync effect (`Canvas.tsx:347-364`) runs `decorateSelection(current, null, false)` and strips the ring React Flow just applied. Header Commit button (`App.tsx:1786`) opens the composer with the ghost never highlighted. |
| R1-5 / R2-7 | Ghost label is a dead ternary; dirty count not plumbed; `+N` badge computed but never rendered; head/selected rings identical | **CONFIRMED** | `CommitNode.tsx:17`: `` `+${commit.message ? "" : ""}` `` is always `"+"`. `CommitNodeData` (`layout.ts:71`) has no dirty count field; `gitDirtyCount` reaches `buildGraph` but only gates ghost existence (`layout.ts:302`). `external_count_before` is computed (`git_state.py:161`), typed, shipped — and consumed nowhere in the frontend. `CommitNode.tsx:13` uses one ring style for `head || selected`. |
| R1-4 | Collapsed external commits (`+N`) missing from trunk | **CONFIRMED** | Same evidence as above; trunk edges at `layout.ts:300` are plain `type: "default"` with inline style. |
| R1-6 | Push not serialized with an active pull rebase | **CONFIRMED** | `registry.git_push` (`registry.py:893-899`) runs `git push` directly with no runtime check and no lock. Frontend disables Pull on `gitQuiescent` (`App.tsx:1789`) but Push only on `gitAction` (`App.tsx:1792`), which clears as soon as the pull POST returns (`App.tsx:1700`) — while the rebase op is still running. |
| R2-9 | §7.3 retirement only happened for done ops; two FS encodings coexist; error-op-with-child tile suppressed contra §5 | **CONFIRMED (latent)** | `layout.ts:313-324` folds *non-done* ops with a child into `opChevron` edges (`:550-556`) and emits `tl:` timeline edges for parentful op tiles (`:557-564`). §7.3: "the `timeline`/`opChevron` FS-ordering fragments … retire; the commit trunk becomes the sole encoding of FS state (decided)". An error op with a child would also be tile-suppressed contra §5 ("op tiles in `state=error` continue to render") — agreed this is unreachable through normal flows today (nothing creates agent nodes with an op parent), so latent. |
| R2-10 | `commit_graph` is O(history) with N+1 subprocesses per request | **CONFIRMED (efficiency)** | `git_state.py:133-160`: internal `git_status()` duplicating the caller's (`app.py:607` + `:617`), full `rev-list` of HEAD, `live_order.index(sha)` inside the loop, one `git show` per referenced sha. `git_status` itself has a redundant `rev-parse --git-dir` probe (`:46` — `status --porcelain=v2` already fails cleanly outside a repo). One nuance: the suggested `--untracked-files=all → normal` swap changes count *semantics* (untracked dirs collapse to one entry), so the pill would undercount; keep `all` unless approximate counts are acceptable. The "30s rev-list timeout silently marks every commit stale" observation is also correct (`:138-139`). |
| — | Smaller R2 items: dropped `create_task` ref in `_broadcast_git_status`; no-op `except X: raise` clauses; hand-built status dict; `commitGitMessage`/`runGitAction` near-dupe; push `setGitStatus` immediately overwritten; GitCommitPanel misses most of §7.5; hardcoded composer prefill; 4 inline commit-edge styles; test gaps vs §12 while `IMPLEMENTATION_STATUS.md` declares stages landed | **ALL CONFIRMED** | `registry.py:859`; `app.py:627-630` (`NonNativeProjectError(PermissionError)`, `StoreReadOnlyError(RuntimeError)` — the clauses catch-and-rethrow with no effect); `app.py:662-666`; `App.tsx:1684-1714` (`1693` overwritten by `1695`); `SidePanel.tsx:238-374` (no member list, no timestamp, no op metadata, no HEAD badge, `useState("Changes from MiniClaw2")`, missing-descriptor branch renders "uncommitted changes" copy for a real sha); `layout.ts:300,307,368,376`; `test_git_state.py` has 4 new git tests, none of the §12 stage-1/2 promised tests (no-node_id broadcast, pull-409, conflict abort/restore, push failure, scheduler guard, message plumbing); `IMPLEMENTATION_STATUS.md` §3a "Landed … Pull is quiescence-guarded" without qualification. |

The second review's two self-refuted candidates (canvas reflow; read-only
404) were re-checked in spirit and require no action.

**Bottom line: every surviving finding in both reviews is real.** The two
reviews overlap on four findings (stale ordering, git_status refresh,
ghost label, push serialization); review 2 additionally found the two
launch blockers.

---

## Part 2 — Fix plan

Ordered so each fix lands independently and the app stays functional.
P0 items are small diffs and unblock the feature's basic verbs; P1 is
the destructive-data path; P2 restores canvas correctness; P3 is
efficiency/quality.

### F1 (P0) — Make the git verb endpoints run on the event loop
*Fixes R2-1; also closes the endpoint half of R2-2.*

`backend/miniclaw2/app.py:621, 638` — change `def git_commit` and
`def git_pull` to `async def`. That puts `quiescent()` +
`spawn_git_op()` on the event loop with no `await` between them
(atomic w.r.t. other loop callbacks) and gives `_launch_node` its
running loop. The op runner already offloads git subprocesses via
`asyncio.to_thread`, so nothing blocks the loop.

Test: spawn a commit/pull through the ASGI app on an idle project and
assert the op reaches RUNNING/DONE (would fail today); assert a second
pull during the first 409s and a pull after completion succeeds.

### F2 (P0) — Re-check the pull guard per scheduler iteration; serialize push
*Fixes R2-2 (scheduler half) and R1-6.*

`backend/miniclaw2/registry.py:749-788` — hoist the guard into a
predicate and evaluate it at the top of **every** `while` iteration,
not just at function entry:

```python
def _pull_active(rt) -> bool:
    return any(r.node.kind is NodeKind.OP and r.node.op_kind == "pull"
               for r in rt.runners.values())
```

Runner registration is synchronous inside `_launch_node`, so the
iteration after the pull launches sees it and stops launching further
nodes — exactly the §6 contract ("refuses to start *any* node while a
pull op is active" while "never block[ing] the pull op's own launch").

Push serialization, backend: in `Registry.git_push`
(`registry.py:893-899`), before pushing, reject when a pull op is
running or queued (scan `rt.runners` + queued op nodes); surface as a
409 ("pull in progress") from the endpoint.

Push serialization, frontend: disable the Push button while a pull op
node is queued/running — the nodes list already contains op nodes and
`NodeInfo.op_kind` exists (`types.ts:268`):

```ts
const pullInFlight = nodes.some(n => n.kind === "op" && n.op_kind === "pull"
  && (n.state === "running" || n.state === "queued"));
```

This also covers the window after the pull POST returns but before the
op completes (the `gitAction` flag alone does not).

Test: scheduler-guard unit test (pull runner active → queued agent not
launched in the same pass); push-during-pull 409.

### F3 (P1) — Never abort a rebase this pull did not create
*Fixes R1-1.*

`backend/miniclaw2/git_state.py:87-98` — detect rebase state before
pulling:

```python
def _rebase_in_progress(cwd: str) -> bool:
    paths = _git(cwd, ["rev-parse", "--git-path", "rebase-merge",
                       "--git-path", "rebase-apply"])
    if paths.returncode != 0:
        return False
    return any((Path(cwd) / p).exists() for p in paths.stdout.splitlines() if p)
```

In `git_pull_rebase`:
1. If a rebase is already in progress, return immediately with an
   error ("a rebase is already in progress in the worktree; resolve or
   abort it first") — do not run pull, do not touch the tree.
2. On pull failure, run `rebase --abort` **only if** a rebase is in
   progress now (i.e. this pull started one). The §5 guarantee ("never
   leaves a mid-rebase tree") applies only to rebases the framework
   initiated.

Test: init repo, start a conflicting rebase manually, call
`git_pull_rebase`, assert the rebase state (rebase-merge dir, conflict
markers) survives and the returned error names the pre-existing rebase.

### F4 (P2) — Refresh the commit graph on `git_status` events
*Fixes R1-2 / R2-3.*

`frontend/src/App.tsx:1351-1361` — keep the immediate
`setGitStatus(...)` (latency), then `void refreshGit()` in the same
branch so `gitCommits` refetches when an op completes (the backend
already emits `git_status` from `_on_runner_done` after every git op
and terminal node). Add `refreshGit` to the `handleEvent` dependency
array (`App.tsx:1365`). This makes the hidden-done-tile decision
coherent: the tile disappears and the new hub appears in the same
refresh.

### F5 (P2) — Interleave stale epochs; make the non-repo branch deterministic
*Fixes R1-3 / R2-8.*

`backend/miniclaw2/git_state.py:119-168` and `app.py:602-618`:

- Extend the signature: `commit_graph(cwd, referenced_shas, alias_map,
  ref_timestamps: dict[str, float] | None = None)`. The caller
  (`get_git_state`) builds `{sha: earliest created_at of any node
  referencing it}` from the node list it already loads — this is the
  "member node timestamps" §7.1 names as the interleave key.
- Ordering: place each stale sha before the first live ref whose
  commit `ts` exceeds the stale sha's member timestamp (ties and
  missing timestamps: after, tie-broken by sha for determinism).
  Only shas with no timestamp fall back to the current append-at-end.
- Non-repo branch (`:137`): iterate `sorted(refs)` and add the
  `and old != sha` alias guard to match `:166`.

Test: extend `test_commit_graph_orders_oldest_first_and_counts_external`
with a reset-away epoch whose member timestamp predates HEAD; assert it
renders *before* HEAD. Assert non-repo output is order-stable and
alias lists exclude self.

### F6 (P2) — Sink-edge fallback: live commits only
*Fixes R2-4 (and consumes F5).*

`frontend/src/canvas/layout.ts:370-376` — when a member did not move
HEAD, scan forward from `epochIndex + 1` for the first descriptor with
`live === true` instead of blindly taking `[epochIndex + 1]`:

```ts
if (!target && epochIndex >= 0) {
  const next = gitCommits.slice(epochIndex + 1).find(c => c.live);
  if (next) target = `commit:${next.sha}`;
}
if (!target && gitDirtyCount > 0) target = "commit:ghost";
```

Stale hubs never receive fallback sink edges (a member whose
`commit_after` *is* a stale sha still targets it directly via
`commitForSha` — that path is honest and stays).

### F7 (P2) — Retire the chevron/timeline encodings for ops; fix the cursor math
*Fixes R2-5 and R2-9 with one refactor; implements §7.3 / §12 stage 3 as decided.*

`frontend/src/canvas/layout.ts`:

- Delete the `opByChildId` / `opsWithChild` fold (`:313-324`) and the
  op `opChevron`/`tl:` edge emission (`:544-565`). Non-done ops render
  as tiles unconditionally (queued/running/error — §5 keeps error
  tiles visible); done ops stay hidden (their record folds into the
  hub). The `timeline` edge type itself stays for error terminals
  (`errtl:` edges), per §7.3.
- `renderedWorkNodeGeometry` (`:1107-1116`): return `null` for
  `state === "done"` ops and tile geometry for all other ops — the
  geometry now counts exactly what renders, killing the phantom
  free-cursor gap.
- Cleanup: `setOpChevronContext` wiring in `Canvas.tsx:241-245` and
  the OpChevron edge component become dead — remove them.

Note: this is the spec-decided behavior ("the commit trunk becomes the
sole encoding of FS state"). If keeping the chevron for *running* ops
is preferred visually, that is a spec revision to argue in
`PROPOSAL_GIT.md`, not a silent deviation — the default here follows
the accepted design.

### F8 (P2) — Commit selection sync
*Fixes R2-6.*

- `frontend/src/App.tsx:2141` — add to `graphNodeIdForSelection`:
  `if (selection.kind === "commit") return selection.sha ?
  \`commit:${selection.sha}\` : "commit:ghost";`
- `frontend/src/canvas/Canvas.tsx:508-510` — set
  `pendingUserSelectionRef.current = { nodeId: data.ghost ?
  "commit:ghost" : \`commit:${data.commit.sha}\`, preserveExisting:
  event.shiftKey }` like every other branch.

The header Commit button then visibly highlights the ghost, and hub
clicks keep their ring.

### F9 (P2) — Render the promised indicators: ghost dirty count, `+N` badge, distinct HEAD ring
*Fixes R1-4, R1-5, R2-7; also retires the 4 inline edge styles.*

- `layout.ts:71` — `CommitNodeData` gains `dirtyCount?: number`; the
  ghost node (`:305`) passes `gitDirtyCount`.
- `CommitNode.tsx:17` — ghost label becomes
  `+${data.dirtyCount ?? ""}` (e.g. `+7`); keep the "changes" caption.
- Ring split (`CommitNode.tsx:13`): HEAD keeps `ring-2 ring-brand/50`;
  selection gets a visually distinct treatment (e.g. `ring-2
  ring-brand ring-offset-2`) so a selected non-HEAD hub reads
  differently from HEAD.
- New `CommitEdge` component (thin grey, dashed variant for
  ghost/stale) registered as edge type `commit`; trunk edges pass
  `data: { externalCount: commit.external_count_before }` and the edge
  renders a small `+N` label via `EdgeLabelRenderer` when
  `externalCount > 0` — §7.1's answer to agent-made mid-run commits.
  All four inline `#9ca3af` styles (`layout.ts:300, 307, 368, 376`)
  collapse into this one themed type (§7.2).

### F10 (P3) — `commit_graph` / `git_status` efficiency
*Fixes R2-10.*

`backend/miniclaw2/git_state.py`:

- One `git rev-list --topo-order --format=%H%x00%s%x00%ct HEAD` pass
  replaces the plain rev-list **and** every per-sha `git show`; parse
  into `sha → (index, subject, ts)` and use the map instead of
  `live_order.index(sha)`.
- `commit_graph` accepts an optional pre-computed `status: GitStatus`
  so `get_git_state` (`app.py:607/617`) stops paying two full worktree
  scans per request.
- Drop the `rev-parse --git-dir` probe in `git_status` (`:46`) —
  `status --porcelain=v2` already fails cleanly outside a repo.
- Keep `--untracked-files=all` (see checklist nuance: `normal`
  collapses untracked directories and would undercount the pill).
- On rev-list failure/timeout, log it; consider surfacing a degraded
  flag rather than silently rendering everything stale (optional).

### F11 (P3) — Quality cleanups

- `registry.py:859` — retain the `create_task` handle (e.g. a
  per-runtime pending-set discarded on completion) so status broadcasts
  can't be GC'd mid-flight and exceptions are retrieved; optionally
  coalesce bursts with a trailing-edge guard.
- `app.py:627-630` — delete the no-op `except NonNativeProjectError:
  raise` / `except StoreReadOnlyError: raise` clauses.
- `app.py:662-666` — return `{"status": asdict(status)}`.
- `App.tsx:1684-1714` — merge `commitGitMessage` into `runGitAction`
  (one refresh/finally ordering); drop the push `setGitStatus`
  (`:1693`) that `refreshGit()` immediately overwrites.
- `SidePanel.tsx:238-374` — bring `GitCommitPanel` up to §7.5: HEAD /
  live / stale badges, timestamp, epoch member list (nodes whose
  `commit_before` resolves to the sha via `aliases`) with rows
  clickable through the existing `onSelectNode`, associated op
  metadata (op node whose `commit_after` equals the sha: auto vs
  manual, parent agent link). Prefill the composer from the current
  epoch's node summaries per §4, falling back to the static string
  only when there are none. Missing-descriptor branch should say
  "commit metadata unavailable", not "uncommitted changes".
- Tests promised by §12 stages 1–2: broadcast payload asserts no
  `node_id` key; pull 409 when non-quiescent; conflict abort/restore
  (now including the F3 pre-existing-rebase case); push failure; the
  F2 scheduler guard; commit-message plumbing.
- `IMPLEMENTATION_STATUS.md` §3a — qualify the ledger until the above
  land (it currently declares the stages complete).

---

## Suggested landing order

1. **F1 + F2** — the verbs don't work and the guard doesn't guard;
   both are small backend diffs and unblock everything else.
2. **F3** — the only data-destroying path.
3. **F4** — makes op completion visible; prerequisite for F7's
   hidden-tile behavior to feel coherent.
4. **F5 → F6** — backend ordering first, then the frontend fallback
   that depends on it.
5. **F7, F8, F9** — independent canvas fixes, any order.
6. **F10, F11** — efficiency and polish, opportunistically.
