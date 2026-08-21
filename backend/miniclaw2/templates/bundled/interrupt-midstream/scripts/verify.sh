#!/usr/bin/env bash
# interrupt-midstream programmatic floor:
#   - the interrupted turn1 node's node.json has state == "cancelled"
#   - events.jsonl has at least one text_delta or activity event (proves
#     we got partway before the interrupt — the regression we care about
#     is "cancel wiped the in-flight buffer")
#   - events.jsonl has NO turn_done event with state=="done"
set -euo pipefail

if [[ -z "${MINICLAW_HOME:-}" ]]; then
  MINICLAW_HOME="$HOME/.miniclaw2"
fi
if [[ -z "${MINICLAW_PROJECT_ID:-}" ]]; then
  echo "MINICLAW_PROJECT_ID not set" >&2
  exit 2
fi

project_dir="$MINICLAW_HOME/projects/$MINICLAW_PROJECT_ID"
if [[ ! -d "$project_dir/hosts" ]]; then
  echo "no node storage under: $project_dir" >&2
  exit 3
fi

python3 - "$project_dir" <<'PY'
import glob, json, os, sys
project_dir = sys.argv[1]

# Templates pre-create all lane entries as virtual nodes, so the newest
# node is usually a later verify/accept virtual. Select the template's
# first regular agent node instead.
candidates = []
node_files = glob.glob(os.path.join(project_dir, "hosts", "*", "nodes", "*", "node.json"))
for nf in node_files:
    if not os.path.isfile(nf):
        continue
    try:
        with open(nf, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        continue
    if data.get("proposed_by") != "template:interrupt-midstream":
        continue
    if data.get("kind") != "agent":
        continue
    if data.get("category") != "regular":
        continue
    if data.get("scheduled_deps") not in (None, []):
        continue
    candidates.append((data.get("created_at") or 0, nf, data))
if not candidates:
    print("could not find interrupt-midstream turn1 node on disk", file=sys.stderr)
    sys.exit(4)
candidates.sort(key=lambda x: x[0])
_, node_file, node = candidates[0]
nid = node["id"]

if node.get("state") != "cancelled":
    print(f"turn1 node {nid} state is {node.get('state')!r}, expected 'cancelled'", file=sys.stderr)
    sys.exit(5)

events_path = os.path.join(os.path.dirname(node_file), "events.jsonl")
saw_partial = False
saw_done = False
if os.path.isfile(events_path):
    with open(events_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ev = rec.get("event") or {}
            t = ev.get("type")
            if t in ("text_delta", "activity"):
                saw_partial = True
            if t == "turn_done" and ev.get("state") == "done":
                saw_done = True

if saw_done:
    print("events.jsonl has a turn_done with state=done — node was not actually interrupted", file=sys.stderr)
    sys.exit(6)
if not saw_partial:
    print("events.jsonl has no text_delta or activity — partial output was wiped or never arrived", file=sys.stderr)
    sys.exit(7)
print("ok")
PY
