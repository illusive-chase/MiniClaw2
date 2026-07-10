#!/usr/bin/env bash
# verify.sh runs in the template's project root (a temporary git workspace).
# Programmatic floor for resume-fix-after-reject:
#   (1) the build/review/fix template nodes all reached `done`
#   (2) the fix node carries parent_node_id == build.id and inherits
#       build's provider session id (proves the resume edge was wired);
#   (3) `mathutils.py` exists and exposes both `add` and at least one
#       other function (the reviewer-requested follow-up);
#   (4) git history shows multiple commits beyond the seed (auto-commit
#       op fired on each agent/gate done).
set -euo pipefail

if [[ -z "${MINICLAW_HOME:-}" ]]; then
  MINICLAW_HOME="$HOME/.miniclaw2"
fi

if [[ -z "${MINICLAW_PROJECT_ID:-}" ]]; then
  echo "MINICLAW_PROJECT_ID not set; cannot locate project state" >&2
  exit 2
fi

project_dir="$MINICLAW_HOME/projects/$MINICLAW_PROJECT_ID"
project_json="$project_dir/project.json"

if [[ ! -f "$project_json" ]]; then
  echo "project.json missing: $project_json" >&2
  exit 3
fi

python3 - "$project_dir" <<'PY'
import json, pathlib, sys

project_dir = pathlib.Path(sys.argv[1])
nodes = []
for node_json in sorted((project_dir / "nodes").glob("*/node.json")):
    try:
        data = json.loads(node_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    if data.get("kind") == "agent" and data.get("proposed_by") == "template:resume-fix-after-reject":
        nodes.append(data)

nodes.sort(key=lambda item: item.get("created_at") or 0)
if len(nodes) < 3:
    print(f"expected at least three template agent nodes, found {len(nodes)}", file=sys.stderr)
    sys.exit(10)

build_node, review_node, fix_node = nodes[:3]
for label, node in (("build", build_node), ("review", review_node), ("fix", fix_node)):
    if node.get("state") != "done":
        print(f"{label} node state != done: {node.get('state')!r}", file=sys.stderr)
        sys.exit(11)

build_node_id = build_node["id"]

if fix_node.get("parent_node_id") != build_node_id:
    print(
        f"fix.parent_node_id ({fix_node.get('parent_node_id')!r}) != build.id ({build_node_id!r})",
        file=sys.stderr,
    )
    sys.exit(13)

# Resume should have inherited at least one of the provider session ids.
build_session = build_node.get("provider_session_id")
fix_session = fix_node.get("provider_session_id")
if build_session and fix_session != build_session:
    print(
        f"fix did not inherit build's provider session: build={build_session!r} fix={fix_session!r}",
        file=sys.stderr,
    )
    sys.exit(14)
PY

# (3) mathutils.py shape
if [[ ! -f "mathutils.py" ]]; then
  echo "mathutils.py missing at project root" >&2
  exit 4
fi

python3 - <<'PY'
import sys
src = open("mathutils.py", encoding="utf-8").read()
if "def add" not in src:
    print("mathutils.py missing `def add`", file=sys.stderr)
    sys.exit(20)
# Count function definitions; we want add + at least one more.
import re
defs = re.findall(r"^\s*def\s+(\w+)\s*\(", src, flags=re.MULTILINE)
if len(set(defs)) < 2:
    print(f"mathutils.py only defines {defs!r}; the fix turn should add a second function", file=sys.stderr)
    sys.exit(21)
PY

# (4) git history — initial + at least two more commits (build + fix at minimum)
count=$(git rev-list --count HEAD 2>/dev/null || echo 0)
if (( count < 3 )); then
  echo "expected >=3 commits, got $count" >&2
  exit 5
fi

echo "ok"
