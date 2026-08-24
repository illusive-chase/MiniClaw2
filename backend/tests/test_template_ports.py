"""Input ports stored on a planspace manifest (embedded template sessions).

The manifest is an untyped YAML dict, so the shape checks here stand in for
the schema validation a pydantic model would have given for free.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from miniclaw2.contextspace import (
    contextspace_root,
    create_planspace,
    read_template_ports,
    write_template_ports,
)
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


def _project_with_lane(registry: ProjectRegistry):
    project = registry.create_project(
        cwd=None, model_preset_id="opus-4-8", temporary=True
    )
    lane = create_planspace(
        project,
        title="ports-lane",
        store_root=registry.store.root,
    )
    project.active_planspace_id = lane
    registry.store.update_project(project)
    return project, lane


class TemplatePortManifestTests(unittest.TestCase):
    def test_write_then_read_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            self.assertEqual(
                read_template_ports(project, lane, store_root=registry.store.root),
                [],
            )

            write_template_ports(
                project,
                lane,
                [
                    {"name": "spec", "description": "the spec to review"},
                    {"name": "notes"},
                ],
                store_root=registry.store.root,
            )

            self.assertEqual(
                read_template_ports(project, lane, store_root=registry.store.root),
                [
                    {
                        "name": "spec",
                        "description": "the spec to review",
                        "consumers": [],
                    },
                    # An absent description normalizes to "" so callers never
                    # have to distinguish missing from empty.
                    {"name": "notes", "description": "", "consumers": []},
                ],
            )

    def test_consumers_carry_the_port_edge(self) -> None:
        """`in:<port>` cannot live in scheduled_deps, so the edge lives here."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            write_template_ports(
                project,
                lane,
                [{"name": "spec", "consumers": ["node-a", "node-b", "node-a"]}],
                store_root=registry.store.root,
            )

            stored = read_template_ports(
                project, lane, store_root=registry.store.root
            )
            self.assertEqual(stored[0]["consumers"], ["node-a", "node-b"])

    def test_illegal_consumers_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            for bad in ["node-a", [""], [7], [None]]:
                with self.subTest(consumers=bad):
                    with self.assertRaises(ValueError):
                        write_template_ports(
                            project,
                            lane,
                            [{"name": "spec", "consumers": bad}],
                            store_root=registry.store.root,
                        )

    def test_declaration_order_is_preserved(self) -> None:
        """Ports render left-to-right on the canvas, so order is author intent."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            names = ["zeta", "alpha", "middle"]
            write_template_ports(
                project,
                lane,
                [{"name": name} for name in names],
                store_root=registry.store.root,
            )

            stored = read_template_ports(
                project, lane, store_root=registry.store.root
            )
            self.assertEqual([port["name"] for port in stored], names)

    def test_illegal_port_names_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            for bad in ["Spec", "1spec", "with-dash", "with space", "", "in:spec"]:
                with self.subTest(name=bad):
                    with self.assertRaises(ValueError):
                        write_template_ports(
                            project,
                            lane,
                            [{"name": bad}],
                            store_root=registry.store.root,
                        )

    def test_non_string_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            with self.assertRaises(ValueError):
                write_template_ports(
                    project,
                    lane,
                    [{"name": 7}],
                    store_root=registry.store.root,
                )
            with self.assertRaises(ValueError):
                write_template_ports(
                    project,
                    lane,
                    ["spec"],
                    store_root=registry.store.root,
                )

    def test_duplicate_port_names_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            with self.assertRaises(ValueError):
                write_template_ports(
                    project,
                    lane,
                    [{"name": "spec"}, {"name": "spec"}],
                    store_root=registry.store.root,
                )

    def test_non_string_description_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            with self.assertRaises(ValueError):
                write_template_ports(
                    project,
                    lane,
                    [{"name": "spec", "description": ["a"]}],
                    store_root=registry.store.root,
                )

    def test_empty_list_drops_the_manifest_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            write_template_ports(
                project,
                lane,
                [{"name": "spec"}],
                store_root=registry.store.root,
            )
            write_template_ports(
                project, lane, [], store_root=registry.store.root
            )

            manifest_path = (
                contextspace_root(registry.store.root)
                / "plugs"
                / "planspaces"
                / lane[len("planspaces.") :]
                / "manifest.yaml"
            )
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            self.assertNotIn("template_ports", raw)
            self.assertEqual(
                read_template_ports(project, lane, store_root=registry.store.root),
                [],
            )

    def test_unknown_lane_is_rejected(self) -> None:
        """Ports must go through the project reachability check, not any lane id."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, _lane = _project_with_lane(registry)

            with self.assertRaises(ValueError):
                read_template_ports(
                    project,
                    "planspaces.someone-else.other",
                    store_root=registry.store.root,
                )
            with self.assertRaises(ValueError):
                write_template_ports(
                    project,
                    "planspaces.someone-else.other",
                    [{"name": "spec"}],
                    store_root=registry.store.root,
                )
            with self.assertRaises(ValueError):
                read_template_ports(project, "", store_root=registry.store.root)

    def test_corrupt_manifest_value_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            manifest_path = (
                contextspace_root(registry.store.root)
                / "plugs"
                / "planspaces"
                / lane[len("planspaces.") :]
                / "manifest.yaml"
            )
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            raw["template_ports"] = "spec"
            manifest_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

            with self.assertRaises(ValueError):
                read_template_ports(project, lane, store_root=registry.store.root)

    def test_ports_do_not_disturb_template_instances(self) -> None:
        """Two extension keys share one manifest; writing one must keep the other."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(store=Store(root=Path(tmp)))
            project, lane = _project_with_lane(registry)

            from miniclaw2.contextspace import (
                append_template_instance,
                read_template_instances,
            )

            append_template_instance(
                project,
                lane,
                {"instance_id": "abc123", "template_slug": "demo"},
                store_root=registry.store.root,
            )
            write_template_ports(
                project,
                lane,
                [{"name": "spec"}],
                store_root=registry.store.root,
            )

            instances = read_template_instances(
                project, lane, store_root=registry.store.root
            )
            self.assertEqual(len(instances), 1)
            self.assertEqual(instances[0]["instance_id"], "abc123")
            self.assertEqual(
                read_template_ports(project, lane, store_root=registry.store.root),
                [{"name": "spec", "description": "", "consumers": []}],
            )


if __name__ == "__main__":
    unittest.main()
