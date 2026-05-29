#!/usr/bin/env bash
# permission-approve programmatic floor:
#   - at least one event in events.jsonl is interaction_request with
#     interaction_type == "permission"
#   - at least one activity has result_kind=="stdout" whose result contains
#     "hello-from-bash"
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

python3 - "$project_dir" <<'PY'
import json, os, sys
project_dir = sys.argv[1]
saw_permission = False
saw_stdout_hit = False
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
            t = ev.get("type")
            if t == "interaction_request" and ev.get("interaction_type") == "permission":
                saw_permission = True
            elif t == "activity" and ev.get("result_kind") == "stdout":
                if "hello-from-bash" in (ev.get("result") or ""):
                    saw_stdout_hit = True
if not saw_permission:
    print("no interaction_request with interaction_type=permission found", file=sys.stderr)
    sys.exit(4)
if not saw_stdout_hit:
    print("no Bash stdout activity contained 'hello-from-bash'", file=sys.stderr)
    sys.exit(5)
print("ok")
PY
