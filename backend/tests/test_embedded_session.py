"""Embedded template sessions: definition → real project → definition.

The round-trip test here is the reason this path exists. Wiring the existing
``launch_template`` and ``serialize_selection`` end to end *looks* equivalent
but silently destroys the template: ``_render_prompt`` replaces every
``{{placeholder}}`` with a literal value and ``serialize_selection`` hardcodes
``inputs: []``, so one lap leaves the prompt holding a hardcoded absolute path
with no arguments and no ports. These tests pin the lossless behaviour down.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from fastapi.testclient import TestClient

from miniclaw2.contextspace import read_template_ports
from miniclaw2.domain import NodeState
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store
from miniclaw2.templates import TemplateError, user_templates_root
from miniclaw2.templates.launcher import (
    EMBEDDED_SESSION_PREFIX,
    embedded_session_slug,
    materialize_embedded_session,
)
from miniclaw2.templates.loader import SCHEMA_VERSION, load_user_template
from miniclaw2.templates.serializer import (
    SerializerError,
    serialize_embedded_session,
)


DEFINITION_PROMPT_A = "Review {{focus}} against {{input.spec}} and report.\n"
DEFINITION_PROMPT_B = "Summarize the review for {{audience}}.\n"


def _write_user_template(
    store_root: Path,
    slug: str,
    *,
    name: str = "Review Flow",
    brief: str = "Two-step review.",
    arguments: list[dict] | None = None,
    inputs: list[dict] | None = None,
    nodes: list[dict] | None = None,
    prompts: dict[str, str] | None = None,
    template_overrides: dict | None = None,
    seed_files: dict[str, str] | None = None,
) -> None:
    root = user_templates_root(store_root) / slug
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    template_yaml = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "brief": brief,
        "lane_mode": "manual",
        "arguments": arguments
        if arguments is not None
        else [
            {"name": "focus", "description": "what to review", "default": None},
            {"name": "audience", "description": "who reads it", "default": "team"},
        ],
        "inputs": inputs
        if inputs is not None
        else [{"name": "spec", "description": "the spec node"}],
    }
    template_yaml.update(template_overrides or {})
    lane_yaml = {
        "nodes": nodes
        if nodes is not None
        else [
            {
                "id": "n0",
                "kind": "agent",
                "category": "regular",
                "prompt_file": "prompts/n0.md",
                "scheduled_deps": ["in:spec"],
                "motivation": "look at the spec",
            },
            {
                "id": "n1",
                "kind": "agent",
                "category": "regular",
                "prompt_file": "prompts/n1.md",
                "scheduled_deps": ["n0"],
                "motivation": "write it up",
            },
        ]
    }
    (root / "template.yaml").write_text(
        yaml.safe_dump(template_yaml, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (root / "lane.yaml").write_text(
        yaml.safe_dump(lane_yaml, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    for rel, text in (
        prompts
        if prompts is not None
        else {"prompts/n0.md": DEFINITION_PROMPT_A, "prompts/n1.md": DEFINITION_PROMPT_B}
    ).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    for rel, text in (seed_files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


class EmbeddedSessionMaterializeTests(unittest.TestCase):
    def test_verifier_without_inputs_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            root = user_templates_root(registry.store.root) / "verifier-flow"
            (root / "scripts").mkdir(parents=True, exist_ok=True)
            (root / "template.yaml").write_text(
                yaml.safe_dump(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "name": "Verifier Flow",
                        "brief": "Verifier-only flow.",
                        "lane_mode": "manual",
                        "arguments": [],
                        "inputs": [],
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
                                "id": "verify",
                                "kind": "verifier",
                                "script_ref": "scripts/check.py",
                                "brief": {
                                    "check_what": "output",
                                    "expected": "valid",
                                    "abnormal": "invalid",
                                },
                            }
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (root / "scripts" / "check.py").write_text("raise SystemExit(0)\n")

            template = load_user_template("verifier-flow", registry.store.root)
            with self.assertRaisesRegex(TemplateError, "agent-only"):
                materialize_embedded_session(template, registry)

            self.assertEqual(registry.list_projects(), [])

    def test_placeholders_survive_materialization(self) -> None:
        """The direct assertion of "do not render": the whole path rests on it."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")
            template = load_user_template("review-flow", registry.store.root)

            project, lane = materialize_embedded_session(template, registry)

            nodes = [
                node
                for node in registry.store.list_nodes(project.id)
                if (node.planspace_id or "") == lane
            ]
            self.assertEqual(len(nodes), 2)
            prompts = sorted((node.prompt_draft or "") for node in nodes)
            self.assertIn("{{focus}}", prompts[0] + prompts[1])
            self.assertIn("{{input.spec}}", prompts[0] + prompts[1])
            self.assertIn("{{audience}}", prompts[0] + prompts[1])
            for node in nodes:
                self.assertEqual(node.state, NodeState.VIRTUAL)
                self.assertNotIn("preview.json", node.prompt_draft or "")

    def test_ports_land_on_the_manifest_not_in_scheduled_deps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")
            template = load_user_template("review-flow", registry.store.root)

            project, lane = materialize_embedded_session(template, registry)

            ports = read_template_ports(project, lane, store_root=registry.store.root)
            self.assertEqual(len(ports), 1)
            self.assertEqual(ports[0]["name"], "spec")
            self.assertEqual(ports[0]["description"], "the spec node")
            self.assertEqual(len(ports[0]["consumers"]), 1)

            for node in registry.store.list_nodes(project.id):
                for dep in node.scheduled_deps:
                    self.assertFalse(dep.startswith("in:"), dep)

    def test_session_is_temporary_and_tagged_with_the_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")
            template = load_user_template("review-flow", registry.store.root)

            project, _lane = materialize_embedded_session(template, registry)

            self.assertTrue(project.temporary)
            # Prefixed, not bare: a bundled test run stores `template.name`, and
            # every bundled template's display name equals its directory name,
            # so a bare value could not distinguish the two.
            self.assertEqual(project.template_id, "embedded:review-flow")
            self.assertEqual(
                embedded_session_slug(project.template_id), "review-flow"
            )
            self.assertIsNone(embedded_session_slug("review-flow"))

    def test_internal_deps_are_still_translated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")
            template = load_user_template("review-flow", registry.store.root)

            project, lane = materialize_embedded_session(template, registry)

            nodes = {
                node.id: node
                for node in registry.store.list_nodes(project.id)
                if (node.planspace_id or "") == lane
            }
            dependents = [n for n in nodes.values() if n.scheduled_deps]
            self.assertEqual(len(dependents), 1)
            self.assertEqual(len(dependents[0].scheduled_deps), 1)
            self.assertIn(dependents[0].scheduled_deps[0], nodes)

    def test_failure_cleans_up_the_temporary_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(
                registry.store.root,
                "broken",
                nodes=[
                    {
                        "id": "n0",
                        "kind": "agent",
                        "category": "regular",
                        "prompt_file": "prompts/n0.md",
                        "model_preset_id": "gpt-5.5",
                    }
                ],
                prompts={"prompts/n0.md": "Hello.\n"},
                inputs=[],
                arguments=[],
            )
            template = load_user_template("broken", registry.store.root)
            before = len(registry.list_projects())

            with self.assertRaises(TemplateError):
                materialize_embedded_session(template, registry)

            self.assertEqual(len(registry.list_projects()), before)


