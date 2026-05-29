# MiniClaw2 testing — dashboard-launched, demo-driven, investigation-free

> **Status: Tier 1 + Tier 2 single-node trio landed.** The dashboard
> test panel, the temporary-workspace feature, the scenario
> loader/launcher, the `verify.sh` runner, the three bundled Tier 1
> scenarios (`hello-text`, `bash-uname`, `write-readme`), and the
> three Tier 2 single-node scenarios (`permission-approve`,
> `plan-mode-approval`, `interrupt-midstream`) are all in. See
> `TESTING.zh.md` for the end-user run-through. Tier 3 (multi-step
> scenarios — `gui-calculator`, `context-md-respected`,
> `resume-fix-after-reject`) and Tier 4 (`reconnect-replay`) remain
> planned; the multi-step ones need a scenario-step expander on the
> `runner_done` callback (see §8) before they can land. Grounded in
> `DESIGN.md §1.1`
> ("investigation-free interface"): every test is a small task whose
> **observable outcome** the human ratifies. Internal correctness —
> gate routing, commit-op rewrite, reconnect replay — is exercised
> transitively. If an internal path is broken, the visible artifact
> is broken too.

## 1. What this document is

This is the integration-test tier. Engineer-facing unit tests under
`backend/tests/` (`test_context`, `test_gate_node`, `test_op_node`,
`test_replay`, `test_temporary_project`, `test_scenarios_loader`,
`test_scenarios_launch`) exist for backend hygiene during
development and are **separate** from this document — they live on
as the fast inner loop for refactors. Tier 1 added the latter three
to that pile when the scenario module landed.

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
brief: "Build a Tk calculator, review it, snapshot it."
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
`ProjectRegistry.start_node` / `start_gate_node` (the same paths the
chat composer uses) to launch the first step. For Tier 1 only the
first step exists; Tier 2+ work adds a scenario-step expander on
the `runner_done` callback (parallel to the auto-commit op's
expander) so subsequent steps land without a scenario-specific
runner — the "just runs like a normal node" guarantee stays.

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
  `echo hello-from-bash` via Bash. The runner emits a
  `permission`-flavored `interaction_request`; the user approves once
  from the gate tab.
- Verify: `events.jsonl` contains an `interaction_request` with
  `interaction_type == "permission"` **and** an `activity` with
  `result_kind == "stdout"` whose `result` contains `hello-from-bash`
  (proves the gate fired *and* the tool ran afterward).
- Acceptance: "a permission prompt appeared in the gate tab; you
  clicked Allow; the agent then ran `echo hello-from-bash` and the
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

### Tier 3 — integrated (gates + ops + edges) (planned)

**gui-calculator** *(flagship visual demo)*
> Agent builds a tkinter calculator → checkpoint gate with markdown contract → write-json review → auto-commit op rewrites the agent's `commit_after`.

- Two declared nodes (`build` agent, `review` gate); `auto_commit:
  true`. The review contract lives in `contract.md`; the reviewer
  writes JSON to `reviews/build.json`.
- Verify: `calculator.py` runs (`python -c "import calculator"`);
  smoke test fires button events for `1 + 2 =` and asserts `3`;
  `9 / 0` produces an error indicator (not a Python traceback);
  `git log --oneline` shows two commits per agent/gate node; the
  `build` node's `commit_after` differs from its `commit_before`
  (proves the rewrite happened).
- Acceptance: a window opens, buttons 0–9 + operators are visible
  and labeled, clicking `1 + 2 =` shows `3`, keyboard entry works,
  `9 / 0` shows an error indicator, `C` clears, closing the window
  exits cleanly.

**Note for maintainers:** this is the explicit scenario to run when
validating the auto-commit op path; no dedicated `auto-commit-only`
scenario exists by design — the auto-commit op is on the critical
path of `gui-calculator`, so a regression there breaks this demo's
verify *and* its acceptance. Document this in the dashboard's test
panel so a future maintainer knows where to look.

**context-md-respected**
> Project-neutral `CONTEXT.md` injection is honored by both providers.

- Seed copies a `CONTEXT.md` containing "End every assistant reply
  with the literal token `[CTX-OK]`."
- Prompt: "What's 2 + 3?" (banal — the interesting signal is the
  marker).
- Verify: assistant text in the first turn's `events.jsonl`
  contains `[CTX-OK]`; the node's `system_context_snapshot` field
  matches the seeded text byte-for-byte.
- Acceptance: "the reply ended with `[CTX-OK]`."

**resume-fix-after-reject**
> Resume edge: agent → gate (reject) → resume agent → gate (approve).

- Three declared nodes: `build` agent, `review` gate (reject path),
  `fix` agent with `resume_from: build` and `when: review.rejected`.
  The first gate is rejected via write-json `{approved: false,
  notes: "add division"}`. A second `review` (or re-using the same
  template via §8) lets the user approve.
- Verify: timeline has the expected node sequence; the `fix` node
  carries `parent_node_id == build.id` and inherits `build`'s
  `provider_session_id`; final `git log` shows the multi-commit
  history; an SVG resume connector is visible in the timeline.
- Acceptance: "you saw the first review render the contract,
  rejected it with the canned notes, watched the resume node
  continue from the build's session (visible `↻` badge), and
  approved the second review."

### Tier 4 — resilience (planned)

**reconnect-replay**
> WS drops mid-stream; reconnect; replay fills the gap; live tail finishes.

- Start an agent node with a prompt that streams for ~10s
  (e.g. "Write a 200-word summary of Python's history; type
  slowly"). Mid-stream, simulate a drop: a **Simulate drop** button
  on the test panel closes the active WS and reopens it with
  `(node_id, last_seq)`.
- Verify: events captured by the second observer + replay produce
  no duplicates, no gaps; the `events.jsonl` matches the union of
  pre-drop + post-replay sequences exactly.
- Acceptance: "after the simulated drop, the transcript kept
  growing without rewinding, no text was duplicated, and the node
  reached `done` cleanly."

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

To revisit when Tier 2+ work starts:

- **Scenario-step expander on `runner_done`.** Tier 1 only launches
  the first step. Multi-step scenarios (Tier 2+) need an expander
  parallel to `_spawn_op_commit` in `ProjectRegistry._on_runner_done`
  that consults the scenario's `nodes:` list + step terminal state +
  optional `when:` predicate and enqueues the next step (or
  finishes the scenario). Threading the `TemplateInstance`-style
  cursor through the registry without polluting general node paths
  is the work to scope.
- **Branching in `scenario.yaml`.** Tier 3's `resume-fix-after-reject`
  needs "the gate was rejected → run the fix step." A minimal
  `when: <step>.rejected` / `when: <step>.approved` predicate,
  evaluated by the same expander, covers the planned scenarios
  without a full template engine. Worth keeping the YAML linear
  until we actually need it.
- **How to drive `reconnect-replay` from the dashboard.** A
  "Simulate drop" button on the test panel is the cleanest option;
  instructing the user to close the tab is the simplest. Decide
  when the scenario is implemented.
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
