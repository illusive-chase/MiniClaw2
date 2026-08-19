from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import miniclaw2.sync as sync_module
from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.registry import ProjectRegistry
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store
from miniclaw2.sync import SCHEMA_VERSION, schema_is_newer
from miniclaw2.tags import TAG_COLORS, create_tag, delete_tag, load_tags


class TagCrudTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "store"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_update_delete_and_atomic_file_shape(self) -> None:
        created = create_tag(self.root, "  Work  ", "coral")

        self.assertEqual(created.name, "Work")
        self.assertEqual(created.color, "coral")
        self.assertRegex(created.id, r"^t_[0-9a-f]{6}$")
        payload = json.loads((self.root / "tags.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["tags"], [created.model_dump()])
        self.assertFalse((self.root / "tags.json.tmp").exists())

        store = Store(self.root)
        updated = store.update_tag(created.id, name="Client", color="sage")
        assert updated is not None
        self.assertEqual((updated.name, updated.color), ("Client", "sage"))
        self.assertEqual(store.list_tags(), [updated])
        self.assertTrue(store.delete_tag(created.id))
        self.assertEqual(load_tags(self.root), [])
        self.assertFalse(store.delete_tag(created.id))

    def test_validation_rejects_duplicate_long_empty_and_invalid_color(self) -> None:
        create_tag(self.root, "Work", "coral")

        with self.assertRaisesRegex(ValueError, "already exists"):
            create_tag(self.root, "  wOrK  ", "amber")
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            create_tag(self.root, "   ", "amber")
        with self.assertRaisesRegex(ValueError, "at most 24"):
            create_tag(self.root, "x" * 25, "amber")
        for color in ("", "neutral", "unknown"):
            with self.subTest(color=color), self.assertRaisesRegex(
                ValueError, "unknown tag color"
            ):
                create_tag(self.root, f"tag-{color}", color)

    def test_update_revalidates_name_uniqueness_and_color(self) -> None:
        first = create_tag(self.root, "First", "coral")
        second = create_tag(self.root, "Second", "amber")
        store = Store(self.root)

        with self.assertRaisesRegex(ValueError, "already exists"):
            store.update_tag(second.id, name=" FIRST ")
        with self.assertRaisesRegex(ValueError, "unknown tag color"):
            store.update_tag(first.id, color="neutral")

    def test_default_color_is_a_user_selectable_palette_color(self) -> None:
        first = create_tag(self.root, "Research")
        delete_tag(self.root, first.id)
        second = create_tag(self.root, "Research")

        self.assertIn(first.color, TAG_COLORS)
        self.assertEqual(first.color, second.color)

    def test_tag_count_is_limited_to_32(self) -> None:
        for index in range(32):
            create_tag(self.root, f"tag-{index}", "coral")

        with self.assertRaisesRegex(ValueError, "at most 32"):
            create_tag(self.root, "overflow", "coral")


class ProjectTagRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "store"
        self.store = Store(self.root)
        self.first_tag = self.store.create_tag("First", "coral")
        self.second_tag = self.store.create_tag("Second", "sage")
        self.project_a = self.store.create_project(
            Project(root_path=str(Path(self.tmp.name) / "repo-a"))
        )
        self.project_b = self.store.create_project(
            Project(root_path=str(Path(self.tmp.name) / "repo-b"))
        )
        self.registry = ProjectRegistry(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_update_project_tags_deduplicates_preserves_order_and_drops_unknown(self) -> None:
        updated = self.registry.update_project_tags(
            self.project_a.id,
            [
                self.second_tag.id,
                "t_ffffff",
                self.first_tag.id,
                self.second_tag.id,
            ],
        )

        assert updated is not None
        self.assertEqual(updated.tag_ids, [self.second_tag.id, self.first_tag.id])
        persisted = json.loads(
            (self.root / "projects" / self.project_a.id / "project.json").read_text()
        )
        self.assertEqual(persisted["tag_ids"], updated.tag_ids)

    def test_delete_tag_strips_it_from_every_project_file(self) -> None:
        for project in (self.project_a, self.project_b):
            updated = self.registry.update_project_tags(
                project.id,
                [self.first_tag.id, self.second_tag.id],
            )
            assert updated is not None
        remote_hosts = self.root / "projects" / self.project_b.id / "hosts"
        (remote_hosts / "remote-host").mkdir(parents=True)

        self.assertTrue(self.registry.delete_tag(self.first_tag.id))

        for project in (self.project_a, self.project_b):
            payload = json.loads(
                (self.root / "projects" / project.id / "project.json").read_text()
            )
            self.assertEqual(payload["tag_ids"], [self.second_tag.id])
        self.assertFalse((remote_hosts / self.store.machine.id).exists())
        self.assertEqual(
            [tag.id for tag in self.store.list_tags()],
            [self.second_tag.id],
        )

    def test_list_projects_drops_unknown_tag_without_dropping_project(self) -> None:
        updated = self.registry.update_project_tags(
            self.project_a.id,
            [self.first_tag.id, self.second_tag.id],
        )
        assert updated is not None
        delete_tag(self.root, self.second_tag.id)

        projects = {project.id: project for project in self.store.list_projects()}

        self.assertIn(self.project_a.id, projects)
        self.assertEqual(projects[self.project_a.id].tag_ids, [self.first_tag.id])


class TagApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "store")
        self.project = self.store.create_project(
            Project(root_path=str(Path(self.tmp.name) / "repo"))
        )
        self.registry = ProjectRegistry(self.store)
        self.client = TestClient(create_app(self.registry))

    def tearDown(self) -> None:
        self.client.close()
        self.tmp.cleanup()

    def test_all_tag_endpoints_and_session_info_fields(self) -> None:
        created = self.client.post(
            "/tags",
            json={"name": "Work", "color": "coral"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        tag_id = created.json()["id"]
        self.assertEqual(self.client.get("/tags").json(), [created.json()])

        renamed = self.client.patch(
            f"/tags/{tag_id}",
            json={"name": "Client", "color": "teal"},
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["name"], "Client")

        assigned = self.client.patch(
            f"/sessions/{self.project.id}/tags",
            json={"tag_ids": [tag_id, "t_ffffff", tag_id]},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(assigned.json()["tag_ids"], [tag_id])
        self.assertEqual(
            assigned.json()["last_activity_at"],
            self.project.created_at,
        )

        deleted = self.client.delete(f"/tags/{tag_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(self.client.get("/tags").json(), [])
        session = self.client.get(f"/sessions/{self.project.id}").json()
        self.assertEqual(session["tag_ids"], [])

    def test_tag_api_validation_and_not_found_responses(self) -> None:
        created = self.client.post(
            "/tags",
            json={"name": "Work", "color": "coral"},
        )
        self.assertEqual(created.status_code, 200)
        duplicate = self.client.post(
            "/tags",
            json={"name": " work ", "color": "amber"},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(
            self.client.patch("/tags/t_ffffff", json={"name": "x"}).status_code,
            404,
        )
        self.assertEqual(self.client.delete("/tags/t_ffffff").status_code, 404)
        self.assertEqual(
            self.client.patch(
                "/sessions/missing/tags",
                json={"tag_ids": []},
            ).status_code,
            404,
        )


class ProjectActivityIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "store"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_constructor_indexes_maximum_node_timestamp(self) -> None:
        store = Store(self.root)
        project = store.create_project(Project(root_path="/tmp/repo", created_at=1.0))
        store.create_node(
            Node(
                project_id=project.id,
                model_preset_id=project.model_preset_id,
                created_at=10.0,
                started_at=20.0,
                finished_at=30.0,
                state=NodeState.DONE,
            )
        )
        store.create_node(
            Node(
                project_id=project.id,
                model_preset_id=project.model_preset_id,
                created_at=40.0,
                state=NodeState.VIRTUAL,
                prompt_draft="later",
            )
        )

        reloaded = Store(self.root)

        self.assertEqual(reloaded.project_last_activity_at(project.id), 40.0)

    def test_full_refresh_reads_project_list_once(self) -> None:
        store = Store(self.root)
        for index in range(3):
            project = store.create_project(
                Project(root_path=f"/tmp/repo-{index}", created_at=float(index + 1))
            )
            store.create_node(
                Node(
                    project_id=project.id,
                    model_preset_id=project.model_preset_id,
                    created_at=float(index + 10),
                )
            )

        with patch.object(store, "list_projects", wraps=store.list_projects) as listed:
            store.refresh_last_activity_index()

        self.assertEqual(listed.call_count, 1)

    def test_shared_host_nodes_are_included(self) -> None:
        store = Store(self.root)
        project = store.create_project(Project(root_path="/tmp/repo", created_at=1.0))
        project_dir = self.root / "projects" / project.id
        remote = Node(
            id="remote-node",
            project_id=project.id,
            model_preset_id=project.model_preset_id,
            created_at=10.0,
            started_at=20.0,
            finished_at=50.0,
            state=NodeState.DONE,
        )
        node_file = (
            project_dir / "hosts" / "remote-host" / "nodes" / remote.id / "node.json"
        )
        node_file.parent.mkdir(parents=True)
        node_file.write_text(
            json.dumps(remote.model_dump(exclude={"provider", "owner_host_id"})),
            encoding="utf-8",
        )

        reloaded = Store(self.root)

        self.assertFalse((project_dir / "nodes" / remote.id).exists())
        self.assertEqual(reloaded.project_last_activity_at(project.id), 50.0)

    def test_project_without_nodes_falls_back_to_created_at(self) -> None:
        store = Store(self.root)
        project = store.create_project(Project(root_path="/tmp/repo", created_at=12.5))

        reloaded = Store(self.root)

        self.assertEqual(reloaded.project_last_activity_at(project.id), 12.5)

    def test_transition_updates_index_and_swallows_activity_failures(self) -> None:
        store = Store(self.root)
        project = store.create_project(Project(root_path="/tmp/repo", created_at=1.0))
        node = store.create_node(
            Node(
                project_id=project.id,
                model_preset_id=project.model_preset_id,
                created_at=2.0,
            )
        )
        runner = NodeRunner(node, project, store, lambda _event: None)

        runner._transition(NodeState.RUNNING, started=True)
        started_at = node.started_at
        assert started_at is not None
        self.assertEqual(store.project_last_activity_at(project.id), started_at)

        with patch.object(
            store,
            "record_project_activity",
            side_effect=RuntimeError("index unavailable"),
        ):
            runner._transition(NodeState.DONE, finished=True)
        self.assertEqual(node.state, NodeState.DONE)

    def test_transition_uses_same_timestamp_maximum_as_full_refresh(self) -> None:
        store = Store(self.root)
        project = store.create_project(Project(root_path="/tmp/repo", created_at=1.0))
        future_created_at = 10_000_000_000.0
        node = store.create_node(
            Node(
                project_id=project.id,
                model_preset_id=project.model_preset_id,
                created_at=future_created_at,
            )
        )
        runner = NodeRunner(node, project, store, lambda _event: None)

        runner._transition(NodeState.RUNNING, started=True)

        self.assertEqual(
            store.project_last_activity_at(project.id),
            future_created_at,
        )

    def test_sync_success_callback_rebuilds_index(self) -> None:
        store = Store(self.root)
        project = store.create_project(Project(root_path="/tmp/repo", created_at=1.0))
        node = store.create_node(
            Node(
                project_id=project.id,
                model_preset_id=project.model_preset_id,
                created_at=2.0,
            )
        )
        store.list_nodes(project.id)
        self.assertEqual(store.project_last_activity_at(project.id), 2.0)
        node.finished_at = 99.0
        node.state = NodeState.DONE
        store.update_node(node)
        store._last_activity_index[project.id] = 2.0

        store.sync._record_success()

        self.assertEqual(store.project_last_activity_at(project.id), 99.0)


class TagSchemaVersionTests(unittest.TestCase):
    def test_schema_version_advances_and_older_build_sees_newer_store(self) -> None:
        # Tags shipped at schema 10; later features may advance it further.
        self.assertGreaterEqual(SCHEMA_VERSION, 10)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "schema.json").write_text(
                json.dumps(
                    {"schema": "node-revision-v9", "schema_version": SCHEMA_VERSION}
                ),
                encoding="utf-8",
            )
            with patch.object(sync_module, "SCHEMA_VERSION", SCHEMA_VERSION - 1):
                self.assertTrue(schema_is_newer(root))


if __name__ == "__main__":
    unittest.main()