class EmbeddedSessionRoundTripTests(unittest.TestCase):
    def test_definition_survives_a_full_round_trip(self) -> None:
        """Core acceptance: definition → embedded project → definition, lossless."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")
            before = load_user_template("review-flow", registry.store.root)

            project, _lane = materialize_embedded_session(before, registry)
            serialize_embedded_session(registry, project, "review-flow")
            after = load_user_template("review-flow", registry.store.root)

            self.assertEqual(after.name, before.name)
            self.assertEqual(after.brief, before.brief)
            self.assertEqual(
                [(a.name, a.description, a.default) for a in after.arguments],
                [(a.name, a.description, a.default) for a in before.arguments],
            )
            self.assertEqual(
                [(i.name, i.description) for i in after.inputs],
                [(i.name, i.description) for i in before.inputs],
            )
            self.assertEqual(len(after.nodes), len(before.nodes))
            for original, restored in zip(before.nodes, after.nodes):
                self.assertEqual(restored.prompt, original.prompt)
                self.assertEqual(restored.kind, original.kind)
                self.assertEqual(restored.category, original.category)
                self.assertEqual(restored.summary, original.summary)
                self.assertEqual(
                    sorted(restored.scheduled_deps or []),
                    sorted(original.scheduled_deps or []),
                )

    def test_port_dependency_survives_the_round_trip(self) -> None:
        """`in:spec` is the edge most at risk: it lives only on the manifest."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")

            project, _lane = materialize_embedded_session(
                load_user_template("review-flow", registry.store.root), registry
            )
            serialize_embedded_session(registry, project, "review-flow")
            after = load_user_template("review-flow", registry.store.root)

            port_deps = [
                dep
                for node in after.nodes
                for dep in (node.scheduled_deps or [])
                if dep.startswith("in:")
            ]
            self.assertEqual(port_deps, ["in:spec"])
            self.assertEqual([i.name for i in after.inputs], ["spec"])

    def test_template_settings_and_seed_assets_survive_the_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(
                registry.store.root,
                "review-flow",
                template_overrides={
                    "allowed_model_preset_ids": ["opus-4-8"],
                    "auto_commit": True,
                    "permission_mode": "acceptEdits",
                    "lane_mode": "auto",
                    "seed": [
                        {"from": "seed/config.txt", "to": "config/default.txt"}
                    ],
                },
                seed_files={"seed/config.txt": "preserve me\n"},
            )
            before = load_user_template("review-flow", registry.store.root)

            project, _lane = materialize_embedded_session(before, registry)
            serialize_embedded_session(registry, project, "review-flow")
            after = load_user_template("review-flow", registry.store.root)

            self.assertEqual(after.allowed_model_preset_ids, ["opus-4-8"])
            self.assertTrue(after.auto_commit)
            self.assertEqual(after.permission_mode, "acceptEdits")
            self.assertEqual(after.lane_mode.value, "auto")
            self.assertEqual(len(after.seed), 1)
            self.assertEqual(after.seed[0][1], "config/default.txt")
            self.assertEqual(after.seed[0][0].read_text(encoding="utf-8"), "preserve me\n")

    def test_inherited_model_and_authored_motivation_survive_a_test_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")
            before = load_user_template("review-flow", registry.store.root)
            self.assertIsNone(before.nodes[0].model_preset_id)

            project, lane = materialize_embedded_session(before, registry)
            target = next(
                node
                for node in registry.store.list_nodes(project.id)
                if (node.planspace_id or "") == lane and not node.scheduled_deps
            )
            self.assertIsNotNone(target.model_preset_id)
            self.assertEqual(target.template_source_motivation, "look at the spec")
            target.state = NodeState.DONE
            target.prompt = target.prompt_draft or ""
            target.prompt_draft = None
            target.summary = "runtime result summary"
            target.started_at = target.created_at
            target.finished_at = target.created_at + 1
            registry.store.update_node(target)

            serialize_embedded_session(registry, project, "review-flow")
            after = load_user_template("review-flow", registry.store.root)

            self.assertIsNone(after.nodes[0].model_preset_id)
            self.assertEqual(after.nodes[0].summary, "look at the spec")

    def test_virtual_edits_update_authored_model_and_motivation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")
            project, lane = materialize_embedded_session(
                load_user_template("review-flow", registry.store.root), registry
            )
            target = next(
                node
                for node in registry.store.list_nodes(project.id)
                if (node.planspace_id or "") == lane and not node.scheduled_deps
            )

            updated = registry.update_virtual(
                project.id,
                target.id,
                motivation="new authored motivation",
                model_preset_id="opus-4-8",
            )
            self.assertIsNotNone(updated)
            serialize_embedded_session(registry, project, "review-flow")
            after = load_user_template("review-flow", registry.store.root)

            self.assertEqual(after.nodes[0].model_preset_id, "opus-4-8")
            self.assertEqual(after.nodes[0].summary, "new authored motivation")

    def test_two_round_trips_are_stable(self) -> None:
        """A second lap must not drift — otherwise loss is merely slower."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")

            snapshots = []
            for _ in range(2):
                project, _lane = materialize_embedded_session(
                    load_user_template("review-flow", registry.store.root), registry
                )
                serialize_embedded_session(registry, project, "review-flow")
                template = load_user_template("review-flow", registry.store.root)
                snapshots.append(
                    (
                        [(n.id, n.prompt, tuple(n.scheduled_deps or [])) for n in template.nodes],
                        [(a.name, a.default) for a in template.arguments],
                        [(i.name, i.description) for i in template.inputs],
                    )
                )
            self.assertEqual(snapshots[0], snapshots[1])

    def test_edits_in_the_session_reach_the_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")

            project, lane = materialize_embedded_session(
                load_user_template("review-flow", registry.store.root), registry
            )
            target = next(
                node
                for node in registry.store.list_nodes(project.id)
                if (node.planspace_id or "") == lane and not node.scheduled_deps
            )
            registry.update_virtual(
                project.id,
                target.id,
                prompt_draft="Look at {{focus}} plus {{extra}} now.\n",
            )

            serialize_embedded_session(registry, project, "review-flow")
            after = load_user_template("review-flow", registry.store.root)

            prompts = [node.prompt for node in after.nodes]
            self.assertTrue(any("{{extra}}" in text for text in prompts))
            # A newly scanned placeholder becomes a declared argument, matching
            # the loader's own scan-then-declare behaviour.
            self.assertIn("extra", [argument.name for argument in after.arguments])

    def test_a_promoted_node_saves_its_prompt_not_an_empty_string(self) -> None:
        """Promotion moves prompt_draft into prompt and clears the draft.

        Goes through the real `promote_virtual` rather than hand-editing the
        fields, so the write-back's state-dependent prompt read is pinned to the
        runtime's actual behaviour.
        """
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")

            project, lane = materialize_embedded_session(
                load_user_template("review-flow", registry.store.root), registry
            )
            target = next(
                node
                for node in registry.store.list_nodes(project.id)
                if (node.planspace_id or "") == lane and not node.scheduled_deps
            )
            promoted = registry.promote_virtual_result(project.id, target.id)
            self.assertIsNotNone(promoted.node, promoted.message)
            moved = registry.store.load_node(project.id, target.id)
            assert moved is not None
            self.assertIsNone(moved.prompt_draft)
            self.assertIn("{{focus}}", moved.prompt)

            serialize_embedded_session(registry, project, "review-flow")
            after = load_user_template("review-flow", registry.store.root)

            self.assertTrue(
                all(node.prompt.strip() for node in after.nodes),
                "a promoted node must not save an empty prompt",
            )
            self.assertTrue(any("{{focus}}" in node.prompt for node in after.nodes))

    def test_written_yaml_carries_no_derived_argument_keys(self) -> None:
        """`required`/`declared` are UI-derived; writing them back is drift.

        The editor's own write schema forbids extra keys, so a committed template
        that grew them would be one the editor could no longer save.
        """
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")

            project, _lane = materialize_embedded_session(
                load_user_template("review-flow", registry.store.root), registry
            )
            serialize_embedded_session(registry, project, "review-flow")

            written = yaml.safe_load(
                (
                    user_templates_root(registry.store.root)
                    / "review-flow"
                    / "template.yaml"
                ).read_text(encoding="utf-8")
            )
            for argument in written["arguments"]:
                self.assertEqual(
                    sorted(argument.keys()),
                    ["default", "description", "name"],
                    argument,
                )
            for port in written["inputs"]:
                self.assertEqual(sorted(port.keys()), ["description", "name"], port)

    def test_frontend_shares_the_session_marker_prefix(self) -> None:
        """The canvas reads `template_id` to decide on template affordances.

        A silent divergence here would leave the backend tagging sessions the
        frontend no longer recognizes, with no failure anywhere until someone
        noticed the ports had stopped rendering.
        """
        types_ts = (
            Path(__file__).resolve().parents[2]
            / "frontend"
            / "src"
            / "types.ts"
        )
        source = types_ts.read_text(encoding="utf-8")
        self.assertIn(
            f'export const EMBEDDED_SESSION_PREFIX = "{EMBEDDED_SESSION_PREFIX}";',
            source,
        )

    def test_session_without_a_direction_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            _write_user_template(registry.store.root, "review-flow")
            project, _lane = materialize_embedded_session(
                load_user_template("review-flow", registry.store.root), registry
            )
            project.active_planspace_id = None
            registry.store.update_project(project)

            with self.assertRaises(SerializerError):
                serialize_embedded_session(registry, project, "review-flow")


class EmbeddedSessionHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        from miniclaw2.app import create_app

        self.registry = ProjectRegistry(store=Store(root=Path(self._home.name)))
        self.client = TestClient(create_app(self.registry))
        _write_user_template(self.registry.store.root, "review-flow")

    def tearDown(self) -> None:
        self.client.close()
        os.environ.pop("MINICLAW_HOME", None)
        self._home.cleanup()

    def test_open_edit_commit_discard_round_trip(self) -> None:
        opened = self.client.post("/user-templates/review-flow/session")
        self.assertEqual(opened.status_code, 200, opened.text)
        session = opened.json()
        self.assertTrue(session["temporary"])
        sid = session["id"]

        nodes = self.client.get(f"/sessions/{sid}/nodes").json()
        agents = [node for node in nodes if node["kind"] == "agent"]
        self.assertEqual(len(agents), 2)
        self.assertTrue(
            any("{{focus}}" in (node.get("prompt_draft") or "") for node in agents)
        )

        target = next(node for node in agents if not node["scheduled_deps"])
        patched = self.client.patch(
            f"/sessions/{sid}/virtuals/{target['id']}",
            json={"prompt_draft": "Now reviewing {{focus}} and {{scope}}.\n"},
        )
        self.assertEqual(patched.status_code, 200, patched.text)

        committed = self.client.post("/user-templates/review-flow/session/commit")
        self.assertEqual(committed.status_code, 200, committed.text)
        detail = committed.json()
        self.assertIn("scope", [argument["name"] for argument in detail["arguments"]])
        self.assertEqual([port["name"] for port in detail["inputs"]], ["spec"])

        fetched = self.client.get("/user-templates/review-flow")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual([p["name"] for p in fetched.json()["inputs"]], ["spec"])

        discarded = self.client.delete("/user-templates/review-flow/session")
        self.assertEqual(discarded.status_code, 204, discarded.text)
        self.assertIsNone(self.registry.get_project(sid))

    def test_reopening_returns_the_same_session(self) -> None:
        """A second open must not strand the first session's unsaved edits."""
        first = self.client.post("/user-templates/review-flow/session")
        second = self.client.post("/user-templates/review-flow/session")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_unknown_template_is_404(self) -> None:
        res = self.client.post("/user-templates/nope/session")
        self.assertEqual(res.status_code, 404, res.text)

    def test_commit_and_discard_without_a_session_are_404(self) -> None:
        commit = self.client.post("/user-templates/review-flow/session/commit")
        self.assertEqual(commit.status_code, 404, commit.text)
        discard = self.client.delete("/user-templates/review-flow/session")
        self.assertEqual(discard.status_code, 404, discard.text)

    def test_a_bundled_run_is_not_mistaken_for_a_session(self) -> None:
        """`launch_template` tags projects with the display name, not the slug."""
        launched = self.client.post(
            "/templates/hello-text/run", json={"model_preset_id": "opus-4-8"}
        )
        self.assertEqual(launched.status_code, 200, launched.text)

        commit = self.client.post("/user-templates/hello-text/session/commit")
        self.assertEqual(commit.status_code, 404, commit.text)


    def test_ports_reach_the_frontend_through_the_contextspace_summary(self) -> None:
        """The canvas reads ports from the summary it already fetches on load."""
        opened = self.client.post("/user-templates/review-flow/session")
        sid = opened.json()["id"]

        described = self.client.get(f"/sessions/{sid}/contextspace")
        self.assertEqual(described.status_code, 200, described.text)
        ports = described.json()["template_ports"]
        self.assertEqual([port["name"] for port in ports], ["spec"])
        self.assertEqual(ports[0]["description"], "the spec node")
        self.assertEqual(len(ports[0]["consumers"]), 1)

    def test_an_ordinary_project_reports_no_ports(self) -> None:
        """The zero-impact guarantee at the API boundary."""
        launched = self.client.post(
            "/templates/hello-text/run", json={"model_preset_id": "opus-4-8"}
        )
        sid = launched.json()["id"]

        described = self.client.get(f"/sessions/{sid}/contextspace")
        self.assertEqual(described.json()["template_ports"], [])


if __name__ == "__main__":
    unittest.main()
