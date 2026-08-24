# Long-running processes on a shared host

You are one of several agents on a machine the human is also using. In
particular, the human is very often running MiniClaw2 itself here — a
backend on `:8000` and a Vite dev server on `:5173` — and that dev server
is how they are watching your work land. Killing it takes the UI down:
first every request fails, then the page stops loading entirely. From
their side nothing explains why, because the backend is still up.

So process cleanup is not a local decision. A pattern that reads as
"stop my dev server" will match theirs too.

## Never match a process by name alone

Do not run these, or anything shaped like them:

- `pkill -f vite`, `pkill -f node`, `pkill -f npm`, `killall node`
- `pkill -f uvicorn`, `pkill -f miniclaw2`, `pkill -f python`
- `kill $(lsof -ti:5173)`, `kill $(lsof -ti:8000)`

`pkill -f` matches every process whose full command line contains the
pattern, across the whole user session. `pkill -f vite` does not mean
"the vite I started" — it means every vite on the host, the human's
included. The same goes for the default MiniClaw2 ports: `5173` and
`8000` belong to the human's session, never to yours.

## Own what you start, and nothing else

- **Pick a port nobody else would.** Never bind `5173`, `8000`, or
  `5174`/`8001` — those collide with the human and with sibling agents.
  Choose something distinctive and high, and use a distinct one per
  service you start.
- **Kill by PID, not by pattern.** Capture the pid when you spawn
  (`npm run dev -- --port 5931 & echo $!`) and terminate that pid.
- **If you must match, match on your own distinctive port**, never the
  bare program name: `pkill -f "vite --port 5931"`.
- **Never kill a process you did not start.** If a port you wanted is
  taken, pick another one. An occupied port is somebody else's live
  work, not a stale leftover to clear — you cannot tell the difference
  from the outside, and guessing wrong destroys their session.
- **Prefer not starting a server at all.** For type and build checks,
  `npx tsc --noEmit` and `npm run build` need no dev server and no port.
  Reach for a dev server only when the turn genuinely requires a live
  page.

Clean up whatever you spawned before the turn ends — by pid. Leaving a
stray server holding a port breaks the next agent as surely as killing
one breaks the human.
