"""NodeRunner — provider-neutral agent node state machine.

For each agent node the runner materializes a private lane under
``.miniclaw2/graph/runs/<node>/lanes/<lane>/``, snapshots its filesystem state,
runs the provider, then walk-diffs and reaps preview writes back into
the durable store. Op nodes (e.g. auto-commit) bypass the reap pipeline
and write their own ``preview.json`` directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import BaseModel

from .artifacts import (
    ALLOWED_ARTIFACT_SUFFIXES,
    clear_published_artifacts,
    publish_artifacts,
    workspace_artifacts_dir,
)
from .contextspace import (
    StaleLaunchSettingsError,
    compose_context_bundle,
    contextspace_root,
    require_resolvable_active_planspace,
)
from .domain import (
    COLD_START_AGENT_OP_KIND,
    Category,
    GateKind,
    GateState,
    GateSubtype,
    HumanGate,
    Node,
    NodeKind,
    NodeState,
    Project,
    ReviewSubtype,
    TokenUsage,
)
from .events import (
    Activity,
    ErrorEvent,
    InteractionRequest,
    NodeStarted,
    NodeUpdated,
    TextDelta,
    TurnDone,
    Usage,
)
from .git_state import (
    commit_all,
    git_head,
    git_pull_rebase,
    git_review_snapshot,
    local_only_shas,
)
from .global_config import load_global_config
from .language import language_launch_instruction, project_preferred_language
from .launch_prompt import (
    anti_self_poisoning_block,
    build_category_launch_block,
    build_dependency_launch_block,
    build_library_init_block,
    build_principle_init_block,
    build_qa_mode_block,
    shared_host_processes_block,
    subagent_synchronicity_block,
)
from .materialize import (
    GRAPH_RUNS_DIRNAME,
    materialize_active_lane,
    node_dir,
    runner_lane_root,
    snapshot_lane,
)
from .model_catalog import get_model_preset
from .preview import (
    ExecutedPreview,
    PreviewValidationError,
    parse_preview,
    render_executed_preview,
    validate_preview_for_node,
)
from .providers import (
    AgentProvider,
    AgentProviderContext,
    AgentProviderEvent,
    GateRequest,
    ReviewReport,
    ReviewSpec,
)
from .providers.claude import ClaudeProvider
from .providers.codex import CodexProvider
from .reap import reap_lane
from .store import Store
from .skills import (
    SkillMaterialization,
    _validate_tree,
    expand_skill_selections,
    inspect_agent_skill,
    materialize_agent_skills,
    record_authored_agent_skill,
    skill_content_hash,
)

logger = logging.getLogger(__name__)

_PREVIEW_REPAIR_RETRIES = 3
_STALE_REVIEW_WARNING = (
    "⚠️ Review snapshot is stale: the working tree changed while the native "
    "review was running. See reviewed-diff.patch for the audited input."
)
_CODEX_FOCUS_WARNING = (
    "Note: the focus text was not applied because Codex native review does not "
    "support focus for uncommitted changes."
)
_CLEAN_REVIEW_PATCH = "# Working tree clean - nothing to review.\n"
_LIBRARY_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

# A cold-start agent is never told that previews exist, so the framework writes
# the three prose fields itself. Motivation and implications are fixed: they are
# the same for every cold start, and the agent has no channel to express them.
_COLD_START_MOTIVATION = "冷启动节点：框架未注入任何上下文"
_COLD_START_IMPLICATIONS = (
    "产物见节点 artifacts 与项目工作树；本节点无框架撰写的分析"
)
_COLD_START_EMPTY_SUMMARY = "冷启动回合结束但没有产生最终文本"
# Tail of the turn's assistant text kept for the summary. Same order of
# magnitude as the summaries agents write for themselves.
_COLD_START_SUMMARY_LIMIT = 4000


class LibraryAuthoringError(ValueError):
    """A librarian turn produced no single valid library entry."""


class NodeRunner:
    """Drives one agent node from start to terminal state."""

    def __init__(
        self,
        node: Node,
        project: Project,
        store: Store,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        reap_lock: asyncio.Lock | None = None,
        on_state_change: Callable[[Node, NodeState], Awaitable[None]] | None = None,
    ) -> None:
        self.node = node
        self.project = project
        self.store = store
        self.on_event = on_event
        self.on_state_change = on_state_change
        self._reap_lock = reap_lock or asyncio.Lock()

        self._seq = 0
        self._gates: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._gate_records: dict[str, HumanGate] = {}
        self._provider: AgentProvider | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._lane_root: Path | None = None
        self._pre_snapshot: dict[str, str] = {}
        self._skill_materialization: SkillMaterialization | None = None
        self._cold_start_text = ""

    # ---- public surface (used by the WS layer via ProjectRuntime) ----

    def resolve_gate(
        self,
        gate_id: str,
        *,
        allow: bool,
        message: str = "",
        updated_input: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        scope: str | None = None,
        interrupt: bool = False,
        permission_mode: str | None = None,
        clear_context: bool = False,
    ) -> bool:
        fut = self._gates.get(gate_id)
        if fut is None or fut.done():
            return False
        fut.get_loop().call_soon_threadsafe(
            fut.set_result,
            {
                "allow": allow,
                "message": message,
                "updated_input": updated_input,
                "response": response,
                "scope": scope,
                "interrupt": interrupt,
                "permission_mode": permission_mode,
                "clear_context": clear_context,
            },
        )
        return True

    async def interrupt(self) -> None:
        if self._provider is not None:
            await self._provider.interrupt()
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

    # ---- main entry point ----

    async def run(self) -> None:
        try:
            if self.node.kind is NodeKind.OP:
                await self._run_op()
            elif self.node.kind is NodeKind.VERIFIER:
                await self._run_verifier()
            elif self.node.subtype is ReviewSubtype.CODE_REVIEW:
                await self._run_code_review()
            else:
                await self._run_agent()
        finally:
            self._cleanup_lane_projection()

    async def _run_agent(self) -> None:
        self.node.commit_before = git_head(self.project.root_path)
        # A cold start runs this same state machine — the transitions, commit
        # bookkeeping, gate plumbing and cancellation handling are all shared.
        # Only the framework's four injection points are short-circuited, and
        # the preview is written by the framework instead of the agent.
        is_cold_start = self._is_cold_start()
        try:
            context_bundle = (
                None if is_cold_start else self._snapshot_context_bundle()
            )
            if is_cold_start:
                self._clear_context_snapshot()
            self._snapshot_launch_settings(context_bundle)
            self._materialize_skills()
            self._validate_launch_settings()
            if is_cold_start:
                # No lane is materialized, but the outputs directory is still
                # the node's artifact channel. _materialize_lane() would have
                # created it on the way past.
                workspace_artifacts_dir(self.project, self.node.id).mkdir(
                    parents=True,
                    exist_ok=True,
                )
            else:
                self._materialize_lane()
            launch_instructions = (
                ""
                if is_cold_start
                else self._build_agent_launch_instructions(context_bundle)
            )
            self.node.launch_instructions_snapshot = launch_instructions
            system_context = (
                "" if context_bundle is None else context_bundle.system_text
            )

            is_human_review = self._is_human_interact_review()
            first_state = (
                NodeState.AWAITING_HUMAN_INPUT
                if is_human_review
                else NodeState.RUNNING
            )
            self._transition(first_state, started=True)
            await self._emit_node_started()
            await self._emit_node_updated()

            final_state: NodeState = NodeState.DONE
            error_msg: str | None = None
            library_snapshot: dict[str, dict[str, str]] = {}
            library_authoring_validated = False

            try:
                try:
                    if is_human_review:
                        prose = await self._request_human_review_prose()
                        if not prose.strip():
                            final_state = NodeState.CANCELLED
                            error_msg = "human reviewer provided no prose"
                        else:
                            self._write_human_review_prose(prose)

                    if final_state is NodeState.DONE:
                        self._take_pre_snapshot()
                        if is_human_review:
                            self._transition(NodeState.RUNNING)
                            await self._emit_node_updated()
                        library_snapshot = self._snapshot_library_entries()
                        final_state, error_msg = await self._run_provider_turn(
                            self.node.prompt,
                            launch_instructions=launch_instructions,
                            system_context=system_context,
                        )
                        if (
                            final_state is NodeState.DONE
                            and self.node.agent_op_kind == "library_edit"
                        ):
                            try:
                                self._validate_library_authoring(
                                    library_snapshot,
                                    record_provenance=False,
                                )
                                library_authoring_validated = True
                            except Exception as exc:  # noqa: BLE001
                                error_msg = str(exc)
                                final_state = NodeState.ERROR
                                await self._emit(ErrorEvent(message=error_msg))
                except asyncio.CancelledError:
                    final_state = NodeState.CANCELLED
                    await self.interrupt()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("runner failed")
                    error_msg = f"Unexpected runner error: {exc}"
                    final_state = NodeState.ERROR
                    await self._emit(ErrorEvent(message=error_msg))
                finally:
                    self._provider = None
            finally:
                self._resolve_open_gates()
                if error_msg is not None:
                    self.node.error = error_msg
                self.node.commit_after = git_head(self.project.root_path)
                if is_cold_start:
                    # No reap: the agent was never told the lane or the preview
                    # contract exists, so there is nothing of its own to fold in.
                    self._write_cold_start_preview(
                        final_state, reason=error_msg or ""
                    )
                elif final_state is NodeState.DONE and self._lane_root is not None:
                    try:
                        final_state = await self._reap_with_preview_repairs(
                            system_context
                        )
                    except asyncio.CancelledError:
                        final_state = NodeState.CANCELLED
                        await self.interrupt()
                        self._write_stub_preview(
                            NodeState.CANCELLED,
                            reason="preview repair cancelled",
                        )
                else:
                    final_state = await self._reap_and_finalize(
                        final_state, reason=error_msg or ""
                    )
                if library_authoring_validated and final_state is NodeState.DONE:
                    try:
                        self._validate_library_authoring(library_snapshot)
                    except Exception as exc:  # noqa: BLE001
                        error_msg = str(exc)
                        self.node.error = error_msg
                        final_state = NodeState.ERROR
                        self._write_stub_preview(
                            NodeState.ERROR,
                            reason=error_msg,
                        )
                        await self._emit(ErrorEvent(message=error_msg))
                self._transition(final_state, finished=True)
                await self._emit_node_updated()
                await self._emit(TurnDone(node=self.node.model_dump()))
        except asyncio.CancelledError:
            raise
        except StaleLaunchSettingsError as exc:
            logger.warning("runner refused launch due to stale settings: %s", exc)
            error_msg = str(exc)
            self.node.error = error_msg
            self.node.commit_after = git_head(self.project.root_path)
            self._write_stub_preview(NodeState.ERROR, reason=error_msg)
            self._transition(NodeState.ERROR, started=True, finished=True)
            await self._emit_node_started()
            await self._emit(ErrorEvent(message=error_msg))
            await self._emit_node_updated()
            await self._emit(TurnDone(node=self.node.model_dump()))
        except Exception as exc:  # noqa: BLE001
            logger.exception("runner failed before start")
            error_msg = f"Unexpected runner error: {exc}"
            self.node.error = error_msg
            self.node.commit_after = git_head(self.project.root_path)
            self._write_stub_preview(NodeState.ERROR, reason=error_msg)
            self._transition(NodeState.ERROR, started=True, finished=True)
            await self._emit_node_started()
            await self._emit(ErrorEvent(message=error_msg))
            await self._emit_node_updated()
            await self._emit(TurnDone(node=self.node.model_dump()))

    async def _run_op(self) -> None:
        """Run a non-provider op node (currently only ``commit``).

        Ops do not participate in the reap pipeline. The op runner
        writes its own ``preview.json`` to the durable store directly so
        the lane projection has a complete record.
        """
        self.node.commit_before = git_head(self.project.root_path)
        context_bundle = self._snapshot_context_bundle()
        self._snapshot_launch_settings(context_bundle)
        self._transition(NodeState.RUNNING, started=True)
        await self._emit_node_started()
        await self._emit_node_updated()

        final_state = NodeState.DONE
        error_msg: str | None = None

        try:
            if self.node.op_kind == "commit":
                message = self.node.prompt.strip() or f"miniclaw:node:{self.node.parent_node_id or self.node.id}"
                new_head, err = await asyncio.to_thread(
                    commit_all, self.project.root_path, message
                )
                if err is not None:
                    error_msg = err
                    final_state = NodeState.ERROR
                elif new_head is None:
                    self.node.summary = "no changes to commit"
                    self.node.commit_after = self.node.commit_before
                else:
                    self.node.summary = f"commit {new_head[:8]}"
                    self.node.commit_after = new_head
            elif self.node.op_kind == "pull":
                old_local = await asyncio.to_thread(local_only_shas, self.project.root_path)
                new_head, err = await asyncio.to_thread(
                    git_pull_rebase, self.project.root_path
                )
                if err is not None:
                    error_msg = err
                    final_state = NodeState.ERROR
                else:
                    new_local = await asyncio.to_thread(local_only_shas, self.project.root_path)
                    if old_local is not None and new_local is not None and len(old_local) == len(new_local):
                        aliases = dict(zip(old_local, new_local, strict=False))
                        if aliases:
                            merged = self.store.read_git_aliases(self.project.id)
                            merged.update(aliases)
                            self.store.write_git_aliases(self.project.id, merged)
                    self.node.summary = f"rebased to {new_head[:8]}" if new_head else "already up to date"
                    self.node.commit_after = new_head
            else:
                error_msg = f"unknown op_kind: {self.node.op_kind}"
                final_state = NodeState.ERROR
        except asyncio.CancelledError:
            final_state = NodeState.CANCELLED
        except Exception as exc:  # noqa: BLE001
            logger.exception("op runner failed")
            error_msg = f"Unexpected op runner error: {exc}"
            final_state = NodeState.ERROR
            await self._emit(ErrorEvent(message=error_msg))
        if error_msg is not None:
            self.node.error = error_msg
        if self.node.commit_after is None:
            self.node.commit_after = git_head(self.project.root_path)
        self._write_op_preview(final_state)
        self._transition(final_state, finished=True)
        await self._emit_node_updated()
        await self._emit(TurnDone(node=self.node.model_dump()))

    async def _run_code_review(self) -> None:
        """Run a provider-native review without lane materialization or reap."""
        self.node.commit_before = git_head(self.project.root_path)
        final_state = NodeState.DONE
        error_msg: str | None = None
        report: ReviewReport | None = None
        artifacts: list[str] = []
        snapshot = None
        stale = False
        focus_warning: str | None = None
        review_summary: str | None = None
        try:
            self._snapshot_launch_settings()
            self._materialize_skills()
            snapshot = await asyncio.to_thread(
                git_review_snapshot, self.project.root_path
            )
            snapshot_path = self.store.node_dir(
                self.project.id, self.node.id
            ) / "reviewed-diff.patch"
            snapshot_path.write_text(
                snapshot.patch if snapshot.dirty_paths else _CLEAN_REVIEW_PATCH,
                encoding="utf-8",
            )
            self._transition(NodeState.RUNNING, started=True)
            await self._emit_node_started()
            await self._emit_node_updated()

            if not snapshot.dirty_paths:
                self.node.summary = "working tree clean — nothing to review"
            else:
                final_state, error_msg, report = await self._run_native_review(
                    system_context=_skill_suggestion_block(
                        self._skill_materialization
                    )
                )
                if final_state is NodeState.DONE and report is None:
                    final_state = NodeState.ERROR
                    error_msg = (
                        "native code review completed without a recognizable report"
                    )
                if report is not None:
                    focus_warning = self._code_review_focus_warning()
                    review_summary = _review_summary(report)
                    if focus_warning:
                        report.raw_markdown = (
                            focus_warning + "\n\n" + report.raw_markdown.lstrip()
                        )
                    current = await asyncio.to_thread(
                        git_review_snapshot, self.project.root_path
                    )
                    stale = current != snapshot
                    artifacts = self._publish_code_review_report(
                        report, stale=stale
                    )
        except asyncio.CancelledError:
            final_state = NodeState.CANCELLED
            await self.interrupt()
        except Exception as exc:  # noqa: BLE001
            logger.exception("code review runner failed")
            error_msg = f"Unexpected code review error: {exc}"
            final_state = NodeState.ERROR
            await self._emit(ErrorEvent(message=error_msg))
        finally:
            self._provider = None
            self._resolve_open_gates()

        if error_msg is not None:
            self.node.error = error_msg
        self.node.commit_after = git_head(self.project.root_path)
        if final_state is NodeState.DONE:
            if report is None:
                summary = self.node.summary or "working tree clean — nothing to review"
                motivation = "code review of uncommitted changes"
                implications = "no uncommitted changes require review"
            else:
                summary = review_summary or _review_summary(report)
                implications = _review_implications(report)
                head = (
                    snapshot.head_sha if snapshot is not None else None
                ) or "unknown"
                motivation = (
                    self.node.brief.check_what
                    if self.node.brief is not None and not focus_warning
                    else f"code review of uncommitted changes since {head[:7]}"
                )
                if stale:
                    summary = _STALE_REVIEW_WARNING + "\n\n" + summary
                if focus_warning:
                    summary = focus_warning + "\n\n" + summary
            self.node.summary = summary
            self._persist_executed_preview(
                final_state,
                motivation=motivation,
                summary=summary,
                next_implications=implications,
                artifacts=artifacts,
            )
        elif artifacts:
            reason = error_msg or f"code review turn ended {final_state.value}"
            summary = (
                f"review completed but the turn ended {final_state.value}: {reason}"
            )
            if focus_warning:
                summary = focus_warning + "\n\n" + summary
            self.node.summary = summary
            self._persist_executed_preview(
                final_state,
                motivation="code review of uncommitted changes",
                summary=summary,
                next_implications=(
                    _review_implications(report)
                    if report is not None
                    else "review report is available in the published artifacts"
                ),
                artifacts=artifacts,
            )
        else:
            self._write_stub_preview(
                final_state, reason=error_msg or "code review cancelled"
            )
        self._transition(final_state, finished=True)
        await self._emit_node_updated()
        await self._emit(TurnDone(node=self.node.model_dump()))

    async def _run_native_review(
        self, *, system_context: str
    ) -> tuple[NodeState, str | None, ReviewReport | None]:
        preset = get_model_preset(
            self.node.model_preset_id, store_root=self.store.root
        )
        provider = _make_provider(preset.provider)
        run_review = getattr(provider, "run_review", None)
        if run_review is None:
            return (
                NodeState.ERROR,
                f"{provider.name} provider does not support native code review",
                None,
            )
        self._provider = provider
        context = AgentProviderContext(
            node=self.node,
            project=self.project,
            request_gate_handler=self._request_gate,
            system_context=system_context,
            store_root=self.store.root,
            skill_materialization=self._skill_materialization,
        )
        focus = (
            self.node.brief.check_what
            if self.node.brief is not None
            else (self.node.prompt.strip() or None)
        )
        target = self.node.review_target
        if target is None:
            return NodeState.ERROR, "code review target is missing", None
        spec = ReviewSpec(target=target, focus=focus)
        report: ReviewReport | None = None
        terminal_seen = False
        async for event in run_review(context, spec):
            await self._handle_provider_event(event)
            if event.kind == "review" and event.report is not None:
                report = event.report
            elif event.kind == "done":
                terminal_seen = True
                state = _state_from_provider(event.final_state)
                if event.final_state is not None and state is None:
                    return (
                        NodeState.ERROR,
                        f"{provider.name} provider returned unknown final_state: "
                        f"{event.final_state}",
                        report,
                    )
                return state or NodeState.DONE, None, report
            elif event.kind == "error":
                terminal_seen = True
                return NodeState.ERROR, event.error or "provider review error", report
        if not terminal_seen:
            return (
                NodeState.ERROR,
                f"{provider.name} review stream ended without a terminal event",
                report,
            )
        return NodeState.ERROR, "provider review failed", report

    def _publish_code_review_report(
        self, report: ReviewReport, *, stale: bool
    ) -> list[str]:
        output_dir = workspace_artifacts_dir(self.project, self.node.id)
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown = report.raw_markdown.strip()
        if stale:
            markdown = _STALE_REVIEW_WARNING + "\n\n" + markdown
        names = ["code-review-report.md"]
        (output_dir / names[0]).write_text(markdown + "\n", encoding="utf-8")
        if report.findings is not None:
            names.append("code-review-findings.json")
            (output_dir / names[-1]).write_text(
                json.dumps(
                    [asdict(finding) for finding in report.findings],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        publish_artifacts(self.project, self.node, names, self.store)
        return [
            ref.name
            for ref in self.node.artifacts
            if ref.status == "published"
        ]

    def _code_review_focus_warning(self) -> str | None:
        focus = (
            self.node.brief.check_what.strip()
            if self.node.brief is not None
            else self.node.prompt.strip()
        )
        if not focus:
            return None
        preset = get_model_preset(
            self.node.model_preset_id, store_root=self.store.root
        )
        return _CODEX_FOCUS_WARNING if preset.provider == "codex" else None

    async def _run_verifier(self) -> None:
        """Run a deterministic verifier script and write an executed preview."""
        self.node.commit_before = git_head(self.project.root_path)
        context_bundle = self._snapshot_context_bundle()
        self._snapshot_launch_settings(context_bundle)
        self._transition(NodeState.RUNNING, started=True)
        await self._emit_node_started()
        await self._emit_node_updated()

        final_state = NodeState.DONE
        error_msg: str | None = None
        exit_code: int | None = None
        timed_out = False
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        script = self.node.verify_script_ref or ""
        try:
            if not script:
                raise ValueError("missing verify_script_ref")
            script_path = Path(script)
            if not script_path.exists():
                raise ValueError(f"verify script not found: {script}")
            await self._emit(
                Activity(
                    kind="tool",
                    status="start",
                    id=f"verifier:{self.node.id}",
                    name="verifier",
                    summary=str(script_path),
                )
            )
            env = dict(os.environ)
            env["CI"] = "1"
            env["MINICLAW_PROJECT_ID"] = self.project.id
            env["MINICLAW_HOME"] = str(self.store.root)
            self._process = await asyncio.create_subprocess_exec(
                "bash",
                str(script_path),
                cwd=self.project.root_path,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_raw, stderr_raw = await asyncio.wait_for(
                    self._process.communicate(),
                    timeout=60.0,
                )
            except asyncio.TimeoutError:
                timed_out = True
                self._process.terminate()
                try:
                    stdout_raw, stderr_raw = await asyncio.wait_for(
                        self._process.communicate(),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    self._process.kill()
                    stdout_raw, stderr_raw = await self._process.communicate()
                exit_code = 124
            else:
                exit_code = int(self._process.returncode or 0)
            stdout = _decode_process_output(stdout_raw)
            stderr = _decode_process_output(stderr_raw)
            stdout_parts.append(stdout)
            stderr_parts.append(stderr)
            if stdout:
                await self._emit(
                    Activity(
                        kind="tool",
                        status="progress",
                        id=f"verifier:{self.node.id}:stdout",
                        name="verifier stdout",
                        summary=_tail_text(stdout, 240),
                        result=stdout,
                        result_kind="stdout",
                    )
                )
            if stderr:
                await self._emit(
                    Activity(
                        kind="tool",
                        status="progress",
                        id=f"verifier:{self.node.id}:stderr",
                        name="verifier stderr",
                        summary=_tail_text(stderr, 240),
                        result=stderr,
                        result_kind="stdout",
                    )
                )
            if timed_out or exit_code != 0:
                final_state = NodeState.ERROR
                tail = _tail_text(stderr or stdout, 2048)
                error_msg = tail or (
                    "verifier timed out" if timed_out else f"verifier exited {exit_code}"
                )
                await self._emit(ErrorEvent(message=error_msg))
            await self._emit(
                Activity(
                    kind="tool",
                    status="finish" if final_state is NodeState.DONE else "failed",
                    id=f"verifier:{self.node.id}",
                    name="verifier",
                    summary=(
                        "verify passed"
                        if final_state is NodeState.DONE
                        else f"verify failed: exit {exit_code}"
                    ),
                    result="\n".join(part for part in [stdout, stderr] if part),
                    result_kind="stdout",
                )
            )
        except asyncio.CancelledError:
            final_state = NodeState.CANCELLED
            await self.interrupt()
        except Exception as exc:  # noqa: BLE001
            logger.exception("verifier runner failed")
            final_state = NodeState.ERROR
            error_msg = f"Unexpected verifier error: {exc}"
            await self._emit(ErrorEvent(message=error_msg))
        finally:
            self._process = None

        if error_msg is not None:
            self.node.error = error_msg
        self.node.commit_after = git_head(self.project.root_path)
        self._write_verifier_preview(
            final_state,
            exit_code=exit_code,
            timed_out=timed_out,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
        )
        self._transition(final_state, finished=True)
        await self._emit_node_updated()
        await self._emit(TurnDone(node=self.node.model_dump()))

    async def _run_provider_turn(
        self,
        prompt: str,
        *,
        launch_instructions: str,
        system_context: str = "",
    ) -> tuple[NodeState, str | None]:
        """Run one provider turn for this node.

        Repair prompts reuse the same node id and provider session, but
        should not mutate ``self.node.prompt``. A shallow node copy
        carries the per-turn prompt into the provider context while
        provider metadata events still update the canonical node.
        """
        preset = get_model_preset(
            self.node.model_preset_id, store_root=self.store.root
        )
        provider = _make_provider(preset.provider)
        self._provider = provider
        turn_node = self.node.model_copy(update={"prompt": prompt})
        context = AgentProviderContext(
            node=turn_node,
            project=self.project,
            request_gate_handler=self._request_gate,
            system_context=system_context,
            launch_instructions=launch_instructions,
            store_root=self.store.root,
            skill_materialization=self._skill_materialization,
        )
        final_state: NodeState | None = None
        error_msg: str | None = None
        terminal_seen = False
        try:
            async for ev in provider.run(context):
                await self._handle_provider_event(ev)
                if ev.kind == "done":
                    terminal_seen = True
                    provider_state = _state_from_provider(ev.final_state)
                    if ev.final_state is not None and provider_state is None:
                        error_msg = (
                            f"{provider.name} provider returned unknown final_state: "
                            f"{ev.final_state}"
                        )
                        final_state = NodeState.ERROR
                        await self._emit(ErrorEvent(message=error_msg))
                    else:
                        final_state = provider_state or NodeState.DONE
                    break
                if ev.kind == "error":
                    terminal_seen = True
                    error_msg = ev.error or "provider error"
                    final_state = NodeState.ERROR
                    if ev.error is None:
                        await self._emit(ErrorEvent(message=error_msg))
                    break
        finally:
            self._provider = None
        if not terminal_seen:
            error_msg = (
                f"{provider.name} provider stream ended without a terminal event"
            )
            final_state = NodeState.ERROR
            await self._emit(ErrorEvent(message=error_msg))
        return final_state or NodeState.ERROR, error_msg

    # ---- materialization + reap ----

    def _materialize_lane(self) -> None:
        workspace_artifacts_dir(self.project, self.node.id).mkdir(
            parents=True,
            exist_ok=True,
        )
        lane_id = self.node.planspace_id or ""
        if not lane_id:
            self._lane_root = None
            self._pre_snapshot = {}
            return
        target_root = runner_lane_root(self.project, self.node.id, lane_id)
        self._lane_root = materialize_active_lane(
            self.project,
            lane_id,
            self.store,
            current_node_id=self.node.id,
            target_root=target_root,
        )

    def _lane_prompt_path(self) -> str:
        lane_id = self.node.planspace_id or ""
        return f"{GRAPH_RUNS_DIRNAME}/{self.node.id}/lanes/{lane_id}".rstrip("/")

    def _foreign_dependency_hosts(self) -> dict[str, str]:
        labels = {
            host.get("mid"): host.get("label") or host.get("mid")
            for host in self.store.list_hosts(self.project.id)
            if isinstance(host.get("mid"), str)
        }
        foreign: dict[str, str] = {}
        for dep_id in self.node.scheduled_deps:
            dependency = self.store.load_node(self.project.id, dep_id)
            if (
                dependency is None
                or not dependency.owner_host_id
                or dependency.owner_host_id == self.store.machine.id
            ):
                continue
            label = labels.get(dependency.owner_host_id) or dependency.owner_host_id
            foreign[dep_id] = str(label)
        return foreign

    def _cleanup_lane_projection(self) -> None:
        run_root = Path(self.project.root_path) / GRAPH_RUNS_DIRNAME / self.node.id
        shutil.rmtree(run_root, ignore_errors=True)

    def _take_pre_snapshot(self) -> None:
        if self._lane_root is None:
            self._pre_snapshot = {}
            return
        self._pre_snapshot = snapshot_lane(self._lane_root)

    def _is_human_interact_review(self) -> bool:
        return (
            self.node.kind is NodeKind.AGENT
            and self.node.category is Category.REVIEW
            and self.node.subtype is ReviewSubtype.HUMAN_INTERACT_REVIEW
        )

    def _is_cold_start(self) -> bool:
        return (
            self.node.kind is NodeKind.AGENT
            and self.node.agent_op_kind == COLD_START_AGENT_OP_KIND
        )

    def _clear_context_snapshot(self) -> None:
        """Record that no ContextSpace text was composed for this node.

        The audit must say "nothing was injected", not merely omit the fields —
        a stale bundle id from an earlier attempt would otherwise read as one.
        """
        self.node.context_bundle_id = None
        self.node.context_bundle_path = None
        self.node.system_context_snapshot = ""
        self.store.update_node(self.node)

    def _scan_workspace_artifacts(self) -> list[str]:
        """Return publishable filenames the agent left in its outputs directory.

        A cold-start agent is not told to declare artifacts, so the framework
        declares whatever it finds. Sorting keeps the published set stable when
        the directory holds more files than ``MAX_ARTIFACTS_PER_NODE``.
        """
        output_dir = workspace_artifacts_dir(self.project, self.node.id)
        try:
            entries = sorted(output_dir.iterdir())
        except OSError:
            return []
        return [
            entry.name
            for entry in entries
            if entry.is_file()
            and entry.suffix in ALLOWED_ARTIFACT_SUFFIXES
        ]

    def _write_cold_start_preview(
        self, final_state: NodeState, *, reason: str = ""
    ) -> None:
        if final_state is not NodeState.DONE:
            self._write_stub_preview(final_state, reason=reason)
            return
        summary = _tail_text(
            self._cold_start_text.strip(), _COLD_START_SUMMARY_LIMIT
        )
        self.node.summary = summary or _COLD_START_EMPTY_SUMMARY
        declared = self._scan_workspace_artifacts()
        publish_artifacts(self.project, self.node, declared, self.store)
        self._persist_executed_preview(
            final_state,
            motivation=_COLD_START_MOTIVATION,
            summary=self.node.summary,
            next_implications=_COLD_START_IMPLICATIONS,
            artifacts=[
                ref.name
                for ref in self.node.artifacts
                if ref.status == "published"
            ],
        )

    async def _request_human_review_prose(self) -> str:
        """Emit a ``human_review_prose`` interaction and await the user's
        free-form prose.

        Returns the prose (stripped). An empty return signals abort —
        the caller treats the node as cancelled and stub-previews.
        """
        interaction_id = uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._gates[interaction_id] = future

        tool_input: dict[str, Any] = {}
        if self.node.brief is not None:
            tool_input["brief"] = self.node.brief.model_dump()
        if self.node.planspace_id:
            tool_input["human_review_path"] = (
                f"{self._lane_prompt_path()}/nodes/{self.node.id}/human-review.md"
            )

        await self._emit(
            InteractionRequest(
                id=interaction_id,
                interaction_type="human_review_prose",  # type: ignore[arg-type]
                tool_name="human_review_prose",
                tool_input=tool_input,
            )
        )
        try:
            response = await future
        finally:
            self._gates.pop(interaction_id, None)
        return _human_review_prose(response).strip()

    def _write_human_review_prose(self, prose: str) -> None:
        """Persist the prose to the durable node store AND the
        materialized lane subtree so the reviewer agent can ``Read`` it.
        """
        durable_dir = self.store.node_dir(self.project.id, self.node.id)
        durable_dir.mkdir(parents=True, exist_ok=True)
        (durable_dir / "human-review.md").write_text(prose, encoding="utf-8")
        if self._lane_root is not None:
            materialized_dir = node_dir(self._lane_root, self.node.id)
            materialized_dir.mkdir(parents=True, exist_ok=True)
            (materialized_dir / "human-review.md").write_text(
                prose, encoding="utf-8"
            )

    def _append_error(self, reason: str) -> None:
        self.node.error = (self.node.error + "\n" if self.node.error else "") + reason

    async def _reap_with_preview_repairs(self, system_context: str) -> NodeState:
        """Reap the lane, re-prompting inline for preview repair up to
        the proposal's retry bound before writing the framework stub.
        """
        ok, reason = await self._try_reap_and_persist()
        if ok:
            return NodeState.DONE

        last_reason = reason
        for attempt in range(1, _PREVIEW_REPAIR_RETRIES + 1):
            prompt = _preview_repair_prompt(self.node, last_reason, attempt)
            await self._emit(
                Activity(
                    kind="agent",
                    status="progress",
                    id=f"preview-repair:{self.node.id}:{attempt}",
                    name="Preview contract repair",
                    summary=(
                        f"{attempt}/{_PREVIEW_REPAIR_RETRIES}: {last_reason}"
                    ),
                )
            )
            repair_state, repair_error = await self._run_provider_turn(
                prompt,
                launch_instructions="",
                system_context=system_context,
            )
            if repair_error is not None:
                self.node.error = repair_error
                self._write_stub_preview(NodeState.ERROR, reason=repair_error)
                return NodeState.ERROR
            if repair_state is not NodeState.DONE:
                self._write_stub_preview(
                    repair_state,
                    reason=(
                        "preview repair turn ended before producing a valid "
                        f"preview (state={repair_state.value})"
                    ),
                )
                return repair_state
            ok, reason = await self._try_reap_and_persist()
            if ok:
                return NodeState.DONE
            last_reason = reason

        reason = (
            "preview contract abandoned after "
            f"{_PREVIEW_REPAIR_RETRIES} repair attempts: {last_reason}"
        )
        self._append_error(reason)
        self._write_stub_preview(NodeState.ERROR, reason=reason)
        return NodeState.ERROR

    async def _try_reap_and_persist(self) -> tuple[bool, str]:
        async with self._reap_lock:
            return self._try_reap_and_persist_unlocked()

    def _try_reap_and_persist_unlocked(self) -> tuple[bool, str]:
        if self._lane_root is None:
            return False, "no materialized lane exists for this node"
        try:
            result = reap_lane(
                self.project, self.node, self._lane_root, self._pre_snapshot, self.store
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("reap pipeline raised")
            return False, f"reap pipeline error: {exc}"
        if result.fatal:
            return False, "; ".join(result.rejection_reasons)
        if not result.own_preview_ok:
            return False, "; ".join(result.rejection_reasons)
        if result.own_preview is None:
            return False, "reap did not return the running node preview"
        own_path = node_dir(self._lane_root, self.node.id) / "preview.json"
        try:
            text = own_path.read_text(encoding="utf-8")
            self.store.write_node_preview(self.project.id, self.node.id, text)
        except OSError as exc:
            logger.exception("failed to persist own preview to durable store")
            return False, f"failed to persist own preview: {exc}"
        publish_artifacts(
            self.project,
            self.node,
            result.own_preview.artifacts,
            self.store,
        )
        for virtual in result.new_virtuals + result.modified_virtuals:
            self.store.update_node(virtual)
        return True, ""

    async def _reap_and_finalize(
        self, provider_final_state: NodeState, *, reason: str = ""
    ) -> NodeState:
        """Run reap (when applicable) and persist the node's own preview.

        Returns the effective terminal state. Cancelled / error runs
        skip reap entirely — virtual writes from failed sessions are
        discarded.
        """
        if provider_final_state is not NodeState.DONE:
            self._write_stub_preview(provider_final_state, reason=reason)
            return provider_final_state
        if self._lane_root is None:
            ok, _reason = self._try_persist_unlaned_preview()
            if not ok:
                self._write_stub_preview(provider_final_state)
            return provider_final_state
        ok, reason = await self._try_reap_and_persist()
        if not ok:
            self._append_error(f"Reap fatal: {reason}")
            self._write_stub_preview(NodeState.ERROR, reason=reason)
            return NodeState.ERROR
        return provider_final_state

    def _try_persist_unlaned_preview(self) -> tuple[bool, str]:
        own_path = (
            node_dir(runner_lane_root(self.project, self.node.id, ""), self.node.id)
            / "preview.json"
        )
        try:
            text = own_path.read_text(encoding="utf-8")
            preview = parse_preview(text)
        except PreviewValidationError as exc:
            return False, "; ".join(exc.issues)
        except OSError as exc:
            return False, f"cannot read own preview: {exc}"
        if not isinstance(preview, ExecutedPreview):
            return False, "running node wrote a virtual preview as its own"
        issues = validate_preview_for_node(preview, self.node)
        if issues:
            return False, "; ".join(issues)
        try:
            self.store.write_node_preview(self.project.id, self.node.id, text)
            publish_artifacts(
                self.project,
                self.node,
                preview.artifacts,
                self.store,
            )
        except OSError as exc:
            logger.exception("failed to persist unlaned preview")
            return False, f"failed to persist own preview: {exc}"
        return True, ""

    def _persist_executed_preview(
        self,
        final_state: NodeState,
        *,
        motivation: str,
        summary: str,
        next_implications: str,
        artifacts: list[str] | None = None,
    ) -> None:
        original_state = self.node.state
        if final_state not in (NodeState.DONE, NodeState.ERROR, NodeState.CANCELLED):
            final_state = NodeState.ERROR
        self.node.state = final_state
        try:
            text = render_executed_preview(
                self.node,
                motivation=motivation,
                summary=summary,
                next_implications=next_implications,
                artifacts=artifacts,
            )
            self.store.write_node_preview(self.project.id, self.node.id, text)
        except Exception:  # noqa: BLE001
            logger.exception("failed to write preview")
        finally:
            self.node.state = original_state

    def _write_stub_preview(self, final_state: NodeState, *, reason: str = "") -> None:
        clear_published_artifacts(self.project, self.node, self.store)
        self._persist_executed_preview(
            final_state,
            motivation=self.node.prompt[:200] if self.node.prompt else "(no motivation recorded)",
            summary=reason or "(framework stub — agent did not write its own preview)",
            next_implications="(framework stub — agent did not record next implications)",
        )

    def _write_op_preview(self, final_state: NodeState) -> None:
        self._persist_executed_preview(
            final_state,
            motivation=f"Auto-commit op for parent {self.node.parent_node_id or 'unknown'}",
            summary=self.node.summary or self.node.error or "op completed",
            next_implications="(commit op — completes the lane's filesystem record)",
        )

    def _write_verifier_preview(
        self,
        final_state: NodeState,
        *,
        exit_code: int | None,
        timed_out: bool,
        stdout: str,
        stderr: str,
    ) -> None:
        if final_state is NodeState.DONE:
            summary = "verify passed"
            next_implications = ""
        elif final_state is NodeState.CANCELLED:
            summary = "verify cancelled"
            next_implications = "verifier cancelled before a verdict"
        else:
            code = exit_code if exit_code is not None else "unknown"
            summary = f"verify failed: exit {code}"
            body = _tail_text(stderr or stdout or self.node.error or "", 2048)
            if timed_out:
                body = (body + "\n" if body else "") + "timed out"
            next_implications = body
        self._persist_executed_preview(
            final_state,
            motivation=self.node.brief.check_what if self.node.brief else "programmatic verifier",
            summary=summary,
            next_implications=next_implications,
        )

    # ---- bundle + settings snapshots ----

    def _build_agent_launch_instructions(self, context_bundle: Any) -> str:
        return _compose_launch_instructions(
            _authoring_init_block(self.node, self.store.root),
            build_category_launch_block(
                self.node,
                lane_path=self._lane_prompt_path(),
                outputs_path=str(
                    workspace_artifacts_dir(
                        self.project,
                        self.node.id,
                    )
                ),
                store_root=self.store.root,
            ),
            _skill_suggestion_block(self._skill_materialization),
            build_qa_mode_block(
                self.node,
                provider=get_model_preset(
                    self.node.model_preset_id,
                    store_root=self.store.root,
                ).provider,
            ),
            build_dependency_launch_block(
                self.node,
                lane_path=self._lane_prompt_path(),
                foreign_hosts=self._foreign_dependency_hosts(),
            ),
            context_bundle.turn_text,
            language_launch_instruction(
                project_preferred_language(self.project)
            ),
            shared_host_processes_block(),
            subagent_synchronicity_block(),
            anti_self_poisoning_block(),
        )

    def _snapshot_context_bundle(self):
        bundle = compose_context_bundle(
            self.project,
            self.node,
            store_root=self.store.root,
        )
        self.node.context_bundle_id = bundle.bundle_id
        try:
            self.node.context_bundle_path = str(
                bundle.bundle_path.relative_to(bundle.context_root)
            )
        except ValueError:
            self.node.context_bundle_path = str(bundle.bundle_path)
        self.node.system_context_snapshot = bundle.project_context
        self.store.update_node(self.node)
        return bundle

    def _snapshot_launch_settings(self, context_bundle: Any | None = None) -> None:
        attached = {
            key: value
            for key, value in self.node.settings_snapshot.items()
            if key in {"extra_principles", "extra_skills"}
        }
        snapshot: dict[str, Any] = {
            key: value
            for key, value in self.project.settings_override.items()
            if key
            not in {
                "model",
                "model_provider",
                "service_tier",
                "reasoning_effort",
            }
        }
        snapshot.update(attached)
        snapshot["cwd"] = self.project.root_path
        project_binding_id = (
            getattr(context_bundle, "project_binding_id", None)
            if context_bundle is not None
            else self.project.project_context_binding_id
        )
        active_planspace_id = self.node.planspace_id
        if project_binding_id:
            snapshot["project_context_binding_id"] = project_binding_id
        if active_planspace_id:
            snapshot["active_planspace_id"] = active_planspace_id
        if self.node.context_bundle_id:
            snapshot["context_bundle_id"] = self.node.context_bundle_id
        preferred_language = project_preferred_language(self.project)
        if preferred_language:
            snapshot["preferred_language"] = preferred_language
        if self.node.category is not None:
            snapshot["category"] = self.node.category.value
        self.node.settings_snapshot = snapshot

    def _materialize_skills(self) -> None:
        preset = get_model_preset(
            self.node.model_preset_id, store_root=self.store.root
        )
        workspace_root = (
            Path(self.project.root_path) / GRAPH_RUNS_DIRNAME / self.node.id
        )
        workspace_root.mkdir(parents=True, exist_ok=True)
        expanded = expand_skill_selections(
            self.node.settings_snapshot.get("extra_skills"),
            store_root=self.store.root,
        )
        if expanded:
            self.node.settings_snapshot["extra_skills"] = expanded
        else:
            self.node.settings_snapshot.pop("extra_skills", None)
        self._skill_materialization = materialize_agent_skills(
            expanded,
            provider=preset.provider,
            store_root=self.store.root,
            workspace_root=workspace_root,
        )
        self.node.settings_snapshot["skill_audit"] = list(
            self._skill_materialization.audit
        )
        self.store.update_node(self.node)

    def _snapshot_library_entries(self) -> dict[str, dict[str, str]]:
        """Capture per-entry hashes immediately before a librarian provider turn."""
        if self.node.agent_op_kind != "library_edit":
            return {}
        root = contextspace_root(self.store.root)
        return {
            "principle": _library_tree_snapshot(root / "plugs" / "principles"),
            "skill": _library_tree_snapshot(root / "skills"),
        }

    def _validate_library_authoring(
        self,
        before: dict[str, dict[str, str]],
        *,
        record_provenance: bool = True,
    ) -> None:
        """Validate and audit the single entry changed by a librarian turn.

        Provenance can be deferred until the final validation after preview repairs.
        """
        root = contextspace_root(self.store.root)
        libraries = {
            "principle": root / "plugs" / "principles",
            "skill": root / "skills",
        }
        after = {
            kind: _library_tree_snapshot(path)
            for kind, path in libraries.items()
        }
        touched = [
            (kind, slug)
            for kind in ("principle", "skill")
            for slug in sorted(set(before.get(kind, {})) | set(after[kind]))
            if before.get(kind, {}).get(slug) != after[kind].get(slug)
        ]
        audit: list[dict[str, Any]] = [
            {
                "kind": kind,
                "slug": slug,
                "action": (
                    "created" if slug not in before.get(kind, {}) else "refined"
                ),
                "content_hash": after[kind].get(slug),
                "verdict": "pending",
            }
            for kind, slug in touched
        ]

        if not touched:
            reason = "librarian node finished without authoring anything"
            self.node.settings_snapshot["library_audit"] = [
                {"verdict": "error", "error": reason}
            ]
            self.store.update_node(self.node)
            raise LibraryAuthoringError(reason)
        if len(touched) != 1:
            entries = ", ".join(f"{kind} `{slug}`" for kind, slug in touched)
            reason = f"librarian node touched multiple library entries: {entries}"
            for item in audit:
                item["verdict"] = "error"
                item["error"] = reason
            self.node.settings_snapshot["library_audit"] = audit
            self.store.update_node(self.node)
            raise LibraryAuthoringError(reason)

        kind, slug = touched[0]
        item = audit[0]
        try:
            if not _LIBRARY_SLUG_RE.fullmatch(slug):
                raise LibraryAuthoringError(
                    f"invalid {kind} slug `{slug}`: expected kebab-case"
                )
            target = libraries[kind] / slug
            if target.is_symlink():
                raise LibraryAuthoringError(
                    f"invalid {kind} `{slug}`: entry must not be a symlink"
                )
            if not target.exists():
                raise LibraryAuthoringError(
                    f"invalid {kind} `{slug}`: entry was removed; librarian "
                    "runs may not delete library entries"
                )
            if not target.is_dir():
                raise LibraryAuthoringError(
                    f"invalid {kind} `{slug}`: entry must be a directory"
                )
            if kind == "skill":
                _validate_tree(target)
                inspected = inspect_agent_skill(target, context_root=root)
                item["content_hash"] = inspected["content_hash"]
            else:
                item["content_hash"] = _validate_authored_principle(target, slug)
            if (
                record_provenance
                and kind == "skill"
                and item["action"] == "created"
            ):
                record_authored_agent_skill(
                    slug,
                    node_id=self.node.id,
                    store_root=self.store.root,
                )
        except Exception as exc:  # noqa: BLE001
            reason = str(exc)
            item["verdict"] = "error"
            item["error"] = reason
            self.node.settings_snapshot["library_audit"] = audit
            self.store.update_node(self.node)
            if isinstance(exc, LibraryAuthoringError):
                raise
            raise LibraryAuthoringError(
                f"invalid {kind} `{slug}`: {reason}"
            ) from exc

        item["verdict"] = "valid"
        self.node.settings_snapshot["library_audit"] = audit
        self.store.update_node(self.node)

    def _validate_launch_settings(self) -> None:
        launch_project = self.project.model_copy(
            update={"active_planspace_id": self.node.planspace_id}
        )
        require_resolvable_active_planspace(
            launch_project,
            store_root=self.store.root,
        )

    # ---- state transitions ----

    def _transition(
        self,
        state: NodeState,
        *,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        previous_state = self.node.state
        self.node.state = state
        now = time.time()
        if started and self.node.started_at is None:
            self.node.started_at = now
        if finished:
            self.node.finished_at = now
        self.store.update_node(self.node)
        if started or finished:
            try:
                activity_at = max(
                    timestamp
                    for timestamp in (
                        self.node.finished_at,
                        self.node.started_at,
                        self.node.created_at,
                    )
                    if timestamp is not None
                )
                self.store.record_project_activity(self.project.id, activity_at)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "failed to update project activity for %s",
                    self.project.id,
                )
        if state is not previous_state and self.on_state_change is not None:
            try:
                asyncio.get_running_loop().create_task(
                    self.on_state_change(self.node, previous_state)
                )
            except RuntimeError:
                pass

    # ---- provider event handling ----

    async def _handle_provider_event(self, ev: AgentProviderEvent) -> None:
        if ev.kind == "event" and ev.event is not None:
            if isinstance(ev.event, Usage):
                self._record_usage(ev.event)
            elif isinstance(ev.event, TextDelta) and self._is_cold_start():
                # Both providers reach TextDelta through this one channel, so a
                # tail kept here is provider-neutral. This is the turn's text
                # tail rather than its last assistant message — for a summary
                # that is the better approximation anyway, since the closing
                # conclusion is always at the end.
                self._cold_start_text = _tail_text(
                    self._cold_start_text + ev.event.text,
                    _COLD_START_SUMMARY_LIMIT,
                )
            skill_used = (
                self._record_skill_use(ev.event)
                if isinstance(ev.event, Activity)
                else False
            )
            await self._emit(ev.event)
            if isinstance(ev.event, Usage) or skill_used:
                await self._emit_node_updated()
            return
        if ev.kind == "session" and ev.session_id:
            self.node.provider_session_id = ev.session_id
            self.store.update_node(self.node)
            await self._emit_node_updated()
            return
        if ev.kind == "turn" and ev.turn_id:
            self.node.provider_turn_id = ev.turn_id
            self.store.update_node(self.node)
            await self._emit_node_updated()
            return
        if ev.kind == "error" and ev.error:
            await self._emit(ErrorEvent(message=ev.error))
            return

    def _record_usage(self, usage: Usage) -> None:
        self.node.usage = TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            cumulative_output_tokens=usage.cumulative_output_tokens,
            cumulative_cache_creation_tokens=usage.cumulative_cache_creation_tokens,
        )
        self.store.update_node(self.node)

    def _record_skill_use(self, activity: Activity) -> bool:
        audit = self.node.settings_snapshot.get("skill_audit")
        if not isinstance(audit, list):
            return False
        combined = "\n".join(
            str(value)
            for value in (
                activity.summary,
                activity.parameters,
                activity.command,
                activity.result,
            )
            if value
        )
        lowered = combined.lower().replace("\\", "/")
        named_skill = ""
        if activity.name.lower() == "skill":
            try:
                payload = json.loads(activity.summary)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                candidate = (
                    payload.get("skill")
                    or payload.get("name")
                    or payload.get("command")
                )
                if isinstance(candidate, str):
                    named_skill = candidate.strip().lower()
        changed = False
        for item in audit:
            if not isinstance(item, dict) or item.get("used"):
                continue
            slug = str(item.get("slug") or "").lower()
            name = str(item.get("name") or "").lower()
            materialized = str(item.get("materialized_path") or "")
            materialized_skill_md = (
                f"{materialized}/SKILL.md".lower().replace("\\", "/")
                if materialized
                else ""
            )
            confident = bool(
                (
                    named_skill
                    and (
                        named_skill in {slug, name, f"skills.{slug}"}
                        or named_skill.endswith(f":{slug}")
                    )
                )
                or (materialized_skill_md and materialized_skill_md in lowered)
                or (slug and f"/skills/{slug}/skill.md" in lowered)
            )
            if confident:
                item["used"] = True
                item["used_at"] = time.time()
                changed = True
        if changed:
            self.store.update_node(self.node)
        return changed

    # ---- emit (persist + push) ----

    async def _emit(self, ev: BaseModel) -> None:
        self._seq += 1
        if hasattr(ev, "seq"):
            ev.seq = self._seq  # type: ignore[attr-defined]
        data = ev.model_dump()
        data["node_id"] = self.node.id
        try:
            self.store.append_event(self.project.id, self.node.id, self._seq, data)
        except Exception:  # noqa: BLE001
            logger.exception("failed to persist event")
        try:
            await self.on_event(data)
        except Exception:  # noqa: BLE001
            logger.exception("on_event handler failed")

    async def _emit_node_updated(self) -> None:
        await self._emit(NodeUpdated(node=self.node.model_dump()))

    async def _emit_node_started(self) -> None:
        await self._emit(
            NodeStarted(
                node_id=self.node.id,
                parent_node_id=self.node.parent_node_id,
                kind=self.node.kind.value,
                provider=self.node.provider,
                model_preset_id=self.node.model_preset_id,
                category=(
                    self.node.category.value
                    if self.node.category is not None
                    else None
                ),
                subtype=(
                    self.node.subtype.value
                    if self.node.subtype is not None
                    else None
                ),
                agent_op_kind=self.node.agent_op_kind,
                prompt=self.node.prompt,
                node=self.node.model_dump(),
            )
        )

    # ---- inline gate flow ----

    async def _request_gate(self, request: GateRequest) -> dict[str, Any]:
        gate = HumanGate(
            id=uuid4().hex[:12],
            node_id=self.node.id,
            kind=GateKind.INLINE,
            subtype=request.subtype,
            tool_name=request.tool_name,
            tool_input=request.tool_input,
            suggestions=request.suggestions,
        )
        self.store.append_gate(self.project.id, gate, "created")
        self._gate_records[gate.id] = gate

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._gates[gate.id] = future

        self._transition(NodeState.WAITING)
        await self._emit_node_updated()
        await self._emit(
            InteractionRequest(
                id=gate.id,
                interaction_type=request.subtype.value,  # type: ignore[arg-type]
                tool_name=request.tool_name,
                tool_input=request.tool_input,
                suggestions=request.suggestions,
                response_hint=request.response_hint,
            )
        )

        try:
            if request.subtype is GateSubtype.ASK_USER:
                response = await future
            else:
                settings = load_global_config(self.store.root).tool_requests
                timeout_seconds = settings.timeout_seconds
                try:
                    response = await asyncio.wait_for(
                        future, timeout=timeout_seconds
                    )
                except TimeoutError:
                    allow = settings.timeout_action == "accept"
                    action = "accepted" if allow else "rejected"
                    message = (
                        f"Tool request timed out after {timeout_seconds:g}s and was "
                        f"automatically {action}."
                    )
                    logger.info(
                        "%s node=%s tool=%s",
                        message,
                        self.node.id,
                        request.tool_name,
                    )
                    response = {"allow": allow, "message": message}
        finally:
            self._gates.pop(gate.id, None)
            if gate.state is GateState.PENDING:
                gate.state = GateState.RESOLVED
                gate.resolved_at = time.time()
            self._transition(NodeState.RUNNING)
            await self._emit_node_updated()

        gate.response = response
        self.store.append_gate(self.project.id, gate, "resolved")
        self._gate_records.pop(gate.id, None)
        return response

    def current_gate_summary(self) -> dict[str, Any] | None:
        """Describe the gate this node is currently blocked on, if any.

        Read-only view for the cross-project active-node endpoint, which needs
        to say *what* a waiting node wants. Only gates with a live future are
        reported: a record whose future is gone is no longer answerable, so
        surfacing it would offer the user a gate they cannot resolve.
        """
        pending = [
            record
            for gate_id, record in self._gate_records.items()
            if record.state is GateState.PENDING and gate_id in self._gates
        ]
        if not pending:
            return None
        gate = max(pending, key=lambda record: record.created_at)
        return {
            "id": gate.id,
            "subtype": gate.subtype.value,
            "tool_name": gate.tool_name,
            "summary": _gate_summary_text(gate),
        }

    def _resolve_open_gates(self) -> None:
        for gate_id, fut in list(self._gates.items()):
            if not fut.done():
                fut.set_result({"allow": False, "message": "node ended"})
                record = self._gate_records.get(gate_id)
                if record is not None and record.state is GateState.PENDING:
                    record.state = GateState.RESOLVED
                    record.resolved_at = time.time()
                    record.response = {"allow": False, "message": "node ended"}
                    self.store.append_gate(self.project.id, record, "resolved")
        self._gates.clear()
        self._gate_records.clear()


GATE_SUMMARY_MAX_CHARS = 80


def _gate_summary_text(gate: HumanGate) -> str:
    """One short line describing what a blocked node is asking for."""
    tool_input = gate.tool_input if isinstance(gate.tool_input, dict) else {}
    if gate.subtype is GateSubtype.ASK_USER:
        questions = tool_input.get("questions")
        if isinstance(questions, list):
            for item in questions:
                if not isinstance(item, dict):
                    continue
                text = item.get("question") or item.get("header")
                if isinstance(text, str) and text.strip():
                    return _clip(text)
        return "等待回答"
    for key in ("command", "file_path", "path", "url", "pattern"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value)
    return gate.tool_name or "等待确认"


def _clip(value: str) -> str:
    text = " ".join(value.split())
    if len(text) <= GATE_SUMMARY_MAX_CHARS:
        return text
    return text[: GATE_SUMMARY_MAX_CHARS - 1] + "…"


def _make_provider(provider: str) -> AgentProvider:
    normalized = (provider or "").lower()
    if normalized == "codex":
        return CodexProvider()
    if normalized == "claude":
        return ClaudeProvider()
    raise ValueError(f"unknown provider: {provider}")


def _compose_launch_instructions(*parts: str) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return "\n\n---\n\n".join(cleaned)


def _skill_suggestion_block(
    materialization: SkillMaterialization | None,
) -> str:
    if materialization is None:
        return ""
    return "\n".join(materialization.suggestions)


def _authoring_init_block(node: Node, store_root: Path) -> str:
    """Return the authoring preset selected by an agent operation kind."""
    root = contextspace_root(store_root)
    principles_dir = root / "plugs" / "principles"
    if node.agent_op_kind == "principle_edit":
        return build_principle_init_block(str(principles_dir))
    if node.agent_op_kind == "library_edit":
        return build_library_init_block(
            str(principles_dir),
            str(root / "skills"),
        )
    return ""


def _library_tree_snapshot(library: Path) -> dict[str, str]:
    if not library.exists():
        return {}
    try:
        entries = sorted(library.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise LibraryAuthoringError(f"cannot inspect library {library}: {exc}") from exc
    return {entry.name: _library_entry_hash(entry) for entry in entries}


def _library_entry_hash(entry: Path) -> str:
    if entry.is_symlink():
        try:
            target = os.readlink(entry)
        except OSError as exc:
            target = f"unreadable:{exc}"
        return hashlib.sha256(f"symlink:{target}".encode("utf-8")).hexdigest()
    if entry.is_dir():
        try:
            return skill_content_hash(entry)
        except Exception as exc:  # noqa: BLE001
            return hashlib.sha256(f"invalid:{exc}".encode("utf-8")).hexdigest()
    digest = hashlib.sha256()
    digest.update(b"file\0")
    try:
        digest.update(entry.read_bytes())
    except OSError as exc:
        digest.update(f"unreadable:{exc}".encode("utf-8"))
    return digest.hexdigest()


def _validate_authored_principle(principle_dir: Path, slug: str) -> str:
    manifest_path = principle_dir / "manifest.yaml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LibraryAuthoringError(
            f"invalid principle `{slug}`: cannot read manifest.yaml: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise LibraryAuthoringError(
            f"invalid principle `{slug}`: malformed manifest.yaml: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise LibraryAuthoringError(
            f"invalid principle `{slug}`: manifest.yaml must be a mapping"
        )
    if manifest.get("kind") != "principle":
        raise LibraryAuthoringError(
            f"invalid principle `{slug}`: manifest kind must be `principle`"
        )
    expected_id = f"principles.{slug}"
    if manifest.get("id") != expected_id:
        raise LibraryAuthoringError(
            f"invalid principle `{slug}`: manifest id must be `{expected_id}`"
        )
    context_path = principle_dir / "CONTEXT.md"
    try:
        context = context_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LibraryAuthoringError(
            f"invalid principle `{slug}`: cannot read CONTEXT.md: {exc}"
        ) from exc
    if not context.strip():
        raise LibraryAuthoringError(
            f"invalid principle `{slug}`: CONTEXT.md must be non-empty"
        )
    try:
        return skill_content_hash(principle_dir)
    except Exception as exc:  # noqa: BLE001
        raise LibraryAuthoringError(
            f"invalid principle `{slug}`: {exc}"
        ) from exc


def _preview_repair_prompt(node: Node, reason: str, attempt: int) -> str:
    lane = node.planspace_id or ""
    category = node.category.value if node.category is not None else "regular"
    lines = [
        "MiniClaw2 could not accept your graph preview writes.",
        "",
        f"Repair attempt {attempt} of {_PREVIEW_REPAIR_RETRIES}.",
        "",
        "Validation failure:",
        reason or "(no structured reason recorded)",
        "",
        "You must now write a valid executed preview JSON file at:",
        f"{GRAPH_RUNS_DIRNAME}/{node.id}/lanes/{lane}/nodes/{node.id}/preview.json",
        "",
        "Use exactly this schema, with no unknown fields:",
        "{",
        f'  "id": "{node.id}",',
        '  "kind": "agent",',
        f'  "category": "{category}",',
    ]
    if node.category is Category.REVIEW and node.subtype is not None:
        lines.append(f'  "subtype": "{node.subtype.value}",')
    lines.extend([
        '  "state": "done",',
        '  "ran_at": "<ISO 8601 UTC timestamp>",',
        f'  "lane": "{lane}",',
        '  "motivation": "<why this node ran>",',
        '  "summary": "<what happened and the key outcome>",',
        '  "next_implications": "<what this enables or blocks downstream>"',
        "}",
        "",
        "If the failure mentions virtual previews, repair or remove only the invalid graph-preview writes under this lane. Do not modify ordinary worktree files unless the validation failure explicitly requires it.",
    ])
    return "\n".join(lines)


def _state_from_provider(value: str | None) -> NodeState | None:
    if value == "cancelled":
        return NodeState.CANCELLED
    if value == "error":
        return NodeState.ERROR
    if value == "done":
        return NodeState.DONE
    return None


def _human_review_prose(response: Any) -> str:
    """Return canonical ``response.prose`` from an interaction response."""
    if not isinstance(response, dict):
        return ""
    nested = response.get("response")
    if isinstance(nested, dict):
        prose = nested.get("prose")
        if isinstance(prose, str):
            return prose
    return ""


def _decode_process_output(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


def _tail_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _review_summary(report: ReviewReport) -> str:
    if report.verdict:
        detail = (report.explanation or "").strip()
        return f"{report.verdict}: {detail}".strip().rstrip(":")[:1000]
    text = report.raw_markdown.strip()
    if not text:
        return "native code review completed"
    paragraph = text.split("\n\n", 1)[0].strip()
    return paragraph[:1000]


def _review_implications(report: ReviewReport) -> str:
    if report.findings:
        lines: list[str] = []
        for finding in report.findings[:8]:
            location = finding.file or ""
            if location and finding.line_start is not None:
                location += f":{finding.line_start}"
            lines.append(
                f"{finding.title} ({location})" if location else finding.title
            )
        return "\n".join(lines)
    return "See code-review-report.md for the native review findings."
