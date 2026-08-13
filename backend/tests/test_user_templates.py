from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from fastapi.testclient import TestClient

from miniclaw2.contextspace import create_planspace, read_template_instances
from miniclaw2.domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    PlanspaceMode,
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store
from miniclaw2.templates import (
    SerializerError,
    Template,
    TemplateArgument,
    TemplateError,
    TemplateInput,
    apply_user_template,
    list_user_templates,
    load_user_template,
    rewrite_user_template,
    serialize_selection,
    user_templates_root,
)
from miniclaw2.templates.launcher import _stamp_lane
from miniclaw2.templates.loader import TemplateNodeSpec, _scan_placeholders


def _seeded_registry(root: Path) -> tuple[ProjectRegistry, Store]:
    store = Store(root=root)
    return ProjectRegistry(store=store), store


def _make_project_with_lane(registry: ProjectRegistry) -> tuple[str, str]:
    """Create a temporary project + activate a fresh planspace. Returns (pid, lane)."""
    project = registry.create_project(
        cwd=None, model_preset_id="opus-4-8", temporary=True
    )
    lane = create_planspace(
        project,
        title="test-lane",
        store_root=registry.store.root,
        seed_text="seed",
    )
    project.active_planspace_id = lane
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
        model_preset_id="opus-4-8",
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


def _function_template(
    root: Path,
    *,
    prompts: list[str],
    deps: list[list[str] | None] | None = None,
    arguments: list[TemplateArgument] | None = None,
    inputs: list[TemplateInput] | None = None,
    name: str = "Function template",
) -> Template:
    deps = deps or [None] * len(prompts)
    return Template(
        slug=root.name,
        name=name,
        brief="Parameterized template.",
        allowed_model_preset_ids=["opus-4-8"],
        auto_commit=False,
        permission_mode=None,
        lane_mode=PlanspaceMode.MANUAL,
        nodes=[
            TemplateNodeSpec(
                id=f"n{index}",
                kind=NodeKind.AGENT,
                category=Category.REGULAR,
                subtype=None,
                brief=None,
                prompt=prompt,
                scheduled_deps=deps[index],
            )
            for index, prompt in enumerate(prompts)
        ],
        seed=[],
        root=root,
        arguments=list(arguments or []),
        inputs=list(inputs or []),
    )


