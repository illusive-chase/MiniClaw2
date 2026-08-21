#!/usr/bin/env bash
# verify.sh runs in the template's project root (a temporary git workspace).
# Programmatic floor for hello-text: locate the assistant's text in the
# project's most recent node and assert it ends with the [OK] marker.
#
# Per TEST.md, the verifier MUST NOT touch the internal protocol; it
# treats events.jsonl as an opaque artifact only as a fallback if no
# observable end-state is available. hello-text writes no files, so the
# transcript is the artifact.
set -euo pipefail

if [[ -z "${MINICLAW_HOME:-}" ]]; then
  MINICLAW_HOME="$HOME/.miniclaw2"
fi

# We don't know the project id from the workspace alone; the wrapping
# Verify endpoint sets MINICLAW_PROJECT_ID before invoking us.
if [[ -z "${MINICLAW_PROJECT_ID:-}" ]]; then
  echo "MINICLAW_PROJECT_ID not set; cannot locate node transcript" >&2
  exit 2
fi

project_dir="$MINICLAW_HOME/projects/$MINICLAW_PROJECT_ID"
if [[ ! -d "$project_dir/hosts" ]]; then
  echo "no node storage under: $project_dir" >&2
  exit 3
fi

# Concatenate every text_delta across every node — there is only one
# in hello-text, but glob keeps the script symmetrical with other templates.
transcript=$(
  for f in "$project_dir"/hosts/*/nodes/*/events.jsonl; do
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
  exit 4
fi

if [[ "$transcript" != *"[OK]"* ]]; then
  echo "transcript missing [OK] marker" >&2
  echo "--- transcript ---" >&2
  printf '%s\n' "$transcript" >&2
  exit 5
fi

echo "ok"
