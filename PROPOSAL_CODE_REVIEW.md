# Proposal: Native Code Review Nodes

Status: proposed design (discussed 2026-07-16), not yet implemented.

Companion to `IMPLEMENTATION_STATUS.md` §3a (the landed Git layer:
epochs, the ghost commit, quiescence) and `PHILOSOPHY.md` §4/§6. When
this document and the code disagree, this document is the position to
argue from until accepted or revised.


## 1. Motivation

MiniClaw2 has two review capabilities today, and neither reviews git
state:

- `agentic_review` / `human_interact_review` agents review **upstream
  node work** through the materialized lane (previews, transcripts,
  artifacts). Their prompt contract is MiniClaw2's own
  (`prompts/category_agentic_review.md`).
- `programmatic_review` verifiers run a deterministic script
  (`runner.py:367`).

Meanwhile both providers ship a specialized, purpose-trained code
reviewer that MiniClaw2 cannot reach:

- **Codex** app-server exposes `review/start` with structured targets
  (`uncommittedChanges`, `baseBranch`, `commit`, `custom`) and
  inline/detached delivery (verified locally against codex-cli
  0.144.1; not part of this repo — re-confirm on upgrade).
- **Claude Code** ships the native `/code-review` slash command,
  which reviews the current diff at a configurable effort level and
  emits a findings report.

And the thing worth reviewing now has a name: since the Git layer
landed (`IMPLEMENTATION_STATUS.md` §3a), uncommitted work is a
first-class concept — the
**epoch** (all nodes sharing one `commit_before`) accumulating toward
the **ghost commit**. Agent nodes never auto-commit; work piles up in
the shared worktree until a commit op runs `commit_all()`
(`runner.py:316`, `git_state.py:269`). "Review the uncommitted
changes" is precisely "review the ghost commit before it becomes
real" — a gate between an epoch and its commit.

The goal: a `code_review` node type that invokes the provider-native
reviewer against the working tree, and whose report MiniClaw2
captures as the node's review outputs — without asking the native
reviewer to understand MiniClaw2's preview contract.


## 2. What this is, and is not

| | dependency review (existing) | code review (this proposal) |
|---|---|---|
| Reviews | upstream nodes' previews/artifacts via the lane | the git working tree (uncommitted changes) |
| Reviewer prompt | MiniClaw2-authored (`category_*.md`) | provider-native (`review/start`, `/code-review`) |
| Preview | agent-written, reap-validated, repair loop | **framework-synthesized** |
| Virtual mutations | may propose/rewrite virtuals | none (report-only) |
| Lane materialization | yes | no |

It is a fourth `ReviewSubtype` on the existing kind/category axes —
`kind=AGENT, category=REVIEW, subtype=code_review` — because it runs
a provider under a model preset and holds provider session state. But
its execution path is closer to op/verifier nodes: the framework owns
the output contract end to end.

Four decisions settled in discussion (2026-07-16):

1. **Framework-synthesized preview.** The runner harvests the native
   report and writes `preview.json` itself, the way op and verifier
   nodes already do (`runner.py:840`, `runner.py:848` via
   `_persist_executed_preview`, `runner.py:806`). No follow-up turn,
   no repair loop. This is forced anyway: `review/start` takes a
   structured target, not prompt text, and prepending
   `launch_instructions` to `/code-review` would break the slash
   command — there is no channel to deliver the preview contract to
   either native reviewer.
2. **Uncommitted-only v1.** The `review_target` field is shaped for
   extension (`base_branch`, `commit`, `custom` later) but only
   `{"type": "uncommitted"}` is implemented and validated in v1.
3. **Report-only.** No virtual fix-node proposals from this node
   type. Downstream planning/agentic-review nodes (or the user) read
   the report and decide. Honest about what the native tools return.
