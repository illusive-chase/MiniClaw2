#!/usr/bin/env bash
# bash-uname programmatic floor:
#  - at least one tool activity in events.jsonl has result_kind=="stdout"
#    AND its result contains "Darwin" or "Linux"
#  - the concatenated assistant text mentions "Darwin" or "Linux"
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
text = []
stdout_hits = []
node_roots = glob.glob(os.path.join(project_dir, "hosts", "*", "nodes"))
for node_root in node_roots:
  for root, _, files in os.walk(node_root):
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
            if t == "text_delta":
                text.append(ev.get("text", ""))
            elif t == "activity" and ev.get("result_kind") == "stdout":
                stdout_hits.append(ev.get("result") or "")

joined_text = "".join(text)
if not any(("Darwin" in s) or ("Linux" in s) for s in stdout_hits):
    print("no Bash stdout activity contained Darwin/Linux", file=sys.stderr)
    print("hits:", stdout_hits, file=sys.stderr)
    sys.exit(4)
if ("Darwin" not in joined_text) and ("Linux" not in joined_text):
    print("assistant text did not mention Darwin or Linux", file=sys.stderr)
    print("---transcript---", file=sys.stderr)
    print(joined_text, file=sys.stderr)
    sys.exit(5)
print("ok")
PY
