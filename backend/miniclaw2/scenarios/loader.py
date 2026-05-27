"""Scenario discovery + YAML parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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


@dataclass(slots=True)
class Scenario:
    """A loaded scenario; immutable view over its on-disk files."""

    name: str
    brief: str
    providers: list[str]
    auto_commit: bool
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

    raw_nodes = data.get("nodes") or []
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ScenarioError(f"{name}: nodes must be a non-empty list")
    nodes: list[NodeSpec] = []
    for idx, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise ScenarioError(f"{name}: node #{idx} must be a mapping")
        node_id = raw.get("id") or f"step{idx}"
        kind = raw.get("kind", "agent")
        if kind not in {"agent", "gate"}:
            raise ScenarioError(f"{name}: unsupported node kind {kind!r}")
        prompt_file = raw.get("prompt_file")
        if not isinstance(prompt_file, str):
            raise ScenarioError(f"{name}: node {node_id} missing prompt_file")
        prompt_path = root / prompt_file
        if not prompt_path.exists():
            raise ScenarioError(
                f"{name}: node {node_id} prompt_file not found: {prompt_file}"
            )
        prompt = prompt_path.read_text(encoding="utf-8")
        contract = ""
        contract_file = raw.get("contract_file")
        if contract_file:
            contract_path = root / contract_file
            if not contract_path.exists():
                raise ScenarioError(
                    f"{name}: node {node_id} contract_file not found: {contract_file}"
                )
            contract = contract_path.read_text(encoding="utf-8")
        nodes.append(
            NodeSpec(id=str(node_id), kind=kind, prompt=prompt, contract=contract)
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
        nodes=nodes,
        seed=seed_entries,
        root=root,
        acceptance=acceptance,
        verify_path=verify_path,
    )


def _strip(text: str) -> str:
    return text.strip()
