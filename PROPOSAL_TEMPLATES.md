# Proposal: Test scenarios become virtual-node templates

Status: design proposal, not yet landed.
Companion to `PROPOSAL_VIRTUAL_NODES.md` (the virtual-node ontology this
builds on) and `IMPLEMENTATION_STATUS.md` (which still lists the scenario
engine and the deferred templates layer as separate).

This proposal collapses the bundled-scenario engine into the virtual-node
graph the rest of the system already runs on. A "test scenario" becomes a
frozen DAG of virtual nodes that the framework stamps into a fresh
planspace on launch. The user sees a normal canvas with normal panels;
the `Verify` card, the acceptance checklist, the scenario-step expander,
and the `scenario_step_id` / `scenario_step_history` plumbing all go
away.

The proposal also introduces a third `NodeKind` — **`verifier`** — for
programmatic review steps. A verifier runs a deterministic script (no
provider call), reaps a preview the same way a review agent does, and
emits a follow-up virtual on failure so the failure has a place on the
canvas to triage from.

This is the bridge between the bundled-scenarios machinery
(`backend/miniclaw2/scenarios/`, `VerifyCard.tsx`, `/sessions/{sid}/verify`,
`ScenarioFutureNode.tsx`) and the virtual-node redesign that landed per
`PROPOSAL_VIRTUAL_NODES.md`.


## 1. The pain we are fixing

The scenario engine was built before virtual nodes existed. Now that
virtual nodes can carry briefs, dependencies, review subtypes, resume
edges, auto-promotion, and reaped previews, the scenario engine is a
second implementation of the same shape:

- **Two scaffolds, one job.** `ProjectRegistry._advance_scenario_step`
  (and `_step_when_matches` / `_resolve_resume_node` /
  `_resolve_review_source_node` / `_infer_review_outcome`) is a parallel
  scheduler to `_auto_promote_next_virtual`. Both walk a DAG, both
  decide what to launch next, both branch on review outcomes — using
  different vocabulary (`when:` predicates vs `scheduled_deps`,
  `scenario_step_history` vs `Node.state in TERMINAL_NODE_STATES`).
- **Field bleed.** `Project.scenario_name`,
  `Project.scenario_step_history`, and `Node.scenario_step_id` exist
  only to keep that second scheduler running. They serialize into every
  `project.json` and `node.json` even on projects that have nothing to
  do with tests.
- **A bespoke verify surface.** `verify.py` shells out to `verify.sh`
  per scenario; `/sessions/{sid}/verify` is a scenario-only REST
  endpoint; `VerifyCard.tsx` is the only place in the UI that ratifies
  work outside the canvas / side-panel pattern; `acceptance.md` is
  parsed by a regex in the frontend into checkboxes that never round-
  trip to the backend.
- **Parallel tile rendering.** `ScenarioFutureNode.tsx` renders dashed
  future steps with its own component; virtual nodes already render
  dashed (`AgentNode` virtual branch) with the same intent.
- **Parallel YAML.** `scenario.yaml` validates `id` / `review_source` /
  `resume_from` / `when` across four passes in `loader.py`. The same
  semantics fall out for free from the virtual-node `scheduled_deps` +
  `parent_node_id` + `proposed_by` shape, plus the reap pipeline's
  cycle / slug checks.
- **An out-of-band node kind for programmatic checks.** `verify.sh`
  exists outside the graph: there is no tile for it, no preview, no
  edge to the work it verified, no place to attach a failure
  explanation. The graph loses a node it should have.


## 2. Diagnosis

Two diagnoses, one for each axis of the redesign:

**Scenarios are templates.** A scenario today is a fixed list of nodes
the framework instantiates one at a time. A template is a fixed list of
virtual nodes the framework instantiates once, up-front. The only thing
the scenario engine does that the virtual-node engine doesn't is
*deferred instantiation* — it materializes step N+1 after step N
terminates. But virtual nodes already carry their own deferred-launch
semantics via `scheduled_deps` + auto-promotion. So the scenario engine
is solving a problem that has already been solved, with worse vocabulary.

