# Proposal: Design Review & Refactor Plan

Status: proposal (review results, not yet acted on). 2026-07-06.

Companion to `PHILOSOPHY.md` and `IMPLEMENTATION_STATUS.md`. This
document records a full-repo design review — philosophy-vs-code drift,
correctness risks, structural debt — and sequences the refactors worth
doing. Where it and the code disagree, this document is the position to
argue from until accepted or revised. Findings were verified against
the code at review time; all 249 backend tests passed and `tsc -b` was
clean on the reviewed tree.

Severity is user-impact (`P0` = silently corrupts state or wedges a
node; `P1` = contract debt that taxes every future feature; `P2` =
structure/hygiene). Effort is S/M/L.


## 1. What is working and should not be touched

The review's headline is positive: the code follows the philosophy to
an unusual degree, and the philosophy itself is load-bearing rather
than aspirational.

- **The two-axis ontology (`kind` × `category`) landed cleanly.**
  Gates-as-review-categories, verifiers as `kind=verifier`, ops on the
  timeline — all present in `domain.py` with real invariant checks.
- **The reap pipeline is well-engineered.** Walk-diff against a
  pre-launch snapshot, strict-whitelist preview schemas, category
  rights enforcement, slug canonicalization, dep resolution, cycle
  detection, all-or-nothing persistence (`reap.py`, `preview.py`,
  `materialize.py`). This is the heart of "outputs as graph mutations"
  and it is solid.
- **Store simplicity.** JSON/JSONL + tmp-rename atomic writes matches
  the single-user, one-node-at-a-time model. The conscious SQLite
  deferral is correct; keep deferring it.
- **Docs discipline.** The PHILOSOPHY/IMPLEMENTATION_STATUS pairing
  (destination vs ledger) is a real asset. The debt is in the *other*
  docs (§6).

Nothing below proposes changing the ontology, the preview contract,
the one-node-at-a-time execution model, or the materialized-filesystem
projection. Those are the right calls.


## 2. P0 — correctness findings

### 2.1 Auto-commit sweeps `.miniclaw2/` into the user's repo

`commit_all` runs `git add -A` in the project root
(`backend/miniclaw2/git_state.py:30`), and the materializer writes the
whole lane projection *inside* that root
(`materialize.py:26-27` — `.miniclaw2/graph/lanes/`, `.miniclaw2/outputs/`).
Nothing writes an ignore entry: `workspace.create_temporary_root`
(`workspace.py:19-38`) git-inits temp workspaces with no `.gitignore`,
and no code touches `.git/info/exclude` for user projects. MiniClaw2's
own repo ignores `.miniclaw2` (root `.gitignore`), which is likely why
this was never noticed in development.

Consequences: every auto-commit op commits regenerated previews,
transcripts (with up-to-4KB tool results per activity), and artifact
copies into the user's history; and because `materialize_active_lane`
rmtree-rebuilds the subtree on every launch (`materialize.py:117`),
the per-node diff the timeline promises (`PHILOSOPHY.md` §6.2) is
polluted with framework noise on every node. This breaks §2's
investigation-free contract at the exact surface the user is told to
trust — the visible diff.

**Fix (S):** at project creation/registration, append `.miniclaw2/` to
`<root>/.git/info/exclude` (not the user's `.gitignore` — stay out of
their tracked files), and/or scope the commit pathspec:
`git add -A -- ':(exclude).miniclaw2'`. Add a bundled template that
asserts a commit-op diff contains no `.miniclaw2/` paths.

### 2.2 The Claude provider cannot report failure

