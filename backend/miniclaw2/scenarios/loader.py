"""Scenario discovery + YAML parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..artifacts import validate_node_output_path


SCENARIOS_DIR = Path(__file__).parent / "bundled"


class ScenarioError(Exception):
    """Raised when a scenario's YAML or referenced files are invalid."""


@dataclass(slots=True)
class NodeSpec:
    """One step in a scenario — a node to enqueue."""

    id: str
    kind: str            # "agent" | "gate"
    prompt: str
    contract: str = ""
    output_kind: str = "freeform"
    output_path: str = ""
    brief_from: str = ""        # for gate steps: source agent step id
    response_path: str = ""     # gate-only: default path for write-json response
    resume_from: str = ""       # agent-only: source step whose session this resumes
    when_step: str = ""         # predicate target — must be an earlier gate step
    when_decision: str = ""     # "approved" | "rejected" — required iff when_step set


@dataclass(slots=True)
class Scenario:
    """A loaded scenario; immutable view over its on-disk files."""

    name: str
    brief: str
    providers: list[str]
    auto_commit: bool
    permission_mode: str | None
    nodes: list[NodeSpec]
    seed: list[tuple[Path, str]]  # (source path, relative dest in tempdir)
    root: Path
    acceptance: str
    verify_path: Path

    def metadata(self) -> dict[str, Any]:
        """Frontend-shaped summary (no body content)."""
        return {
            "name": self.name,
            "brief": self.brief,
            "providers": list(self.providers),
            "auto_commit": self.auto_commit,
            "node_count": len(self.nodes),
        }


def list_scenarios() -> list[Scenario]:
    """Return every bundled scenario, sorted by name."""
    out: list[Scenario] = []
    if not SCENARIOS_DIR.exists():
        return out
    for child in sorted(SCENARIOS_DIR.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "scenario.yaml").exists():
            continue
        out.append(load_scenario(child.name))
    return out


