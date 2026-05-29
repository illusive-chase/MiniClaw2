Exercises the WebSocket reconnect + replay path. The agent generates a
long, line-formatted list while the human watches; clicking "Simulate
WS drop" in the project header closes the live socket with a normal
code, triggering the ws.ts reconnect loop that re-issues a
`replay_request` with `(node_id, last_seq)`. The backend replays from
the per-node `events.jsonl` and re-attaches to the live tail, so the
transcript continues without rewinding or duplicating.

Verify checks that the on-disk JSONL has contiguous monotonic seqs
(seq 1..N with no gaps) and that the final transcript contains the
end-of-stream marker `[END]`. The human ratifies that no text appeared
twice or rewound during the simulated drop.