`ClaudeProvider.stream_events()` never yields a `done` event — it
returns on end-of-turn or ~2s of PTY quiescence
(`providers/claude_native/__init__.py:218-229`) — so the runner's
`async for` falls through with `final_state` still defaulted to `DONE`
(`runner.py:504-521`). Nothing checks child liveness: a crashed
`claude` binary reads as a successful turn (`__init__.py:335-341`
swallows PTY EOF), which then fails reap (no preview) and triggers up
to three preview-repair turns, each spawning a fresh `--resume`
against the broken session (`runner.py:603-641`). One crash becomes
four spawns and a stub preview, reported as a preview-contract problem
instead of a provider death. Interrupt (Ctrl-C over PTY,
`__init__.py:232-238`) also lands in this exhaustion-means-DONE path,
so Codex can report `cancelled` but Claude structurally cannot.

**Fix (M):** emit an explicit `done` from the Claude adapter; poll
`pty.isalive()` in the stream loop and yield `kind="error"` on child
death; make the runner treat bare generator exhaustion as an error,
and document turn-termination as part of the provider contract in
`providers/base.py`.

### 2.3 Ask-gate timeout chain wedges nodes after ~60s

Three layers disagree: the runner's gate future is unbounded
(`runner.py:924-925`), the hook bridge's HTTP timeout is 600s
(`claude_hook_bridge.py:29`), but the installed hook entries set no
`timeout` field (`hook_installer.py:42-43`), so Claude Code's default
hook timeout (~60s) kills the bridge first. A user who takes longer
than that to answer an ask-user gate leaves `AskUserQuestion` running
as a native TUI prompt inside an invisible PTY: the node sits in
`WAITING` forever, and the eventual answer is written to a dead
socket. Relatedly, `hook_runtime.set_port` is never called — the port
is only correct because `__main__.py:36` exports an env var; anyone
running uvicorn directly gets a 45s `SessionStart` stall per spawn
plus the same invisible-prompt hang (`hook_runtime.py:56-66`,
`app.py:215-218` ignores install failure).

**Fix (S):** write a large `timeout` into installed hook entries; call
`set_port()` from app startup with the actual bound port; give the
runner-side gate a supervised timeout that interrupts the session
rather than hanging.

### 2.4 Session-transcript retargeting can replay stale history or adopt the wrong session

Two related defects in the native-Claude plumbing:

- On session-id rotation, `_retarget` resets the JSONL offset to 0 on
  the new file (`claude_native/__init__.py:283-288`), whose head
  contains the *copied prior conversation* — including old
  end-of-turn records — so the retargeted turn can end instantly and
  re-emit stale events into `events.jsonl`. The constructor comments
  guard against exactly this (`__init__.py:82-90`); `_retarget`
  bypasses the guard.
- The fingerprint fallback matches on the first 30 chars of the
  *composed* turn text (`transcript.py:105-106`) — identical template
  header for every node of a category — and scans all project hashes
  modified in the last 60s (`input.py:171-180`). Two projects
  launching within a minute can adopt each other's sessions. Rare,
  silent, severe.

**Fix (S/M):** seed the retarget offset from the matched fresh user
marker; fingerprint on `node.prompt` (the text after the `---`), and
scope the fallback scan to the expected project hash.

### 2.5 A stale `active_planspace_id` silently strips the preview contract

If the persisted active-planspace id no longer resolves,
`_select_active_planspace` returns `None` instead of falling back
(`contextspace.py:901-912`), so the node launches with
`planspace_id=None` → no lane materialization (`runner.py:525-536`) →
reap is skipped and the node terminates with a framework stub preview.
One stale string flips the system from "every node writes a preview"
to "no contract at all", with no visible error. Similarly, a stale
persisted `preferred_language` raises at launch and turns every run
into `NodeState.ERROR` (`language.py:56-86`, called at
`runner.py:199-201`).

**Fix (S):** refuse the launch with a visible error (or fall back to
the first bound planspace, logged); make `project_preferred_language`
lenient on read-back (degrade to `None`), strict only on wire input.

### 2.6 Smaller robustness items (grouped)

- `context_refresh` task exceptions are never retrieved; the UI
  spinner just stops (`context_refresh.py:80-85,131-146`). Surface
  `last_error` in the status payload. (S)
