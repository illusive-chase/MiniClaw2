# MiniClaw2 testing proposal — demo-driven, investigation-free benchmark

> **Status: proposal.** No implementation has landed yet — this
> document describes the shape we intend to build. Grounded in
> `DESIGN.md §1.1` ("investigation-free interface"): the test harness
> is a *simulated user*, and a simulated user observes effects, never
> internals.

## 1. What we are testing

The benchmark is a **single behavioral axis**: did the demo behave as
its brief promised? There is no separate "graph-machinery" test stack
running alongside it. Internal correctness — state transitions, event
log shape, gate routing, commit-op rewrite, reconnect replay — is
exercised transitively, because a broken internal path produces a
broken external artifact. If a gate mis-routes, the user sees no gate;
if the auto-commit op forgets to rewrite `commit_after`, the per-node
diff comes back empty. Those failures show up as observable defects.

A test scenario is a small, well-defined task ("implement a GUI
calculator") handed to MiniClaw2 as if a human researcher had typed it
into a fresh project. The pass/fail signal is what an observer sees:
the produced program runs, the produced UI looks and behaves right,
the produced gate output exists at the contracted path. The
framework's own state log is never inspected to decide pass/fail.

## 2. Scenario layout — files in the codebase

Each scenario lives as a directory under `backend/tests/scenarios/`:

    backend/tests/scenarios/<name>/
      brief.md            # the task as a user would receive it
      contract.md         # gate contract (when the scenario uses a gate)
      acceptance.md       # human-visible checklist of expected effects
      operator.py         # programmatic "user" that drives gates / chat
      verify.sh           # black-box programmatic check; exit 0 = floor passed

Scenarios are source-controlled, version-able, discoverable by name,
and reviewable as documents. A reader who has never touched the
benchmark runner can read `brief.md` + `acceptance.md` and know what
the scenario claims.

## 3. Workspace isolation — fresh empty tempdir per run

Each scenario run gets a **fresh, empty tempdir** as the project root.
The runner:

1. Creates the tempdir via `tempfile.mkdtemp(prefix="miniclaw2-bench-")`.
2. Runs `git init` and lays down an empty initial commit so commit-op
   nodes produce real two-commit diffs.
3. Copies the scenario's `contract.md` (if present) into the tempdir
   for the agent to reference.
4. Sets `MINICLAW_HOME` to a sibling tempdir so the on-disk store does
   not leak across runs.
5. `POST /sessions {cwd=<tempdir>, provider=<claude|codex>, auto_commit=true}`.
6. On success: deletes both tempdirs. On failure: leaves them and
   prints the paths so the human can poke at the produced artifacts
   and the event log.

No state is shared across scenarios. No state is shared across
providers within a scenario. No reuse of `$MINICLAW_HOME` from prior
runs.

## 4. Verification — visual / interactive primary, programmatic as a floor

This is the load-bearing piece of the principle.

**Programmatic verification (`verify.sh`) is necessary but never
sufficient.** A scenario only passes when the human observer confirms
the visual / interactive acceptance criteria. Anything that could be
"working" by `verify.sh` while *looking* broken to a human is a
failure — and for GUI scenarios that gap is enormous.

Verifier responsibilities:

- `verify.sh` — black-box programmatic floor. Smoke-imports the
  produced module, runs a headless behavior check where possible
  (instantiate the widget class, fire button-press events
  programmatically, assert label text), runs the scenario's own
  unit tests if it produced any. Exit 0 if the smoke passes. Must
  not touch `events.jsonl`, `node.json`, or the WS protocol; treats
  the produced repo as an opaque deliverable.
- `acceptance.md` — the human checklist. Items are observable from
  the outside: "a window opens with buttons 0–9", "clicking `1`,
  `+`, `2`, `=` shows `3`", "closing the window exits cleanly". The
  runner prints this list, launches the produced app (or instructs
  the human to launch it), and reads y/n per item from stdin.

A scenario is **passed** only when:

1. `verify.sh` exits 0, **and**
2. Every item in `acceptance.md` is marked OK by the human.

Either alone is a failure. The programmatic floor catches regressions
that would slip past tired eyes (an emoji-only display that shows the
right digits but lays them out wrong); the interactive check catches
the "the program runs but the UI is ugly / non-functional / wrong"
cases that programmatic checks structurally cannot.

Programmatic-only scenarios are forbidden, even when the artifact has
no GUI: the acceptance step then asks the human to observe terminal
output, file contents, or process behavior. The principle stands
regardless of artifact shape.

## 5. Provider matrix — Claude and Codex, both, every time

Every scenario runs against **both Claude and Codex**. The runner
invokes the scenario once per provider in independent tempdirs, and
reports per-provider:

    gui-calculator
      claude  ✓ programmatic ✓ interactive   (3 nodes, 1 gate, 84s)
      codex   ✓ programmatic ✗ interactive   ("digit buttons unresponsive")

Both providers are run because the orchestration layer (`NodeRunner`,
adapter translation in `providers/claude.py` and `providers/codex.py`,
gate normalization, context injection) is exactly the surface we're
validating end-to-end. Provider divergence is itself a useful signal:
if Claude passes and Codex fails on the same scenario, the failure is
either in the Codex adapter or in the provider's own ability to
deliver the task — and either is information we want surfaced.

The brief, contract, operator, and verifier do not vary by provider.
A scenario is "passing" only when both providers pass.

## 6. First scenario — `gui-calculator`

Chosen because:

- It produces a real artifact (a Tk window) that *only* a human can
  fully validate. A pure-CLI calculator would let programmatic checks
  pretend they were sufficient; a GUI forces the visual / interactive
  axis to exist.
- It has a clean two-commit boundary (build → review → snapshot) so
  the auto-commit op and the checkpoint-gate path are both on the
  critical path of the demo — covered transitively per §1.
- It is small enough that 1 agent + 1 gate + 1 auto-commit op suffice,
  but the produced artifact is rich enough that "looks broken" is
  obvious to the human.

Sketch of the per-file contents (real text lives in the files once
written):

**`brief.md`** — the task as the user receives it:

> Build a Python desktop calculator with a graphical interface using
> only the standard library (`tkinter`). Support `+`, `-`, `*`, `/`,
> a clear button, and an equals button. Keyboard entry of digits and
> operators should also work. Provide a `tests/` directory with at
> least one smoke test that constructs the calculator class and
> programmatically fires a `1 + 2 =` sequence, asserting the
> displayed result. The app must launch via `python calculator.py`
> from the project root.

**`contract.md`** — gate contract for the review node:

> # Expected
> The repo now contains `calculator.py` runnable via
> `python calculator.py`, and `tests/test_calculator.py`. The smoke
> test passes locally.
>
> # Unexpected
> Hardcoded operation results, missing operators, runtime crashes on
> common input (including divide-by-zero), non-stdlib dependencies,
> a non-resizable or invisible window.
>
> # Response protocol
> Write JSON to `reviews/build.json` with shape
> `{"approved": bool, "notes": str}`.

**`operator.py`** — the simulated user:

- On plan-mode `interaction_request`: approve.
- On any other inline gate (permission, ask-user): approve with
  sensible defaults; abort the scenario if the agent asks a question
  the operator cannot answer from the brief.
- On the checkpoint-review request: emit
  `{"decision": "write-json", "response": {"path": "reviews/build.json", "payload": {"approved": true, "notes": "auto-approved by bench operator"}}}`.

**`verify.sh`** — programmatic floor:

- `test -f calculator.py`
- `python -c "import calculator"` exits 0
- `pytest -q tests/test_calculator.py` exits 0
- A small headless harness (`tools/bench_verify_calculator.py`,
  scenario-local) instantiates the calculator class, fires button
  events programmatically for `1 + 2 =`, asserts the display reads
  `3`, and asserts a `9 / 0` sequence produces an error indicator
  rather than a Python traceback.

**`acceptance.md`** — the human checklist:

- [ ] A window opens when running `python calculator.py` from the
      project root.
- [ ] Buttons for `0`–`9`, `+`, `-`, `*`, `/`, `=`, `C` (clear) are
      visibly present and labeled.
- [ ] Clicking `1`, `+`, `2`, `=` shows `3` in the display.
- [ ] Typing digits and operators on the keyboard updates the
      display the same way clicks do.
- [ ] `9 / 0 =` shows an error indicator (not a Python traceback in
      the terminal).
- [ ] `C` clears the display to `0` or empty.
- [ ] Closing the window via the OS close button exits the process
      cleanly (no stack trace in the terminal).

## 7. The runner

A new `python -m miniclaw2.bench <scenario> [--provider {claude,codex,both}]`
entry point. Default provider is `both`.

Per-provider behavior:

1. Build a fresh tempdir per §3. Set `MINICLAW_HOME` to a sibling
   tempdir.
2. Spin up the FastAPI app in-process (TestClient or asgi-lifespan).
3. Import `operator.py` from the scenario directory. Call its
   `kickoff(send)` hook to send the first `user_message`; route
   incoming `interaction_request` envelopes to its `on_gate(request)
   -> response`; route `node_updated` and `turn_done` to its
   `on_node(node)` for the operator's own bookkeeping.
4. When all nodes have reached terminal states and the project is
   idle, run `verify.sh` in the produced repo with a 60s timeout. If
   it exits non-zero, record programmatic failure and skip the
   interactive step.
5. If programmatic passed: print `acceptance.md`, launch the
   produced app (or instruct the human), and read y/n per item from
   stdin. Record interactive pass/fail.
6. Print the per-provider report line as in §5; on any failure,
   preserve the tempdir and print its path along with the path to
   the scenario's `events.jsonl` under `MINICLAW_HOME`.

The runner is the only piece of machinery that touches the WS
protocol. Scenarios stay declarative — the operator script knows
about gate-response shapes (because that is part of the user model)
but not about the internal event log.

## 8. Out of scope for v1

- Recorded provider transcripts (VCR-style replay). Worth adding
  once one scenario passes end-to-end live on both providers;
  encoding recordings before that risks freezing bugs into the
  baseline.
- Headless visual diffing (screenshot baseline + perceptual diff).
  The human is the visual oracle for v1; baselines come once we
  know what "correct" looks like across both providers.
- CI integration. The benchmark requires a display and real provider
  credentials; it is a developer-driven gate, not a CI gate, for now.
- Scenarios beyond `gui-calculator`. The next two (after the format
  has been used in anger): a "fix this failing test suite" scenario
  (exercises a `resume` edge after a rejected gate) and a
  "build a small CLI" scenario (exercises a long agent + multiple
  bash tool invocations).
- Recording real operator scripts derived from human sessions.

## 9. Open questions

To revisit when the first scenario is implemented end-to-end:

- **Operator generality.** Is `operator.py` an importable module with
  declared callbacks, or a subprocess driven by a small RPC? The
  former is simpler; the latter lets the operator outlive a single
  Python interpreter (useful if scenarios start running inside
  containers / VMs / different Python versions). Default: importable
  module.
- **Visual acceptance UX.** Tty checklist read from stdin vs a tiny
  Tk window with checkboxes. Tty first; revisit if scenario count
  grows past ~5.
- **Multi-node demos.** `gui-calculator` is 1 agent + 1 gate +
  auto-commit op. Once the format is comfortable, the next scenario
  exercises a `resume` edge (reject the gate, agent fixes it, gate
  approves on the second pass) so the resume path is observable too.
- **Cross-provider artifact comparison.** Same brief, two providers,
  two produced repos — is there a useful diff to surface? Likely
  yes, but the format will fall out of running a few scenarios first.
