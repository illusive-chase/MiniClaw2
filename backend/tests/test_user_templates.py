from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store
from miniclaw2.templates import (
    SerializerError,
    apply_user_template,
    list_user_templates,
    load_user_template,
    serialize_selection,
    user_templates_root,
)
from miniclaw2.templates.launcher import _stamp_lane


def _seeded_registry(root: Path) -> tuple[ProjectRegistry, Store]:
    store = Store(root=root)
    return ProjectRegistry(store=store), store


def _make_project_with_lane(registry: ProjectRegistry) -> tuple[str, str]:
    """Create a temporary project + activate a fresh planspace. Returns (pid, lane)."""
    project = registry.create_project(
        cwd=None, provider="claude", temporary=True
    )
    from miniclaw2.contextspace import create_planspace

    lane = create_planspace(
        project,
        title="test-lane",
        store_root=registry.store.root,
        seed_text="seed",
    )
    settings = dict(project.settings_override)
    settings["active_planspace_id"] = lane
    project.settings_override = settings
    registry.store.update_project(project)
    return project.id, lane


def _add_virtual(
    store: Store,
    pid: str,
    lane: str,
    *,
    prompt_draft: str,
    category: Category = Category.REGULAR,
    subtype: ReviewSubtype | None = None,
    brief: ReviewBrief | None = None,
    scheduled_deps: list[str] | None = None,
    resume_from: str | None = None,
    summary: str = "",
) -> Node:
    node = Node(
        project_id=pid,
        kind=NodeKind.AGENT,
        state=NodeState.VIRTUAL,
        planspace_id=lane,
        provider="claude",
        prompt="",
        prompt_draft=prompt_draft,
        category=category,
        subtype=subtype,
        brief=brief,
        scheduled_deps=list(scheduled_deps or []),
        resume_from_node_id=resume_from,
        proposed_by="test",
        summary=summary,
    )
    store.create_node(node)
    return node


class UserTemplateSerializerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.registry, self.store = _seeded_registry(Path(self._home.name))

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_HOME", None)
        self._home.cleanup()

    def test_save_and_reload_round_trip(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        a = _add_virtual(
            self.store, pid, lane, prompt_draft="Say hello.", summary="greeter"
        )
        b = _add_virtual(
            self.store,
            pid,
            lane,
            prompt_draft="Say goodbye.",
            scheduled_deps=[a.id],
        )

        template = serialize_selection(
            self.store,
            pid,
            [a.id, b.id],
            name="Greetings",
            brief="Hello then goodbye.",
        )

        self.assertEqual(len(template.nodes), 2)
        # Verify prompts got laid out with predictable slugs.
        first, second = template.nodes
        self.assertEqual(first.id, "n0")
        self.assertEqual(second.id, "n1")
        self.assertEqual(second.scheduled_deps, ["n0"])
        self.assertIn("Say hello.", first.prompt)
        self.assertIn("Say goodbye.", second.prompt)

        listed = list_user_templates(self.store.root)
        self.assertEqual([tpl.name for tpl in listed], ["Greetings"])

        reloaded = load_user_template("greetings", self.store.root)
        self.assertEqual(reloaded.brief, "Hello then goodbye.")

    def test_op_nodes_are_silently_filtered(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        a = _add_virtual(self.store, pid, lane, prompt_draft="One.")
        op = Node(
            project_id=pid,
            kind=NodeKind.OP,
            op_kind="commit",
            state=NodeState.DONE,
            planspace_id=lane,
            provider="claude",
        )
        self.store.create_node(op)

        template = serialize_selection(
            self.store,
            pid,
            [a.id, op.id],
            name="filter-op",
            brief="",
        )
        self.assertEqual(len(template.nodes), 1)
        self.assertEqual(template.nodes[0].kind.value, "agent")

    def test_verifier_in_selection_is_rejected(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        a = _add_virtual(self.store, pid, lane, prompt_draft="One.")
        verifier = Node(
            project_id=pid,
            kind=NodeKind.VERIFIER,
            category=Category.REVIEW,
            subtype=ReviewSubtype.PROGRAMMATIC_REVIEW,
            brief=ReviewBrief(
                check_what="run", expected="pass", abnormal="fail"
            ),
            state=NodeState.VIRTUAL,
            planspace_id=lane,
            provider="claude",
            verify_script_ref="/tmp/verify.sh",
            scheduled_deps=[a.id],
            proposed_by="test",
        )
        self.store.create_node(verifier)

        with self.assertRaises(SerializerError):
            serialize_selection(
                self.store,
                pid,
                [a.id, verifier.id],
                name="reject-verifier",
                brief="",
            )

    def test_resume_dangling_is_rejected(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        # Simulate a completed executed node A so B can resume from it.
        a = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            state=NodeState.DONE,
            planspace_id=lane,
            provider="claude",
            prompt="One.",
            provider_session_id="sess-a",
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(a)
        b = _add_virtual(
            self.store,
            pid,
            lane,
            prompt_draft="Continue.",
            scheduled_deps=[a.id],
            resume_from=a.id,
        )
        # Select only B; A is outside → resume dangles.
        with self.assertRaises(SerializerError):
            serialize_selection(
                self.store,
                pid,
                [b.id],
                name="dangling-resume",
                brief="",
            )

    def test_disconnected_selection_is_rejected(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        a = _add_virtual(self.store, pid, lane, prompt_draft="One.")
        b = _add_virtual(self.store, pid, lane, prompt_draft="Two.")
        # No edges between a and b.
        with self.assertRaises(SerializerError):
            serialize_selection(
                self.store,
                pid,
                [a.id, b.id],
                name="disconnected",
                brief="",
            )

    def test_empty_selection_is_rejected(self) -> None:
        pid, _ = _make_project_with_lane(self.registry)
        with self.assertRaises(SerializerError):
            serialize_selection(self.store, pid, [], name="x", brief="")

    def test_name_collision_is_rejected(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        a = _add_virtual(self.store, pid, lane, prompt_draft="One.")
        serialize_selection(
            self.store, pid, [a.id], name="Collide", brief=""
        )
        with self.assertRaises(SerializerError):
            serialize_selection(
                self.store, pid, [a.id], name="collide", brief=""
            )

    def test_transient_state_rejects(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        running = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            state=NodeState.RUNNING,
            planspace_id=lane,
            provider="claude",
            prompt="Working…",
            started_at=1.0,
        )
        self.store.create_node(running)
        with self.assertRaises(SerializerError):
            serialize_selection(
                self.store, pid, [running.id], name="transient", brief=""
            )

    def test_executed_terminal_collapses_prompt(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        done = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            state=NodeState.DONE,
            planspace_id=lane,
            provider="claude",
            prompt="Reify me.",
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(done)
        template = serialize_selection(
            self.store, pid, [done.id], name="collapse", brief=""
        )
        # The executed node's `prompt` should have flowed into the
        # template's per-node prompt file.
        self.assertIn("Reify me.", template.nodes[0].prompt)


class ApplyUserTemplateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.registry, self.store = _seeded_registry(Path(self._home.name))

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_HOME", None)
        self._home.cleanup()

    def _build_greetings_template(self) -> str:
        pid, lane = _make_project_with_lane(self.registry)
        a = _add_virtual(self.store, pid, lane, prompt_draft="Hello.")
        b = _add_virtual(
            self.store, pid, lane, prompt_draft="Goodbye.", scheduled_deps=[a.id]
        )
        template = serialize_selection(
            self.store, pid, [a.id, b.id], name="Greetings", brief="Say both."
        )
        return template.root.name

    def test_apply_into_active_lane_stamps_translated_deps(self) -> None:
        slug = self._build_greetings_template()
        template = load_user_template(slug, self.store.root)

        target_pid, target_lane = _make_project_with_lane(self.registry)
        target_project = self.registry.get_project(target_pid)
        assert target_project is not None

        created = apply_user_template(template, target_project, self.registry)
        self.assertEqual(len(created), 2)
        first, second = created
        self.assertEqual(first.state, NodeState.VIRTUAL)
        self.assertEqual(first.planspace_id, target_lane)
        self.assertEqual(second.scheduled_deps, [first.id])
        self.assertEqual(first.proposed_by, f"template:{template.name}")

    def test_apply_with_anchor_binds_root_deps(self) -> None:
        slug = self._build_greetings_template()
        template = load_user_template(slug, self.store.root)

        target_pid, target_lane = _make_project_with_lane(self.registry)
        target_project = self.registry.get_project(target_pid)
        assert target_project is not None
        # Anchor is an existing virtual on the same lane.
        anchor = _add_virtual(self.store, target_pid, target_lane, prompt_draft="Anchor.")

        created = apply_user_template(
            template, target_project, self.registry, anchor_node_id=anchor.id
        )
        first, second = created
        self.assertEqual(first.scheduled_deps, [anchor.id])
        # Non-root virtual keeps its translated in-template dep.
        self.assertEqual(second.scheduled_deps, [first.id])

    def test_apply_rejects_no_active_planspace(self) -> None:
        slug = self._build_greetings_template()
        template = load_user_template(slug, self.store.root)

        project = self.registry.create_project(
            cwd=None, provider="claude", temporary=True
        )
        # settings_override["active_planspace_id"] is unset.
        with self.assertRaises(Exception):
            apply_user_template(template, project, self.registry)


class UserTemplateHttpApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        os.environ.pop("MINICLAW_HOME", None)
        self._home.cleanup()

    def test_save_apply_and_delete_round_trip(self) -> None:
        # Set up a project with a couple of virtuals in the active lane.
        launched = self.client.post(
            "/templates/hello-text/run", json={"provider": "claude"}
        )
        self.assertEqual(launched.status_code, 200, launched.text)
        sid = launched.json()["id"]

        listed = self.client.get(f"/sessions/{sid}/nodes")
        nodes = listed.json()
        agent_node = next(n for n in nodes if n["kind"] == "agent")

        # Save that single agent virtual as a template.
        save_res = self.client.post(
            f"/sessions/{sid}/user-templates",
            json={
                "name": "Hello",
                "brief": "Simple hello.",
                "node_ids": [agent_node["id"]],
            },
        )
        self.assertEqual(save_res.status_code, 200, save_res.text)
        saved = save_res.json()
        self.assertEqual(saved["slug"], "hello")
        self.assertEqual(saved["node_count"], 1)

        # List picks it up.
        lst = self.client.get("/user-templates")
        self.assertEqual(lst.status_code, 200)
        self.assertIn("Hello", [t["name"] for t in lst.json()])

        # Apply into the same project — should stamp a new virtual.
        apply_res = self.client.post(
            f"/sessions/{sid}/user-templates/hello/apply",
            json={"anchor_node_id": None},
        )
        self.assertEqual(apply_res.status_code, 200, apply_res.text)
        stamped_ids = apply_res.json()["node_ids"]
        self.assertEqual(len(stamped_ids), 1)
        # The freshly stamped virtual is present in the project.
        listed2 = self.client.get(f"/sessions/{sid}/nodes")
        listed2_ids = [n["id"] for n in listed2.json()]
        self.assertIn(stamped_ids[0], listed2_ids)

        # Delete removes the disk template.
        del_res = self.client.delete("/user-templates/hello")
        self.assertEqual(del_res.status_code, 204, del_res.text)
        lst_after = self.client.get("/user-templates")
        self.assertEqual(lst_after.json(), [])

    def test_save_returns_400_on_invalid_selection(self) -> None:
        launched = self.client.post(
            "/templates/hello-text/run", json={"provider": "claude"}
        )
        sid = launched.json()["id"]
        # Empty selection.
        res = self.client.post(
            f"/sessions/{sid}/user-templates",
            json={"name": "Bad", "brief": "", "node_ids": []},
        )
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
