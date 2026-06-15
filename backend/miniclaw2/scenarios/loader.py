"""Scenario discovery + YAML parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..domain import Category, ReviewBrief, ReviewSubtype
SCENARIOS_DIR = Path(__file__).parent / "bundled"
SCENARIO_ORDER = [
    "hello-text",
    "bash-uname",
    "write-readme",
    "permission-approve",
    "plan-mode-approval",
    "interrupt-midstream",
    "context-md-respected",
    "resume-fix-after-reject",
    "reconnect-replay",
    "gui-calculator",
]
_SCENARIO_RANK = {name: idx for idx, name in enumerate(SCENARIO_ORDER)}


class ScenarioError(Exception):
    """Raised when a scenario's YAML or referenced files are invalid."""


@dataclass(slots=True)
class NodeSpec:
    """One step in a scenario — an agent node to enqueue."""

    id: str
    prompt: str
    kind: str = "agent"
    category: Category = Category.REGULAR
    subtype: ReviewSubtype | None = None
    brief: ReviewBrief | None = None
    contract: str = ""
    review_source: str = ""      # review-only: source step id this reviews
    resume_from: str = ""        # source step whose session this resumes
    when_step: str = ""          # predicate target — must be an earlier review step
    when_outcome: str = ""       # "approved" | "rejected" — required iff when_step set

    def metadata(self) -> dict[str, Any]:
        """Frontend-shaped summary for scenario future phantoms."""
        return {
            "id": self.id,
            "kind": self.kind,
            "category": self.category.value,
            "subtype": self.subtype.value if self.subtype else None,
            "review_source": self.review_source or None,
            "resume_from": self.resume_from or None,
            "when_step": self.when_step or None,
            "when_outcome": self.when_outcome or None,
            "prompt_preview": _strip(self.prompt).replace("\n", " ")[:160],
            "brief": self.brief.model_dump() if self.brief else None,
        }


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
            "nodes": [node.metadata() for node in self.nodes],
        }


def list_scenarios() -> list[Scenario]:
    """Return every bundled scenario, ordered from simple to integrated."""
    out: list[Scenario] = []
    if not SCENARIOS_DIR.exists():
        return out
    for child in sorted(
        SCENARIOS_DIR.iterdir(),
        key=lambda path: (_SCENARIO_RANK.get(path.name, len(SCENARIO_ORDER)), path.name),
    ):
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
        if kind != "agent":
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
        else:
            raise ScenarioError(f"{name}: agent node {node_id} missing prompt_file")

        category_raw = raw.get("category", "regular")
        if not isinstance(category_raw, str):
            raise ScenarioError(f"{name}: node {node_id} category must be a string")
        try:
            category = Category(category_raw)
        except ValueError as exc:
            raise ScenarioError(
                f"{name}: node {node_id} category must be planning, regular, or review"
            ) from exc

        subtype: ReviewSubtype | None = None
        subtype_raw = raw.get("subtype")
        if subtype_raw is not None:
            if not isinstance(subtype_raw, str):
                raise ScenarioError(f"{name}: node {node_id} subtype must be a string")
            try:
                subtype = ReviewSubtype(subtype_raw)
            except ValueError as exc:
                raise ScenarioError(
                    f"{name}: node {node_id} subtype must be agentic_review or human_interact_review"
                ) from exc
        node_brief: ReviewBrief | None = None
        brief_raw = raw.get("brief")
        if brief_raw is not None:
            if not isinstance(brief_raw, dict):
                raise ScenarioError(f"{name}: node {node_id} brief must be a mapping")
            try:
                node_brief = ReviewBrief.model_validate(brief_raw)
            except Exception as exc:  # noqa: BLE001
                raise ScenarioError(f"{name}: node {node_id} invalid brief: {exc}") from exc
        if category is Category.REVIEW:
            if subtype is None:
                raise ScenarioError(f"{name}: review node {node_id} missing subtype")
            if node_brief is None:
                raise ScenarioError(f"{name}: review node {node_id} missing brief")
        else:
            if subtype is not None:
                raise ScenarioError(
                    f"{name}: non-review node {node_id} must not carry subtype"
                )
            if node_brief is not None:
                raise ScenarioError(
                    f"{name}: non-review node {node_id} must not carry brief"
                )

        contract = ""
        contract_file = raw.get("contract_file")
        if contract_file:
            contract_path = root / contract_file
            if not contract_path.exists():
                raise ScenarioError(
                    f"{name}: node {node_id} contract_file not found: {contract_file}"
                )
            contract = contract_path.read_text(encoding="utf-8")

        review_source = raw.get("review_source", "") or ""
        if review_source and not isinstance(review_source, str):
            raise ScenarioError(f"{name}: node {node_id} review_source must be a string")
        if review_source and category is not Category.REVIEW:
            raise ScenarioError(
                f"{name}: node {node_id} has review_source but is not category=review"
            )

        resume_from = raw.get("resume_from", "") or ""
        if resume_from and not isinstance(resume_from, str):
            raise ScenarioError(f"{name}: node {node_id} resume_from must be a string")

        when_raw = raw.get("when", "") or ""
        when_step = ""
        when_outcome = ""
        if when_raw:
            if not isinstance(when_raw, str):
                raise ScenarioError(f"{name}: node {node_id} when must be a string")
            parts = when_raw.split(".")
            if len(parts) != 2 or not parts[0] or parts[1] not in {"approved", "rejected"}:
                raise ScenarioError(
                    f"{name}: node {node_id} when must be '<step>.approved' or '<step>.rejected' (got {when_raw!r})"
                )
            when_step, when_outcome = parts[0], parts[1]

        nodes.append(
            NodeSpec(
                id=node_id,
                kind=kind,
                category=category,
                subtype=subtype,
                brief=node_brief,
                prompt=prompt,
                contract=contract,
                review_source=review_source,
                resume_from=resume_from,
                when_step=when_step,
                when_outcome=when_outcome,
            )
        )

    # Second pass: for each review_source, validate the referenced
    # source step exists earlier in the list and is an agent.
    by_id = {spec.id: i for i, spec in enumerate(nodes)}
    for review_idx, spec in enumerate(nodes):
        if not spec.review_source:
            continue
        src_idx = by_id.get(spec.review_source)
        if src_idx is None:
            raise ScenarioError(
                f"{name}: review step {spec.id} review_source references unknown step {spec.review_source!r}"
            )
        if src_idx >= review_idx:
            raise ScenarioError(
                f"{name}: review step {spec.id} review_source must reference an earlier step"
            )
        src = nodes[src_idx]
        if src.kind != "agent":
            raise ScenarioError(
                f"{name}: review step {spec.id} review_source must reference an agent step (got {src.kind})"
            )

    # Third pass: resume_from must reference an earlier step (any category, since
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

    # Fourth pass: when_step must reference an earlier review step. The
    # outcome is inferred from review-node graph mutations.
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
        if nodes[src_idx].category is not Category.REVIEW:
            raise ScenarioError(
                f"{name}: step {spec.id} when must reference a review step (got {nodes[src_idx].category.value})"
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