**Verification is a node, not a sidecar.** `verify.sh` runs in the
project root, reads `events.jsonl`, asserts a condition, exits 0 or
non-zero. That is exactly the shape of a review verdict — only without
a provider call. The current `gate review` ontology accepts two
subtypes (`agentic_review`, `human_interact_review`), both of which
launch an LLM. There is no slot for "this review is deterministic." A
new `NodeKind` opens that slot without contorting the existing review
subtypes.


## 3. The change

### 3.1 The verifier node kind

```
NodeKind = {agent, op, verifier}
```

A verifier node:

- Has `category = review` implicitly (the field still exists on the
  Node model for ontology continuity, but the kind pins it).
- Carries `verify.script_ref` — a path *inside the template directory*
  the framework resolves at instantiation. The path is copied into the
  durable node record as an absolute filesystem path; runtime mutations
  to the template do not affect already-instantiated runs.
- Carries a standard `brief` (`check_what` / `expected` / `abnormal`).
  The brief is the operator-facing explanation of what the script
  checks; it renders on the tile and side panel exactly like a review
  agent's brief.
- Runs no provider call. The runner subprocesses the script with the
  same env current `verify.sh` gets (`CI=1`, `MINICLAW_HOME`,
  `MINICLAW_PROJECT_ID`, `cwd = project.root_path`, 60s timeout).

**Verdict semantics:**

- **Exit 0 → preview state=done.** Framework writes a stub preview
  whose `summary` is "verify passed" and whose `next_implications` is
  empty. No virtuals written. The tile renders green ✓.
- **Exit non-zero or timeout → preview state=done.** Framework writes
  a stub preview whose `summary` contains the exit code + last 2 KB
  of stderr. *Plus* one virtual is auto-written:
  ```json
  {
    "kind": "agent",
    "category": "planning",
    "state": "virtual",
    "proposed_by": "node:<verifier-id>",
    "motivation": "verify failed: <one-line summary>",
    "prompt_draft": "Investigate the verify failure above and
                     propose a fix or revised plan.",
    "scheduled_deps": ["<verifier-id>"]
  }
  ```
  The tile renders red ✗ with the exit code visible. The auto-written
  planning virtual surfaces on the canvas dashed; the user can promote
  it directly or edit its prompt.

The verifier never enters `awaiting_human_input` — there is no user
interaction. Cancellation kills the subprocess and stubs the preview
with `state=cancelled`.

**Why a new kind rather than an op subtype:** ops today are pure side
effects (`commit`) and explicitly do not participate in review
verdicts. Verifiers participate the same way agent reviews do
(preview-with-judgment, mutation-as-verdict). Collapsing them into
`op` would force the runner to branch on op subtype where it currently
branches on kind, and would put review semantics on a kind that was
designed to be verdict-free. Splitting them keeps each kind's job
small.

### 3.2 The template

A template lives under `backend/miniclaw2/templates/bundled/<name>/`:

```
backend/miniclaw2/templates/bundled/<name>/
  template.yaml          # metadata: brief, providers, auto_commit,
                         # permission_mode, lane mode
  lane.yaml              # the predefined virtual-node DAG
  prompts/<slug>.md      # prompt_draft body for each agent virtual
  scripts/<slug>.sh      # verifier scripts
  seed/                  # files copied verbatim into the workspace
```

`template.yaml` shape:

```yaml
name: gui-calculator
brief: "Build a PySide6 Qt calculator, review it, snapshot it."
providers: [claude, codex]
auto_commit: true
permission_mode: default
lane_mode: manual                  # auto | manual; default manual
seed:
  - from: seed/CONTEXT.md
    to: CONTEXT.md
```

**No `capabilities:` field, no per-template UI hooks.** Templates are
100% native: every affordance a template can use already exists in the
canvas / side-panel vocabulary for non-template projects. If a check
cannot be expressed as agent + verifier + human review, it does not
become a template (see §8 non-goals).

`lane.yaml` shape — each entry is a virtual node spec; the same
schemas the virtual-node redesign already validates:

```yaml
nodes:
  - id: build
    kind: agent
    category: regular
    prompt_file: prompts/build.md

  - id: verify_files
    kind: verifier
    script_ref: scripts/verify_files.sh
    scheduled_deps: [build]
    brief:
      check_what: "PySide6 in requirements.txt, calculator.py
                   importable, at least one auto-commit landed."
      expected: "exit 0"
      abnormal: "missing files, import error, no commits."

  - id: accept
    kind: agent
    category: review
    subtype: human_interact_review
    scheduled_deps: [build, verify_files]
    prompt_file: prompts/accept.md
    brief:
      check_what: "Open the calculator; click 1 + 2 =;
                   confirm 3 shows. Confirm 9 / 0 shows an
                   error indicator, not a traceback. Close
                   the window cleanly."
      expected: "all bullets above hold."
      abnormal: "any bullet fails — note which and how."
```

`brief.check_what` on the `accept` node *is* what `acceptance.md` used
to render in the `VerifyCard` checklist. Bullet form is fine; the
human-interact review prose form already handles arbitrary text.

`resume_from`, `review_source`, `when` from the old scenario YAML drop
entirely:

- `resume_from` becomes a `scheduled_deps` entry plus the framework's
  existing resume-from-terminal-parent behavior, which already wires
  the new node's `parent_node_id` and inherits the provider session
  when the parent reached `done` with a live session.
- `review_source` was the same shape as `scheduled_deps` always; one
  field, not two.
- `when: <step>.approved | rejected` drops because the review
  verdict's graph mutations *are* the branch. A reviewer that proposes
  no follow-up virtuals leaves the lane to continue as planned; a
  reviewer that proposes follow-up virtuals naturally diverts the
  promotion order. There is no separate `when` predicate to maintain.

### 3.3 Template instantiation

`POST /templates/{name}/run {provider}` replaces
`POST /scenarios/{name}/run`. On invocation:

1. Create a temporary project (existing flow). Set
   `Project.template_id = name` (renamed from `scenario_name`, see §4).
2. Apply `template.yaml`'s `auto_commit` and `permission_mode` to the
   new project's `settings_override`. No other template-specific
   settings exist.
3. Seed workspace files (existing `_seed_workspace` from
   `scenarios/launcher.py`, copied verbatim into the templates
   package).
4. Create a planspace named `Template: <name>` with the
   `lane.yaml`-declared mode.
5. Validate `lane.yaml`: every `id` unique, every `scheduled_deps`
   entry resolves, no cycles. (The reap pipeline already enforces
   these; this is the same code path, run once at instantiation.)
6. For each node in `lane.yaml`, persist a `Node` with
   `state=virtual`, `proposed_by=f"template:{name}"`,
   `prompt_draft` taken from `prompt_file` for agent nodes (verifier
   nodes have no prompt), `brief` taken from the spec, `category` /
   `subtype` set per the spec, `scheduled_deps` pinned to the
   canonical node ids in the same pass.
7. Trigger one round of `_auto_promote_next_virtual`. In `auto` mode
   the first eligible virtual launches; in `manual` mode it sits
   ready-to-promote on the canvas waiting for the user's click.

After this, the project is indistinguishable from any other planspace.
The canvas shows a dashed lane of N virtuals; the user advances it
the same way they advance their own work. The only durable trace that
"this came from a template" is `Project.template_id`.

### 3.4 Auto-promotion through the template lane

Unchanged. `_auto_promote_next_virtual` already walks
`scheduled_deps`, already picks the earliest-created eligible virtual,
already broadcasts `node_updated` on promotion. The scenario-step
expander and all its helpers are deleted; no replacement is needed.

### 3.5 The Tests modal entry point

`GET /templates` lists bundled templates; `POST /templates/{name}/run`
launches one. The frontend `TestsPanel` reads `/templates` instead of
`/scenarios`. The Run button per provider stays exactly as it is.

That is the entire frontend change for templates. There is no test-
only menu item, no test-only canvas affordance, no test-only WebSocket
envelope. Anything a template needs the user to do, the user does
through the same controls a normal project exposes (clicking a tile,
filling a human-review form, hitting Stop). If a check cannot be
expressed that way, the check is not bundled as a template — see §8.

