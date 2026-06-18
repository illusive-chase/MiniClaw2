# Proposal: Test scenarios become virtual-node templates

Status: landed in the current implementation.
Companion to `PROPOSAL_VIRTUAL_NODES.md` (the virtual-node ontology this
builds on) and `IMPLEMENTATION_STATUS.md` (which tracks the current
runtime state).

This proposal collapses the bundled-scenario engine into the virtual-node
graph the rest of the system already runs on. A "test scenario" becomes a
frozen DAG of virtual nodes that the framework stamps into a fresh
planspace on launch. The user sees a normal canvas with normal panels;
the `Verify` card, the acceptance checklist, the scenario-step expander,
and the `scenario_step_id` / `scenario_step_history` plumbing all go
away.

The proposal also introduces a third `NodeKind` — **`verifier`** — for
programmatic review steps. A verifier runs a deterministic script (no
provider call), writes a framework preview the same way an op node does,
and surfaces failure as `state=ERROR` so downstream human review can
triage from the verifier tile.

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
NodeKind     = {agent, op, verifier}
ReviewSubtype = {agentic_review, human_interact_review, programmatic_review}
```

A verifier node:

- Has `category = review` and `subtype = programmatic_review` —
  ontology slot for "this review is deterministic; no provider in the
  loop." The `Node._check_invariants` validator gains a `VERIFIER`
  branch: `brief` required, `subtype` must be `programmatic_review`,
  `prompt` and `prompt_draft` must be empty, `verify_script_ref` must
  be set.
- Carries `verify_script_ref` on the Node model (new optional field;
  required when `kind = verifier`). At template instantiation the
  framework resolves the YAML's relative `script_ref` to an absolute
  filesystem path and writes it durably. Runtime mutations to the
  template do not affect already-instantiated runs.
- Carries a standard `brief` (`check_what` / `expected` / `abnormal`).
  The brief is the operator-facing explanation of what the script
  checks; it renders on the tile and side panel exactly like an
  agent review's brief.
- Runs no provider call. The runner subprocesses the script with the
  same env current `verify.sh` gets (`CI=1`, `MINICLAW_HOME`,
  `MINICLAW_PROJECT_ID`, `cwd = project.root_path`, 60 s timeout).

**Verdict semantics — the state distinction does the work; no virtual
writes ever come out of a verifier:**

- **Exit 0 → `state = DONE`.** Framework writes a framework-authored
  executed preview (same shape `_run_op` writes today): `summary =
  "verify passed"`, `next_implications = ""`. The tile renders in the
  state-review tone (green ✓).
- **Exit non-zero or timeout → `state = ERROR`.** Framework writes
  `node.error = <last 2 KB of stderr>` and an executed preview whose
  `summary = "verify failed: exit <N>"` (with the stderr tail in the
  preview body for triage). The tile renders red automatically via
  the existing `state-error` tone. **Crucially**, because `state` is
  ERROR — not DONE — `_on_runner_done`'s existing DONE-only gate
  (`registry.py:501-502`) halts auto-promotion. The downstream
  `accept` (human-interact review) sits dashed until the user clicks
  it, at which point the human reviews the failure context (the
  verifier's preview is upstream) and decides.
- **Cancellation → `state = CANCELLED`.** The verifier runner's
  `interrupt()` calls `process.terminate()` then `process.kill()`
  after a 2 s grace period; the preview is stubbed with
  `state=cancelled`.

The verifier never enters `awaiting_human_input` — there is no user
interaction. The verifier never writes virtuals; the lane's downstream
review nodes are the only place where verdicts become graph mutations.

**Why a new kind rather than an op subtype:** ops today are pure side
effects (`commit`) and explicitly do not participate in review
verdicts. Verifiers participate the same way agent reviews do
(preview-with-judgment). Collapsing them into `op` would force the
runner to branch on op subtype where it currently branches on kind,
and would put review semantics on a kind that was designed to be
verdict-free. Splitting them keeps each kind's job small.

**Why a new ReviewSubtype rather than relaxing the
"review requires subtype" invariant:** the invariant
(`domain.py:187-190`) exists so every review surfaces a subtype-shaped
tile and side-panel branch on the frontend. Adding
`PROGRAMMATIC_REVIEW` keeps that contract intact and gives the
frontend an obvious switch point (no human prose form, no reviewer
agent — just a script-result panel).

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

`brief.check_what` on the `accept` node *is* what `acceptance.md`
used to render in the `VerifyCard` checklist. Bullets from each
scenario's `acceptance.md` fold into the corresponding template's
`accept` node `brief.check_what` as multi-line text (joined with
`\n`). The existing `ReviewBrief` model (`domain.py:95-98`) accepts
arbitrary strings; no schema change needed.

For lanes with a fix-after-review pattern, the spec carries
`resume_from` as a sibling of `scheduled_deps`:

```yaml
nodes:
  - id: build
    kind: agent
    prompt_file: prompts/build.md

  - id: review
    kind: agent
    category: review
    subtype: human_interact_review
    scheduled_deps: [build]
    prompt_file: prompts/review.md
    brief: { check_what: …, expected: …, abnormal: … }

  - id: fix
    kind: agent
    scheduled_deps: [build, review]
    resume_from: build              # inherit build's provider session
    prompt_file: prompts/fix.md