def _write_user_function_template(
    store: Store,
    slug: str,
    *,
    prompt: str,
    arguments: list[dict[str, object]] | None = None,
    inputs: list[dict[str, str]] | None = None,
    scheduled_deps: list[str] | None = None,
) -> None:
    root = user_templates_root(store.root) / slug
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "template.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "name": slug,
                "brief": "Function template.",
                "allowed_model_preset_ids": ["opus-4-8"],
                "lane_mode": "manual",
                "arguments": arguments or [],
                "inputs": inputs or [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "lane.yaml").write_text(
        yaml.safe_dump(
            {
                "nodes": [
                    {
                        "id": "n0",
                        "kind": "agent",
                        "category": "regular",
                        "prompt_file": "prompts/n0.md",
                        "scheduled_deps": scheduled_deps or [],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "prompts" / "n0.md").write_text(prompt, encoding="utf-8")


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

    def test_save_emits_v2_arguments_from_loader_placeholder_scan(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        node = _add_virtual(
            self.store,
            pid,
            lane,
            prompt_draft="Use {{topic}} but keep {{Bad-Name}} literal.",
        )

        template = serialize_selection(
            self.store,
            pid,
            [node.id],
            name="Parameterized selection",
            brief="Scans placeholders.",
        )

        template_data = yaml.safe_load(
            (template.root / "template.yaml").read_text(encoding="utf-8")
        )
        scanned, _ = _scan_placeholders(template.nodes[0].prompt)
        self.assertEqual(template_data["schema_version"], 2)
        self.assertEqual(template_data["inputs"], [])
        self.assertEqual(
            [argument["name"] for argument in template_data["arguments"]],
            scanned,
        )
        self.assertEqual(scanned, ["topic"])
        self.assertEqual(
            [argument.name for argument in template.arguments],
            ["topic"],
        )
        self.assertTrue(template.arguments[0].declared)

    def test_rewrite_preserves_original_when_backup_rename_fails(self) -> None:
        _write_user_function_template(
            self.store,
            "atomic",
            prompt="Original prompt that must survive.",
        )
        root = user_templates_root(self.store.root) / "atomic"

        def snapshot() -> dict[str, bytes]:
            return {
                str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

        before = snapshot()
        path_type = type(root)
        original_replace = path_type.replace

        def fail_original_rename(path: Path, target: Path) -> Path:
            if path == root:
                raise OSError("backup rename failed")
            return original_replace(path, target)

        with patch.object(
            path_type,
            "replace",
            autospec=True,
            side_effect=fail_original_rename,
        ):
            with self.assertRaisesRegex(OSError, "backup rename failed"):
                rewrite_user_template(
                    "atomic",
                    name="Renamed display title",
                    brief="Valid candidate.",
                    nodes=[
                        {
                            "id": "n0",
                            "kind": "agent",
                            "category": "regular",
                            "prompt": "Replacement prompt.",
                        }
                    ],
                    arguments=[],
                    inputs=[],
                    store_root=self.store.root,
                )

        self.assertEqual(snapshot(), before)
        self.assertEqual(
            sorted(path.name for path in root.parent.iterdir()),
            ["atomic"],
        )

    def test_ui_continuation_resume_source_is_serialized_as_dep(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        source = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            state=NodeState.DONE,
            planspace_id=lane,
            model_preset_id="opus-4-8",
            prompt="Original turn.",
            provider_session_id="sess-a",
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(source)
        continuation = _add_virtual(
            self.store,
            pid,
            lane,
            prompt_draft="Continue from source.",
            resume_from=source.id,
        )

        template = serialize_selection(
            self.store,
            pid,
            [source.id, continuation.id],
            name="Continuation",
            brief="Resume chain.",
        )

        self.assertEqual([node.id for node in template.nodes], ["n0", "n1"])
        self.assertEqual(template.nodes[1].resume_from, "n0")
        self.assertEqual(template.nodes[1].scheduled_deps, ["n0"])

        reloaded = load_user_template("continuation", self.store.root)
        self.assertEqual(reloaded.nodes[1].resume_from, "n0")
        self.assertEqual(reloaded.nodes[1].scheduled_deps, ["n0"])

    def test_op_nodes_are_silently_filtered(self) -> None:
        pid, lane = _make_project_with_lane(self.registry)
        a = _add_virtual(self.store, pid, lane, prompt_draft="One.")
        op = Node(
            project_id=pid,
            kind=NodeKind.OP,
            op_kind="commit",
            state=NodeState.DONE,
            planspace_id=lane,
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
            model_preset_id="opus-4-8",
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
            model_preset_id="opus-4-8",
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
            model_preset_id="opus-4-8",
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
            cwd=None, model_preset_id="opus-4-8", temporary=True
        )
        # active_planspace_id is unset.
        with self.assertRaises(Exception):
            apply_user_template(template, project, self.registry)

    def test_parameters_and_inputs_are_stamped_once_with_instance_record(self) -> None:
        target_pid, target_lane = _make_project_with_lane(self.registry)
        target_project = self.registry.get_project(target_pid)
        assert target_project is not None
        source = _add_virtual(
            self.store,
            target_pid,
            target_lane,
            prompt_draft="Source.",
        )
        anchor = _add_virtual(
            self.store,
            target_pid,
            target_lane,
            prompt_draft="Ignored anchor.",
        )
        template = _function_template(
            Path("/templates/compare"),
            prompts=[
                "Topic={{topic}} source={{input.source}}",
                "Finish {{style}}.",
            ],
            deps=[["in:source"], ["n0"]],
            arguments=[
                TemplateArgument(name="topic"),
                TemplateArgument(name="style", default="brief"),
            ],
            inputs=[TemplateInput(name="source")],
            name="Compare",
        )

        created = apply_user_template(
            template,
            target_project,
            self.registry,
            anchor_node_id=anchor.id,
            arguments={"topic": r"literal \1 and \g<0>"},
            input_bindings={"source": source.id},
        )

        first, second = created
        expected_path = (
            f".miniclaw2/graph/runs/{first.id}/lanes/{target_lane}/nodes/"
            f"{source.id}/preview.json"
        )
        self.assertEqual(
            first.prompt_draft,
            rf"Topic=literal \1 and \g<0> source={expected_path}",
        )
        self.assertEqual(first.scheduled_deps, [source.id])
        self.assertNotIn(anchor.id, first.scheduled_deps)
        self.assertEqual(second.scheduled_deps, [first.id])
        self.assertEqual(second.prompt_draft, "Finish brief.")
        self.assertIsNotNone(first.template_instance_id)
        self.assertEqual(second.template_instance_id, first.template_instance_id)

        records = read_template_instances(
            target_project,
            target_lane,
            store_root=self.store.root,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["instance_id"], first.template_instance_id)
        self.assertEqual(records[0]["template_slug"], "compare")
        self.assertEqual(
            records[0]["arguments"],
            {"topic": r"literal \1 and \g<0>", "style": "brief"},
        )
        self.assertEqual(records[0]["input_bindings"], {"source": source.id})
        self.assertIsNone(records[0]["parent_instance_id"])

    def test_inserted_placeholder_is_not_expanded_and_fails_leak_guard(self) -> None:
        target_pid, target_lane = _make_project_with_lane(self.registry)
        target_project = self.registry.get_project(target_pid)
        assert target_project is not None
        template = _function_template(
            Path("/templates/one-pass"),
            prompts=["Value: {{topic}}"],
            arguments=[
                TemplateArgument(name="topic"),
                TemplateArgument(name="other"),
            ],
        )
        before = {node.id for node in self.store.list_nodes(target_pid)}

        with self.assertRaisesRegex(TemplateError, "unresolved template argument: other"):
            apply_user_template(
                template,
                target_project,
                self.registry,
                arguments={"topic": "{{other}}", "other": "expanded"},
            )

        self.assertEqual(
            {node.id for node in self.store.list_nodes(target_pid)},
            before,
        )
        self.assertEqual(
            read_template_instances(
                target_project,
                target_lane,
                store_root=self.store.root,
            ),
            [],
        )

    def test_default_empty_string_is_optional_but_required_argument_is_not(self) -> None:
        target_pid, _ = _make_project_with_lane(self.registry)
        target_project = self.registry.get_project(target_pid)
        assert target_project is not None
        optional = _function_template(
            Path("/templates/optional"),
            prompts=["Value={{value}}"],
            arguments=[TemplateArgument(name="value", default="")],
        )
        created = apply_user_template(optional, target_project, self.registry)
        self.assertEqual(created[0].prompt_draft, "Value=")

        required = _function_template(
            Path("/templates/required"),
            prompts=["Value={{value}}"],
            arguments=[TemplateArgument(name="value")],
        )
        with self.assertRaisesRegex(TemplateError, "missing required"):
            apply_user_template(required, target_project, self.registry)

    def test_unknown_argument_and_input_names_are_rejected(self) -> None:
        target_pid, target_lane = _make_project_with_lane(self.registry)
        target_project = self.registry.get_project(target_pid)
        assert target_project is not None
        source = _add_virtual(self.store, target_pid, target_lane, prompt_draft="Source")
        template = _function_template(
            Path("/templates/known-names"),
            prompts=["{{topic}}"],
            arguments=[TemplateArgument(name="topic")],
            inputs=[TemplateInput(name="source")],
        )
        with self.assertRaisesRegex(TemplateError, "unknown template argument"):
            apply_user_template(
                template,
                target_project,
                self.registry,
                arguments={"topic": "ok", "extra": "no"},
                input_bindings={"source": source.id},
            )
        with self.assertRaisesRegex(TemplateError, "unknown template input"):
            apply_user_template(
                template,
                target_project,
                self.registry,
                arguments={"topic": "ok"},
                input_bindings={"source": source.id, "extra": source.id},
            )

    def test_input_binding_cycle_is_rejected_before_any_node_is_created(self) -> None:
        target_pid, target_lane = _make_project_with_lane(self.registry)
        target_project = self.registry.get_project(target_pid)
        assert target_project is not None
        future_node_id = "pending00001"
        source = _add_virtual(
            self.store,
            target_pid,
            target_lane,
            prompt_draft="Source.",
            scheduled_deps=[future_node_id],
        )
        template = _function_template(
            Path("/templates/cycle"),
            prompts=["Consume source."],
            deps=[["in:source"]],
            inputs=[TemplateInput(name="source")],
        )
        before = {node.id for node in self.store.list_nodes(target_pid)}

        with patch(
            "miniclaw2.domain.uuid4",
            return_value=SimpleNamespace(hex=future_node_id),
        ):
            with self.assertRaisesRegex(TemplateError, "introduce a cycle"):
                apply_user_template(
                    template,
                    target_project,
                    self.registry,
                    input_bindings={"source": source.id},
                )

        self.assertEqual(
            {node.id for node in self.store.list_nodes(target_pid)},
            before,
        )

    def test_old_persisted_node_without_template_instance_id_still_loads(self) -> None:
        target_pid, target_lane = _make_project_with_lane(self.registry)
        node = _add_virtual(
            self.store,
            target_pid,
            target_lane,
            prompt_draft="Old node.",
        )
        node_file = self.store.node_dir(target_pid, node.id) / "node.json"
        payload = json.loads(node_file.read_text(encoding="utf-8"))
        payload.pop("template_instance_id")
        node_file.write_text(json.dumps(payload), encoding="utf-8")

        loaded = self.store.load_node(target_pid, node.id)
        assert loaded is not None
        self.assertIsNone(loaded.template_instance_id)


class UserTemplateHttpApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        from miniclaw2.app import create_app

        self.registry, self.store = _seeded_registry(Path(self._home.name))
        self.client = TestClient(create_app(self.registry))

    def tearDown(self) -> None:
        self.client.close()
        os.environ.pop("MINICLAW_HOME", None)
        self._home.cleanup()

    def test_save_apply_and_delete_round_trip(self) -> None:
        # Set up a project with a couple of virtuals in the active lane.
        launched = self.client.post(
            "/templates/hello-text/run", json={"model_preset_id": "opus-4-8"}
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
            "/templates/hello-text/run", json={"model_preset_id": "opus-4-8"}
        )
        sid = launched.json()["id"]
        # Empty selection.
        res = self.client.post(
            f"/sessions/{sid}/user-templates",
            json={"name": "Bad", "brief": "", "node_ids": []},
        )
        self.assertEqual(res.status_code, 400)

    def test_rewrite_round_trips_complete_editor_state(self) -> None:
        _write_user_function_template(
            self.store,
            "editable",
            prompt="Old prompt.",
        )
        payload = {
            "name": "Edited template",
            "brief": "Edited through the template canvas.",
            "nodes": [
                {
                    "id": "first",
                    "kind": "agent",
                    "category": "regular",
                    "subtype": None,
                    "brief": None,
                    "prompt": "Topic={{topic}} source={{input.source}}",
                    "motivation": "Compare the two source branches.",
                    "scheduled_deps": ["in:source"],
                    "resume_from": None,
                },
                {
                    "id": "second",
                    "kind": "agent",
                    "category": "planning",
                    "subtype": None,
                    "brief": None,
                    "prompt": (
                        "Continue {{required_missing}} {{required_null}}"
                        " {{discovered}}."
                    ),
                    "scheduled_deps": ["first"],
                    "resume_from": "first",
                },
            ],
            "arguments": [
                {
                    "name": "topic",
                    "description": "Comparison topic",
                    "default": "",
                },
                {"name": "required_missing", "description": "Missing default"},
                {
                    "name": "required_null",
                    "description": "Explicit null",
                    "default": None,
                },
            ],
            "inputs": [
                {"name": "source", "description": "Upstream source"}
            ],
        }

        response = self.client.put("/user-templates/editable", json=payload)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["slug"], "editable")
        self.assertEqual(body["name"], "Edited template")
        self.assertEqual(body["nodes"][0]["prompt"], payload["nodes"][0]["prompt"])
        self.assertEqual(
            body["nodes"][0]["motivation"],
            "Compare the two source branches.",
        )
        self.assertEqual(
            [argument["name"] for argument in body["arguments"]],
            ["topic", "required_missing", "required_null", "discovered"],
        )
        self.assertFalse(body["arguments"][0]["required"])
        self.assertTrue(all(arg["required"] for arg in body["arguments"][1:]))

        loaded = load_user_template("editable", self.store.root)
        self.assertEqual(loaded.name, "Edited template")
        self.assertEqual(loaded.nodes[0].scheduled_deps, ["in:source"])
        self.assertEqual(
            loaded.nodes[0].summary,
            "Compare the two source branches.",
        )
        self.assertEqual(loaded.nodes[1].resume_from, "first")
        self.assertEqual(loaded.arguments[0].default, "")
        self.assertTrue(all(argument.declared for argument in loaded.arguments))

        listed = self.client.get("/user-templates")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()[0]["slug"], "editable")
        self.assertEqual(listed.json()[0]["name"], "Edited template")

        detail = self.client.get("/user-templates/editable")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["slug"], "editable")

    def test_rewrite_validation_failure_preserves_existing_directory(self) -> None:
        _write_user_function_template(
            self.store,
            "atomic",
            prompt="Original prompt that must survive.",
        )
        root = user_templates_root(self.store.root) / "atomic"

        def snapshot() -> dict[str, bytes]:
            return {
                str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

        before = snapshot()
        response = self.client.put(
            "/user-templates/atomic",
            json={
                "name": "Broken",
                "brief": "Unknown dependency.",
                "nodes": [
                    {
                        "id": "n0",
                        "kind": "agent",
                        "category": "regular",
                        "prompt": "Broken prompt.",
                        "scheduled_deps": ["missing"],
                    }
                ],
                "arguments": [],
                "inputs": [],
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(snapshot(), before)
        self.assertEqual(
            sorted(path.name for path in root.parent.iterdir()),
            ["atomic"],
        )
        self.assertEqual(
            load_user_template("atomic", self.store.root).nodes[0].prompt,
            "Original prompt that must survive.",
        )

    def test_rewrite_rejects_verifier_and_invalid_slug(self) -> None:
        _write_user_function_template(self.store, "guarded", prompt="Keep me.")
        verifier_response = self.client.put(
            "/user-templates/guarded",
            json={
                "name": "Verifier",
                "nodes": [
                    {
                        "id": "check",
                        "kind": "verifier",
                        "category": "review",
                        "prompt": "",
                    }
                ],
            },
        )
        self.assertEqual(verifier_response.status_code, 400, verifier_response.text)
        self.assertIn("只能包含 agent", verifier_response.json()["detail"])
        self.assertEqual(
            load_user_template("guarded", self.store.root).nodes[0].prompt,
            "Keep me.",
        )

        invalid_slug_response = self.client.put(
            "/user-templates/bad..slug",
            json={"name": "Bad slug", "nodes": []},
        )
        self.assertEqual(invalid_slug_response.status_code, 400)
        self.assertIn("slug 非法", invalid_slug_response.json()["detail"])

    def test_detail_includes_full_prompt_but_list_stays_compact(self) -> None:
        prompt = "Start " + ("complete prompt source " * 20) + "{{topic}} end"
        _write_user_function_template(
            self.store,
            "long-prompt",
            prompt=prompt,
        )

        detail = self.client.get("/user-templates/long-prompt")
        listed = self.client.get("/user-templates")

        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["nodes"][0]["prompt"], prompt)
        self.assertLessEqual(
            len(detail.json()["nodes"][0]["prompt_preview"]),
            160,
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        listed_node = listed.json()[0]["nodes"][0]
        self.assertNotIn("prompt", listed_node)
        self.assertLessEqual(len(listed_node["prompt_preview"]), 160)

    def test_apply_rejects_non_native_project_without_creating_nodes(self) -> None:
        launched = self.client.post(
            "/templates/hello-text/run", json={"model_preset_id": "opus-4-8"}
        )
        self.assertEqual(launched.status_code, 200, launched.text)
        sid = launched.json()["id"]

        nodes = self.store.list_nodes(sid)
        agent_node = next(node for node in nodes if node.kind is NodeKind.AGENT)
        saved = self.client.post(
            f"/sessions/{sid}/user-templates",
            json={
                "name": "Foreign guard",
                "brief": "Must not be stamped into a foreign project.",
                "node_ids": [agent_node.id],
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        project = self.registry.get_project(sid)
        assert project is not None
        project.machine_id = "remote-machine-id"
        project.machine_label = "remote-host"
        self.store.update_project(project)
        node_ids_before = [node.id for node in self.store.list_nodes(sid)]

        response = self.client.post(
            f"/sessions/{sid}/user-templates/foreign-guard/apply",
            json={"anchor_node_id": None},
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("remote-host", response.json()["detail"])
        self.assertEqual(
            [node.id for node in self.store.list_nodes(sid)],
            node_ids_before,
        )

    def test_apply_validates_arguments_and_input_bindings_and_lists_instance(self) -> None:
        sid, lane = _make_project_with_lane(self.registry)
        source = _add_virtual(self.store, sid, lane, prompt_draft="Source.")
        project = self.registry.get_project(sid)
        assert project is not None
        other_lane = create_planspace(
            project,
            title="other-lane",
            store_root=self.store.root,
            seed_text="other",
        )
        cross_lane = _add_virtual(
            self.store,
            sid,
            other_lane,
            prompt_draft="Other lane.",
        )
        _write_user_function_template(
            self.store,
            "parameterized",
            prompt="{{topic}}/{{optional}}/{{input.source}}",
            arguments=[
                {"name": "topic"},
                {"name": "optional", "default": ""},
            ],
            inputs=[{"name": "source"}],
            scheduled_deps=["in:source"],
        )
        url = f"/sessions/{sid}/user-templates/parameterized/apply"
        before = {node.id for node in self.store.list_nodes(sid)}

        missing_argument = self.client.post(
            url,
            json={"input_bindings": {"source": source.id}},
        )
        self.assertEqual(missing_argument.status_code, 400, missing_argument.text)
        self.assertIn("missing required", missing_argument.json()["detail"])

        missing_binding = self.client.post(
            url,
            json={"arguments": {"topic": "x"}},
        )
        self.assertEqual(missing_binding.status_code, 400, missing_binding.text)

        nonexistent = self.client.post(
            url,
            json={
                "arguments": {"topic": "x"},
                "input_bindings": {"source": "not-a-node"},
            },
        )
        self.assertEqual(nonexistent.status_code, 400, nonexistent.text)
        self.assertIn("does not exist", nonexistent.json()["detail"])

        cross_lane_response = self.client.post(
            url,
            json={
                "arguments": {"topic": "x"},
                "input_bindings": {"source": cross_lane.id},
            },
        )
        self.assertEqual(cross_lane_response.status_code, 400, cross_lane_response.text)
        self.assertIn("outside the active planspace", cross_lane_response.json()["detail"])
        self.assertEqual({node.id for node in self.store.list_nodes(sid)}, before)

        applied = self.client.post(
            url,
            json={
                "arguments": {"topic": "x"},
                "input_bindings": {"source": source.id},
            },
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        body = applied.json()
        self.assertTrue(body["instance_id"])
        self.assertEqual(len(body["node_ids"]), 1)

        node_response = self.client.get(
            f"/sessions/{sid}/nodes/{body['node_ids'][0]}"
        )
        self.assertEqual(node_response.status_code, 200, node_response.text)
        self.assertEqual(
            node_response.json()["template_instance_id"],
            body["instance_id"],
        )
        self.assertIn(
            f"x//.miniclaw2/graph/runs/{body['node_ids'][0]}/lanes/",
            node_response.json()["prompt_draft"],
        )

        records_response = self.client.get(
            f"/sessions/{sid}/planspaces/{lane}/template-instances"
        )
        self.assertEqual(records_response.status_code, 200, records_response.text)
        self.assertEqual(records_response.json()[0]["instance_id"], body["instance_id"])
        self.assertEqual(
            records_response.json()[0]["arguments"],
            {"topic": "x", "optional": ""},
        )

    def test_apply_returns_400_when_input_binding_closes_a_cycle(self) -> None:
        sid, lane = _make_project_with_lane(self.registry)
        future_node_id = "pending00001"
        source = _add_virtual(
            self.store,
            sid,
            lane,
            prompt_draft="Source.",
            scheduled_deps=[future_node_id],
        )
        _write_user_function_template(
            self.store,
            "cycle",
            prompt="Consume source.",
            inputs=[{"name": "source"}],
            scheduled_deps=["in:source"],
        )
        before = {node.id for node in self.store.list_nodes(sid)}

        with patch(
            "miniclaw2.domain.uuid4",
            return_value=SimpleNamespace(hex=future_node_id),
        ):
            response = self.client.post(
                f"/sessions/{sid}/user-templates/cycle/apply",
                json={"input_bindings": {"source": source.id}},
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("introduce a cycle", response.json()["detail"])
        self.assertEqual({node.id for node in self.store.list_nodes(sid)}, before)


if __name__ == "__main__":
    unittest.main()
