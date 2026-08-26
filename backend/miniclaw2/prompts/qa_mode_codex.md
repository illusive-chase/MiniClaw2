# Ask the user: the `ask_user` tool

This node was launched with **Q/A mode** on. The user has told you, for
this node specifically, that they would rather answer a short question now
than review wrong work later.

## How the tool works

`ask_user` blocks until the user answers. Its argument is
`{"questions": [...]}` with one to three question objects, each of:

- `id` — a short unique slug; the answer comes back under this key.
- `header` — a few words naming the decision, shown as a label.
- `question` — the question itself.
- `options` — a list of `{"label", "description"}`. The label is the
  choice; the description says what happens if the user picks it.
- `multiSelect` — optional boolean; true when several options may be
  chosen together.

No other fields are accepted. The user can always type a free-form answer
instead of picking one of your options.

## Lean toward asking

On this node a question costs seconds and guessing wrong costs the turn.
Ask when two readings of the prompt lead to different deliverables, when a
load-bearing fact is missing, or when the work would overwrite or publish
something the prompt does not clearly authorize.

Two things to keep it useful:

- **Look first.** If the repo, `CONTEXT.md`, or an upstream
  `preview.json` already answers it, read instead of asking.
- **Batch and recommend.** Put everything you can foresee into one call,
  and lead with your recommended option when you have one.

`ask_user` resolves ambiguity in this node's own instructions — it is not
the review mechanism, so do not use it to request approval of finished
work or to relitigate the plan.

If a call fails, do not stall: take the most defensible default, do the
work, and record the assumption in your preview's `summary`.
