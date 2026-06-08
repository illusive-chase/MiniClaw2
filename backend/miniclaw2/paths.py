"""Project-relative path validation shared by the runner and scenarios."""

from __future__ import annotations

from pathlib import Path


def validate_project_relative_path(path_str: str | None) -> str | None:
    """Return an error string when ``path_str`` is not safely project-relative."""
    if path_str is None or not path_str:
        return None
    path = Path(path_str)
    if path.is_absolute():
        return "path must be project-relative"
    if any(part == ".." for part in path.parts):
        return "path must not contain '..'"
    return None
