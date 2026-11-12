from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Project
from miniclaw2.home_layout import (
    HOME_FILENAME,
    HomeFolder,
    HomeFolderItem,
    HomeLayout,
    HomeProjectItem,
    check_membership,
    merge_home_layout,
    reconcile,
)
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.migrations.validation import read_object
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


def p(project_id: str) -> HomeProjectItem:
    return HomeProjectItem(project=project_id)


def f(folder_id: str, name: str, *projects: str) -> HomeFolderItem:
    return HomeFolderItem(folder=HomeFolder(id=folder_id, name=name, projects=list(projects)))


def layout(*items) -> HomeLayout:
    return HomeLayout(items=list(items))


# ---- structure and membership ----

def test_structure_rejects_duplicates_and_bad_folders() -> None:
    with pytest.raises(ValueError):
        layout(p("a"), p("a"))
    with pytest.raises(ValueError):
        layout(p("a"), f("g_1", "x", "a"))
    with pytest.raises(ValueError):
        layout(f("g_1", "x", "a"), f("g_1", "y", "b"))
    with pytest.raises(ValueError):
        HomeFolder(id="g_1", name="   ", projects=["a"])
    with pytest.raises(ValueError):
        HomeFolder(id="g_1", name="x" * 41, projects=["a"])
    with pytest.raises(ValueError):
        HomeFolder(id="g_1", name="x", projects=[])
    with pytest.raises(ValueError):
        HomeFolder(id="folder", name="x", projects=["a"])
    assert HomeFolder(id="g_1", name="  研究  ", projects=["a"]).name == "研究"


def test_membership_requires_exactly_the_active_projects() -> None:
    check_membership(layout(p("a"), f("g_1", "x", "b")), ["a", "b"])
    with pytest.raises(ValueError, match="missing"):
        check_membership(layout(p("a")), ["a", "b"])
    with pytest.raises(ValueError, match="archived or unknown"):
        check_membership(layout(p("a"), p("z")), ["a"])


def test_reconcile_prunes_then_appends_in_given_order() -> None:
    current = layout(p("gone"), f("g_1", "x", "gone2"), f("g_2", "y", "a", "gone3"), p("b"))
    result, changed = reconcile(current, ["c", "a", "b", "d"])
    assert changed
    assert result.model_dump() == layout(f("g_2", "y", "a"), p("b"), p("c"), p("d")).model_dump()
    again, changed = reconcile(result, ["c", "a", "b", "d"])
    assert not changed and again.model_dump() == result.model_dump()
    created, changed = reconcile(None, ["a"])
    assert changed and created.project_ids() == ["a"]


def test_revision_tracks_content() -> None:
    assert layout(p("a"), p("b")).revision() == layout(p("a"), p("b")).revision()
    assert layout(p("a"), p("b")).revision() != layout(p("b"), p("a")).revision()


# ---- three-way merge ----

def _merge(tmp_path: Path, base: HomeLayout, local: HomeLayout, remote: HomeLayout) -> HomeLayout:
    roots = [tmp_path / name for name in ("base", "local", "remote")]
    for root, value in zip(roots, (base, local, remote)):
        atomic_json(root / HOME_FILENAME, value.model_dump())
    merge_home_layout(*roots)
    merged_local = read_object(roots[1] / HOME_FILENAME)
    assert merged_local == read_object(roots[2] / HOME_FILENAME)
    return HomeLayout.model_validate(merged_local)


def test_merge_keeps_local_order_and_honours_remote_adds_and_removes(tmp_path: Path) -> None:
    base = layout(p("a"), p("b"), p("c"))
    local = layout(p("c"), p("a"), p("b"))
    remote = layout(p("a"), p("b"), p("x"), p("y"))
    merged = _merge(tmp_path, base, local, remote)
    # c was removed remotely; local order of the survivors wins; additions follow.
    assert merged.model_dump() == layout(p("a"), p("b"), p("x"), p("y")).model_dump()


def test_merge_puts_remote_addition_into_shared_folder(tmp_path: Path) -> None:
    base = layout(p("a"), f("g_1", "Web", "b"))
    local = layout(f("g_1", "Web", "b"), p("a"))
    remote = layout(p("a"), f("g_1", "Web", "b", "n"))
    merged = _merge(tmp_path, base, local, remote)
    assert merged.model_dump() == layout(f("g_1", "Web", "b", "n"), p("a")).model_dump()


def test_merge_takes_remote_rename_only_over_untouched_local_name(tmp_path: Path) -> None:
    base = layout(f("g_1", "Old", "a"), f("g_2", "Two", "b"))
    local = layout(f("g_1", "Old", "a"), f("g_2", "Mine", "b"))
    remote = layout(f("g_1", "Theirs", "a"), f("g_2", "Theirs too", "b"))
    merged = _merge(tmp_path, base, local, remote)
    assert merged.model_dump() == layout(f("g_1", "Theirs", "a"), f("g_2", "Mine", "b")).model_dump()


def test_merge_adds_remote_folder_and_dissolves_untouched_deleted_folder(tmp_path: Path) -> None:
    base = layout(p("a"), p("b"), f("g_1", "Gone", "c", "d"), p("e"))
    local = layout(p("a"), p("b"), f("g_1", "Gone", "c", "d"), p("e"))
    remote = layout(f("g_2", "New", "a", "b"), p("c"), p("d"), p("e"))
    merged = _merge(tmp_path, base, local, remote)
    assert merged.model_dump() == layout(p("c"), p("d"), p("e"), f("g_2", "New", "a", "b")).model_dump()


