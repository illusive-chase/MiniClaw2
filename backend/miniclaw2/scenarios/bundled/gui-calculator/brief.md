# gui-calculator

Flagship visual demo for the multi-step scenario engine, the auto-commit
op, and the passive review gate flow.

## What it exercises

- Multi-step scenarios: `build` (agent) → `review` (passive gate).
- Auto-commit op: rewrites the build node's `commit_after` to a real
  two-commit diff, then advances the cursor to the review step.
- Passive gate flow: the gate runs no agent. Its brief is whatever the
  build agent wrote to `.miniclaw2/outputs/<build-id>/brief.md`. The
  human reads that brief and responds via write-json.
- A real GUI artifact: `calculator.py` is a tkinter window that
  the human exercises by hand.

## Why no programmatic GUI smoke

Driving Tk button events headlessly is flaky across machines. `verify.sh`
only confirms `calculator.py` imports, the auto-commit op rewrote the
build node's hash, and the reviewer wrote `reviews/build.json`. The
actual UX (does `1 + 2 =` yield `3`? does `9 / 0` show an error
indicator?) lives in `acceptance.md` for the human to ratify.
