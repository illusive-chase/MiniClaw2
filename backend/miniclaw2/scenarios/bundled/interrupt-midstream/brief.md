Agent runs a long Bash loop; the human hits Stop midway. The node
must transition to `cancelled` and the partial output already streamed
into `events.jsonl` must remain intact. Validates the interrupt path
end-to-end (button → runner → provider → on-disk state), the
"cancelled" terminal state, and that partial transcripts are not wiped
on cancel.
