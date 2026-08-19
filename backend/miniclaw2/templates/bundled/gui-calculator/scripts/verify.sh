#!/usr/bin/env bash
# gui-calculator programmatic floor:
#   - calculator.py exists at the project root
#   - requirements.txt declares PySide6
#   - calculator.py references PySide6 and does not import Tk libraries
#   - importing the module does not error (window does not open, and
#     PySide6 does not need to be installed for import-only verification)
#   - git log shows at least 2 commits (auto-commit op fired at least once)
#   - the build node's commit_after differs from commit_before (proves
#     the auto-commit op rewrote the agent's commit_after)
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

if [[ ! -f requirements.txt ]]; then
  echo "requirements.txt missing at project root" >&2
  exit 4
fi

if ! python3 - <<'PY'
from pathlib import Path
import ast
import sys

source = Path("calculator.py").read_text(encoding="utf-8")
try:
    tree = ast.parse(source, filename="calculator.py")
except SyntaxError as exc:
    print(f"calculator.py is not valid Python: {exc}", file=sys.stderr)
    sys.exit(1)

imports = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imports.extend(alias.name.split(".", 1)[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imports.append(node.module.split(".", 1)[0])

blocked = {"tkinter", "customtkinter"}
bad = sorted(name for name in imports if name.lower() in blocked)
if bad:
    print(f"calculator.py must not import Tk libraries: {', '.join(bad)}", file=sys.stderr)
    sys.exit(1)
if "PySide6" not in imports:
    print("calculator.py must import PySide6 for the GUI", file=sys.stderr)
    sys.exit(1)

requirements = Path("requirements.txt").read_text(encoding="utf-8")
entries = []
for raw in requirements.splitlines():
    line = raw.split("#", 1)[0].strip()
    if line:
        entries.append(line.lower())
if not any(line.startswith("pyside6") for line in entries):
    print("requirements.txt must declare PySide6", file=sys.stderr)
    sys.exit(1)
PY
then
  exit 5
fi

# Import-only check. This should not require PySide6 to be installed;
# the generated app should import PySide6 only when the GUI is launched.
if ! python3 -c "import sys; sys.path.insert(0, '.'); import calculator" >/tmp/miniclaw-gui-import.log 2>&1; then
  echo "calculator.py failed to import:" >&2
  cat /tmp/miniclaw-gui-import.log >&2
  exit 6
fi

commit_count=$(git rev-list --count HEAD 2>/dev/null || echo 0)
if (( commit_count < 2 )); then
  echo "expected at least 2 commits (initial + auto-commit); got $commit_count" >&2
  exit 9
fi

project_dir="$MINICLAW_HOME/projects/$MINICLAW_PROJECT_ID"
if [[ ! -d "$project_dir/nodes" && ! -d "$project_dir/hosts" ]]; then
  echo "no node storage under: $project_dir" >&2
  exit 10
fi

python3 - "$project_dir" <<'PY'
import glob, json, os, sys
project_dir = sys.argv[1]
agents = []
node_files = glob.glob(os.path.join(project_dir, "nodes", "*", "node.json"))
node_files += glob.glob(os.path.join(project_dir, "hosts", "*", "nodes", "*", "node.json"))
for nf in node_files:
    if not os.path.isfile(nf):
        continue
    try:
        with open(nf, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        continue
    if data.get("kind") == "agent" and data.get("proposed_by") == "template:gui-calculator":
        agents.append(data)
agents.sort(key=lambda item: item.get("created_at") or 0)
build_node = agents[0] if agents else None
if build_node is None:
    print("could not find template build node", file=sys.stderr)
    sys.exit(11)
before = build_node.get("commit_before")
after = build_node.get("commit_after")
if not before or not after:
    print(f"build node missing commit hashes: before={before!r} after={after!r}", file=sys.stderr)
    sys.exit(12)
if before == after:
    print("build node's commit_after was NOT rewritten by the auto-commit op", file=sys.stderr)
    sys.exit(13)
print("ok")
PY
