"""Scenario launch — seeds files into a temporary workspace and kicks off the first node."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..domain import Project
from ..registry import ProjectRegistry
from .loader import Scenario, ScenarioError, load_scenario


def launch_scenario(
    name: str,
    provider: str,
    registry: ProjectRegistry,
) -> tuple[Project, Scenario]:
    """Create a temporary project for ``name`` + ``provider`` and start step 0.

    Multi-step scenarios chain follow-up steps via the same ``runner_done``
    hook the auto-commit op uses; v1 (Tier 1) only ships single-step
    scenarios, so the registry hook is unchanged. Tiers 2-4 will add a
    scenario-step expander analogous to ``_spawn_op_commit``.

    Raises :class:`ScenarioError` if the scenario is malformed or the
    provider isn't supported by it.
    """
    scenario = load_scenario(name)
    if provider.lower() not in scenario.providers:
        raise ScenarioError(
            f"scenario {name!r} does not support provider {provider!r}"
        )

    project = registry.create_project(
        cwd=None,
        provider=provider,
        auto_commit=scenario.auto_commit or None,
        permission_mode=scenario.permission_mode,
        approval_policy=_approval_policy_for(provider, scenario.permission_mode),
        sandbox=_sandbox_for(provider, scenario.permission_mode),
        temporary=True,
        scenario_name=name,
    )

    _seed_workspace(scenario, Path(project.root_path))

    first = scenario.nodes[0]
    if first.kind == "agent":
        runner = registry.start_node(
            project.id,
            first.prompt,
            scenario_step_id=first.id,
        )
    else:
        # Gate kinds are retired; scenarios with non-agent first steps
        # cannot launch until the scenario is updated.
        registry.delete_project(project.id)
        raise ScenarioError(
            f"scenario first-node kind {first.kind!r} is no longer supported"
        )

    if runner is None:
        # Should not happen — the project was just created so nothing is running.
        registry.delete_project(project.id)
        raise ScenarioError(f"failed to start scenario {name!r}")

    return project, scenario


def _seed_workspace(scenario: Scenario, root: Path) -> None:
    for src, dst_rel in scenario.seed:
        target = (root / dst_rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise ScenarioError(
                f"seed destination escapes workspace root: {dst_rel}"
            ) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(src, target)


def _approval_policy_for(provider: str, permission_mode: str | None) -> str | None:
    if provider.lower() != "codex":
        return None
    if permission_mode == "bypassPermissions":
        return "never"
    if permission_mode == "default":
        return "untrusted"
    return None


def _sandbox_for(provider: str, permission_mode: str | None) -> str | None:
    if provider.lower() != "codex":
        return None
    if permission_mode == "bypassPermissions":
        return "workspace-write"
    if permission_mode == "default":
        return "read-only"
    return None
