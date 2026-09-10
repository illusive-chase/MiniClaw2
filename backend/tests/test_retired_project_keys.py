"""Stores written before the lane cursor was removed must still open.

``Project`` is ``extra="forbid"`` and :meth:`Store.list_projects` answers a
``ValidationError`` by logging and skipping the record — so a leftover
``active_planspace_id`` in ``project.json`` would not surface as an error, it
would make the project disappear from the user's list with one line in the log.
Every project record written before this refactor carries the key, which makes
this the one failure mode in the change that reads as data loss.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from miniclaw2.domain import Project
from miniclaw2.store import Store


class RetiredProjectKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(root=Path(self.tmp.name) / "store")

    def _write_legacy_keys(self, pid: str, **extra: object) -> None:
        """Put the retired keys back into an already-written project.json."""
        path = self.store.root / "projects" / pid / "project.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(extra)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_list_projects_keeps_a_record_carrying_the_retired_keys(self) -> None:
        project = Project(root_path=str(Path(self.tmp.name) / "repo"), name="legacy")
        self.store.create_project(project)
        self._write_legacy_keys(
            project.id,
            active_planspace_id="planspaces.some-binding.a-lane",
            planspace_selection_explicit=True,
        )

        listed = self.store.list_projects()

        self.assertEqual([p.id for p in listed], [project.id])
        self.assertEqual(listed[0].name, "legacy")
        # The keys are dropped, not mapped onto some surviving attribute: the
        # project-level cursor has no successor, and a node's lane is the only
        # lane anything reads now.
        self.assertFalse(hasattr(listed[0], "active_planspace_id"))
        self.assertFalse(hasattr(listed[0], "planspace_selection_explicit"))

    def test_rewriting_a_legacy_record_drops_the_retired_keys(self) -> None:
        """The keys are not merely tolerated on read — they stop being stored."""
        project = Project(root_path=str(Path(self.tmp.name) / "repo"))
        self.store.create_project(project)
        self._write_legacy_keys(
            project.id, active_planspace_id="planspaces.b.lane"
        )

        reloaded = self.store.list_projects()[0]
        reloaded.name = "renamed"
        self.store.update_project(reloaded)

        payload = json.loads(
            (self.store.root / "projects" / project.id / "project.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("active_planspace_id", payload)
        self.assertNotIn("planspace_selection_explicit", payload)
        self.assertEqual(payload["name"], "renamed")

    def test_an_unrecognized_key_is_still_rejected(self) -> None:
        """Only the two named keys are forgiven.

        The pop is a migration for keys this codebase itself wrote, not a
        blanket relaxation of ``extra="forbid"`` — a genuinely corrupt or
        foreign record must still be skipped rather than silently half-loaded.
        """
        project = Project(root_path=str(Path(self.tmp.name) / "repo"))
        self.store.create_project(project)
        self._write_legacy_keys(project.id, totally_unknown_field="x")

        with self.assertLogs("miniclaw2.store", level="ERROR"):
            self.assertEqual(self.store.list_projects(), [])


if __name__ == "__main__":
    unittest.main()
