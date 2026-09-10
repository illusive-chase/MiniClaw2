"""The subagent ledger and the Stop gate built on it.

The ``Agent`` tool is always asynchronous inside a MiniClaw2 node: it
returns an agent id immediately and the work product arrives later as a
queued notification. Two failures follow from that, and these tests pin
the guards against both — a turn that ends while a subagent is still
running discards its work, and a question asked while one is running can
fork the conversation so the CLI answers a branch the model never saw.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2 import app as app_module
from miniclaw2.providers.claude import ClaudeProvider
from miniclaw2.providers.claude_native import hook_runtime
from miniclaw2.providers.claude_native.ask_payload import deny_ask_directive
from miniclaw2.providers.claude_native.transcript import (
    TranscriptTranslator,
    finished_task_ids,
)


class SubagentLedgerTest(unittest.TestCase):
    """Membership tracking, driven by the CLI's own lifecycle events."""

    def setUp(self) -> None:
        self.node_id = "node-ledger"
        hook_runtime.reset_subagent_ledger(self.node_id)
        self.addCleanup(hook_runtime.reset_subagent_ledger, self.node_id)

    def test_start_then_stop_leaves_nothing_outstanding(self) -> None:
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        self.assertTrue(hook_runtime.has_running_subagents(self.node_id))
        self.assertEqual(hook_runtime.running_subagents(self.node_id), ["Explore"])

        hook_runtime.record_subagent_stop(self.node_id, "agent-1", [])

        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))
        self.assertFalse(hook_runtime.should_block_stop(self.node_id))

    def test_a_yielding_subagent_keeps_its_entry(self) -> None:
        """``SubagentStop`` is not a completion event.

        A subagent that backgrounds a shell command stops and is later
        resumed under the same ``agent_id``. Its own ``SubagentStop``
        payload still lists it as running, and that snapshot — not the
        stop itself — is what says whether the work is done. Retiring on
        the stop alone frees the node to end its turn while the result is
        still coming.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")

        hook_runtime.record_subagent_stop(self.node_id, "agent-1", ["agent-1"])

        self.assertTrue(hook_runtime.has_running_subagents(self.node_id))
        self.assertEqual(hook_runtime.running_subagents(self.node_id), ["Explore"])

    def test_the_snapshot_retires_agents_whose_own_stop_never_arrived(self) -> None:
        """Reconciliation, not one-by-one removal.

        Another configured ``SubagentStop`` hook may block, and
        ``install_hooks`` deliberately preserves such hooks — so our own
        hook can miss a stop entirely. The snapshot is authoritative, so
        an id it no longer lists is retired whichever stop carried it.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        hook_runtime.record_subagent_start(self.node_id, "agent-2", "Plan")

        hook_runtime.record_subagent_stop(self.node_id, "agent-2", [])

        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))

    def test_a_missing_snapshot_falls_back_to_the_stopped_id(self) -> None:
        """``None`` is "no snapshot", which is not "nothing running".

        The array is absent on an older CLI or when the task registry was
        unreachable. Treating that as an empty registry would retire
        every tracked subagent at once.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        hook_runtime.record_subagent_start(self.node_id, "agent-2", "Plan")

        hook_runtime.record_subagent_stop(self.node_id, "agent-1", None)

        self.assertEqual(hook_runtime.running_subagents(self.node_id), ["Plan"])

    def test_unknown_stop_is_ignored_rather_than_treated_as_an_error(self) -> None:
        """Only emptiness matters; an id we never saw start is already absent."""
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")

        hook_runtime.record_subagent_stop(
            self.node_id, "agent-never-started", ["agent-1"]
        )

        self.assertTrue(hook_runtime.has_running_subagents(self.node_id))

    def test_a_node_that_dispatched_nothing_never_blocks(self) -> None:
        """Fail open: the common case must be untouched by any of this."""
        self.assertFalse(hook_runtime.should_block_stop("node-never-seen"))
        self.assertFalse(hook_runtime.has_running_subagents("node-never-seen"))
        self.assertEqual(hook_runtime.running_subagents("node-never-seen"), [])

    def test_block_budget_runs_out_so_a_hung_subagent_cannot_strand_the_node(
        self,
    ) -> None:
        """The give-up path is the point of the budget.

        A subagent that never returns would otherwise hold the turn open
        forever. The budget must also run out below Claude Code's own cap
        of 8 consecutive continuations, so the decision to end the turn
        stays ours: once the CLI overrides the hook it ends the turn
        without telling the backend, and the node would then wait out the
        stall timeout instead.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-hung", "Explore")

        blocks = 0
        while hook_runtime.should_block_stop(self.node_id):
            blocks += 1
            self.assertLess(blocks, 8, "budget must stay below the CLI's cap")

        self.assertGreater(blocks, 0)
        self.assertTrue(hook_runtime.has_running_subagents(self.node_id))

    def test_reset_clears_a_previous_turns_subagents(self) -> None:
        """Each turn is a fresh ``claude --resume`` process.

        A previous turn's subagents died with it, so carrying their ids
        forward would refuse a turn that owes nothing.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")

        hook_runtime.reset_subagent_ledger(self.node_id)

        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))

    def test_ledgers_do_not_leak_between_nodes(self) -> None:
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        self.addCleanup(hook_runtime.reset_subagent_ledger, "node-other")

        self.assertFalse(hook_runtime.has_running_subagents("node-other"))
        self.assertFalse(hook_runtime.should_block_stop("node-other"))


class SubagentReconcileTest(unittest.TestCase):
    """The ledger against the CLI's own in-flight snapshot.

    ``background_tasks`` is sampled at the moment a hook fires, so it
    answers "what is still running" where the ledger only accumulates
    what earlier hook calls reported. The parent's ``Stop`` is the point
    where that distinction decides whether the turn may end.
    """

    def setUp(self) -> None:
        self.node_id = "node-reconcile"
        hook_runtime.reset_subagent_ledger(self.node_id)
        self.addCleanup(hook_runtime.reset_subagent_ledger, self.node_id)

    def test_an_empty_snapshot_retires_everything(self) -> None:
        """This is the only place entries reliably retire.

        A subagent reports itself as running in its own ``SubagentStop``
        payload, so the ledger cannot empty on those events alone. If the
        parent's ``Stop`` could not clear it, every turn that dispatched
        an agent would spend the full block budget and then report
        finished work as lost.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        hook_runtime.record_subagent_start(self.node_id, "agent-2", "Plan")

        hook_runtime.reconcile_subagents(self.node_id, [])

        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))

    def test_a_listed_subagent_still_holds_the_turn(self) -> None:
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        hook_runtime.record_subagent_start(self.node_id, "agent-2", "Plan")

        hook_runtime.reconcile_subagents(self.node_id, ["agent-2"])

        self.assertEqual(hook_runtime.running_subagents(self.node_id), ["Plan"])

    def test_a_missing_snapshot_leaves_the_ledger_alone(self) -> None:
        """``None`` is not ``[]``.

        The array is documented as present only when the task registry is
        reachable, so a missing one must not be read as an empty registry.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")

        hook_runtime.reconcile_subagents(self.node_id, None)

        self.assertTrue(hook_runtime.has_running_subagents(self.node_id))

    def test_an_unseen_subagent_in_the_snapshot_is_adopted(self) -> None:
        """The snapshot is authoritative in both directions.

        A ``SubagentStart`` can be missed — another hook blocked, the POST
        failed — and the turn must still wait for work the CLI says is in
        flight.
        """
        hook_runtime.reconcile_subagents(self.node_id, ["agent-unseen"])

        self.assertTrue(hook_runtime.has_running_subagents(self.node_id))
        self.assertEqual(
            hook_runtime.running_subagents(self.node_id), ["agent-unseen"]
        )

    def test_an_empty_snapshot_for_an_untouched_node_creates_nothing(self) -> None:
        hook_runtime.reconcile_subagents("node-never-dispatched", [])

        self.assertFalse(hook_runtime.has_running_subagents("node-never-dispatched"))


class CompletionNotificationRetirementTest(unittest.TestCase):
    """The signal that closes the gap between "stopped" and "reconciled".

    A subagent's own ``SubagentStop`` fires from inside its query loop
    while its task is still ``running``, so it lists itself in the
    snapshot that travels with that stop and the ledger keeps the entry.
    The status turns terminal only afterwards, and the next hook with an
    authoritative snapshot is the parent's ``Stop`` — the very gate the
    ledger is meant to answer *before*. Without a third signal the
    parent is refused a question even after collecting the result.
    """

    def setUp(self) -> None:
        self.node_id = "node-notify"
        hook_runtime.reset_subagent_ledger(self.node_id)
        self.addCleanup(hook_runtime.reset_subagent_ledger, self.node_id)

    def _notification(self, task_ids: list[str], status: str = "completed") -> dict:
        ids = "\n".join(f"<task-id>{task_id}</task-id>" for task_id in task_ids)
        return {
            "type": "queue-operation",
            "operation": "enqueue",
            "content": (
                f"<task-notification>\n{ids}\n"
                f"<status>{status}</status>\n"
                "<summary>Agent \"Investigate\" finished</summary>\n"
                "</task-notification>"
            ),
        }

    def test_a_completed_notification_retires_the_agent(self) -> None:
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")

        finished = finished_task_ids(self._notification(["agent-1"]))
        hook_runtime.retire_subagents(self.node_id, finished)

        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))

    def test_the_ask_gate_reopens_once_the_result_has_landed(self) -> None:
        """The fault this whole change targets.

        The gate exists so a question cannot suspend the turn while a
        notification is still pending. Once that notification has been
        delivered the justification is gone, and continuing to refuse
        blocks a legitimate question for the rest of the turn.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        self.assertTrue(hook_runtime.has_running_subagents(self.node_id))

        hook_runtime.retire_subagents(
            self.node_id, finished_task_ids(self._notification(["agent-1"]))
        )

        self.assertEqual(hook_runtime.running_subagents(self.node_id), [])

    def test_failed_and_killed_agents_are_also_finished(self) -> None:
        for status in ("failed", "killed", "stopped"):
            with self.subTest(status=status):
                hook_runtime.reset_subagent_ledger(self.node_id)
                hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")

                hook_runtime.retire_subagents(
                    self.node_id,
                    finished_task_ids(self._notification(["agent-1"], status)),
                )

                self.assertFalse(hook_runtime.has_running_subagents(self.node_id))

    def test_a_blocked_agent_keeps_its_entry(self) -> None:
        """``blocked`` is not terminal: it will notify again.

        Retiring here would free the turn while the agent is still
        waiting on input, which is the original fault in reverse.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")

        finished = finished_task_ids(self._notification(["agent-1"], "blocked"))

        self.assertEqual(finished, [])
        self.assertTrue(hook_runtime.has_running_subagents(self.node_id))

    def test_only_the_named_agent_is_retired(self) -> None:
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        hook_runtime.record_subagent_start(self.node_id, "agent-2", "Plan")

        hook_runtime.retire_subagents(
            self.node_id, finished_task_ids(self._notification(["agent-1"]))
        )

        self.assertEqual(hook_runtime.running_subagents(self.node_id), ["Plan"])

    def test_a_multi_id_notification_retires_each_one(self) -> None:
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        hook_runtime.record_subagent_start(self.node_id, "agent-2", "Plan")

        hook_runtime.retire_subagents(
            self.node_id,
            finished_task_ids(self._notification(["agent-1", "agent-2"])),
        )

        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))

    def test_retirement_never_adds_an_unknown_id(self) -> None:
        """Strictly a narrowing of the ledger.

        A notification for a shell task, or for a previous session's
        orphan, names an id this node never dispatched. Adopting it
        would hold the turn open for work that does not exist.
        """
        hook_runtime.retire_subagents(self.node_id, ["agent-stranger"])

        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))


class TaskNotificationParsingTest(unittest.TestCase):
    """Which records may retire a subagent, and which may not.

    The same XML appears in records the *model* wrote — it quotes
    notifications when reasoning about them. Reading those would let an
    agent retire its own subagents by talking about them, so only the
    carriers the CLI itself writes are trusted.
    """

    def _body(self, task_id: str = "agent-1", status: str = "completed") -> str:
        return (
            f"<task-notification>\n<task-id>{task_id}</task-id>\n"
            f"<status>{status}</status>\n</task-notification>"
        )

    def test_queue_operation_records_are_trusted(self) -> None:
        record = {
            "type": "queue-operation",
            "operation": "enqueue",
            "content": self._body(),
        }

        self.assertEqual(finished_task_ids(record), ["agent-1"])

    def test_the_queued_command_attachment_is_trusted(self) -> None:
        record = {
            "type": "attachment",
            "attachment": {
                "type": "queued_command",
                "commandMode": "task-notification",
                "prompt": self._body(),
            },
        }

        self.assertEqual(finished_task_ids(record), ["agent-1"])

    def test_an_assistant_record_quoting_the_xml_is_ignored(self) -> None:
        """Otherwise the model retires its own subagents by describing one.

        Thinking text that reproduces a notification is the observed
        shape here, and it must not reach the ledger.
        """
        record = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": self._body()}],
            },
            "content": self._body(),
        }

        self.assertEqual(finished_task_ids(record), [])

    def test_a_plain_user_prompt_is_ignored(self) -> None:
        """A restart's orphan notice arrives this way.

        It names agents from a *previous* session, which this turn's
        ledger never held, so nothing is lost by refusing it — while
        trusting user-role text would make the gate user-controllable.
        """
        record = {
            "type": "user",
            "message": {"role": "user", "content": self._body()},
            "content": self._body(),
        }

        self.assertEqual(finished_task_ids(record), [])

    def test_an_ordinary_record_yields_nothing(self) -> None:
        self.assertEqual(finished_task_ids({"type": "assistant"}), [])
        self.assertEqual(
            finished_task_ids(
                {"type": "queue-operation", "content": "no xml here"}
            ),
            [],
        )

    def test_a_notification_without_a_status_is_not_a_completion(self) -> None:
        record = {
            "type": "queue-operation",
            "content": "<task-notification>\n<task-id>agent-1</task-id>\n"
            "</task-notification>",
        }

        self.assertEqual(finished_task_ids(record), [])

    def test_a_mixed_status_notification_retires_nothing(self) -> None:
        """Fail closed when the record is ambiguous.

        Retiring every listed id off one terminal status among several
        could free an agent that is still running.
        """
        record = {
            "type": "queue-operation",
            "content": (
                "<task-notification>\n"
                "<task-id>agent-1</task-id>\n<status>completed</status>\n"
                "<task-id>agent-2</task-id>\n<status>blocked</status>\n"
                "</task-notification>"
            ),
        }

        self.assertEqual(finished_task_ids(record), [])


class _HookClientTest(unittest.TestCase):
    """Shared plumbing for tests that drive the hook routes over HTTP."""

    def _client(self) -> tuple[TestClient, object]:
        raw = tempfile.TemporaryDirectory()
        self.addCleanup(raw.cleanup)
        env = patch.dict(
            os.environ, {"MINICLAW_HOME": str(Path(raw.name) / "store")}
        )
        env.start()
        self.addCleanup(env.stop)
        install = patch.object(
            app_module, "install_hooks", return_value=Path(raw.name)
        )
        install.start()
        self.addCleanup(install.stop)
        client = TestClient(app_module.create_app())
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client, raw

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {hook_runtime.token()}"}


class TurnCompleteSubagentGateTest(_HookClientTest):
    """``/hook/turn-complete`` decides whether the turn is allowed to end."""

    def setUp(self) -> None:
        self.node_id = "node-gate"
        hook_runtime.reset_subagent_ledger(self.node_id)
        self.addCleanup(hook_runtime.reset_subagent_ledger, self.node_id)
        self.event = hook_runtime.register_turn_complete(
            self.node_id, "session-owned"
        )
        self.addCleanup(hook_runtime.unregister_turn_complete, self.node_id)

    def _stop(
        self,
        client: TestClient,
        session_id: str,
        running_agent_ids: list[str] | None = None,
    ) -> dict:
        res = client.post(
            "/hook/turn-complete",
            json={
                "node_id": self.node_id,
                "session_id": session_id,
                "stop_hook_active": False,
                "running_agent_ids": running_agent_ids,
            },
            headers=self._auth(),
        )
        self.assertEqual(res.status_code, 200)
        return res.json()

    def test_running_subagent_blocks_and_withholds_turn_complete(self) -> None:
        """Both halves matter.

        Blocking without withholding turn-complete would leave the CLI
        running while the backend believed the turn was over; the two
        sides have to agree the turn is still live.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        client, _ = self._client()

        body = self._stop(client, "session-owned", ["agent-1"])

        self.assertEqual(body["decision"], "block")
        self.assertIn("Explore", body["reason"])
        self.assertIn("TaskStop", body["reason"])
        self.assertFalse(self.event.is_set())

    def test_the_stop_snapshot_releases_a_ledger_the_stops_could_not_clear(
        self,
    ) -> None:
        """The fix for a turn that could never end.

        A subagent lists itself as running in its own ``SubagentStop``
        payload, so the ledger still holds it when the parent stops.
        Without reconciling against the parent's own snapshot the turn
        would burn its whole block budget and then report finished work
        as lost.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        client, _ = self._client()

        body = self._stop(client, "session-owned", [])

        self.assertNotIn("decision", body)
        self.assertTrue(body["accepted"])
        self.assertTrue(self.event.is_set())
        self.assertEqual(hook_runtime.abandoned_subagent_note(self.node_id), "")

    def test_a_snapshot_only_subagent_still_blocks(self) -> None:
        """A missed ``SubagentStart`` must not let the turn end."""
        client, _ = self._client()

        body = self._stop(client, "session-owned", ["agent-unseen"])

        self.assertEqual(body["decision"], "block")
        self.assertFalse(self.event.is_set())

    def test_no_subagents_ends_the_turn_normally(self) -> None:
        client, _ = self._client()

        body = self._stop(client, "session-owned")

        self.assertNotIn("decision", body)
        self.assertTrue(body["accepted"])
        self.assertTrue(self.event.is_set())

    def test_a_descendant_session_can_neither_block_nor_spend_the_budget(
        self,
    ) -> None:
        """A nested ``claude`` inherits ``MINICLAW_NODE_ID`` but owns nothing.

        Letting its ``Stop`` block would hold the parent's turn open on a
        stranger's behalf; letting it spend the budget would drain the
        parent's only protection before the parent ever stopped.
        """
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        client, _ = self._client()

        body = self._stop(client, "nested-session", [])

        self.assertNotIn("decision", body)
        self.assertFalse(body["accepted"])
        self.assertFalse(self.event.is_set())
        # Its snapshot was ignored too, so the parent's ledger stands.
        self.assertTrue(hook_runtime.should_block_stop(self.node_id))

    def test_exhausted_budget_ends_the_turn_and_records_the_loss(self) -> None:
        hook_runtime.record_subagent_start(self.node_id, "agent-hung", "Explore")
        while hook_runtime.should_block_stop(self.node_id):
            pass
        client, _ = self._client()

        body = self._stop(client, "session-owned", ["agent-hung"])

        self.assertNotIn("decision", body)
        self.assertTrue(self.event.is_set())
        self.assertIn("Explore", hook_runtime.abandoned_subagent_note(self.node_id))


class SubagentHookRouteTest(_HookClientTest):
    """``/hook/subagent`` is the ledger's only writer."""

    def setUp(self) -> None:
        self.node_id = "node-route"
        hook_runtime.reset_subagent_ledger(self.node_id)
        self.addCleanup(hook_runtime.reset_subagent_ledger, self.node_id)
        # The route only accepts writes from the node's own PTY, so the
        # ownership slot has to exist for any of these to land.
        hook_runtime.register_turn_complete(self.node_id, "session-owned")
        self.addCleanup(hook_runtime.unregister_turn_complete, self.node_id)

    def test_start_and_stop_round_trip_through_the_route(self) -> None:
        client, _ = self._client()

        started = client.post(
            "/hook/subagent",
            json={
                "node_id": self.node_id,
                "session_id": "session-owned",
                "phase": "start",
                "agent_id": "agent-1",
                "agent_type": "Explore",
            },
            headers=self._auth(),
        )
        self.assertEqual(started.status_code, 200)
        self.assertTrue(hook_runtime.has_running_subagents(self.node_id))

        stopped = client.post(
            "/hook/subagent",
            json={
                "node_id": self.node_id,
                "session_id": "session-owned",
                "phase": "stop",
                "agent_id": "agent-1",
                "running_agent_ids": [],
            },
            headers=self._auth(),
        )
        self.assertEqual(stopped.status_code, 200)
        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))

    def test_a_descendant_sessions_subagents_stay_out_of_the_ledger(self) -> None:
        """``MINICLAW_NODE_ID`` is inherited; it names a node, proves nothing.

        A nested ``claude`` launched from a Bash call dispatches its own
        agents. Charging them here would deny the parent's questions and
        block its Stop for work it never dispatched — permanently, if the
        descendant exits without its stop reaching us.
        """
        client, _ = self._client()

        res = client.post(
            "/hook/subagent",
            json={
                "node_id": self.node_id,
                "session_id": "nested-session",
                "phase": "start",
                "agent_id": "agent-1",
                "agent_type": "Explore",
            },
            headers=self._auth(),
        )

        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["accepted"])
        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))

    def test_a_report_without_a_session_id_is_refused(self) -> None:
        """Fail closed: missing proof is treated as failed proof."""
        client, _ = self._client()

        res = client.post(
            "/hook/subagent",
            json={
                "node_id": self.node_id,
                "phase": "start",
                "agent_id": "agent-1",
            },
            headers=self._auth(),
        )

        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["accepted"])
        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))

    def test_a_still_listed_subagent_survives_its_own_stop(self) -> None:
        client, _ = self._client()
        client.post(
            "/hook/subagent",
            json={
                "node_id": self.node_id,
                "session_id": "session-owned",
                "phase": "start",
                "agent_id": "agent-1",
                "agent_type": "Explore",
            },
            headers=self._auth(),
        )

        client.post(
            "/hook/subagent",
            json={
                "node_id": self.node_id,
                "session_id": "session-owned",
                "phase": "stop",
                "agent_id": "agent-1",
                "running_agent_ids": ["agent-1"],
            },
            headers=self._auth(),
        )

        self.assertTrue(hook_runtime.has_running_subagents(self.node_id))

    def test_unknown_phase_is_rejected(self) -> None:
        """A typo must not silently leave the ledger unguarded."""
        client, _ = self._client()

        res = client.post(
            "/hook/subagent",
            json={
                "node_id": self.node_id,
                "session_id": "session-owned",
                "phase": "restart",
                "agent_id": "agent-1",
            },
            headers=self._auth(),
        )

        self.assertEqual(res.status_code, 400)

    def test_route_requires_the_hook_token(self) -> None:
        client, _ = self._client()

        res = client.post(
            "/hook/subagent",
            json={
                "node_id": self.node_id,
                "session_id": "session-owned",
                "phase": "start",
                "agent_id": "agent-1",
            },
            headers={"Authorization": "Bearer wrong"},
        )

        self.assertEqual(res.status_code, 403)
        self.assertFalse(hook_runtime.has_running_subagents(self.node_id))


