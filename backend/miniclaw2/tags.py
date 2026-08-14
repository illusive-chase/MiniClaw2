"""Global project tags persisted in the metadata sync root."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


TAG_COLORS = (
    "coral",
    "amber",
    "sage",
    "teal",
    "azure",
    "indigo",
    "plum",
    "clay",
)
MAX_TAGS = 32
MAX_TAG_NAME_LENGTH = 24
TAGS_FILENAME = "tags.json"


class Tag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    color: str
    created_at: float

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if len(value) != 8 or not value.startswith("t_"):
            raise ValueError("tag id must use the form t_ followed by 6 hex digits")
        try:
            int(value[2:], 16)
        except ValueError as exc:
            raise ValueError("tag id must use the form t_ followed by 6 hex digits") from exc
        return value

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tag name must not be empty")
        if len(normalized) > MAX_TAG_NAME_LENGTH:
            raise ValueError(
                f"tag name must be at most {MAX_TAG_NAME_LENGTH} characters"
            )
        return normalized

    @field_validator("color")
    @classmethod
    def _validate_color(cls, value: str) -> str:
        if value not in TAG_COLORS:
            raise ValueError(f"unknown tag color: {value}")
        return value


class _TagFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    tags: list[Tag]

    @model_validator(mode="after")
    def _validate_collection(self) -> "_TagFile":
        if len(self.tags) > MAX_TAGS:
            raise ValueError(f"at most {MAX_TAGS} tags are allowed")
        ids = [tag.id for tag in self.tags]
        if len(ids) != len(set(ids)):
            raise ValueError("tag ids must be unique")
        names = [tag.name.casefold() for tag in self.tags]
        if len(names) != len(set(names)):
            raise ValueError("tag names must be unique ignoring case")
        return self


def load_tags(store_root: Path) -> list[Tag]:
    path = store_root / TAGS_FILENAME
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid tag file {path}: {exc}") from exc
    try:
        return _TagFile.model_validate(payload).tags
    except ValueError as exc:
        raise ValueError(f"invalid tag file {path}: {exc}") from exc


def save_tags(store_root: Path, tags: list[Tag]) -> None:
    validated = _TagFile(version=1, tags=tags)
    path = store_root / TAGS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(validated.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def create_tag(store_root: Path, name: str, color: str | None = None) -> Tag:
    tags = load_tags(store_root)
    if len(tags) >= MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags are allowed")
    normalized_name = _normalized_name(name)
    _require_unique_name(tags, normalized_name)
    selected_color = _default_color(normalized_name) if color is None else color
    existing_ids = {tag.id for tag in tags}
    tag_id = _new_tag_id(existing_ids)
    tag = Tag(
        id=tag_id,
        name=normalized_name,
        color=selected_color,
        created_at=time.time(),
    )
    save_tags(store_root, [*tags, tag])
    return tag


def update_tag(
    store_root: Path,
    tag_id: str,
    *,
    name: str | None = None,
    color: str | None = None,
) -> Tag | None:
    tags = load_tags(store_root)
    index = next((i for i, tag in enumerate(tags) if tag.id == tag_id), None)
    if index is None:
        return None
    current = tags[index]
    normalized_name = current.name if name is None else _normalized_name(name)
    _require_unique_name(tags, normalized_name, exclude_id=tag_id)
    updated = current.model_copy(
        update={
            "name": normalized_name,
            "color": current.color if color is None else color,
        }
    )
    updated = Tag.model_validate(updated.model_dump())
    tags[index] = updated
    save_tags(store_root, tags)
    return updated


def delete_tag(store_root: Path, tag_id: str) -> bool:
    tags = load_tags(store_root)
    remaining = [tag for tag in tags if tag.id != tag_id]
    if len(remaining) == len(tags):
        return False
    save_tags(store_root, remaining)
    return True


def _normalized_name(name: str) -> str:
    return Tag(
        id="t_000000",
        name=name,
        color=TAG_COLORS[0],
        created_at=0.0,
    ).name


def _require_unique_name(
    tags: list[Tag],
    name: str,
    *,
    exclude_id: str | None = None,
) -> None:
    folded = name.casefold()
    if any(tag.id != exclude_id and tag.name.casefold() == folded for tag in tags):
        raise ValueError(f"tag name already exists: {name}")


def _default_color(name: str) -> str:
    digest = hashlib.sha256(name.casefold().encode("utf-8")).digest()
    return TAG_COLORS[int.from_bytes(digest[:4], "big") % len(TAG_COLORS)]


def _new_tag_id(existing_ids: set[str]) -> str:
    while True:
        candidate = f"t_{uuid4().hex[:6]}"
        if candidate not in existing_ids:
            return candidate
