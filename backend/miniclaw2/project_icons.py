"""Project icon vocabulary: a glyph key plus a hue key.

Only our own keys are stored, never the icon library's component names, so a
library swap leaves saved data untouched. Adding a glyph is backward
compatible; removing or renaming one needs a data migration. The frontend's
``projectIcons.ts`` holds the render side of the same two lists — two copies on
purpose, as with ``tags.py`` / ``tagPalette.ts``, so Python never has to read
frontend assets.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator


_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

PROJECT_GLYPHS: tuple[str, ...] = (
    # development
    "code", "terminal", "braces", "git-branch", "bug", "package", "boxes",
    "layers", "puzzle", "workflow", "wrench", "hammer", "cpu", "bot",
    "sparkles", "rocket",
    # infrastructure and data
    "database", "server", "cloud", "network", "globe", "shield", "lock",
    "key-round", "hard-drive", "activity", "chart-line", "chart-pie",
    # research and learning
    "brain", "flask", "microscope", "atom", "dna", "sigma", "book-open",
    "graduation-cap", "lightbulb", "target",
    # content and communication
    "file-text", "pen-tool", "palette", "image", "music", "video", "camera",
    "feather", "message-circle", "mail",
    # life and everything else
    "house", "building", "map", "compass", "plane", "shopping-cart", "wallet",
    "gamepad", "trophy", "heart", "star", "sun", "moon", "leaf", "flame",
    "zap", "anchor", "smartphone", "monitor", "calendar", "users",
)

PROJECT_HUES: tuple[str, ...] = (
    "coral", "amber", "sage", "teal", "azure", "indigo", "plum", "clay",
)


class ProjectIcon(BaseModel):
    """A stored icon. Reads accept any well-formed key.

    A record synced from a newer client may name a glyph this build does not
    ship; rejecting it here would make the whole project unloadable. Writes go
    through ``require_known_icon`` instead, and the frontend renders an unknown
    key as the default look.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    glyph: str
    hue: str

    @field_validator("glyph", "hue")
    @classmethod
    def _well_formed(cls, value: str) -> str:
        if not _KEY_RE.match(value):
            raise ValueError(f"malformed project icon key: {value!r}")
        return value


def require_known_icon(icon: ProjectIcon) -> ProjectIcon:
    """Reject keys this build cannot render; every write path calls this."""
    if icon.glyph not in PROJECT_GLYPHS:
        raise ValueError(f"unknown project glyph: {icon.glyph}")
    if icon.hue not in PROJECT_HUES:
        raise ValueError(f"unknown project hue: {icon.hue}")
    return icon


def default_project_icon() -> ProjectIcon:
    """The look for projects nobody chose one for.

    Only template runs create such projects at runtime; existing records got
    the same value written by the v22 migration.
    """
    return ProjectIcon(glyph=PROJECT_GLYPHS[0], hue=PROJECT_HUES[0])