def load_scenario(name: str) -> Scenario:
    """Parse one scenario by directory name. Raises :class:`ScenarioError`."""
    root = SCENARIOS_DIR / name
    if not root.is_dir():
        raise ScenarioError(f"scenario not found: {name}")

    yaml_path = root / "scenario.yaml"
    if not yaml_path.exists():
        raise ScenarioError(f"missing scenario.yaml: {name}")
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ScenarioError(f"{name}: scenario.yaml must be a mapping")

    # Prefer the one-line `brief:` field in scenario.yaml; fall back to
    # the first non-empty line of brief.md so the dashboard row stays terse.
    raw_brief = data.get("brief")
    if isinstance(raw_brief, str) and raw_brief.strip():
        brief = raw_brief.strip()
    else:
        brief_path = root / "brief.md"
        if brief_path.exists():
            first_line = next(
                (
                    line.strip()
                    for line in brief_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ),
                "",
            )
            brief = first_line
        else:
            brief = ""

    providers = data.get("providers") or ["claude", "codex"]
    if not isinstance(providers, list) or not all(isinstance(p, str) for p in providers):
        raise ScenarioError(f"{name}: providers must be a list of strings")

    permission_mode = data.get("permission_mode")
    if permission_mode is not None:
        if not isinstance(permission_mode, str) or permission_mode not in {
            "default",
            "acceptEdits",
            "plan",
            "bypassPermissions",
        }:
            raise ScenarioError(
                f"{name}: permission_mode must be default, acceptEdits, plan, or bypassPermissions"
            )

    raw_nodes = data.get("nodes") or []
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ScenarioError(f"{name}: nodes must be a non-empty list")
    nodes: list[NodeSpec] = []
    seen_ids: set[str] = set()
    for idx, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise ScenarioError(f"{name}: node #{idx} must be a mapping")
        node_id = str(raw.get("id") or f"step{idx}")
        if node_id in seen_ids:
            raise ScenarioError(f"{name}: duplicate node id {node_id!r}")
        seen_ids.add(node_id)
        kind = raw.get("kind", "agent")
        if kind not in {"agent", "gate"}:
            raise ScenarioError(f"{name}: unsupported node kind {kind!r}")

        prompt = ""
        prompt_file = raw.get("prompt_file")
        if prompt_file is not None:
            if not isinstance(prompt_file, str):
                raise ScenarioError(f"{name}: node {node_id} prompt_file must be a string")
            prompt_path = root / prompt_file
            if not prompt_path.exists():
                raise ScenarioError(
                    f"{name}: node {node_id} prompt_file not found: {prompt_file}"
                )
            prompt = prompt_path.read_text(encoding="utf-8")
        elif kind == "agent":
            raise ScenarioError(f"{name}: agent node {node_id} missing prompt_file")

        contract = ""
        contract_file = raw.get("contract_file")
        if contract_file:
            contract_path = root / contract_file
            if not contract_path.exists():
                raise ScenarioError(
                    f"{name}: node {node_id} contract_file not found: {contract_file}"
                )
            contract = contract_path.read_text(encoding="utf-8")

        output_kind = raw.get("output_kind", "freeform")
        if output_kind not in {"freeform", "summary", "interface", "review_brief"}:
            raise ScenarioError(f"{name}: node {node_id} has unsupported output_kind {output_kind!r}")
        output_path = raw.get("output_path", "")
        if output_path is not None and not isinstance(output_path, str):
            raise ScenarioError(f"{name}: node {node_id} output_path must be a string")
        if validate_node_output_path(output_path):
            raise ScenarioError(
                f"{name}: node {node_id} output_path must be project-relative and may not contain '..'"
            )

        brief_from = raw.get("brief_from", "") or ""
        if brief_from and not isinstance(brief_from, str):
            raise ScenarioError(f"{name}: node {node_id} brief_from must be a string")
        if brief_from and kind != "gate":
            raise ScenarioError(
                f"{name}: node {node_id} has brief_from but is not a gate"
            )

        response_path = raw.get("response_path", "") or ""
        if response_path and not isinstance(response_path, str):
            raise ScenarioError(f"{name}: node {node_id} response_path must be a string")
        if validate_node_output_path(response_path):
            raise ScenarioError(
                f"{name}: node {node_id} response_path must be project-relative and may not contain '..'"
            )

        resume_from = raw.get("resume_from", "") or ""
        if resume_from and not isinstance(resume_from, str):
            raise ScenarioError(f"{name}: node {node_id} resume_from must be a string")
        if resume_from and kind != "agent":
            raise ScenarioError(
                f"{name}: node {node_id} has resume_from but is not an agent"
            )

        when_raw = raw.get("when", "") or ""
        when_step = ""
        when_decision = ""
        if when_raw:
            if not isinstance(when_raw, str):
                raise ScenarioError(f"{name}: node {node_id} when must be a string")
            parts = when_raw.split(".")
            if len(parts) != 2 or not parts[0] or parts[1] not in {"approved", "rejected"}:
                raise ScenarioError(
                    f"{name}: node {node_id} when must be '<step>.approved' or '<step>.rejected' (got {when_raw!r})"
                )
            when_step, when_decision = parts[0], parts[1]

        nodes.append(
            NodeSpec(
                id=node_id,
                kind=kind,
                prompt=prompt,
                contract=contract,
                output_kind=output_kind,
                output_path=output_path or "",
                brief_from=brief_from,
                response_path=response_path,
                resume_from=resume_from,
                when_step=when_step,
                when_decision=when_decision,
            )
        )

    # Second pass: for each gate with brief_from, validate the referenced
    # source step exists earlier in the list and is an agent, then force
    # that agent step's effective output_kind to review_brief so the
    # engine-side contract injection prompts it to write the brief.
    by_id = {spec.id: i for i, spec in enumerate(nodes)}
    for gate_idx, spec in enumerate(nodes):
        if spec.kind != "gate" or not spec.brief_from:
            continue
        src_idx = by_id.get(spec.brief_from)
        if src_idx is None:
            raise ScenarioError(
                f"{name}: gate {spec.id} brief_from references unknown step {spec.brief_from!r}"
            )
        if src_idx >= gate_idx:
            raise ScenarioError(
                f"{name}: gate {spec.id} brief_from must reference an earlier step"
            )
        src = nodes[src_idx]
        if src.kind != "agent":
            raise ScenarioError(
                f"{name}: gate {spec.id} brief_from must reference an agent step (got {src.kind})"
            )
        src.output_kind = "review_brief"

    # Third pass: resume_from must reference an earlier step (any kind, since
    # we capture the node id and inherit its provider session).
    for cur_idx, spec in enumerate(nodes):
        if not spec.resume_from:
            continue
        src_idx = by_id.get(spec.resume_from)
        if src_idx is None:
            raise ScenarioError(
                f"{name}: step {spec.id} resume_from references unknown step {spec.resume_from!r}"
            )
        if src_idx >= cur_idx:
            raise ScenarioError(
                f"{name}: step {spec.id} resume_from must reference an earlier step"
            )

    # Fourth pass: when_step must reference an earlier gate step. The
    # decision is recorded on gate completions only.
    for cur_idx, spec in enumerate(nodes):
        if not spec.when_step:
            continue
        src_idx = by_id.get(spec.when_step)
        if src_idx is None:
            raise ScenarioError(
                f"{name}: step {spec.id} when references unknown step {spec.when_step!r}"
            )
        if src_idx >= cur_idx:
            raise ScenarioError(
                f"{name}: step {spec.id} when must reference an earlier step"
            )
        if nodes[src_idx].kind != "gate":
            raise ScenarioError(
                f"{name}: step {spec.id} when must reference a gate step (got {nodes[src_idx].kind})"
            )

    seed_entries: list[tuple[Path, str]] = []
    for entry in data.get("seed") or []:
        if not isinstance(entry, dict):
            raise ScenarioError(f"{name}: seed entries must be mappings")
        src = entry.get("from")
        dst = entry.get("to")
        if not isinstance(src, str) or not isinstance(dst, str):
            raise ScenarioError(f"{name}: seed entries need string 'from' and 'to'")
        src_path = root / src
        if not src_path.exists():
            raise ScenarioError(f"{name}: seed source missing: {src}")
        seed_entries.append((src_path, dst))

    acceptance_path = root / "acceptance.md"
    acceptance = (
        acceptance_path.read_text(encoding="utf-8") if acceptance_path.exists() else ""
    )

    verify_path = root / "verify.sh"
    if not verify_path.exists():
        raise ScenarioError(f"{name}: missing verify.sh")

    return Scenario(
        name=name,
        brief=brief,
        providers=[p.lower() for p in providers],
        auto_commit=bool(data.get("auto_commit", False)),
        permission_mode=permission_mode,
        nodes=nodes,
        seed=seed_entries,
        root=root,
        acceptance=acceptance,
        verify_path=verify_path,
    )


def _strip(text: str) -> str:
    return text.strip()
