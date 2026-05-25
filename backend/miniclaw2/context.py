"""Project-level context loader.

Reads ``<project_root>/CONTEXT.md`` (if present) and returns its contents
as a single string, to be appended to the provider's system prompt /
prepended to the first user turn. Provider-neutral by design: the same
text is injected into Claude (via ``system_prompt.append``) and Codex
(via prepended ``turn/start`` input) so both providers see the same
project-level context.
"""

from __future__ import annotations

from pathlib import Path


def load_project_context(root_path: str) -> str:
    path = Path(root_path) / "CONTEXT.md"
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return ""
