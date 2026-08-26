# Subagents must return within this turn

If you delegate to subagents, collect their results **before you hand
control back**. A node's turn ends when you stop calling tools; MiniClaw2
reaps the node at that point and the child processes go with it.

So a dispatch whose contract is "results arrive later, as a notification"
never completes here. The launch call succeeds, the agent id comes back,
and the notification window closes before anything can land in it — the
tokens are spent for nothing. This is a property of the execution model,
not a transient failure: retrying the same dispatch produces the same
silence.

What this rules out and what it leaves:

- **Do not** dispatch a subagent in a mode that reports back after the
  turn — background, async, fire-and-forget. If a dispatch returns
  something like "launched successfully" instead of the work product,
  that is the mode you must not rely on.
- **Do** use subagents whose results you receive inside this turn, and
  wait for them. Parallel fan-out is fine; unresolved fan-out is not.
- **Do** fall back to investigating directly when a synchronous form
  isn't available. Your own tool calls always land within the turn.

If you have already dispatched subagents that cannot report back in this
turn, do not spend another cycle re-dispatching them the same way. Redo
the work synchronously, or narrow the scope and say in your preview what
went uncovered.
