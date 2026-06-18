#!/usr/bin/env bash
# plan-mode-approval programmatic floor:
#   - at least one event is interaction_request with interaction_type=="plan_approval"
#   - PLAN_OK.txt exists in the workspace root with content "plan-approved\n"
#   - no other untracked or modified files
set -euo pipefail

if [[ -z "${MINICLAW_HOME:-}" ]]; then
  MINICLAW_HOME="$HOME/.miniclaw2"
fi
if [[ -z "${MINICLAW_PROJECT_ID:-}" ]]; then
  echo "MINICLAW_PROJECT_ID not set" >&2
  exit 2
fi

project_dir="$MINICLAW_HOME/projects/$MINICLAW_PROJECT_ID"
if [[ ! -d "$project_dir/nodes" ]]; then
  echo "no nodes directory: $project_dir/nodes" >&2
  exit 3
fi

if [[ ! -f PLAN_OK.txt ]]; then
  echo "PLAN_OK.txt not found in workspace root" >&2
  exit 4
fi
expected=$'plan-approved'
actual=$(cat PLAN_OK.txt)
if [[ "$actual" != "$expected" ]]; then
  echo "PLAN_OK.txt content mismatch" >&2
  echo "--- expected ---" >&2
  printf '%s\n' "$expected" >&2
  echo "--- actual ---" >&2
  printf '%s\n' "$actual" >&2
  exit 5
fi

extra=$(git status --porcelain | awk '{print $2}' | grep -v '^PLAN_OK\.txt$' || true)
if [[ -n "$extra" ]]; then
  echo "unexpected files in workspace:" >&2
  printf '%s\n' "$extra" >&2
  exit 6
fi

python3 - "$project_dir" <<'PY'
import json, os, sys
project_dir = sys.argv[1]
saw_plan = False
for root, _, files in os.walk(os.path.join(project_dir, "nodes")):
    if "events.jsonl" not in files:
        continue
    with open(os.path.join(root, "events.jsonl"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ev = rec.get("event") or {}
            if ev.get("type") == "interaction_request" and ev.get("interaction_type") == "plan_approval":
                saw_plan = True
if not saw_plan:
    print("no interaction_request with interaction_type=plan_approval found", file=sys.stderr)
    sys.exit(7)
print("ok")
PY
