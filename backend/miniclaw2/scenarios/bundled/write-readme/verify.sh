#!/usr/bin/env bash
# write-readme programmatic floor:
#   - README.md exists in the workspace root with content "# scratch\n"
#   - no other tracked or untracked files were added beyond .git/ + README.md
set -euo pipefail

if [[ ! -f README.md ]]; then
  echo "README.md not found in workspace root" >&2
  exit 2
fi

actual=$(cat README.md)
expected=$'# scratch'
if [[ "$actual" != "$expected" ]]; then
  echo "README.md content mismatch" >&2
  echo "--- expected ---" >&2
  printf '%s\n' "$expected" >&2
  echo "--- actual ---" >&2
  printf '%s\n' "$actual" >&2
  exit 3
fi

# Anything other than README.md showing up in `git status` is a fail.
extra=$(git status --porcelain | awk '{print $2}' | grep -v '^README\.md$' || true)
if [[ -n "$extra" ]]; then
  echo "unexpected files in workspace:" >&2
  printf '%s\n' "$extra" >&2
  exit 4
fi

echo "ok"
