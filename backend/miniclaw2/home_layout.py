"""The project home screen arrangement, persisted as ``home.json``.

``home.json`` sits at the metadata root next to ``config.json`` and travels
with sync. It holds only the order of the home grid and its folders; a
project's own look (``Project.icon``) stays in its record. Archived projects
never appear here — the home screen lists them from the project records.

Invariants a write must satisfy, checked against the live project set:
every unarchived project appears exactly once, no archived or unknown id
appears, folders are non-empty with unique ids, and folder names are 1–40
characters after trimming. Reads are lenient instead: ``reconcile`` folds any
drift (a project created on another device, a deletion that arrived through
sync) back into a layout that satisfies them, so a stale file converges rather
than failing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .migrations.access import storage_function
from .migrations.transaction import atomic_json


HOME_FILENAME = "home.json"
MAX_FOLDER_NAME_LENGTH = 40
_FOLDER_ID_PREFIX = "g_"


class HomeConflictError(RuntimeError):
    """The client edited a layout that has since changed on the server."""


class HomeFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    projects: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        suffix = value[len(_FOLDER_ID_PREFIX):]
        if (
            not value.startswith(_FOLDER_ID_PREFIX)
            or not 1 <= len(suffix) <= 32
            or not all(char in "0123456789abcdef" for char in suffix)
        ):
            raise ValueError("folder id must use the form g_ followed by hex digits")
        return value

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("folder name must not be empty")
        if len(normalized) > MAX_FOLDER_NAME_LENGTH:
            raise ValueError(
                f"folder name must be at most {MAX_FOLDER_NAME_LENGTH} characters"
            )
        return normalized


class HomeProjectItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str


class HomeFolderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder: HomeFolder


HomeItem = HomeProjectItem | HomeFolderItem


class HomeLayout(BaseModel):
    """Structural invariants only; membership is checked against projects."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    items: list[HomeItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_structure(self) -> "HomeLayout":
        seen: set[str] = set()
        folder_ids: set[str] = set()
        for project_id in self.project_ids():
            if project_id in seen:
                raise ValueError(f"project appears twice on the home screen: {project_id}")
            seen.add(project_id)
        for item in self.items:
            if isinstance(item, HomeFolderItem):
                if item.folder.id in folder_ids:
                    raise ValueError(f"duplicate folder id: {item.folder.id}")
                folder_ids.add(item.folder.id)
        return self

    def project_ids(self) -> list[str]:
        ids: list[str] = []
        for item in self.items:
            if isinstance(item, HomeProjectItem):
                ids.append(item.project)
            else:
                ids.extend(item.folder.projects)
        return ids

    def revision(self) -> str:
        """Content hash the client echoes back to detect concurrent edits."""
        canonical = json.dumps(
            self.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load_home(store_root: Path) -> HomeLayout | None:
    path = store_root / HOME_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return HomeLayout.model_validate(payload)
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid home layout {path}: {exc}") from exc


@storage_function
def save_home(store_root: Path, layout: HomeLayout) -> None:
    atomic_json(store_root / HOME_FILENAME, HomeLayout.model_validate(layout.model_dump()).model_dump())


def check_membership(layout: HomeLayout, active_ids: Iterable[str]) -> None:
    """Raise unless ``layout`` lists exactly the unarchived projects."""
    expected = set(active_ids)
    present = layout.project_ids()
    unknown = [project_id for project_id in present if project_id not in expected]
    if unknown:
        raise ValueError(f"home layout lists archived or unknown projects: {', '.join(unknown)}")
    missing = sorted(expected - set(present))
    if missing:
        raise ValueError(f"home layout is missing projects: {', '.join(missing)}")


def reconcile(layout: HomeLayout | None, active_ids: Sequence[str]) -> tuple[HomeLayout, bool]:
    """Drop ids that are gone, then append unlisted projects in the given order.

    Returns the converged layout and whether it differs from the input. A
    folder emptied by the pruning disappears with it.
    """
    current = layout or HomeLayout()
    active = set(active_ids)
    items: list[HomeItem] = []
    for item in current.items:
        if isinstance(item, HomeProjectItem):
            if item.project in active:
                items.append(item)
            continue
        kept = [project_id for project_id in item.folder.projects if project_id in active]
        if kept:
            items.append(HomeFolderItem(folder=item.folder.model_copy(update={"projects": kept})))
    listed = {project_id for item in items for project_id in _item_project_ids(item)}
    for project_id in active_ids:
        if project_id not in listed:
            items.append(HomeProjectItem(project=project_id))
            listed.add(project_id)
    result = HomeLayout(items=items)
    return result, layout is None or result.model_dump() != current.model_dump()


def append_project(layout: HomeLayout | None, project_id: str) -> HomeLayout:
    current = layout or HomeLayout()
    if project_id in current.project_ids():
        return current
    return HomeLayout(items=[*current.items, HomeProjectItem(project=project_id)])


def remove_project(layout: HomeLayout | None, project_id: str) -> HomeLayout:
    current = layout or HomeLayout()
    return _without(current, {project_id})


def merge_home_layout(base: Path, local: Path, remote: Path) -> None:
    """Three-way merge of ``home.json`` before the Git merge; local wins.

    The arrangement is the user's own recent work on this device, so a
    conflict keeps the local order rather than refusing the sync. What the
    remote added or removed is still honoured: removed projects drop out,
    added ones are appended (into a folder the local side also has, when that
    is where the remote put them), a folder created remotely arrives whole,
    and a folder deleted remotely dissolves in place if this device never
    touched it. A remote rename wins only over an untouched local name. The
    result is written to both sides so Git sees no difference to merge.
    """
    paths = [root / HOME_FILENAME for root in (base, local, remote)]
    if not any(path.exists() for path in paths):
        return
    ancestor, ours, theirs = (load_home(root) or HomeLayout() for root in (base, local, remote))
    merged = _merge(ancestor, ours, theirs)
    payload = merged.model_dump()
    atomic_json(local / HOME_FILENAME, payload)
    atomic_json(remote / HOME_FILENAME, payload)


def _merge(base: HomeLayout, local: HomeLayout, remote: HomeLayout) -> HomeLayout:
    base_ids, remote_ids = set(base.project_ids()), set(remote.project_ids())
    removed = base_ids - remote_ids
    base_folders, local_folders, remote_folders = (
        _folders(layout) for layout in (base, local, remote)
    )
    base_top = {item.project for item in base.items if isinstance(item, HomeProjectItem)}

    items: list[HomeItem] = []
    for item in _without(local, removed).items:
        if isinstance(item, HomeProjectItem):
            items.append(item)
            continue
        folder = item.folder
        before, theirs = base_folders.get(folder.id), remote_folders.get(folder.id)
        if before is not None and theirs is None and _same_folder(local_folders[folder.id], before):
            items.extend(HomeProjectItem(project=project_id) for project_id in folder.projects)
            continue
        if before is not None and theirs is not None and folder.name == before.name and theirs.name != before.name:
            folder = folder.model_copy(update={"name": theirs.name})
        items.append(HomeFolderItem(folder=folder))

    for folder_id, theirs in remote_folders.items():
        if folder_id in base_folders or folder_id in local_folders:
            continue
        placed = {project_id for item in items for project_id in _item_project_ids(item)}
        members: list[str] = []
        for project_id in theirs.projects:
            if project_id not in placed:
                members.append(project_id)
                continue
            index = _top_level_index(items, project_id)
            if index is not None and project_id in base_top:
                # Never grouped on this device, grouped over there: follow it.
                items.pop(index)
                members.append(project_id)
        if members:
            items.append(HomeFolderItem(folder=theirs.model_copy(update={"projects": members})))

    placed = {project_id for item in items for project_id in _item_project_ids(item)}
    remote_home = {
        project_id: folder_id
        for folder_id, folder in remote_folders.items()
        for project_id in folder.projects
    }
    for project_id in remote.project_ids():
        if project_id in base_ids or project_id in placed:
            continue
        target = remote_home.get(project_id)
        folder_index = next(
            (
                index for index, item in enumerate(items)
                if isinstance(item, HomeFolderItem) and item.folder.id == target
            ),
            None,
        )
        if folder_index is None:
            items.append(HomeProjectItem(project=project_id))
        else:
            folder = _folder_at(items, folder_index)
            items[folder_index] = HomeFolderItem(
                folder=folder.model_copy(update={"projects": [*folder.projects, project_id]})
            )
        placed.add(project_id)
    return _deduplicated(items)


def _folder_at(items: list[HomeItem], index: int) -> HomeFolder:
    item = items[index]
    assert isinstance(item, HomeFolderItem)
    return item.folder


def _folders(layout: HomeLayout) -> dict[str, HomeFolder]:
    return {
        item.folder.id: item.folder
        for item in layout.items
        if isinstance(item, HomeFolderItem)
    }


def _same_folder(left: HomeFolder, right: HomeFolder) -> bool:
    return left.name == right.name and left.projects == right.projects


def _top_level_index(items: list[HomeItem], project_id: str) -> int | None:
    return next(
        (
            index for index, item in enumerate(items)
            if isinstance(item, HomeProjectItem) and item.project == project_id
        ),
        None,
    )


def _item_project_ids(item: HomeItem) -> list[str]:
    return [item.project] if isinstance(item, HomeProjectItem) else list(item.folder.projects)


def _without(layout: HomeLayout, project_ids: set[str]) -> HomeLayout:
    items: list[HomeItem] = []
    for item in layout.items:
        if isinstance(item, HomeProjectItem):
            if item.project not in project_ids:
                items.append(item)
            continue
        kept = [project_id for project_id in item.folder.projects if project_id not in project_ids]
        if kept:
            items.append(HomeFolderItem(folder=item.folder.model_copy(update={"projects": kept})))
    return HomeLayout(items=items)


def _deduplicated(items: list[HomeItem]) -> HomeLayout:
    seen: set[str] = set()
    folder_ids: set[str] = set()
    result: list[HomeItem] = []
    for item in items:
        if isinstance(item, HomeProjectItem):
            if item.project not in seen:
                seen.add(item.project)
                result.append(item)
            continue
        if item.folder.id in folder_ids:
            continue
        kept = [project_id for project_id in item.folder.projects if project_id not in seen]
        seen.update(kept)
        if kept:
            folder_ids.add(item.folder.id)
            result.append(HomeFolderItem(folder=item.folder.model_copy(update={"projects": kept})))
    return HomeLayout(items=result)