### 3.6 The end-user run-through

User clicks **Run · claude** on `gui-calculator` in the Tests modal:

1. A new temporary project opens. The canvas shows three dashed tiles
   on a lane: `build` (agent), `verify_files` (verifier, ⚙ glyph),
   `accept` (human review).
2. In auto mode `build` launches immediately. In manual mode it waits
   for the user's click. From the user's perspective these are just
   virtual tiles — no extra "scenario" affordance is needed.
3. `build` runs, writes its preview, commits, transitions to done.
   `verify_files` becomes eligible.
4. `verify_files` promotes. The framework runs `scripts/verify_files.sh`
   in the project root. The tile updates with exit code; preview is
   written; if non-zero, a planning virtual ("Investigate the verify
   failure …") appears below.
5. `accept` becomes eligible. It enters `awaiting_human_input`. The
   side panel renders the brief inline: "Open the calculator; click
   1 + 2 =; confirm 3 shows. …" The user follows the instructions,
   types free-form prose (`all good` or itemized notes) into the
   `human-review.md` form, submits.
6. The reviewer agent launches, reads the brief + the user's prose +
   upstream previews, writes its preview, optionally proposes follow-
   up virtuals.
7. Lane terminal. The user is done. There is no separate `Verify`
   card to expand, no acceptance checklist to tick — the same
   judgment is captured in the `accept` node's prose. "This test
   passed" is "the lane is all-terminal and the `accept` reviewer
   proposed no follow-up virtuals."


## 4. What this replaces

- **`Project.scenario_name`** → renamed to `template_id`. Semantic
  widens from "this project was launched from a scenario" to "this
  project was instantiated from a template". The existing field is
  renamed in place (nuke-and-restart migration; no rows survive).
- **`Project.scenario_step_history`** → removed. The graph IS the
  history.
- **`Node.scenario_step_id`** → removed. Virtual nodes are addressed
  by their canonical ids assigned at instantiation.
- **`backend/miniclaw2/scenarios/`** package → renamed to
  `backend/miniclaw2/templates/`, restructured per §3.2 file layout.
- **`scenarios/loader.py`** four-pass validation of `review_source` /
  `resume_from` / `when` → removed. `lane.yaml` validation reuses the
  reap pipeline's existing cycle / slug-resolution code.
- **`scenarios/launcher.py`** → kept (renamed `templates/launcher.py`)
  but trimmed: the `_seed_workspace`, `_approval_policy_for`, and
  `_sandbox_for` helpers move over unchanged; the rest is replaced by
  the §3.3 instantiation pass.
- **`scenarios/verify.py`** → removed. Verifier nodes do this in-line
  via the runner.
- **`ProjectRegistry._advance_scenario_step`** plus
  `_step_when_matches` / `_resolve_resume_node` /
  `_resolve_review_source_node` / `_infer_review_outcome` → all
  removed. `_auto_promote_next_virtual` is the only scheduler.
- **`/scenarios` REST endpoints** (`GET /scenarios`,
  `GET /scenarios/{name}`, `POST /scenarios/{name}/run`) → replaced
  by `/templates` analogues.
- **`POST /sessions/{sid}/verify`** → removed.
- **`ScenarioSummary`, `ScenarioDetail`, `ScenarioRunRequest`,
  `VerifyResponse`** Pydantic models → replaced by template analogues
  (drop the verify model entirely).
- **`VerifyCard.tsx`** → removed. The brief on the `accept` virtual
  carries the same information; the human review prose form is where
  the user ratifies.
- **`ScenarioFutureNode.tsx`** → removed. Virtual nodes already
  render dashed.
- **`TestsPanel.tsx`** reads `/templates` instead of `/scenarios`;
  otherwise unchanged.
- **`acceptance.md` parsing regex** in `VerifyCard` → removed.
- **`session.scenario_name === "reconnect-replay"`** check in the
  project header menu → removed entirely along with the
  `Simulate WS drop` menu item. Test cases get no test-only UI
  affordances; reconnect-replay drops from the bundled catalogue
  (see §8).
- **`session_info.scenario_name`** → renamed `template_id` on the
  wire envelope.


## 5. What stays the same

- **The temporary-workspace mechanism.** `workspace.py.create_temporary_root`,
  the `miniclaw2-tmp-` prefix safety guard, and the
  `temporary: true` flag on `Project` are all untouched. Templates are
  the first heavy consumer, just as scenarios were before.
- **The Tests modal as the UI entry point.** Same listing layout,
  same per-provider Run buttons. Only the underlying endpoint name
  changes.
- **One node at a time per project.** Templates are linear lanes for
  the same FS-coherence reasons (`PHILOSOPHY` §11). The DAG governs
  promotion eligibility only.
- **Existing review-agent paths.** `agentic_review` and
  `human_interact_review` runner branches are unchanged. Verifier is a
  new third branch in the kind switch, not a modification of the
  existing two.
- **Auto-commit op.** Still framework-injected after agent done; not
  declared in `lane.yaml`. The verifier never auto-commits.
- **Materialization and reap.** The verifier writes its own preview
  through the same `.miniclaw2/graph/lanes/<lane>/nodes/<id>/preview.json`
  path; the framework is the writer instead of the provider, but the
  shape on disk is identical.
- **`PROPOSAL_VIRTUAL_NODES` schemas.** Executed-node preview schema
  applies unchanged to verifier nodes (strict whitelist, same fields).
- **The seed-files mechanism.** `seed/` directory + `from`/`to`
  entries port over verbatim.


## 6. Disk and storage

`projects/<pid>/project.json` gains `template_id`, loses
`scenario_name` and `scenario_step_history`. `projects/<pid>/nodes/<nid>/`
is unchanged: verifier nodes persist a `node.json` + `events.jsonl`
(framework-emitted events for the script run: `verifier_started`,
`verifier_stdout_chunk`, `verifier_stderr_chunk`, `verifier_done`) +
`preview.json` like any other node.

Templates live read-only at `backend/miniclaw2/templates/bundled/<name>/`.
No durable per-template state exists; instantiation produces standard
node records. No new keys appear in `Project.settings_override` —
templates use the same `auto_commit` / `permission_mode` slots a hand-
created project does.


## 7. Migration

Nuke and restart, same posture as `PROPOSAL_VIRTUAL_NODES.md` §7. No
back-dated translation, no `legacy/` directory.

Concrete steps at impl time:

1. Delete existing temporary projects (every project with
   `scenario_name` set is by construction temporary and disposable).
2. Drop `scenario_name` / `scenario_step_history` /
   `scenario_step_id` fields from the Pydantic models; existing
   non-temporary projects load fine because Pydantic ignores unknown
   fields on the way out.
3. Rewrite 9 of the 10 bundled scenarios as templates
   (`reconnect-replay` does not port — see §8). Effort estimate:
   - Tier 1 (`hello-text`, `bash-uname`, `write-readme`): one verifier
     + one accept agent each. ~15 min each.
   - Tier 2 (`permission-approve`, `plan-mode-approval`,
     `interrupt-midstream`): same shape; gate behavior is provider-
     level, not template-level. ~15 min each.
   - Tier 3 (`gui-calculator`, `context-md-respected`,
     `resume-fix-after-reject`): full multi-node lanes; resume becomes
     `scheduled_deps` + native resume edge. ~30 min each.
4. Drop the `scenarios/` package, `VerifyCard.tsx`,
   `ScenarioFutureNode.tsx`, the `Simulate WS drop` menu item, and
   the legacy fields in one commit after the templates land.

Until step 4, scenarios and templates coexist briefly so the rewrite
doesn't need to be atomic.


## 8. Non-goals

- **Any test-only frontend affordance.** Templates are 100% native:
  every interaction the user performs while running a template is one
  the canvas / side-panel / Tests-modal vocabulary already offers for
  non-template projects. No menu items keyed on template id, no
  bespoke WebSocket envelopes, no `Simulate X` buttons, no
  test-scoped settings flags. If a check cannot be expressed within
  the native vocabulary it does not become a template.
- **The `reconnect-replay` scenario.** Drops from the catalogue.
  Its check requires a `Simulate WS drop` button that exists only to
  trigger the failure mode — exactly the test-only affordance the
  rule above forbids. The underlying reconnect-replay code path is
  exercised every time a real WS drops; verifying it stays a manual
  developer activity (close the dev-tools network throttle, kill the
  laptop wifi, etc.) outside the bundled-template surface.
- **User-authored templates from the UI.** Bundled-in-repo only, same
  as scenarios today.
- **Template parameters / slot interpolation / `on_state` branching /
  `next:` loops** — the earlier sketch in `IMPLEMENTATION_STATUS` §8.
  Explicitly *not* this proposal; that machinery is deferred. This
  proposal is "frozen DAG of virtual nodes you can stamp out", which
  is a strict subset of the IMPLEMENTATION_STATUS §8 sketch.
- **Template versioning.** Last-writer-wins on disk; instantiated runs
  already snapshot what they need (prompt_draft, brief, script_ref).
- **Cross-template references.** Each template instantiates
  independently.
- **A separate kind for "deterministic ops with verdict."** Verifier
  IS that kind. If more variants emerge (HTTP probe, JSON schema
  check, screenshot diff), they extend verifier's `script_ref`
  protocol; the kind stays one.
- **Verifier participation in the planning category.** A verifier may
  *propose* a planning virtual on failure (the auto-written
  investigate-the-failure one), but it does not itself plan, and its
  prompt cannot be edited mid-run (there is no prompt — it's a script
  path).
- **Recovering `scenario_step_history` as a frontend audit.** The
  graph IS the audit. The canvas shows what happened.
- **Live mid-script verifier output streaming.** v1 captures the
  subprocess's stdout/stderr after exit, the same as `verify.sh` does
  today. Streaming chunks through to the canvas is straightforward
  but not load-bearing.
- **A template-level pre-flight check.** Validation happens at
  instantiation; `lane.yaml` parse errors fail the
  `POST /templates/{name}/run` call before any side effects land.
- **Frontend rendering of the auto-written failure virtual as
  anything special.** It renders as a normal dashed planning tile;
  the verifier's red ✗ + the dep edge are enough orientation.


## 9. Resolved questions

These were the four tradeoffs called out before this proposal landed;
settled here:

- **Q1 (verify as op subtype vs new kind):** new kind. Verifier
  carries review semantics (preview-with-judgment, mutation-as-
  verdict); ops are verdict-free side effects. Splitting keeps each
  kind's job small. Confirmed by the user.
- **Q2 (`scenario_name` field — keep, drop, rename):** rename to
  `template_id`. The provenance is worth keeping; the semantics
  widen to "instantiated from template X" which is genuinely useful
  beyond the test path. Confirmed by the user.
- **Q3 (per-template UI affordances like Simulate WS drop):** no
  template-specific UI exists. Earlier drafts of this proposal
  routed rare affordances through a `capabilities` list on the
  template; the user vetoed that path with the rule "test cases are
  100% native." Consequence: any scenario whose check requires a
  test-only button or envelope (today only `reconnect-replay`) drops
  from the catalogue rather than earning a frontend hook. Confirmed
  by the user.
- **Q4 (verifier failure surfaces):** the verifier's own preview
  captures exit code + stderr (last 2 KB); plus the framework auto-
  writes one planning virtual prompting the next agent to
  investigate. The failure has a place on the canvas to triage from
  without forcing the user to write the follow-up themselves.


## 10. Why we keep these constraints

- **Template = frozen DAG.** Anything fancier (parameters, slot
  interpolation, branching DSL) ends up reinventing scenario YAML's
  worst parts. The virtual-node DAG is already the planning vocabulary;
  the template is just a pre-recorded one.
- **Verifier verdicts go through the same channel as agent reviews.**
  Otherwise there are two different ways to fail a check (programmatic
  exit code vs reviewer-written virtuals) and two different UI
  affordances. Funneling both through "review preview + optional
  follow-up virtuals" keeps the ontology one-way.
- **No test-only frontend hooks, period.** The alternative is what
  we have today: a `scenario_name === "X"` check scattered through
  the frontend, plus a `Simulate WS drop` button that only exists to
  push the framework into a failure mode. Both bleed test-specific
  vocabulary into the product surface. We pay the cost (one scenario
  drops from the catalogue) to keep the frontend free of test-shaped
  code; this matches PHILOSOPHY §3's "the framework should not know
  it is being tested."
- **Auto-written failure virtual on verify exit ≠ 0.** Without it,
  the canvas after a failed verify looks the same as after a passed
  verify (one extra tile, terminal). Forcing the user to read the
  preview to know there's something to do is a regression. The
  follow-up virtual is the visual handle.
- **Templates instantiate up-front, not lazily.** Lazy
  materialization is what the scenario engine does today; it's the
  source of the parallel scheduler. Up-front instantiation makes the
  template a normal lane the moment it lands.
- **No back-compat on `scenario_name`.** Renaming-in-place is
  cheaper than maintaining both fields across the wire envelope, the
  Pydantic models, and the persistence layer. Single-user disposable
  temporary projects make this safe.
- **Verifier has no `awaiting_human_input` substate.** The whole
  point of the verifier is "no human in the loop"; the `accept` node
  next door is where the human goes.


## 11. Implementation sequencing

Horizontal backend-first, mirroring `PROPOSAL_VIRTUAL_NODES.md` §11:

1. **Domain types.** Add `NodeKind.VERIFIER`. Rename
   `Project.scenario_name → template_id`. Drop
   `Project.scenario_step_history` and `Node.scenario_step_id`. Add a
   `verify_script_ref` field on `Node` (optional; required when
   `kind=verifier`).
2. **Templates module.** Create `backend/miniclaw2/templates/` with
   `loader.py` (parses `template.yaml` + `lane.yaml`),
   `launcher.py` (project create + seed + lane instantiation),
   `bundled/` (the rewritten scenarios). The legacy
   `backend/miniclaw2/scenarios/` package stays in place during this
   step.
3. **Verifier runner.** A new branch in `NodeRunner` switched on
   `NodeKind.VERIFIER`: subprocess the script, capture stdout/stderr,
   write framework-authored `preview.json`, on failure write the
   auto-investigate virtual through the existing reap pipeline.
4. **Auto-promotion confirmation.** Verify
   `_auto_promote_next_virtual` correctly handles a verifier-then-
   review chain (the verifier is just a terminal-state node from the
   scheduler's POV, same as agents).
5. **REST surface.** Add `/templates` + `/templates/{name}/run`.
   Remove `/sessions/{sid}/verify`. Keep `/scenarios/*` temporarily.
6. **Frontend pass.** Add `VerifierNode.tsx` (⚙ glyph, exit-code
   badge, red ✗ on failure). Switch `TestsPanel` to `/templates`.
   Drop `VerifyCard.tsx`, `ScenarioFutureNode.tsx`, and the
   `Simulate WS drop` menu item with its `scenario_name === …`
   conditional. No new wire envelope fields land in this step;
   `SessionInfo` only changes by renaming `scenario_name` →
   `template_id`.
7. **Rewrite bundled scenarios as templates.** Per the §7 effort
   estimate. Each template lands as one PR commit so failure to
   re-derive a Tier 3 scenario doesn't block Tier 1.
8. **Remove legacy.** Drop `backend/miniclaw2/scenarios/`,
   `_advance_scenario_step` + helpers,
   `ScenarioSummary` / `ScenarioDetail` / `ScenarioRunRequest` /
   `VerifyResponse` Pydantic models, `/scenarios/*` REST endpoints,
   `Project.scenario_name` references in `app.py`, the
   `scenario_step_id` argument on `start_node`. This step must be
   last; until it lands the two paths can coexist briefly so the
   bundled-scenario rewrite isn't atomic with the removal.
