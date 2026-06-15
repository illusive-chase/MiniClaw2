# gui-calculator

Flagship visual demo for the multi-step scenario engine, the auto-commit
op, and the human-interact review-agent flow.

## What it exercises

- Multi-step scenarios: `build` (regular agent) → `review`
  (human-interact review agent).
- Auto-commit op: rewrites the build node's `commit_after` to a real
  two-commit diff, then advances the cursor to the review step.
- Review flow: the review node first collects free-form human prose,
  writes it to `human-review.md`, then launches a reviewer agent whose
  preview and graph mutations are the verdict.
- A real GUI artifact: `calculator.py` is a PySide6 / Qt Widgets
  window that the human exercises by hand.

## Why no programmatic GUI smoke

Driving desktop GUI button events headlessly is flaky across machines.
`verify.sh` only confirms `calculator.py` imports without opening a
window, the project declares and references PySide6, the auto-commit op
rewrote the build node's hash, and the review agent resolved. The actual
UX (does `1 + 2 =` yield `3`? does
`9 / 0` show an error indicator?) lives in `acceptance.md` for the
human to ratify.
