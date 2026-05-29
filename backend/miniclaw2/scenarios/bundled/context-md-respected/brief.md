Validates that `<project_root>/CONTEXT.md` is loaded at node launch and
injected into both providers. The seeded CONTEXT.md tells the agent to
end every reply with `[CTX-OK]`; a banal arithmetic prompt is used so
the marker is the only interesting signal. Verify also checks the
node's `system_context_snapshot` field matches the seeded file byte-for
-byte so a silent regression in the loader path is caught.