def test_merge_keeps_locally_edited_folder_that_remote_deleted(tmp_path: Path) -> None:
    base = layout(f("g_1", "Keep", "a", "b"))
    local = layout(f("g_1", "Keep", "b", "a"))
    remote = layout(p("a"), p("b"))
    merged = _merge(tmp_path, base, local, remote)
    assert merged.model_dump() == layout(f("g_1", "Keep", "b", "a")).model_dump()


def test_merge_without_any_file_writes_nothing(tmp_path: Path) -> None:
    roots = [tmp_path / name for name in ("base", "local", "remote")]
    for root in roots:
        root.mkdir()
    merge_home_layout(*roots)
    assert not any((root / HOME_FILENAME).exists() for root in roots)


# ---- store and registry lifecycle ----

class HomeLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repos = [self.root / f"repo-{index}" for index in range(3)]
        for repo in self.repos:
            repo.mkdir()
        self.store = Store(self.root / "home")
        self.registry = ProjectRegistry(self.store)
        self.registry.initialize()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _create(self, index: int) -> Project:
        return self.registry.create_project(str(self.repos[index]), name=f"P{index}")

    def _file(self) -> dict:
        return json.loads((self.store.root / HOME_FILENAME).read_text())

    def test_first_read_creates_file_in_creation_order_then_writes_track_lifecycle(self) -> None:
        a, b = self._create(0), self._create(1)
        self.assertFalse((self.store.root / HOME_FILENAME).exists())
        self.assertEqual(self.registry.home_layout().project_ids(), [a.id, b.id])
        self.assertEqual(self._file()["items"], [{"project": a.id}, {"project": b.id}])

        c = self._create(2)
        self.assertEqual(HomeLayout.model_validate(self._file()).project_ids(), [a.id, b.id, c.id])

        self.registry.archive_project(a.id)
        self.assertEqual(HomeLayout.model_validate(self._file()).project_ids(), [b.id, c.id])
        self.registry.unarchive_project(a.id)
        self.assertEqual(HomeLayout.model_validate(self._file()).project_ids(), [b.id, c.id, a.id])

        self.registry.delete_project(b.id)
        self.assertEqual(HomeLayout.model_validate(self._file()).project_ids(), [c.id, a.id])

    def test_delete_drops_emptied_folder(self) -> None:
        a, b = self._create(0), self._create(1)
        current = self.registry.home_layout()
        grouped = layout(f("g_1", "Only", a.id), p(b.id))
        self.registry.replace_home_layout(grouped, base_revision=current.revision())
        self.registry.delete_project(a.id)
        self.assertEqual(self._file()["items"], [{"project": b.id}])

    def test_replace_checks_revision_and_membership(self) -> None:
        a, b = self._create(0), self._create(1)
        current = self.registry.home_layout()
        with self.assertRaises(ValueError):
            self.registry.replace_home_layout(layout(p(a.id)), base_revision=current.revision())
        from miniclaw2.home_layout import HomeConflictError

        with self.assertRaises(HomeConflictError):
            self.registry.replace_home_layout(layout(p(b.id), p(a.id)), base_revision="stale")
        saved = self.registry.replace_home_layout(
            layout(f("g_1", "Both", b.id, a.id)), base_revision=current.revision()
        )
        self.assertEqual(self.registry.home_layout().model_dump(), saved.model_dump())


class HomeApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        self.registry = ProjectRegistry(Store(self.root / "home"))
        self.client = TestClient(create_app(registry=self.registry))

    def tearDown(self) -> None:
        self.client.close()
        self._tmp.cleanup()

    def _create(self, name: str) -> str:
        res = self.client.post("/sessions", json={
            "cwd": str(self.cwd), "model_preset_id": "opus-4-8",
            "name": name, "icon": {"glyph": "code", "hue": "coral"},
        })
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["id"]

    def test_get_put_and_conflict(self) -> None:
        a, b = self._create("A"), self._create("B")
        home = self.client.get("/home").json()
        self.assertEqual(home["items"], [{"project": a}, {"project": b}])
        items = [{"folder": {"id": "g_ab", "name": "组", "projects": [b, a]}}]
        saved = self.client.put("/home", json={"items": items, "base_revision": home["revision"]})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["items"], items)
        stale = self.client.put("/home", json={"items": items, "base_revision": home["revision"]})
        self.assertEqual(stale.status_code, 409)
        invalid = self.client.put("/home", json={
            "items": [{"project": a}], "base_revision": saved.json()["revision"],
        })
        self.assertEqual(invalid.status_code, 400)

    def test_archived_project_can_change_icon(self) -> None:
        a = self._create("A")
        self.assertEqual(self.client.patch(f"/sessions/{a}/archive", json={"archived": True}).status_code, 200)
        self.assertEqual(self.client.get("/home").json()["items"], [])
        res = self.client.patch(f"/sessions/{a}/icon", json={"glyph": "star", "hue": "amber"})
        self.assertEqual(res.status_code, 200, res.text)
