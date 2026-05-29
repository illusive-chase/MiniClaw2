Project in `plan` permission mode. The agent first proposes a plan, a
`plan_approval` interaction request fires, the user approves, and the
agent then performs the write. Validates plan-mode happy path on both
providers and that approval transitions the runner cleanly into write
mode.
