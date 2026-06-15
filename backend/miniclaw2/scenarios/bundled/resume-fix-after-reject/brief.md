Three-step scenario exercising the resume edge through a human-interact
review agent. The build agent writes a deliberately incomplete
`mathutils.py` (only `add`). The reviewer submits free-form prose
asking for `subtract`; the expander then launches a `fix` agent that
resumes the build's provider session (inheriting its conversation) and
adds the requested function. Auto-commit is on, so the timeline shows
interleaved op nodes and the final history has at least three commits
beyond the seed.
