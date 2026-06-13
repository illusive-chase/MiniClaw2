# Anti-self-poisoning filter

Every preview you write — your own, and any virtuals you propose —
becomes part of the durable record that future agents will read on
every subsequent launch in this lane. Treat them as load-bearing
text, not session narration.

Do **not** commit the following as durable findings:

- **Transient errors.** "The tool returned a 500." "Permission was
  denied on this single call." "The Read tool timed out once."
  These are facts about one session, not facts about the project.
- **Negative tool claims.** "The reviewer cannot evaluate this."
  "The API does not work." "The test is impossible to run." If the
  cause is transient, these become load-bearing for every future
  agent and silently redirect future runs away from approaches that
  in fact work.
- **Single-run environment quirks.** "The test took 90 seconds
  here." "The build needed a manual `pnpm install` first." Worth
  recording if you have evidence the quirk is reproducible.
  Otherwise it is noise.
- **Workarounds for transient failures.** Do not propose virtual
  nodes whose motivation is to plan around a flake. Fix the flake
  or surface it as an open question; do not bake it into the plan.

What **may** be written:

- **Stable findings about the project.** "The signup endpoint hits
  Stripe at `/v2/customers`; the legacy `/v1` path is unused."
- **Decisions made.** "We are using JWT, not session cookies, for
  this auth flow." Include the reasoning if it is non-obvious.
- **Open questions discovered.** "The token TTL is unspecified;
  defaulted to 24h pending product input."
- **Things explicitly ruled out of scope.** "Mobile clients out of
  scope for this direction."

If you find yourself wanting to record a transient observation, ask
whether the next agent reading this lane in a week would still need
it. If not, leave it out.
