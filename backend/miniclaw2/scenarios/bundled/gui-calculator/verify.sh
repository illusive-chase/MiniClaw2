#!/usr/bin/env bash
# gui-calculator programmatic floor:
#   - calculator.py exists at the project root
#   - importing the module does not error (window does not open)
#   - git log shows at least 2 commits (auto-commit op fired at least once)
#   - the build node's commit_after differs from commit_before (proves
#     the auto-commit op rewrote the agent's commit_after)
#   - reviews/build.json exists and parses as JSON
#
# GUI behavior (1+2=3, 9/0 error indicator, C clear, window close)
# is human-verified in acceptance.md.
set -euo pipefail

if [[ -z "${MINICLAW_HOME:-}" ]]; then
  MINICLAW_HOME="$HOME/.miniclaw2"
fi
if [[ -z "${MINICLAW_PROJECT_ID:-}" ]]; then
  echo "MINICLAW_PROJECT_ID not set" >&2
  exit 2
fi

if [[ ! -f calculator.py ]]; then
  echo "calculator.py missing at project root" >&2
  exit 3
fi

# Import-only check. Force a non-interactive backend so any accidental
# top-level Tk() would fail loudly rather than hang.
if ! python3 -c "import sys; sys.path.insert(0, '.'); import calculator" >/tmp/miniclaw-gui-import.log 2>&1; then
  echo "calculator.py failed to import:" >&2
  cat /tmp/miniclaw-gui-import.log >&2
  exit 4
fi

if [[ ! -f reviews/build.json ]]; then
  echo "reviews/build.json missing — was the gate resolved with write-json?" >&2
  exit 5
fi
if ! python3 -c "import json,sys; json.load(open('reviews/build.json'))" 2>/dev/null; then
  echo "reviews/build.json is not valid JSON" >&2
  exit 6
fi

commit_count=$(git rev-list --count HEAD 2>/dev/null || echo 0)
if (( commit_count < 2 )); then
  echo "expected at least 2 commits (initial + auto-commit); got $commit_count" >&2
  exit 7
fi

project_dir="$MINICLAW_HOME/projects/$MINICLAW_PROJECT_ID"
if [[ ! -d "$project_dir/nodes" ]]; then
  echo "no nodes directory: $project_dir/nodes" >&2
  exit 8
fi

python3 - "$project_dir" <<'PY'
import json, os, sys
project_dir = sys.argv[1]
build_node = None
for entry in os.listdir(os.path.join(project_dir, "nodes")):
    nf = os.path.join(project_dir, "nodes", entry, "node.json")
    if not os.path.isfile(nf):
        continue
    try:
        with open(nf, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        continue
    if data.get("scenario_step_id") == "build" and data.get("kind") == "agent":
        build_node = data
        break
if build_node is None:
    print("could not find build node in nodes/", file=sys.stderr)
    sys.exit(9)
before = build_node.get("commit_before")
after = build_node.get("commit_after")
if not before or not after:
    print(f"build node missing commit hashes: before={before!r} after={after!r}", file=sys.stderr)
    sys.exit(10)
if before == after:
    print("build node's commit_after was NOT rewritten by the auto-commit op", file=sys.stderr)
    sys.exit(11)
print("ok")
PY
