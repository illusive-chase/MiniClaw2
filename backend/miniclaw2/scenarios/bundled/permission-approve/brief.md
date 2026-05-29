Tool use triggers a default-deny permission gate; the user approves
once; the tool then runs and the agent reports the output. Validates
the inline permission flow (`interaction_request` with
`interaction_type=permission`), the gate tab UX, and that an approval
unblocks the runner instead of cancelling the node.