- Hook install merges into `~/.claude/settings.json` machine-wide and
  can replace a corrupt-but-recoverable settings file with a
  hooks-only one (`hook_installer.py:33,85-98`). (S)
- Orphaned `claude` PTYs / `codex app-server` processes survive a
  backend crash; no startup sweep. (M)
- Codex receive loop head-of-line blocks on an open gate
  (`codex.py:88-95,230-249`). (M, acceptable for v1 — document it)
- Verifier timeout hardcoded at 60s (`runner.py:389`). (S, config)


## 3. P1 — contract seams

These are the boundaries where debt taxes every future feature
(third provider, forks, settings UI, slash commands).

### 3.1 The gate response is an untyped dict tunneled through five layers

`GateRequest` is a clean dataclass, but the *response* is
`dict[str, Any]` threaded through a nine-kwarg signature copy-pasted
across `events.InteractionResponse` → `app.py` WS handler →
`registry.resolve_gate` (`registry.py:1370-1399`) →
`runner.resolve_gate` (`runner.py:91-122`) → per-provider heuristic
decoding (`codex.py:556-580,644-676`;
`claude_native/ask_payload.py:104-159`, which literally says "we
accept several shapes because the frontend has evolved"). The runner
also shape-guesses prose out of the same dict
(`runner.py:1021-1040`). A third provider means a third decoder.

**Fix (M):** define one typed `GateResponse` in `events.py` /
`providers/base.py`, normalize at the WS boundary, delete the
heuristic decoders. This is the highest-leverage single refactor in
the backend.

### 3.2 Provider abstraction leaks and has no home

- `_make_provider` lives in `runner.py:953-959` and is privately
  imported by `context_refresh.py:105`. Move to
  `providers/__init__.py`. (S)
- `Node.provider_session_id` and `Node.cli_session_id` are always
  written identically (`runner.py:848-849`); collapse to one field,
  keep the load-time rename in `store.py:206-216`. (S)
- `response_hint={"codex_method": ...}` flows provider-named keys to
  the frontend wire (`events.py:48`). (S)
- `AgentProviderEvent.kind` is a bare `str` with five magic values;
  `ProviderWireEvent` includes types no provider emits
  (`base.py:23-40`). Tighten to enums/unions. (S)
- `minimal_mode` means different things per provider, and the
  `--allowed-tools` "enforcement" claim for the context-refresh agent
  is dubious — spawn args also pass `--dangerously-skip-permissions`
  and `bypassPermissions` (`spawn.py:72-87`), under which an
  allowlist pre-approves rather than restricts. Verify and correct
  both the behavior and the `IMPLEMENTATION_STATUS.md` §4 claim. (M)

### 3.3 The wire `node_updated` payload is the whole node, repeatedly

`NodeUpdated(node=self.node.model_dump())` (`runner.py:889-890`) sends
— and, via `_emit`, *persists to `events.jsonl`* — the full node
including `system_context_snapshot` (the composed project context,
kilobytes) and `settings_snapshot`, on every state transition and
every usage tick. Event logs grow with redundant multi-KB node dumps;
every WS observer receives them. **Fix (S/M):** dump a slim wire
projection (exclude the snapshot fields; they're fetchable via REST),
or at minimum exclude them from the persisted event record.

### 3.4 "Session" means two unrelated things

`PHILOSOPHY.md` §1: "the atom of computation is a session" — a
provider conversation. But the REST surface names *projects* sessions:
28 `/sessions/...` routes in `app.py`, `SessionInfo`,
`frontend/src/api.ts` calling project ids `sessionId` throughout. Every
new contributor (and every agent working on this repo) must hold the
translation "API session = project ≠ provider session" in their head.
**Fix (M, mechanical):** rename routes and frontend helpers to
`/projects/{pid}`; keep `/sessions` as deprecated aliases for one
release if anything external depends on them.

### 3.5 One concept, four names: planspace / lane / direction / plug

Backend fields say `planspace_id`, materialization and reap say
`lane`, UI copy says "Direction", ContextSpace calls it a plug. The
philosophy itself bans "planspace" from primary surfaces (§4) — the UI
copy is right — but the *code* should pick one term. Recommendation:
`lane` in code (it is already the term at the two most important
seams, materialize + reap), "direction" in UI copy, "planspace"
retired to PHILOSOPHY prose. (M, mechanical, do it opportunistically
with §4 renames.)


## 4. P2 — structural refactors

### 4.1 One choke point for lane-graph mutations

Today there are **three** independent implementations of "safely add
or modify virtuals in a lane":

1. Agent path — `reap.py` (slug canonicalization, dep rewrite, lane
   scoping, cycle check, category rights).
2. User path — `registry.create_virtual` / `update_virtual` /
   `delete_virtual` (`registry.py:821-1155`, with its own
   `_normalize_virtual_scheduled_deps`, `_resolve_virtual_create_lane`,
   and its own `has_cycle` call).
3. Template path — `templates/launcher._stamp_lane`.

Philosophy §8 says every output is a graph mutation; the architecture
should have exactly one graph-mutation service. **Extract
`lane_graph.py` (or `virtuals.py`)** owning: dep normalization + cycle
check + lane scoping + persistence + `node_updated`/`node_removed`
broadcast. Reap keeps its preview-parsing front half and calls the
service; REST and templates call it directly. This is the structural
refactor with the best payoff/risk ratio: it shrinks `registry.py` by
~a third, makes agent-vs-user mutation rights testable in one place,
and gives forks (§9 of IMPLEMENTATION_STATUS) a clean surface to build
on. (L)

### 4.2 Registry and app monoliths

- `registry.py` (1399 lines) is repository + policy + runtime
  supervisor. After 4.1 it slims naturally; what remains splits into
  runtime supervision (runtimes, runner lifecycle, gates, interrupt)
  and promotion policy (`_next_promotion_candidate`,
  `_auto_promote_next_virtual`) — the latter is pure logic that
  deserves its own tested module. (M)
- `app.py` (983 lines) is one `create_app()` closure holding ~40
  routes. Split into APIRouter modules by domain (projects, virtuals,
  planspaces, templates, context, ws). Mechanical, improves
  navigability; do it opportunistically when touching routes. (M)
- `contextspace.py` (1248 lines) mixes bundle composition, repo
  bootstrap, CRUD, and UI wire-shaping — including a data-layer
  function importing the task layer mid-function
  (`contextspace.py:409`). Split composition / repository /
  serialization. (M)

### 4.3 Runner kind-dispatch duplication

`NodeRunner` runs three kinds through if-dispatch with the
`NodeStarted` constructor copy-pasted four times
(`runner.py:159-173,250-264,280-292,336-348`) and three near-identical
terminal sequences (stub preview → transition → `node_updated` →
`turn_done`). Extract `_emit_node_started()` and a shared
`_finalize(state)`; consider `AgentRunner`/`OpRunner`/`VerifierRunner`
only if a fourth kind ever appears. (S)

### 4.4 The ontology matrix is encoded four times

The kind × category × subtype legality rules live in
`domain.Node._check_invariants` (`domain.py:183-242`),
`preview.ExecutedPreview._check` (`preview.py:53-73`),
`preview.VirtualPreview._check` (`preview.py:104-130`), and again as
cross-checks in `validate_preview_for_node` (`preview.py:161-189`) —
plus a fifth mirror in `frontend/src/types.ts`. Adding one category
touches all five. Extract one table-driven validator shared by the
domain model and both preview schemas. (M)

Also in `domain.py`:

- `Node.summary` is overloaded — executed nodes store a summary,
  virtuals store their *motivation* there (admitted at
  `preview.py:237`: "virtuals carry motivation in summary slot"). Add
  an explicit `motivation` field; stop punning. (S, plus a load-time
  migration line in `store._migrate_node_payload`)
- `domain.ContextBundle` (`domain.py:259-268`) is dead code — nothing
  imports it, and its docstring claims a caller
  (`compose_context_bundle`) that actually uses
  `contextspace.ComposedContextBundle`. Delete. (S)
- `GateKind` has one member (`INLINE`) — vestige of retired
  checkpoint gates. Delete the enum and field. (S)

### 4.5 Verified-dead code (delete on sight)

`SpawnArgs` (`spawn.py:50-54`), `AgentProviderContext.dump_model`
(`base.py:76-77`), `UNSUPPORTED_SUBMIT_MODIFIERS`
(`keybindings.py:27-33`), `_write_passthrough`
(`claude_hook_bridge.py:130-133`), `throttle_env`'s unread
`MINICLAW_CLAUDE_THROTTLE_*` overrides (`input.py:227-242`),
`context.load_project_context` (test-only; production uses two other
loaders), `PlugRef.role` (parsed, echoed, never used), the unread
`defaults.context_budget` written into `contextspace.yaml`
(`contextspace.py:259-266` vs hardcoded `6000` at `:1028`), and the
never-written `plugs/planspaces/<id>/events.jsonl` in
IMPLEMENTATION_STATUS §4's layout diagram.


## 5. Philosophy adjudications (decisions for the author, not code fixes)

1. **Planspace plugs are plugs in name only.** §7's headline — "the
   same plug can be reused across projects" — holds for global/skill
   plugs but not planspaces: the plug dir holds only a manifest; all
   real state lives in the per-project store. Binding a planspace to a
   second project would share a mode flag and nothing else. Either
   make planspace state actually live under the plug (big, enables
   cross-project directions) or narrow §7's claim to global/skill
   plugs. Recommend the doc fix now, the mechanism only when a real
   need appears.
2. **`protocol` plugs exist only in §7.** No backend recognition
   (`_plug_kind` returns "unknown"; composition drops them silently).
   Strike from §7 or mark explicitly future.
3. **Per-lane state is split across four stores** — mode in the plug
   manifest, activation in `settings_override` *or* binding raw (two
   competing fields, `contextspace.py:901-904`), visibility in
   `Project.planspace_view`, content in store+graph. Pick one
   activation authority (recommend: project settings; binding raw is
   redundant) and document the split.
4. **`system_context_snapshot` under-promises.** It stores only
   project CONTEXT.md (`runner.py:788`), not the full injected system
   text (global + skills included). Rename or widen.


## 6. Documentation debt

`PHILOSOPHY.md` and `IMPLEMENTATION_STATUS.md` are current. The rest
of the doc set contradicts them:

- **`CONTEXT.md` is stale in ways that violate §8.4 at project
  level.** It tells every agent (it is injected into *every* launch)
  that the Claude adapter is SDK-based (it's PTY/native now), that
  scenarios live in `scenarios/bundled/` (retired for templates), that
  tests are unittest-style (`pytest` runs them), that "passive review
  gates" exist (retired), and to look at `scenarios/verify.py` (gone).
  This is self-poisoning by the framework's own definition — stable
  guidance that is no longer true. Run the repo's own context-refresh
  flow, or hand-fix. **Highest-priority doc fix.**
- **`README.md`** still describes the removed `PhantomNode` composer
  and the removed output-contract enum (`freeform`/`summary`/
  `interface`/`review_brief`).
- **`TEST.md` / `TESTING.zh.md`** describe the retired scenario
  engine (`_advance_scenario_step`, `verify.sh`, Tier catalogues).
  Rewrite around bundled templates or archive.
- **`TEMP_EXAMPLE.md`** is a self-declared archive; move to
  `docs/archive/` (with `TEST.md` if archived) so the repo root stops
  asserting stale positions.


## 7. Frontend findings

The frontend is in better shape than its size suggests: `tsc -b` is
clean, the gate UI is genuinely shared between canvas and panel
(`PendingGateInline` + `GateReviewForm` — no duplication), `ws.ts` has
reconnect + `replay_request` resume, and the React Flow selection-loop
workaround in `Canvas.tsx:402-409` is a model of a well-documented
hack. The findings below are ordered by how directly they violate
PHILOSOPHY.

### 7.1 §4 banned vocabulary is all over primary surfaces

PHILOSOPHY §4 bans `node`, `kind`, `planspace` (as a read noun),
`bundle`, `provider session/turn`, `verdict`, `acceptance` from
primary surfaces — they belong only in Inspect. The Inspect drawer
honors this; the rest of the UI does not:

- `components/TestsPanel.tsx:49-51` — user-facing copy reads
  "opens as a normal **virtual-node lane** … inspect **verifier**
  results, and complete human-review steps"; line 81 shows an
  "N nodes" count chip.
- `App.tsx:1695` — the pending banner reads "**Node** `1a2b3c4d` is
  awaiting your …" (schema noun + raw id).
- `components/PendingGateInline.tsx:62` — the fallback renders the raw
  wire enum: "Pending **checkpoint_review** on 1a2b3c4d."; the canvas
  tile also shows `pendingGate.interaction_type` as a chip
  (`canvas/nodes/AgentNode.tsx:324`).
- `panel/AgentPanel.tsx:464,752` — KV rows labeled "Lane" display the
  raw `planspace_id` string; `panel/SidePanel.tsx:329-333`
  (PlanspaceLanePanel) uses the raw `planspaceId` as the title
  fallback *and* always prints it in mono beneath.
- Assorted: "Node no longer exists." (`SidePanel.tsx:302`), "updated
  by node <id>" (`PlanspaceFilePanel.tsx:140`), "Add virtual node"
  (`PlanspaceLaneNode.tsx:54`), "Drag onto a virtual node…"
  (`ContextNode.tsx:86-87`), "attach to any node in any project"
  (`ContextNodePanel.tsx:171`), tooltip "Agent {state} · {category}"
  (`AgentNode.tsx:600-601`).

The codebase already contains the counter-example done right:
`ContextNode.tsx:152-155` translates plug kinds to "project memory" /
"memory link". **Fix:** one `vocab.ts` module mapping schema terms →
user words (step, direction, memory, review…), used by every label
above, plus a CI grep (or eslint `no-restricted-syntax` rule) that
fails on §4 words inside JSX string literals outside
`InspectDrawer.tsx`. Without the lint this will regress within weeks.

### 7.2 The "session = project" collision is fully mirrored client-side

`types.ts` (`SessionInfo`, `SessionContextSpaceInfo`, `SessionFile`),
`api.ts` (48 occurrences of `sessionId`; `createSession` /
`listSessions` / `renameSession`), and prop names throughout mean
*project* everywhere. The §3.4 rename must land as one cross-layer
change; the frontend half is mechanical (types + api + props) and
should ship in the same PR as the route rename to avoid a
half-translated wire.

### 7.3 `App.tsx` is the frontend's registry.py

1727 lines; one `App()` component with **34 `useState`, 17
`useEffect`, 46 `useCallback`, 15 `useRef`** owning routing, WS
dispatch, selection, gates, reviews, ContextSpace, templates, panel
layout, and toasts. Same split prescription as §4.2, same
opportunistic pace: extract domain hooks (`useProjectNodes`,
`useGates`, `useContextSpace`, `useTemplates`) as each area is next
touched. No framework change needed.

### 7.4 The wire protocol assumes one active node — the client papers over it

`text_delta`, `thinking`, `activity`, `usage`, and
`interaction_request` carry no `node_id`; `App.tsx:994-1058`
attributes every one of them to `activeNodeIdRef.current` (set by the
last `node_started`). The moment two nodes run concurrently —
auto-promotion racing a manual launch — transcript deltas and gates
silently attach to the wrong node. This is the client-side face of
§3.3: when slimming `node_updated`, also stamp `node_id` on every
streaming envelope; the ref becomes a fallback, not the mechanism.

### 7.5 Belt-and-braces state sync hides the real contract

`handleEvent` both upserts full `node_updated` dumps *and* calls
`refreshNodes()` (full GET) on `node_started` / `interaction_request`
/ `turn_done`. It works, but it means the full-dump `node_updated`
payload (§3.3) is not actually load-bearing for reconciliation — the
refetch already covers it. Slim events + refetch-on-transition is
sufficient; this makes §3.3 cheaper than it looks.

### 7.6 Smaller items

- **Module-level mutable singletons as React context.**
  `setAgentNodeContext` / `setPlanspaceLaneContext`
  (`AgentNode.tsx:447-449`) inject App callbacks into React Flow node
  components by mutating module globals. Works for exactly one canvas;
  breaks tests and any future multi-canvas view. Replace with React
  context when next touched — not urgent.
- **Legacy enum in the client union.** `"checkpoint_review"` survives
  in `types.ts:27` and branch logic at `App.tsx:1680` — must be
  removed in lockstep with the backend §3.1 cleanup.
- **Dead wire field.** `response_hint` is declared (`types.ts:32`) and
  never read anywhere in the client. Either the provider-named hints
  of §3.2 get a consumer or the field leaves the wire.
- **`layout.ts` buildGraph** is a single ~600-line pure function
  (`canvas/layout.ts:206-813`). Acceptable while it stays pure; split
  into stages (collect → position → color) when it next needs a
  feature, not before.


## 8. Sequenced plan

Suggested order; each phase leaves the tree green and shippable.

| Phase | Items | Effort | Risk |
|---|---|---|---|
| **0 — stop the bleeding** | 2.1 git exclude; 2.2 Claude done/liveness; 2.3 hook timeout + port; 2.4 retarget/fingerprint; 2.5 lenient read-backs | ~5 small PRs | Low — each is local, testable |
| **1 — harden contracts** | 3.1 typed GateResponse (+ drop `checkpoint_review` client-side, 7.6); 3.2 provider seam cleanups; 3.3 slim node_updated + 7.4 node_id on stream envelopes; 2.6 context-refresh error surfacing | 2–3 PRs | Medium — touches wire shapes; add replay-compat tests |
| **2 — structure** | 4.1 lane_graph service; 4.3 runner dedup; 4.4 ontology table + motivation field; 4.5 dead code | 3–4 PRs | Medium — behavior-preserving, cover with existing suite |
| **3 — naming & docs** | 3.4 sessions→projects (backend + frontend 7.2 in one PR); 3.5 lane naming; 7.1 vocab.ts + §4-vocabulary lint; §5 philosophy adjudications; §6 doc refresh (CONTEXT.md first — can precede everything) | 2–3 PRs | Low — mechanical |
| **later** | 4.2 registry/app splits; 7.3 App.tsx domain hooks; 7.6 React-context migration, layout.ts staging (all opportunistic, as code is touched) | ongoing | Low |

CONTEXT.md refresh is independent of everything and poisons every
agent launch until fixed — do it first, today.


## 9. Non-goals (explicitly not proposed)

- **No SQLite migration** — the deferral in IMPLEMENTATION_STATUS §10
  remains correct.
- **No Node model split** into virtual/executed subclasses — the
  churn (storage migration, wire, frontend mirror) outweighs the
  benefit; the `motivation` field + shared ontology table capture most
  of the value.
- **No live mid-session reap** — reap-only-at-terminal is a conscious
  v1 boundary (IMPLEMENTATION_STATUS §6).
- **No schema-aware review forms** — documented non-goal; free-form
  human prose is the design.
- **No design expansion** of ContextSpace (acceptance states, PLAN
  approval, drag-drop binding UX) ahead of the pending items already
  listed — `TEMP_EXAMPLE.md`'s warning against protocol-depth
  expansion still applies.