4. **Snapshot + serialize.** The reviewed diff is snapshotted at
   launch for audit, and the scheduler gives code_review nodes
   exclusive workspace access, extending the pull-op quiescence
   pattern (`registry.py:787`, `IMPLEMENTATION_STATUS.md` §3a).


## 3. Domain model

### 3.1 New subtype and target

```python
class ReviewSubtype(StrEnum):          # domain.py:67
    AGENTIC_REVIEW = "agentic_review"
    HUMAN_INTERACT_REVIEW = "human_interact_review"
    PROGRAMMATIC_REVIEW = "programmatic_review"
    CODE_REVIEW = "code_review"        # new

class ReviewTarget(BaseModel):         # new, domain.py
    model_config = ConfigDict(extra="forbid")
    type: Literal["uncommitted"]       # v1; later: base_branch | commit | custom
```

`Node` gains `review_target: ReviewTarget | None = None`
(`domain.py:189`). Defaulted to `{"type": "uncommitted"}` at creation
when `subtype=code_review` and omitted by the caller.

### 3.2 Validation (`domain.py:293` branch)

For `category=REVIEW, subtype=CODE_REVIEW`:

- `kind` must be `AGENT`; `model_preset_id` required as usual — the
  preset's `provider` field is what selects the codex vs claude
  native reviewer.
- `review_target` required (auto-defaulted); forbidden on every other
  kind/subtype.
- `brief` **optional** (relaxing the current review-agents-require-
  brief rule for this subtype only). The native reviewer defines its
  own rubric; when a brief is present, its `check_what` rides along
  as focus text where the provider has a channel for it (§6.2). The
  same carve-out applies in `VirtualPreview` validation
  (`preview.py:106`).
- `prompt` / `prompt_draft` **optional** — same role as brief: focus
  text, not the review instruction. Promotion eligibility
  (`registry.py:1127`) gets a carve-out: a code_review virtual may
  promote with an empty draft.

`ExecutedPreview` needs no schema change: `subtype` already carries
any `ReviewSubtype` value (`preview.py:35`). Reap is untouched
because code_review nodes never enter the reap pipeline (§5).


## 4. Git entanglement

This is the part the feature must get right, and the landed Git layer
(`IMPLEMENTATION_STATUS.md` §3a) already built the vocabulary.

### 4.1 The target is the epoch

At launch the runner records `commit_before = git_head()` exactly as
`_run_agent` does (`runner.py:180`). The review scope is
`commit_before..worktree` — staged, unstaged, and untracked,
excluding `.miniclaw2/` (which is git-excluded via
`ensure_miniclaw_git_excluded`, so native reviewers that respect
gitignore semantics never see framework files). That is the ghost
commit's content: what `commit_all` would gather.

### 4.2 Snapshot: record what was reviewed

Before invoking the provider, the runner captures:

- `git diff HEAD` (staged + unstaged) plus the untracked file list
  (from `git status --porcelain=v2`, the same source as
  `git_status()`, `git_state.py:47`);
- a fingerprint: `(head_sha, sorted dirty paths, diff sha256)`.

The snapshot is written to the durable node dir as
`reviewed-diff.patch` — a direct store write like `human-review.md`
(`runner.py:208`), *not* an artifact, because the artifact whitelist
is `.md/.json/.html` (`artifacts.py:18`) and widening it for this is
not worth it. This is the immutable "what the reviewer actually saw"
record, in the spirit of the derive-don't-mirror authority split
(`PHILOSOPHY.md` §6.3, `IMPLEMENTATION_STATUS.md` §3a): the report and
the snapshot are facts about the run; git state remains git's business.

After the provider returns, the runner recomputes the fingerprint.
On mismatch (a terminal-side edit mid-review — the scheduler prevents
node-side mutation, §4.3), the synthesized preview's summary and the
report artifact are prefixed with an explicit staleness warning. The
node still completes: a slightly-stale review is a degraded result,
not an error.

### 4.3 Serialization: extend the quiescence guard