```

`review_source` and `when` from the old scenario YAML drop entirely:

- `review_source` was the same shape as `scheduled_deps` always; one
  field, not two. Review virtuals now declare their source via
  `scheduled_deps` alone.
- `when: <step>.approved | rejected` drops because the review
  verdict's graph mutations *are* the branch. A reviewer that proposes
  no follow-up virtuals leaves the lane to continue as planned; a
  reviewer that proposes follow-up virtuals naturally diverts the
  promotion order. There is no separate `when` predicate to maintain.

`resume_from` survives as an explicit `lane.yaml` field on agent specs
— *not* derived from `scheduled_deps`. It lands as a new optional
`Node.resume_from_node_id` field (kept distinct from `parent_node_id`,
which review virtuals already overload for review-source provenance —
see `registry.py:957`). Validation at instantiation:

- The named step must exist earlier in the lane.
- It must also appear in this node's `scheduled_deps` (so the resume
  parent is guaranteed terminal before promote).

The session-inheritance hop happens at promote time, not at
instantiation (the parent hasn't run yet). See §3.4 for the
`promote_virtual` change.

**Lane mode guidance.** `lane_mode: auto` is for happy-path templates
whose normal flow terminates every node in DONE state. Any template
whose normal flow includes a non-DONE termination — `interrupt-midstream`
expects the user to hit Stop during `build`; the verifier intentionally
ERRORs on failed checks — declares `manual`, because `_on_runner_done`
(correctly) halts auto-promotion on non-DONE terminals
(`registry.py:501-502`). The default is `manual`, and most bundled
templates should keep it.

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
   `state=virtual`, `proposed_by=f"template:{name}"`, and
   `scheduled_deps` pinned to the canonical node ids in the same pass.
   Then per kind:
   - **Agent virtuals:** `prompt_draft` from `prompt_file`; `brief` /
     `category` / `subtype` from the spec. If the spec carries
     `resume_from`, set `Node.resume_from_node_id` to the canonical
     id of the named step.
   - **Verifier virtuals:** `prompt_draft = None`; `category = review`;
     `subtype = programmatic_review`; `brief` from the spec;
     `verify_script_ref` set to the absolute resolved script path.

   For every node, render and persist a `preview.json` via
   `render_virtual_preview(node)` so the brief and dependency
   structure show on the canvas before any node has promoted — this
   generalizes what `start_node` does today for review virtuals
   (`registry.py:446-465`) to the whole lane.
7. Trigger one round of `_auto_promote_next_virtual`. In `auto` mode
   the first eligible virtual launches; in `manual` mode it sits
   ready-to-promote on the canvas waiting for the user's click.

After this, the project is indistinguishable from any other planspace.
The canvas shows a dashed lane of N virtuals; the user advances it
the same way they advance their own work. The only durable trace that
"this came from a template" is `Project.template_id`.

### 3.4 Auto-promotion through the template lane

`_auto_promote_next_virtual` is unchanged — it already walks
`scheduled_deps`, picks the earliest-created eligible virtual, and
broadcasts `node_updated` on promotion. The scenario-step expander and
all its helpers are deleted; no replacement is needed.

`promote_virtual` (`registry.py:617-668`) gains one step to support
the `resume_from` carrier. After validating the virtual is promotable
but before flipping `state = QUEUED`:

- If `node.resume_from_node_id` is set, load the resume parent.
- If the parent terminated in `DONE` and carries a live
  `provider_session_id` / `sdk_session_id`, copy `provider`,
  `provider_session_id`, `sdk_session_id` onto the virtual — identical
  to what `start_node` does today at `registry.py:437-439`.
- If the parent did not terminate in `DONE` or has no live session,
  refuse to promote. The lane halts. The user inspects, edits the
  virtual to drop the `resume_from` if appropriate, or fixes the
  upstream and retries.

`resume_from_node_id` is required to also appear in `scheduled_deps`,
so the parent is guaranteed terminal when the virtual becomes
eligible. The "parent in DONE with session" check at promote time is
the final guard; in the happy path it always passes.

### 3.5 The Tests modal entry point

`GET /templates` lists bundled templates; `POST /templates/{name}/run`
launches one. The frontend `TestsPanel` reads `/templates` instead of
`/scenarios`. The Run button per provider stays exactly as it is.

The optional `scenario_name` slot on `CreateSessionRequest`
(`app.py:55, 181`) drops entirely — templates only come into existence
via `POST /templates/{name}/run`. There is no `template_id` slot on
the session-create path. One fewer entry point, one fewer migration.

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
   in the project root. On exit 0 the tile turns green (`state=DONE`)
   and `accept` becomes eligible. On non-zero exit the tile turns red
   (`state=ERROR`, exit code + stderr tail in the preview), and
   auto-promotion halts at the DONE-only gate — `accept` sits dashed
   waiting for the user's click. Either way, no virtual is auto-written;
   the downstream review is the only place where verdicts become graph
   mutations.
5. `accept` becomes eligible (or is hand-promoted by the user after a
   verifier failure). It enters `awaiting_human_input`. The side panel
   renders the brief inline: "Open the calculator; click 1 + 2 =;
   confirm 3 shows. …" The user follows the instructions, types
   free-form prose (`all good` or itemized notes) into the
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
- **`CreateSessionRequest.scenario_name`** (`app.py:55, 181`) →
  removed entirely. Templates only come into existence via
  `POST /templates/{name}/run`; the session-create path no longer
  touches the template/scenario axis. No `template_id` slot is added
  to the session-create model.
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
  declared in `lane.yaml`. The verifier never auto-commits — the
  existing `_on_runner_done` check (`registry.py:489-495`) only spawns
  the commit op when `finished_node.kind is NodeKind.AGENT`, so
  `kind=VERIFIER` is excluded by accident-already and needs no extra
  guard.
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
     `resume-fix-after-reject`): full multi-node lanes;
     `resume-fix-after-reject` uses `scheduled_deps` plus the new
     `resume_from` field on the `fix` virtual to inherit `build`'s
     provider session at promote time. ~30 min each.
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
- **Verifier writing to the graph.** A verifier never proposes
  virtuals. It writes one framework-authored executed preview per
  run and that is all. Its prompt cannot be edited mid-run (there is
  no prompt — it's a script path). Failure surfaces via `state=ERROR`
  on the verifier tile; the downstream review node is where any
  follow-up planning happens.
- **Recovering `scenario_step_history` as a frontend audit.** The
  graph IS the audit. The canvas shows what happened.
- **Live mid-script verifier output streaming.** v1 captures the
  subprocess's stdout/stderr after exit, the same as `verify.sh` does
  today. Streaming chunks through to the canvas is straightforward
  but not load-bearing.
- **A template-level pre-flight check.** Validation happens at
  instantiation; `lane.yaml` parse errors fail the
  `POST /templates/{name}/run` call before any side effects land.


## 9. Resolved questions

Tradeoffs called out across drafts of this proposal, settled here:

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
- **Q4 (verifier failure surfaces):** the verifier terminates in
  `state=ERROR` on non-zero exit, with the last 2 KB of stderr in
  `node.error` and the exit code + stderr tail in its preview. No
  framework-authored follow-up virtual is written. The downstream
  human-interact review node is the canonical place where verdicts
  about the failure become graph mutations. The state distinction
  (ERROR vs DONE) gives the tile its red render and halts
  auto-promotion at `_on_runner_done`'s DONE-only gate
  (`registry.py:501-502`) — the user has to acknowledge the failure
  before the lane continues. Settled with the user after rejecting
  an earlier sketch that auto-wrote a planning virtual.
- **Q5 (`resume_from` carrier — overload `parent_node_id` or new
  field?):** new field. `Node.resume_from_node_id: str | None` is
  added (optional; set by template instantiation when a `lane.yaml`
  step declares `resume_from`). `parent_node_id` stays semantically
  clean as the review-source pointer (`registry.py:957` already
  overloads it for that). `promote_virtual` reads
  `resume_from_node_id` at promote time and copies the parent's
  provider session before flipping `state=QUEUED`. Confirmed by the
  user.
- **Q6 (verifier subtype — new `PROGRAMMATIC_REVIEW` or relax
  invariant?):** new subtype. Adds `ReviewSubtype.PROGRAMMATIC_REVIEW`.
  The `Node._check_invariants` validator gains a `VERIFIER` branch
  rather than relaxing the "review requires subtype" rule. The
  frontend gets a clean branch point (no human form, no reviewer
  agent — just a script-result panel). Confirmed by the user.
- **Q7 (`CreateSessionRequest.scenario_name` — drop or rename to
  `template_id`?):** drop. Templates only enter the system via
  `POST /templates/{name}/run`. The session-create path no longer
  touches the template axis. Confirmed by the user.


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
- **Verifier failure surfaces as `state=ERROR`, not as a follow-up
  virtual.** The state distinction does double duty: the tile
  renders red automatically via the existing `state-error` tone, and
  `_on_runner_done`'s existing DONE-only gate
  (`registry.py:501-502`) halts auto-promotion. The downstream review
  is the sole locus for "what next" — it sees the verifier's preview
  as upstream context and decides. Keeping the verifier verdict-
  carrying without giving it write-rights to the graph means only
  one node kind (review) proposes virtuals. Earlier drafts had the
  verifier auto-write a planning virtual on failure; the user
  rejected that path as parallel-failure-vocabulary that the
  downstream review already covers.
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

1. **Domain types.** Add `NodeKind.VERIFIER` and
   `ReviewSubtype.PROGRAMMATIC_REVIEW`. Add two `Node` fields:
   `resume_from_node_id: str | None` (optional; carries the
   `lane.yaml` `resume_from` semantic) and
   `verify_script_ref: str | None` (optional; required when
   `kind=verifier`). Extend `Node._check_invariants` with a
   `VERIFIER` branch: `brief` required, `subtype` must be
   `programmatic_review`, `category` is `review`, `prompt` and
   `prompt_draft` must be empty, `verify_script_ref` must be set.
   Rename `Project.scenario_name → template_id`. Drop
   `Project.scenario_step_history` and `Node.scenario_step_id`. Drop
   `CreateSessionRequest.scenario_name` entirely — no `template_id`
   slot is added to the session-create path.
2. **Templates module.** Create `backend/miniclaw2/templates/` with
   `loader.py` (parses `template.yaml` + `lane.yaml`),
   `launcher.py` (project create + seed + lane instantiation),
   `bundled/` (the rewritten scenarios). The legacy
   `backend/miniclaw2/scenarios/` package stays in place during this
   step.
3. **Verifier runner.** A new branch in `NodeRunner` switched on
   `NodeKind.VERIFIER`: subprocess the script with the existing
   verify.sh env (`CI=1`, `MINICLAW_HOME`, `MINICLAW_PROJECT_ID`,
   `cwd = project.root_path`, 60 s timeout); capture stdout/stderr;
   write a framework-authored `preview.json` directly to the store
   (bypassing the reap pipeline, the same way `_run_op` does today).
   Verdict mapping:
   - Exit 0 → `state=DONE`, preview `summary="verify passed"`.
   - Non-zero exit / timeout → `state=ERROR`, `node.error = <last
     2 KB of stderr>`, preview `summary="verify failed: exit <N>"`
     with the stderr tail in the preview body.
   - Cancellation → `state=CANCELLED`, stub preview.

   Verifier `interrupt()` terminates the subprocess (`SIGTERM` then
   `SIGKILL` after a 2 s grace), distinct from agent `interrupt()`
   which calls `provider.interrupt()`.
4. **Promote-virtual + auto-promotion changes.** Extend
   `promote_virtual` (`registry.py:617-668`) to read
   `Node.resume_from_node_id` and copy the parent's
   `provider` / `provider_session_id` / `sdk_session_id` onto the
   virtual before flipping `state=QUEUED`. Refuse to promote if the
   resume parent did not terminate in `DONE` with a live session;
   the lane halts and the user must inspect.
   Verify `_auto_promote_next_virtual` correctly handles a
   verifier-then-review chain (the verifier is just a terminal-state
   node from the scheduler's POV, same as agents); confirm that a
   verifier in `state=ERROR` correctly halts auto-promotion at the
   existing DONE-only gate (this is current behavior — no code
   change, but pin a test).
5. **REST surface.** Add `/templates` + `/templates/{name}/run`.
   Remove `/sessions/{sid}/verify` and `/scenarios/*`.
6. **Frontend pass.** Render verifier nodes through the existing
   agent/review tile and panel branches, with a read-only
   programmatic-review body for verifier virtuals. Switch `TestsPanel`
   to `/templates`. Drop `VerifyCard.tsx`, `ScenarioFutureNode.tsx`, and the
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
   `scenario_step_id` argument on `start_node`.
