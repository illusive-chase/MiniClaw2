#!/usr/bin/env bash
# verify.sh runs in the scenario's project root (a temporary git workspace).
# Programmatic floor for reconnect-replay:
#   (1) the single declared node reached state=done;
#   (2) its events.jsonl carries strictly increasing seq numbers with no
#       gaps (seq 1..N) — replay's correctness depends on the JSONL
#       being the canonical, contiguous source;
#   (3) the assembled transcript contains the literal token `[END]`
#       (proves the agent ran past the simulated drop to completion).
#
# Note: client-side duplicate/gap behavior is verified by the human
# acceptance step; we cannot observe what the browser displayed from a
# shell script.
set -euo pipefail

if [[ -z "${MINICLAW_HOME:-}" ]]; then
  MINICLAW_HOME="$HOME/.miniclaw2"
fi

if [[ -z "${MINICLAW_PROJECT_ID:-}" ]]; then
  echo "MINICLAW_PROJECT_ID not set; cannot locate project state" >&2
  exit 2
fi

project_dir="$MINICLAW_HOME/projects/$MINICLAW_PROJECT_ID"
if [[ ! -d "$project_dir/nodes" ]]; then
  echo "no nodes directory: $project_dir/nodes" >&2
  exit 3
fi

python3 - "$project_dir" <<'PY'
import json, pathlib, sys

project_dir = pathlib.Path(sys.argv[1])
node_dirs = sorted((project_dir / "nodes").iterdir())
if not node_dirs:
    print("no node directories found", file=sys.stderr)
    sys.exit(10)

# We declare a single node; if extras showed up (e.g. spurious op),
# this still validates the most recent agent node.
agent_node = None
for nd in node_dirs:
    nj = nd / "node.json"
    if not nj.exists():
        continue
    data = json.loads(nj.read_text(encoding="utf-8"))
    if data.get("kind") == "agent":
        agent_node = data
        agent_dir = nd

if agent_node is None:
    print("no agent node found", file=sys.stderr)
    sys.exit(11)

if agent_node.get("state") != "done":
    print(f"agent node state != done: {agent_node.get('state')!r}", file=sys.stderr)
    sys.exit(12)

events_path = agent_dir / "events.jsonl"
if not events_path.exists():
    print(f"events.jsonl missing for agent node {agent_node.get('id')!r}", file=sys.stderr)
    sys.exit(13)

seqs = []
transcript_parts = []
for line in events_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line:
        continue
    rec = json.loads(line)
    seq = rec.get("seq")
    if not isinstance(seq, int):
        print(f"event missing integer seq: {rec!r}", file=sys.stderr)
        sys.exit(14)
    seqs.append(seq)
    ev = rec.get("event") or {}
    if ev.get("type") == "text_delta":
        transcript_parts.append(ev.get("text", ""))

if not seqs:
    print("events.jsonl is empty", file=sys.stderr)
    sys.exit(15)

# Strictly increasing, no duplicates, no gaps (start may be 1 or 0
# depending on backend numbering; require monotonic +1 from first).
expected = list(range(seqs[0], seqs[0] + len(seqs)))
if seqs != expected:
    # Surface the first divergence for debugging.
    for i, (got, want) in enumerate(zip(seqs, expected)):
        if got != want:
            print(
                f"seq gap or duplicate at index {i}: expected {want}, got {got}",
                file=sys.stderr,
            )
            break
    else:
        print(
            f"seq list length mismatch: got {len(seqs)} entries",
            file=sys.stderr,
        )
    sys.exit(16)

transcript = "".join(transcript_parts)
if "[END]" not in transcript:
    print("transcript missing [END] marker — agent did not finish", file=sys.stderr)
    sys.exit(17)
PY

echo "ok"
