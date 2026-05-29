Three-step scenario exercising the resume edge and the `when:`
predicate. The build agent writes a deliberately incomplete
`mathutils.py` (only `add`) and a review brief. The reviewer rejects
with `{approved: false, notes: "..."}`; the expander branches to a
`fix` agent that resumes the build's provider session (inheriting
its conversation) and addresses the notes. Auto-commit is on, so the
timeline shows interleaved op nodes and the final history has at
least three commits beyond the seed.
