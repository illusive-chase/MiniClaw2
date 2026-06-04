# MiniClaw2 testing — dashboard-launched, demo-driven, investigation-free

> **Status: Tier 1, Tier 2, Tier 3, and Tier 4 catalogues all landed
> (engine + assets — live-smoke pending on Tier 3/4).** The dashboard
> test panel, the temporary-workspace feature, the scenario
> loader/launcher, the `verify.sh` runner, the three bundled Tier 1
> scenarios (`hello-text`, `bash-uname`, `write-readme`), and the
> three Tier 2 single-node scenarios (`permission-approve`,
> `plan-mode-approval`, `interrupt-midstream`) are all in. Tier 3
> adds `gui-calculator`, `context-md-respected`, and
> `resume-fix-after-reject`; Tier 4 adds `reconnect-replay`. See
> `TESTING.zh.md` for the end-user run-through.
>
> **Multi-step scenario engine landed.** The scenario-step expander
> question from §8 is resolved: `ProjectRegistry._advance_scenario_step`
> runs from `_on_runner_done` after the auto-commit branch, routes
> through op parents, records each step in
> `Project.scenario_step_history`, halts on non-DONE terminal states,
> and enqueues the next step. Nodes grew `scenario_step_id`; the
> scenario loader gained `brief_from:` (gate-only) which auto-promotes
> the source agent step's `output_kind` to `review_brief`.
>
> **Gate semantics changed.** The `gate` node is now a **passive
> human checkpoint** — no agent run. The previous agent step writes a
> brief at `.miniclaw2/outputs/<id>/brief.md` (driven by the new
> `NodeOutputKind.REVIEW_BRIEF` contract); the gate reads that brief
> and renders it for the human, who responds via write-json / no-op
> as before. See `DESIGN.md §3.2` for the rationale.
>
> **Tier 3 + Tier 4 catalogue closed (assets pending live smoke).**
> `gui-calculator` is the flagship two-step demo (build agent → passive
> review gate, `auto_commit: true`). `context-md-respected` is a
> single-node scenario that seeds `CONTEXT.md` and checks both the
> `[CTX-OK]` marker in the transcript and that `system_context_snapshot`
> matches the seed byte-for-byte. `resume-fix-after-reject` is the
> three-step reject-driven branch (build → reject review → fix); its
> verify checks `scenario_step_history` recorded `decision: "rejected"`,
> `fix.parent_node_id == build.id`, and `fix` inherited the build's
> provider session. `reconnect-replay` is the Tier 4 resilience demo:
> the project header grows a **Simulate WS drop** button (conditional
> on `scenario_name === "reconnect-replay"`) that closes the live
> socket with code 1000 so `ws.ts`'s existing reconnect loop fires
> with `(node_id, last_seq)`; verify checks `events.jsonl` is a
> contiguous monotonic sequence and the transcript reaches the
> end-of-stream marker. New scenario-engine YAML extensions: `when:
> <step>.approved|rejected` (string predicate evaluated against the
> recorded gate decision) and `resume_from: <step_id>` (resolved via
> history to `start_node`'s `resume_from_node_id`). Gate completions
> now stamp `Node.review_outcome` from the write-json payload
> (`approved: false` → `"rejected"`, else `"approved"`). Grounded in
> `DESIGN.md §1.1` ("investigation-free interface"): every test is a
> small task whose **observable outcome** the human ratifies. Internal
> correctness — gate routing, commit-op rewrite, reconnect replay — is
> exercised transitively. If an internal path is broken, the visible
> artifact is broken too.

## 1. What this document is

This is the integration-test tier. Engineer-facing unit tests under
`backend/tests/` (`test_context`, `test_gate_node`, `test_op_node`,
`test_replay`, `test_temporary_project`, `test_scenarios_loader`,
`test_scenarios_launch`, `test_scenarios_expander`) exist for backend
hygiene during development and are **separate** from this document
— they live on as the fast inner loop for refactors. Tier 1 added the
loader / launcher tests when the scenario module landed; the
gate-redesign + multi-step expander work added `test_scenarios_expander`
and rewrote `test_gate_node` for the passive flow.

The integration tier covers **whole demos** behaving correctly across
both providers. Each demo is a **scenario** — a small task launched
from the dashboard's built-in test section, run in a fresh temporary
workspace, and ratified by a human after every node has reached a
terminal state.

## 2. Where it lives — the dashboard test section

There is **no CLI runner**. The entrypoint is a "Tests" panel in the
dashboard:

- The panel lists every bundled scenario discovered by the backend
  (`GET /scenarios`).
- Each row shows the scenario's name, one-line brief, and a per-
  provider Run button (Claude / Codex).
- Clicking Run creates a fresh temporary project, seeds the
  scenario's starter files into it, creates the first node, and
  switches the dashboard into the existing single-project view.
- From that point on **the scenario is just a normal project**: the
  user supervises in the normal node timeline, chats / approves
  gates / hits Stop as they would for their own work. Multi-step
  scenarios will enqueue their next nodes via the same `runner_done`
  hook the auto-commit op uses (Tier 2+ work); Tier 1 scenarios are
  single-step so the user never has to type a follow-up prompt.
- After every declared node has reached a terminal state, the
  project view reveals a **Verify** card with two halves:
  - **Programmatic floor.** A "Run verify.sh" button that POSTs to
    `/sessions/{sid}/verify`; the backend runs `verify.sh` in the
    project root (60s timeout, `MINICLAW_PROJECT_ID` +
    `MINICLAW_HOME` injected into env) and returns stdout/stderr +
    exit code.
  - **Human acceptance.** The scenario's `acceptance.md` is
    rendered as a checklist of checkboxes the user ticks (parsed
    from `- ` bullet items).

A scenario is **passed** only when:

1. `verify.sh` exits 0, **and**
2. Every item in the acceptance checklist is ticked OK by the
   human.

Either alone is a failure. **The human acceptance step is never
skipped, even for the simplest Tier 1 scenarios** — "an LLM produced
a sensible reply" is fuzzy enough that a human ratifies it. The
principle stands regardless of artifact shape: programmatic checks
catch regressions that would slip past tired eyes; human checks
catch "the program runs but the artifact is broken / wrong / ugly"
that programmatic checks structurally cannot.

## 3. General feature — temporary workspaces

The test panel rides on a feature that is **not test-specific** and
landed alongside Tier 1: a project may be created with `temporary:
true`, in which case the backend:

1. Creates a fresh tempdir (`tempfile.mkdtemp(prefix="miniclaw2-tmp-")`),
   via `backend/miniclaw2/workspace.py:create_temporary_root()`.
2. Runs `git init --initial-branch=main` + an empty initial commit
   so commit-op nodes produce real two-commit diffs.
3. Uses that path as the project's `root_path`.
4. Marks the project as `temporary` on disk (`Project.temporary =
   True`); deleting the project also removes the worktree (the
   `remove_temporary_root` helper refuses to rmtree any path that
   doesn't carry the `miniclaw2-tmp-` prefix).

This is exposed via the existing `POST /sessions` endpoint
(`temporary: true` flag). Test scenarios are the first heavy
consumer of this feature and the reason it landed now. A "Scratch
project" affordance for ad-hoc experiments is straightforward to
add on top of this flag but is **not** in v1 — the user explicitly
deferred it to a follow-up.

A temporary project is otherwise indistinguishable from a permanent
one: nodes run the same way, the timeline UI renders the same, the
auto-commit op still works against the seeded git history, and
reconnect replay still resumes the live tail.

## 4. Scenario layout — files in the codebase

Each scenario lives under `backend/miniclaw2/scenarios/bundled/<name>/`:

    backend/miniclaw2/scenarios/bundled/<name>/
      brief.md          # multi-line description (body); first non-empty line is a fallback
      scenario.yaml     # metadata: provider matrix, node spec, seed files, settings
      prompts/<id>.md   # per-node prompt files (referenced from scenario.yaml)
      contract.md       # gate contract (when the scenario uses a gate)
      seed/             # files copied into the tempdir at launch (e.g. CONTEXT.md)
      verify.sh         # black-box programmatic check; exit 0 = floor passed
      acceptance.md     # human checklist rendered in the Verify card

`scenario.yaml` minimum shape (Tier 1 — what landed):

```yaml
name: hello-text
brief: "Simplest end-to-end run: agent produces a sensible text reply."
providers: [claude, codex]        # always both
auto_commit: false
nodes:
  - id: turn1
    kind: agent
    prompt_file: prompts/turn1.md
```

The dashboard row uses the YAML `brief:` field (terse one-liner);
`brief.md` is reserved for longer-form context shown elsewhere or
to maintainers reading the directory.

Extended shape (Tiers 2–4 — for forward reference, not v1 impl):

```yaml
name: gui-calculator
brief: "Build a PySide6 Qt calculator, review it, snapshot it."
providers: [claude, codex]
auto_commit: true
permission_mode: default          # or "plan" for plan-mode-approval
seed:
  - from: seed/CONTEXT.md         # copied verbatim into tempdir
    to: CONTEXT.md
nodes:
  - id: build
    kind: agent
    prompt_file: prompts/build.md
  - id: review
    kind: gate
    prompt_file: prompts/review.md
    contract_file: contract.md
  # auto-commit ops are NOT declared here — they happen because auto_commit: true
  - id: fix                       # for resume-fix-after-reject
    kind: agent
    prompt_file: prompts/fix.md
    resume_from: build            # explicit conversation continuation
    when: review.rejected         # scripting branch — see §8
```

The scenario engine in `backend/miniclaw2/scenarios/` parses this
YAML, copies seed files into the tempdir, and uses
`ProjectRegistry.start_node` / `start_gate_node` (the same registry
APIs that drive `+ Node` agent launches and the user-gate auto-spawn)
to launch the first step. For Tier 1 only the first step exists;
Tier 2+ work adds a scenario-step expander on the `runner_done`
callback (parallel to the auto-commit op's expander) so subsequent
steps land without a scenario-specific runner — the "just runs like a
normal node" guarantee stays.

## 5. Provider matrix — Claude and Codex, both, every time

Every scenario runs once per provider, in independent temporary
workspaces. The dashboard surfaces the two as separate rows under
the scenario; running both is two clicks, not one. A scenario is
"passing" only when both providers pass — both `verify.sh` exits
and both human checklists are fully ticked.

Provider divergence is itself useful signal: if Claude passes and
Codex fails on the same scenario, the failure is either in the Codex
adapter or in Codex's own ability to deliver the task — and either
is information we want surfaced.

## 6. Scenario catalogue

Tiered from simplest to most integrated. **Tier 1 landed; Tiers 2–4
are planned** and the YAML/contract shape accommodates them without
churn.

### Tier 1 — basic agent (✓ landed)

**hello-text**
> Agent should produce a coherent text reply, no tool use.

- Prompt: "Reply with one sentence about Python. End your reply with the literal token `[OK]`."
- Verify: assistant text in `events.jsonl` contains `[OK]`.
- Acceptance: "the reply reads like a sensible sentence about Python (not a refusal, not gibberish, not just `[OK]` on its own)."

**bash-uname**
> Agent should run a single Bash command and report its output.

- Prompt: "Run `uname -a` via Bash and tell me what OS this is, in one sentence."
- Verify: `events.jsonl` contains a tool activity with `result_kind == "stdout"` whose `result` field contains "Darwin" or "Linux"; and the assistant text mentions one of those.
- Acceptance: "in the tool panel, expanding the Bash output shows a real uname line, and the assistant's sentence is consistent with it."

**write-readme**
> Agent should produce one file via Edit/Write.

- Prompt: "Create a `README.md` at the project root containing exactly the single line `# scratch` (followed by a newline). Do not create any other files."
- Verify: `tempdir/README.md` exists with content `# scratch\n`; no other tracked files were added.
- Acceptance: "in the node's diff panel, you can see `README.md` was added with the expected content."

### Tier 2 — inline gates and interactivity (✓ landed)

**permission-approve**
> Tool triggers a default-deny permission gate; user approves; tool runs.

- `permission_mode: default`. Prompt tells the agent to run
  `python3 -c 'print("hello-from-bash")'` via Bash. The runner emits a
  `permission`-flavored `interaction_request`; the user approves once
  from the gate tab.
- Verify: `events.jsonl` contains an `interaction_request` with
  `interaction_type == "permission"` **and** an `activity` with
  `result_kind == "stdout"` whose `result` contains `hello-from-bash`
  (proves the gate fired *and* the tool ran afterward).
- Acceptance: "a permission prompt appeared in the gate tab; you
  clicked Allow; the agent then ran `python3 -c 'print("hello-from-bash")'` and the
  output is visible in the Bash tile."

**plan-mode-approval**
> Project in plan permission mode; agent plans; user approves; agent executes; one file lands.

- `permission_mode: plan`. Prompt asks the agent to propose a plan
  and, on approval, write `PLAN_OK.txt` with the single line
  `plan-approved`.
- Verify: `events.jsonl` contains an `interaction_request` with
  `interaction_type == "plan_approval"`; `PLAN_OK.txt` exists in the
  workspace root with the exact content; `git status --porcelain` is
  clean apart from that one file.
- Acceptance: "a plan-approval prompt appeared in the gate tab; you
  approved it; the diff panel then shows `PLAN_OK.txt` added with the
  correct content; no extra files were created."

**interrupt-midstream**
> Long-running Bash; user hits Stop; node ends `cancelled`; partial output preserved.

- `permission_mode: bypassPermissions`. Prompt:
  `for i in $(seq 1 60); do echo "line $i"; sleep 1; done`.
- Verify: the latest node's `node.json` has `state == "cancelled"`;
  its `events.jsonl` contains at least one `text_delta` or `activity`
  event (the regression we're guarding against is "cancel wiped the
  in-flight buffer"); and no `turn_done` event with `state == "done"`
  is present.
- Acceptance: "you hit Stop after a few seconds; the node tile turned
  muted-grey (cancelled); the partial Bash output is still in the
  tool tile; no follow-up assistant turn fired after the cancel."

### Tier 3 — integrated (gates + ops + edges)

**gui-calculator** *(flagship visual demo — ✓ engine + assets landed; live smoke pending)*
> Agent builds a PySide6 / Qt Widgets calculator → passive review gate displays an agent-authored brief → user writes JSON review → auto-commit op rewrites the agent's `commit_after`.

- Two declared nodes (`build` agent, `review` passive gate);
  `auto_commit: true`. The `build` step's `output_kind` is auto-promoted
  to `review_brief` by the loader (because `review.brief_from: build`)
  — the build agent writes `.miniclaw2/outputs/<build-id>/brief.md`
  with `# How to run` / `# What to verify` / `# Response schema`
  sections. The gate then renders that brief verbatim; the reviewer
  writes JSON to `reviews/build.json`.
- Verify (programmatic floor): `requirements.txt` declares PySide6;
  `calculator.py` references PySide6, does not import Tk libraries,
  and imports cleanly without opening a window or requiring PySide6 to
  be installed (`python3 -c "import calculator"`);
  `reviews/build.json` exists and parses; `git rev-list --count HEAD
  >= 2` (initial + at least one auto-commit); the build node's
  `commit_after != commit_before` on disk (proves the auto-commit op
  rewrote it).
- Verify (human acceptance): a window opens with digits 0–9 +
  operators visible and labeled, clicking `1 + 2 =` shows `3`,
  `9 / 0` shows an error indicator (not a Python traceback), `C`
  clears, closing the window exits cleanly. Plus: the review tab
  rendered an agent-authored brief naming the exact setup and run
  commands (not the generic template).

**Note for maintainers:** this is the explicit scenario to run when
validating (a) the auto-commit op path, (b) the multi-step scenario
expander, and (c) the passive-gate + `review_brief` flow. No dedicated
`auto-commit-only` or `passive-gate-only` scenario exists by design —
all three are on the critical path of `gui-calculator`, so a regression
in any of them breaks this demo's verify *and* its acceptance.
Document this in the dashboard's test panel so a future maintainer
knows where to look.

**context-md-respected** *(✓ engine + assets landed; live smoke pending)*
> Project-neutral `CONTEXT.md` injection is honored by both providers.

- Seed copies a `CONTEXT.md` containing "End every assistant reply
  with the literal token `[CTX-OK]`."
- Prompt: "What's 2 + 3?" (banal — the interesting signal is the
  marker).
- Verify: assistant text in the first turn's `events.jsonl`
  contains `[CTX-OK]`; at least one node's `system_context_snapshot`
  field matches the seeded text byte-for-byte.
- Acceptance: "the reply ended with `[CTX-OK]`."

**resume-fix-after-reject** *(✓ engine + assets landed; live smoke pending)*
> Resume edge: agent → gate (reject) → resume agent.

- Three declared nodes: `build` agent (writes `mathutils.py` with only
  `add`), `review` gate sourced via `brief_from: build`, `fix` agent
  with `resume_from: build` and `when: review.rejected`. The first
  gate is rejected via write-json `{approved: false, notes: "..."}`.
  The expander records `decision: "rejected"` on the review's history
  entry and branches into `fix`; the fix step resumes the build's
  provider session and addresses the notes (a second review is *not*
  declared in v1 — the verify floor + acceptance covers the resume
  path without a second human turn).
- Verify: `scenario_step_history` shows `build` / `review` / `fix`
  all `terminal_state: done` and review's `decision: "rejected"`;
  the `fix` node carries `parent_node_id == build.id` and inherits
  `build`'s `provider_session_id`; `mathutils.py` ends up with both
  `add` and at least one more function the reviewer asked for;
  `git rev-list --count HEAD >= 3` (seed + per-step auto-commit ops).
- Acceptance: "you saw the build node produce `mathutils.py` with
  only `add` and a brief naming the import command, rejected the
  review with `{approved: false, notes: …}`, watched the fix node
  appear with the `↻ build` resume badge, and confirmed the final
  `mathutils.py` contains both the original `add` and the function
  you asked for."

### Tier 4 — resilience

**reconnect-replay** *(✓ engine + assets landed; live smoke pending)*
> WS drops mid-stream; reconnect; replay fills the gap; live tail finishes.

- Single agent node with a prompt asking for "10 short facts about
  Python, one per line, each prefixed `Fact N:`, ending the last line
  with `[END]`." The project header surfaces a **Simulate WS drop**
  button (conditional on `scenario_name === "reconnect-replay"`) that
  calls `useSessionSocket`'s new `simulateDrop()` — closing the active
  socket with code 1000 so `ws.ts`'s existing reconnect loop fires
  with the tracked `(node_id, last_seq)`.
- Verify: `events.jsonl` for the streaming node carries strictly
  increasing seqs with no gaps (the canonical source replay reads
  from); transcript contains `[END]`; node `state == "done"`. Client-
  side duplicate / gap behavior is *not* observable from the shell
  and is therefore deferred to the human acceptance step.
- Acceptance: "the transcript was visibly growing line-by-line when
  you clicked Simulate WS drop; the ws indicator briefly flickered to
  `connecting` then `open`; the transcript continued from where it
  left off (no rewind, no visibly duplicated facts); the node reached
  `done` cleanly and ended with `[END]`."

### Manual case — ContextSpace bootstrap and bundle injection

**contextspace-bootstrap-manual** *(manual dashboard case, not a bundled scenario)*
> Start MiniClaw2 with an isolated `MINICLAW_HOME`, bootstrap a
> ContextSpace from the dashboard, launch one node, and verify that
> project `CONTEXT.md` plus active planspace `STATUS.md` / `PLAN.md`
> are snapshotted and injected.

Setup uses a special home so the test does not touch the developer's
real `~/.miniclaw2`:

```bash
rm -rf /private/tmp/miniclaw2-contextspace-test
mkdir -p /private/tmp/miniclaw2-contextspace-test/workspace
cat >/private/tmp/miniclaw2-contextspace-test/workspace/CONTEXT.md <<'EOF'
# ContextSpace Manual Test Workspace

This project exists only for MiniClaw2 ContextSpace testing.

Agents should mention the phrase `contextspace-manual-test` when asked to
summarize the project context.
EOF

cd backend
MINICLAW_HOME=/private/tmp/miniclaw2-contextspace-test/home \
  python -m miniclaw2 --host 127.0.0.1 --port 8000 --log-level info
```

In another shell:

```bash
cd frontend
npm run dev
```

Create or select a session whose cwd is
`/private/tmp/miniclaw2-contextspace-test/workspace` and provider is
either `codex` or `claude`. The API equivalent is:

```bash
curl -s -X POST http://127.0.0.1:8000/sessions \
  -H 'content-type: application/json' \
  -d '{"cwd":"/private/tmp/miniclaw2-contextspace-test/workspace","provider":"codex","name":"ContextSpace Manual Test"}'
```

Dashboard steps:

1. Open `http://localhost:5173/`.
2. Select `ContextSpace Manual Test`.
3. Open the `ContextSpace` panel.
4. Confirm root points at
   `/private/tmp/miniclaw2-contextspace-test/home/contextspace`, `Root`
   is missing, and `Resolved binding` is none.
5. Click `Create`; use title `Manual ContextSpace Track`.
6. Confirm root is present, resolved binding is
   `project.manual-contextspace-track`, and active planspace is
   `planspaces.manual-contextspace-track`.
7. Send this prompt:

   ```text
   Summarize the loaded project/contextspace context. Mention whether you saw the phrase contextspace-manual-test.
   ```

Expected disk and UI evidence:

- ContextSpace files exist under
  `/private/tmp/miniclaw2-contextspace-test/home/contextspace`, including
  `contextspace.yaml`,
  `bindings/projects/project.manual-contextspace-track.yaml`,
  `plugs/planspaces/manual-contextspace-track/manifest.yaml`,
  `STATUS.md`, `PLAN.md`, and `SKILLS.md`.
- The completed node has `context_bundle_id`,
  `context_bundle_path`, `project_context_binding_id`, and
  `active_planspace_id` in its detail view / `node.json`.
- The context bundle sources include:
  - `/private/tmp/miniclaw2-contextspace-test/workspace/CONTEXT.md`
    with `injection: system`;
  - `plugs/planspaces/manual-contextspace-track/STATUS.md` with
    `injection: turn`;
  - `plugs/planspaces/manual-contextspace-track/PLAN.md` with
    `injection: turn`.
- The assistant reply or summary artifact mentions
  `contextspace-manual-test`.
- Server logs show only normal `200 OK` requests and no ContextSpace
  exception.

Notes:

- A permission gate may appear if the provider writes the summary
  artifact through a shell command. Approve it; this is expected.
- The planspace `events.jsonl` can remain empty in this case. This
  flow does not require a `memory-delta.json`, so it does not test
  automatic `STATUS.md` writeback.

## 7. Out of scope for v1

- Recorded provider transcripts (VCR-style replay). Worth adding
  once one scenario passes end-to-end live on both providers;
  encoding recordings before that risks freezing bugs into the
  baseline.
- Headless visual diffing (screenshot baseline + perceptual diff).
  The human is the visual oracle for v1; baselines come once we
  know what "correct" looks like across both providers.
- CI integration. The benchmark requires a display, real provider
  credentials, and human ratification by design; it is a developer-
  driven gate, not a CI gate.
- A simulated-user (`operator.py`) framework. The dashboard makes
  the real user the operator. Scenarios that need scripted gate
  responses (e.g. an "auto-approve every gate" affordance for
  unattended re-runs) are reconsidered after Tier 1 has been used.
- User-authored scenarios from the UI. For now scenarios are
  bundled in the repo and discovered at backend startup.

## 8. Open questions

To revisit after live-smoking the Tier 3 / Tier 4 catalogue:

- **Branching in `scenario.yaml`.** *Resolved.* `when:
  <step>.approved|rejected` is a string predicate parsed at load
  time; `_advance_scenario_step` walks forward skipping steps whose
  predicate doesn't match the recorded gate `decision`. Gate
  completions stamp `Node.review_outcome` from the write-json
  payload (`approved: false` → `"rejected"`, else `"approved"`); the
  expander mirrors that onto the history entry. YAML stays linear
  (no DAG) until a real second use-case forces our hand.
- **`resume_from:` step field.** *Resolved.* Agent steps may declare
  `resume_from: <step_id>`; the loader validates the target is an
  earlier step, the expander resolves the matching `node_id` from
  history and passes it as `start_node`'s `resume_from_node_id`. The
  new node inherits the source's provider session.
- **How to drive `reconnect-replay` from the dashboard.** *Resolved.*
  A small "Simulate WS drop" button lives in the project header,
  conditional on `session.scenario_name === "reconnect-replay"`. It
  calls `useSessionSocket`'s `simulateDrop()` helper, which closes
  the live socket with code 1000 so the existing reconnect path
  fires with `(node_id, last_seq)`. No test-panel-specific UI
  needed.
- **Cross-provider artifact comparison.** Same brief, two providers,
  two produced repos — is there a useful diff to surface? Likely
  yes, but the format will fall out of running a few scenarios
  first.
- **Acceptance state persistence.** Tier 1 keeps the tick state in
  frontend-only React state — closing the project view loses the
  ticks. If a "passing" record matters across reloads, persist
  `Project.scenario_run` with the verify exit code and per-item
  acceptance state. Not painful enough to fix yet.
- **Scratch-project affordance.** The `temporary: true` flag is now
  general, but no "New scratch project" button exists in the
  dashboard. Add when ad-hoc-experiment use shows up; the wiring is
  one button + a `POST /sessions {temporary: true}` call.
- **Second-review step for `resume-fix-after-reject`.** v1 ships
  three steps (build / review / fix) so the resume edge is the
  observable outcome. Adding a `review_2` step (the "approve the
  second review" line from the original acceptance copy) would
  exercise `when:` with the approved branch too. Hold until we
  have a second use-case that wants the same shape.