A review of a moving tree is meaningless, and the worktree is shared
by design (`PHILOSOPHY.md` §6.2). The pull op already established the
pattern (`registry.py:787`, `app.py:650`); code_review generalizes
it in `_schedule_queued` (`registry.py:750`):

- A queued code_review node launches **only when the project is
  otherwise idle** (`rt.active_count == 0` for other nodes).
- While a code_review node sits at the head of the queue, no later
  queued node is launched past it — the pool drains to it. This is
  the semantic barrier: "review the epoch once it settles."
- While a code_review node runs, no other node launches (exactly the
  `_pull_active` rule; refactor both into one
  `_exclusive_node_active` check).

With the default `concurrency=1` (`domain.py:152`) this guard is
almost always a no-op; it exists for concurrency>1 projects.

Unlike pull, code_review never mutates the tree, so there is no
abort/restore branch and no interaction with the alias map
(`git_state.py` aliases only matter for rebases; the review's only
recorded sha is `commit_before`, handled like every other node's).

### 4.4 `auto_commit` interplay

With `auto_commit` on, `_on_runner_done` spawns a commit op after
every agent node (`registry.py:849`) — epochs are singletons and the
tree is clean by the time anything else runs. A code_review node in
such a project will usually short-circuit with "working tree clean"
(§5 step 1), which is correct but useless. The feature's natural home
is manual-commit projects, where epochs accumulate and the ghost
commit is a real object. Reviewing *committed* ranges in auto_commit
projects is exactly the `commit`/`base_branch` target extension
deferred to v2 (§10). Documented, not worked around.

### 4.5 The review→commit gate

The intended workflow shape, expressible today with existing
machinery: a code_review virtual with `scheduled_deps` on the epoch's
work nodes, and a commit op that follows it. The ghost commit's
composer panel (`GitCommitPanel`) grows a
**Review** action beside Commit (§8) so the gate is one click. On the
canvas, the node is a member of its epoch like any other — the
derived commit-edge rules (`IMPLEMENTATION_STATUS.md` §3a) apply
unchanged, so it naturally edges into the hub for the commit that
gathers the epoch it reviewed.


## 5. Runner: `_run_code_review()`

A fourth dispatch branch in `NodeRunner.run()` (`runner.py:168`),
shaped like `_run_op`/`_run_verifier` — provider-backed but with a
framework-owned output contract. No lane materialization, no
`build_category_launch_block`, no reap, no preview-repair loop.

1. **Baseline + short-circuit.** Record `commit_before`. If
   `git_status().dirty_count == 0` (which already excludes
   `.miniclaw2/`, `git_state.py:47`), finish immediately: state DONE,
   synthesized preview with summary "working tree clean — nothing to
   review", no provider call. Mirrors the ghost composer's degrade
   case (`IMPLEMENTATION_STATUS.md` §3a).
2. **Snapshot** per §4.2; persist `reviewed-diff.patch`.
3. **Provider review turn.** Call `provider.run_review(context,
   spec)` (§6). Events stream through the existing
   `_handle_provider_event` plumbing unchanged — deltas, activities,
   session/turn ids, usage all land in `events.jsonl` and the WS feed,
   so the UI shows live progress and `provider_session_id` persists
   for the record.
4. **Harvest.** The provider yields one `kind="review"` event
   carrying a normalized `ReviewReport` (§6.4) before its terminal
   event. Missing report on a `done` terminal → node ERROR with a
   clear message (the native review ran but produced nothing
   recognizable).
5. **Divergence check** per §4.2.
6. **Publish outputs.** Runner writes
   `.miniclaw2/outputs/<node_id>/code-review-report.md` (always) and
   `code-review-findings.json` (when structured findings exist), then
   runs them through `publish_artifacts` (`artifacts.py:38`) so they
   get the standard validation/refs treatment.
7. **Synthesize preview.** Via `_persist_executed_preview`
   (`runner.py:806`), extended to accept an `artifacts` list —
   `render_executed_preview` (`preview.py:194`) currently renders
   none, which is the one small schema-adjacent change this needs.
   Field mapping:
   - `motivation`: `brief.check_what` when present, else
     `"code review of uncommitted changes since <sha7>"`.
   - `summary`: provider verdict + explanation when structured
     (codex `overall_correctness` / `overall_explanation`), else the
     report's lead paragraph, capped; prefixed with the staleness
     warning when §4.2 tripped.
   - `next_implications`: top findings as `title (file:line)` lines,
     capped, else pointer to the report artifact.
8. **Terminal states.** Cancellation and provider errors follow the
   agent path's conventions; on any failure before step 7 the
   existing `_write_stub_preview` (`runner.py:831`) keeps the lane
   record complete.


## 6. Provider contract

### 6.1 Interface

`providers/base.py` gains:

```python
@dataclass(slots=True)
class ReviewSpec:
    target: ReviewTarget            # v1: uncommitted
    focus: str | None = None        # brief.check_what / prompt, if any

@dataclass(slots=True)
class ReviewFinding:
    title: str
    body: str
    file: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    priority: str | None = None
    confidence: float | None = None

@dataclass(slots=True)
class ReviewReport:
    raw_markdown: str               # always present — the report as prose
    findings: list[ReviewFinding] | None = None   # codex: structured; cc: None
    verdict: str | None = None      # e.g. codex overall_correctness
    explanation: str | None = None
```

`AgentProviderEvent` gains `report: ReviewReport | None` and the
`kind="review"` value. The `AgentProvider` protocol
(`providers/base.py:84`) gains:

```python
async def run_review(
    self, context: AgentProviderContext, spec: ReviewSpec
) -> AsyncIterator[AgentProviderEvent]: ...
```

Same lifecycle contract as `run()`: must terminate with
`kind="done"`/`kind="error"`, yields `session`/`turn`/wire events on
the way. Capability is checked with `hasattr` at dispatch; a preset
whose provider lacks `run_review` fails the node at launch with an
actionable error rather than degrading to a prompt-engineered
imitation (silent semantic substitution is worse than refusal).

### 6.2 Codex: `review/start`, inline

`CodexProvider.run_review` reuses the whole JSON-RPC client
(`codex.py:35`): `initialize` → `thread/start` or `thread/resume`
(persisting the thread id as today) → then instead of `turn/start`
(`codex.py:79`):

```json
{ "method": "review/start",
  "params": { "threadId": "...",
              "target": { "type": "uncommittedChanges" },
              "delivery": "inline" } }
```

- **Inline delivery** (decided): the review runs on the node's own
  thread, so `provider_session_id` semantics are untouched. Detached
  delivery and its separate `reviewThreadId` are deferred (§10).
- The event loop (`codex.py:89`), approval interception, and
  `turn/interrupt` (`codex.py:107`) are reused as-is; the review turn
  id feeds the same interrupt path (confirm against the schema at
  implementation).
- On turn completion the provider assembles `ReviewReport` from the
  review output item — codex-cli 0.144.1 emits findings with title,
  body, priority, confidence, and code locations plus
  `overall_correctness`/`overall_explanation`; the exact item shape
  must be pinned against the local app-server schema at
  implementation time (the §1 caveat: verified against codex-cli
  0.144.1, re-confirm on upgrade).
- **Capability detection**: after `initialize`, probe for
  `review/start` support (schema/capabilities advertisement, or a
  version floor on the CLI). Unsupported → `kind="error"` naming the
  minimum codex-cli version. `review/start` is an app-server protocol
  capability, so detection must precede use — an old codex-cli would
  otherwise fail opaquely.
- `spec.focus` has no channel on the `uncommittedChanges` target;
  it is dropped for codex in v1 (noted in the API docs; the `custom`
  target can carry it in v2).

### 6.3 Claude Code: `/code-review`

`ClaudeProvider.run_review` reuses `ClaudeNativeSession` end to end
(`claude.py:41`): same PTY spawn, same JSONL drain, same Stop-hook
terminal signal. The differences from `run()`:

- The submitted text is the bare slash command — **not**
  `context.turn_text()`, and `system_prompt_append` stays empty for
  this turn's session: nothing may precede `/` or the TUI treats it
  as prose. Focus text rides as the command's argument string:
  `/code-review <focus>` — slash-command arguments are free text
  passed to the skill, so `spec.focus` and an instruction to
  constrain scope ("review only uncommitted changes: staged,
  unstaged, and untracked — git diff HEAD plus untracked files")
  travel there.
- **Scope caveat, stated honestly**: `/code-review` reviews "the
  current diff", which may include committed-but-unpushed work
  relative to the default branch. The argument-string narrowing is
  best-effort prompt guidance, not a structured target. On a
  workflow where commits happen through MiniClaw2 commit ops on the
  working branch, the two scopes usually coincide; the residual
  divergence is documented, and the `reviewed-diff.patch` snapshot
  is the ground truth for what *should* have been reviewed.
- **Harvest**: the report is the final assistant message of the
  turn, extracted from the JSONL the session already tails
  (`TranscriptTranslator` sees every record; the provider
  accumulates the last assistant text block and wraps it as
  `ReviewReport(raw_markdown=...)`, findings `None`).
- **Failure detection**: an unknown slash command produces a
  recognizable "Unknown slash command"-shaped response instead of a
  review; the provider matches this and yields `kind="error"`
  naming the required Claude Code version. Never `--fix` or
  `--comment` — report-only (decision 3).

### 6.4 Normalization

`raw_markdown` is the lowest common denominator and is always
persisted as `code-review-report.md`. Structured `findings` are a
codex-only bonus in v1, persisted as `code-review-findings.json` and
used to enrich `summary`/`next_implications`. Nothing downstream may
require the structured form — that is what keeps the two providers
honestly interchangeable behind one subtype.


## 7. What the node does *not* get

- **No lane, no reap.** The node writes nothing under
  `.miniclaw2/graph/`; `reap_lane` never runs for it. This closes the
  output-contract mismatch (a native reviewer emits findings but never
  writes the `preview.json` that terminal reap validates, which would
  mark the node a preview-contract error) by construction rather than
  by adaptation.
- **No virtual mutations.** Review category normally grants virtual
  write rights at reap (`reap.py`); since code_review skips reap,
  the right is moot. The planning loop reads the report artifact
  like any other node output.
- **No launch instruction block.** `launch_prompt.py:37` routing is
  untouched; `_template_for_node` never sees a code_review node
  because `_run_code_review` doesn't call it. (A guard assert there
  is cheap insurance.)


## 8. API and UI surface

- **Virtual creation** (`app.py:815` endpoint, `registry.py:1189`):
  accepts `subtype: "code_review"` plus optional `review_target`
  (defaulted) and optional `brief`/`prompt_draft`. Promotion
  (`app.py:867`, `registry.py:1114`) carries the new field through
  with the empty-draft carve-out (§3.2). Planning/review agents may
  propose code_review virtuals; the planning prompt templates gain a
  line documenting the subtype and its defaults.
- **`POST /sessions/{sid}/git/review`** — convenience endpoint
  parallel to `/git/commit` (`app.py:631`): spawns a queued
  code_review node (a `spawn_git_op`-style registry helper,
  `registry.py:901`, but producing an agent node under the project's
  model preset). No quiescence 409 needed at the endpoint — the
  scheduler guard (§4.3) owns serialization; the node simply waits.
- **Ghost commit composer** gains a **Review** button beside Commit
  (`GitCommitPanel`): spawns via the endpoint above and selects the
  new node. Disabled when `dirty_count == 0`, same as Commit.
- **Node panel**: renders like other review nodes; the report
  artifact is the primary content (artifact viewing machinery
  exists); the preview summary carries the verdict line. The
  `reviewed-diff.patch` snapshot is exposed on the node's detail
  panel alongside the existing per-node diff view (`app.py:1049`).
- **WS surface unchanged**: no new client input types — creation and
  promotion ride existing REST; progress rides existing node events.
  (The codex gap analysis anticipated a `start_review` WS message; a
  REST spawn endpoint + normal node lifecycle turns out to be
  sufficient because the review is a node, not a modal session state.)


## 9. Edge cases

- **Clean tree** — short-circuit DONE, "working tree clean" (§5.1).
- **`.miniclaw2/`-only dirt** — `dirty_count` already excludes it →
  same short-circuit; consistent with the ghost not rendering.
- **Untracked-only changes** — dirty per porcelain; codex
  `uncommittedChanges` covers untracked natively; cc is instructed
  via the argument string; the snapshot records the untracked list
  either way.
- **Terminal-side edit mid-review** — fingerprint mismatch →
  staleness warning on preview + report (§4.2).
- **Interrupt/cancel** — provider interrupt paths reused; stub
  preview written; state CANCELLED.
- **Old codex CLI / missing `/code-review`** — early, named error
  (§6.2/§6.3); node ERROR, no silent fallback.
- **Empty findings, positive verdict** — a valid, useful outcome:
  summary carries the clean verdict; report artifact still
  published.
- **Non-repo project** — `git_head()` returns `None`
  (`git_state.py:261`); creation of code_review nodes is rejected
  for non-repo projects at the endpoint, same condition that
  disables the git header controls.


## 10. Out of scope (v1)

- **`base_branch` / `commit` / `custom` targets** — the
  `review_target` shape anticipates them; codex maps directly, cc
  needs per-target prompt design. Also the answer for auto_commit
  projects (§4.4).
- **Detached codex reviews** and `reviewThreadId` persistence.
- **Auto-converting findings into virtual fix nodes** — revisit once
  real reports show whether codex's structured findings are reliable
  enough to script against (decision 3).
- **`--fix` / `--comment` modes of `/code-review`** — mutation and
  external publication are both out of the report-only contract.
- **Review-blocking commit enforcement** — the gate is a workflow
  pattern (§4.5), not a hard constraint on commit ops.


## 11. Staged implementation

Each stage lands independently and leaves the app functional.

1. **Domain + scheduler.** `ReviewSubtype.CODE_REVIEW`,
   `ReviewTarget`, `Node.review_target`, validation carve-outs
   (domain + `VirtualPreview` + promotion); `artifacts` parameter on
   `render_executed_preview`/`_persist_executed_preview`; the
   `_exclusive_node_active` scheduler guard. Tests: validator
   matrix, promotion with empty draft, scheduler barrier under
   concurrency>1.
2. **Runner path with a stub provider.** `_run_code_review` complete
   (short-circuit, snapshot, harvest, publish, synthesize,
   divergence warning) against a fake provider yielding a canned
   `ReviewReport`. Tests: clean-tree short-circuit, artifact
   publication, preview field mapping, stale-fingerprint warning,
   cancel/stub paths.
3. **Codex provider.** `run_review` + capability probe + findings
   mapping pinned against the local app-server schema; version floor
   recorded. Tests: JSON-RPC exchange against a scripted fake
   app-server (existing codex test pattern), unsupported-version
   error.
4. **Claude provider.** `run_review` with bare-slash submission,
   final-message harvest, unknown-command detection. Tests: JSONL
   fixture drains, focus-argument composition.
5. **API + UI.** `/git/review` endpoint, ghost-composer Review
   button, node panel report rendering, virtual-creation plumbing.
6. **Docs.** Ledger entry in
   `IMPLEMENTATION_STATUS.md`; one-line subtype additions to the
   planning prompt templates; `PHILOSOPHY.md` §6.1 gains the
   review-gates-the-ghost sentence.
