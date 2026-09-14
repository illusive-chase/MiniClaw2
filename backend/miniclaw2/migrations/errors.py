from __future__ import annotations

from pathlib import Path


class MigrationError(RuntimeError):
    def __init__(self, state: str, detail: str, path: Path | None = None) -> None:
        self.state = state
        self.detail = detail
        self.path = path
        super().__init__(f"{detail}" + (f"：{path}" if path else ""))

    def payload(self) -> dict[str, str | None]:
        return {"state": self.state, "detail": str(self), "path": str(self.path) if self.path else None}