class DenyAskDirectiveTest(unittest.TestCase):
    """The shape Claude needs in order to refuse ``AskUserQuestion``."""

    def test_reason_reaches_the_model(self) -> None:
        """On a deny, ``permissionDecisionReason`` is the only channel.

        Anything the agent needs in order to recover — that subagents are
        outstanding, and that ``TaskStop`` is the way out — has to travel
        in this one string.
        """
        directive = deny_ask_directive("1 subagent still running; use TaskStop")

        specific = directive["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "PreToolUse")
        self.assertEqual(specific["permissionDecision"], "deny")
        self.assertIn("TaskStop", specific["permissionDecisionReason"])


class AskDispatchGateTest(unittest.IsolatedAsyncioTestCase):
    """Asking while a subagent runs is the fault this whole change targets.

    The completion notification enqueues during the suspension, forks
    against the user's answer, and the CLI can then answer the
    notification branch locally — a turn that ends without the model ever
    being called.
    """

    def setUp(self) -> None:
        self.node_id = "node-ask"
        hook_runtime.reset_subagent_ledger(self.node_id)
        self.addCleanup(hook_runtime.reset_subagent_ledger, self.node_id)

    def _context(self) -> SimpleNamespace:
        self.gate_calls = 0

        async def request_gate(_request):
            self.gate_calls += 1
            return {"response": {"answers": {}}}

        return SimpleNamespace(
            node=SimpleNamespace(id=self.node_id),
            request_gate=request_gate,
        )

    def _payload(self) -> dict:
        return {
            "tool_input": {
                "questions": [
                    {"question": "继续吗？", "header": "Next", "options": []}
                ]
            }
        }

    async def test_question_is_denied_while_a_subagent_runs(self) -> None:
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        provider = ClaudeProvider()
        context = self._context()

        directive = await provider._dispatch_ask(self._payload(), context)

        specific = directive["hookSpecificOutput"]
        self.assertEqual(specific["permissionDecision"], "deny")
        self.assertIn("Explore", specific["permissionDecisionReason"])
        self.assertIn("TaskStop", specific["permissionDecisionReason"])
        # The gate is never opened, so no human is left waiting on a
        # question whose answer could not be delivered safely.
        self.assertEqual(self.gate_calls, 0)

    async def test_question_goes_through_when_nothing_is_outstanding(self) -> None:
        provider = ClaudeProvider()
        context = self._context()

        directive = await provider._dispatch_ask(self._payload(), context)

        self.assertEqual(
            directive["hookSpecificOutput"]["permissionDecision"], "allow"
        )
        self.assertEqual(self.gate_calls, 1)

    async def test_a_retired_subagent_reopens_the_channel(self) -> None:
        hook_runtime.record_subagent_start(self.node_id, "agent-1", "Explore")
        hook_runtime.record_subagent_stop(self.node_id, "agent-1", [])
        provider = ClaudeProvider()
        context = self._context()

        directive = await provider._dispatch_ask(self._payload(), context)

        self.assertEqual(
            directive["hookSpecificOutput"]["permissionDecision"], "allow"
        )


class SyntheticTurnDetectionTest(unittest.TestCase):
    """A turn the CLI answered without calling the model.

    Diagnostic only: the final error a poisoned node reports is "did not
    write its own preview", which names the symptom and not the cause.
    Counting these leaves the real reason in the log.
    """

    def test_synthetic_assistant_record_is_counted(self) -> None:
        translator = TranscriptTranslator()

        translator.translate(
            {
                "type": "assistant",
                "message": {
                    "id": "msg-1",
                    "model": "<synthetic>",
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "content": [{"type": "text", "text": "No response requested."}],
                },
            }
        )

        self.assertEqual(translator.synthetic_turns, 1)

    def test_a_real_turn_is_not_counted(self) -> None:
        translator = TranscriptTranslator()

        translator.translate(
            {
                "type": "assistant",
                "message": {
                    "id": "msg-2",
                    "model": "claude-opus-5",
                    "usage": {"input_tokens": 12, "output_tokens": 30},
                    "content": [{"type": "text", "text": "done"}],
                },
            }
        )

        self.assertEqual(translator.synthetic_turns, 0)

    def test_zero_usage_alone_is_not_enough(self) -> None:
        """Both halves are required.

        A real model call can report zero counted tokens; only the
        ``<synthetic>`` marker says the model was never reached.
        """
        translator = TranscriptTranslator()

        translator.translate(
            {
                "type": "assistant",
                "message": {
                    "id": "msg-3",
                    "model": "claude-opus-5",
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                    "content": [{"type": "text", "text": "hi"}],
                },
            }
        )

        self.assertEqual(translator.synthetic_turns, 0)

    def test_detection_does_not_change_the_event_stream(self) -> None:
        """It must stay diagnostic.

        The guardrails that prevent the fork sit upstream; a detector
        that judged turns could kill healthy ones instead.
        """
        translator = TranscriptTranslator()

        events = translator.translate(
            {
                "type": "assistant",
                "message": {
                    "id": "msg-4",
                    "model": "<synthetic>",
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                    "content": [{"type": "text", "text": "No response requested."}],
                },
            }
        )

        self.assertTrue(events)
        self.assertFalse(any(event.kind == "error" for event in events))


class SubagentToolClassificationTest(unittest.TestCase):
    """``Agent`` superseded ``Task`` as the subagent tool's name."""

    def _activity(self, name: str):
        translator = TranscriptTranslator()
        events = translator.translate(
            {
                "type": "assistant",
                "message": {
                    "id": "msg-tool",
                    "model": "claude-opus-5",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tooluse_1",
                            "name": name,
                            "input": {"prompt": "go"},
                        }
                    ],
                },
            }
        )
        return [e.event for e in events if e.kind == "event"][0]

    def test_agent_calls_are_classified_as_agent_activity(self) -> None:
        """The check used to name ``Task`` only.

        Every dispatch on this host is now an ``Agent`` call, so that
        branch had stopped matching and subagent work was being reported
        as an ordinary tool call.
        """
        self.assertEqual(self._activity("Agent").kind, "agent")

    def test_the_historical_task_name_still_classifies(self) -> None:
        self.assertEqual(self._activity("Task").kind, "agent")

    def test_ordinary_tools_are_unaffected(self) -> None:
        self.assertEqual(self._activity("Read").kind, "tool")


if __name__ == "__main__":
    unittest.main()
