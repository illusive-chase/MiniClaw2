#!/usr/bin/env bash
# verify.sh runs in the scenario's project root (a temporary git workspace).
# Programmatic floor for context-md-respected:
#   (1) the assistant text contains the [CTX-OK] marker (proves CONTEXT.md
#       reached the provider and influenced output);
#   (2) the node's system_context_snapshot equals the seeded CONTEXT.md
#       byte-for-byte (proves the loader path captured the right text).
set -euo pipefail

if [[ -z "${MINICLAW_HOME:-}" ]]; then
  MINICLAW_HOME="$HOME/.miniclaw2"
fi

if [[ -z "${MINICLAW_PROJECT_ID:-}" ]]; then
  echo "MINICLAW_PROJECT_ID not set; cannot locate node transcript" >&2
  exit 2
fi

project_dir="$MINICLAW_HOME/projects/$MINICLAW_PROJECT_ID"
if [[ ! -d "$project_dir/nodes" ]]; then
  echo "no nodes directory: $project_dir/nodes" >&2
  exit 3
fi

if [[ ! -f "CONTEXT.md" ]]; then
  echo "CONTEXT.md missing from workspace root" >&2
  exit 4
fi

# (1) transcript marker check — concatenate text_delta across all nodes
transcript=$(
  for f in "$project_dir"/nodes/*/events.jsonl; do
    [[ -f "$f" ]] || continue
    python3 -c '
import json, sys
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    rec = json.loads(line)
    ev = rec.get("event") or {}
    if ev.get("type") == "text_delta":
        sys.stdout.write(ev.get("text", ""))
' "$f"
  done
)

if [[ -z "$transcript" ]]; then
  echo "transcript is empty — no text_delta events found" >&2
  exit 5
fi

if [[ "$transcript" != *"[CTX-OK]"* ]]; then
  echo "transcript missing [CTX-OK] marker — CONTEXT.md was not honored" >&2
  echo "--- transcript ---" >&2
  printf '%s\n' "$transcript" >&2
  exit 6
fi

# (2) snapshot equality — read system_context_snapshot from every node
# and assert at least one matches the seeded CONTEXT.md byte-for-byte.
python3 - "$project_dir" "CONTEXT.md" <<'PY'
import json, pathlib, sys

project_dir = pathlib.Path(sys.argv[1])
expected = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")

found = False
for node_json in sorted((project_dir / "nodes").glob("*/node.json")):
    try:
        data = json.loads(node_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    snap = data.get("system_context_snapshot", "")
    if snap == expected:
        found = True
        break

if not found:
    print("no node carried a system_context_snapshot matching CONTEXT.md", file=sys.stderr)
    sys.exit(7)
PY

echo "ok"
