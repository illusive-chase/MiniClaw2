Three-step scenario exercising the resume edge through a passive review
gate. The build agent writes a deliberately incomplete `mathutils.py`
(only `add`) and a review brief. The reviewer submits a free-form review
asking for `subtract`; the expander then launches a `fix` agent that
resumes the build's provider session (inheriting its conversation) and
adds the requested function. Auto-commit is on, so the timeline shows
interleaved op nodes and the final history has at least three commits
beyond the seed.
