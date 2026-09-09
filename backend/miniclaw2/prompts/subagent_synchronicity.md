# The Agent tool is asynchronous here — always

Inside a MiniClaw2 node the `Agent` tool **always** dispatches in the
background. This is not a mode you select and can therefore avoid: the
launch call returns an agent id immediately, the work product arrives
later as a notification, and that happens whether or not you pass
`run_in_background`. There is no synchronous form of this tool to reach
for, so do not go looking for one.

Two consequences, and the second is the serious one.

**Results you never receive.** A node's turn ends when you stop calling
tools; MiniClaw2 reaps the node at that point and the child processes go
with it. A dispatch whose contract is "results arrive later" cannot
complete inside the turn that made it — the notification window closes
before anything can land in it. Retrying produces the same silence,
because this is a property of the execution model, not a transient
failure.

**Turn semantics you break.** A background agent's completion
notification can enqueue while you are suspended in
`AskUserQuestion`. The notification and the user's answer then land as
two different branches of the same conversation, and the CLI may follow
the notification branch — whose input is a bare attachment. It responds
there locally, without calling the model at all, and that counts as a
legitimate end of turn. Every subsequent attempt inherits the same dead
branch. A node can burn its entire budget this way and produce nothing,
with no error that names the cause. This is why the constraint is
enforced by hooks and not left to your judgement:

- `AskUserQuestion` is **denied** while any subagent you dispatched is
  still running.
- The `Stop` hook **refuses to end your turn** while any subagent is
  still running, and re-prompts you to wait.

So, concretely:

- **Do not** dispatch subagents for work this node has to deliver.
  Investigate directly with `Read`, `Grep`, `Glob`, and `Bash` — your own
  tool calls always land inside the turn.
- **If you have already dispatched one**, wait for its completion
  notification before you finish or ask a question. Do not re-dispatch,
  and do not try to end the turn early; the `Stop` hook will send you
  back.
- **If a subagent will never return**, end it with `TaskStop` and say in
  your preview what went uncovered. That is the way out — not another
  dispatch.
