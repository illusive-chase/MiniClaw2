"""Template discovery + YAML parsing.

Two roots are supported:

- **Bundled** templates ship with the backend under
  ``backend/miniclaw2/templates/bundled/``. They can create fresh temporary
  projects and are reached via the Tests modal.
- **User** templates live under
  ``$MINICLAW_CONTEXT_HOME/templates/<slug>/`` and are stamped into an
  existing project's active planspace via drag-and-drop. They are authored
  by ``serializer.serialize_selection``.

Both flavours share the same YAML shape and parsing pipeline (``_load_from_root``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..contextspace import contextspace_root
from ..domain import (
    Category,
    NodeKind,
    PlanspaceMode,
    ReviewBrief,
    ReviewSubtype,
    normalize_planspace_mode,
)
from ..model_catalog import normalize_model_preset_id
from ..virtual_graph import has_cycle

TEMPLATES_DIR = Path(__file__).parent / "bundled"
TEMPLATE_ORDER = [
    "hello-text",
    "bash-uname",
    "write-readme",
    "interrupt-midstream",
    "context-md-respected",
    "resume-fix-after-reject",
    "gui-calculator",
]
_TEMPLATE_RANK = {name: idx for idx, name in enumerate(TEMPLATE_ORDER)}


def user_templates_root(store_root: Path | None = None) -> Path:
    """Return the on-disk root where user templates live."""
    return contextspace_root(store_root) / "templates"


class TemplateError(Exception):
    """Raised when a template's YAML or referenced files are invalid."""


@dataclass(slots=True)
class TemplateNodeSpec:
    id: str
    kind: NodeKind
    category: Category
    subtype: ReviewSubtype | None
    brief: ReviewBrief | None
    prompt: str = ""
    script_ref: Path | None = None
    scheduled_deps: list[str] | None = None
    resume_from: str = ""
    summary: str = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "category": self.category.value,
            "subtype": self.subtype.value if self.subtype else None,
            "scheduled_deps": list(self.scheduled_deps or []),
            "resume_from": self.resume_from or None,
            "prompt_preview": self.prompt.strip().replace("\n", " ")[:160],
            "brief": self.brief.model_dump() if self.brief else None,
        }


@dataclass(slots=True)
class Template:
    name: str
    brief: str
    allowed_model_preset_ids: list[str]
    auto_commit: bool
    permission_mode: str | None
    lane_mode: PlanspaceMode
    nodes: list[TemplateNodeSpec]
    seed: list[tuple[Path, str]]
    root: Path

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "brief": self.brief,
            "allowed_model_preset_ids": list(self.allowed_model_preset_ids),
            "auto_commit": self.auto_commit,
            "node_count": len(self.nodes),
            "nodes": [node.metadata() for node in self.nodes],
        }


def list_templates() -> list[Template]:
    out: list[Template] = []
    if not TEMPLATES_DIR.exists():
        return out
    for child in sorted(
        TEMPLATES_DIR.iterdir(),
        key=lambda path: (_TEMPLATE_RANK.get(path.name, len(TEMPLATE_ORDER)), path.name),
    ):
        if not child.is_dir():
            continue
        if not (child / "template.yaml").exists():
            continue
        out.append(load_template(child.name))
    return out


def load_template(name: str) -> Template:
    root = TEMPLATES_DIR / name
    if not root.is_dir():
        raise TemplateError(f"template not found: {name}")
    return _load_from_root(root, name)


def list_user_templates(store_root: Path | None = None) -> list[Template]:
    """Enumerate templates authored via the UI."""
    root = user_templates_root(store_root)
    out: list[Template] = []
    if not root.exists():
        return out
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        if not (child / "template.yaml").exists():
            continue
        try:
            out.append(_load_from_root(child, child.name))
        except TemplateError:
            # Skip malformed user templates rather than fail the whole list.
            continue
    return out


def load_user_template(slug: str, store_root: Path | None = None) -> Template:
    root = user_templates_root(store_root) / slug
    if not root.is_dir():
        raise TemplateError(f"user template not found: {slug}")
    return _load_from_root(root, slug)


def _load_from_root(root: Path, name: str) -> Template:
    template_data = _read_yaml(root / "template.yaml", name, "template.yaml")
    lane_data = _read_yaml(root / "lane.yaml", name, "lane.yaml")

    raw_name = template_data.get("name") or name
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise TemplateError(f"{name}: template name must be a string")
    name = raw_name.strip()

    brief = _brief(root, template_data, name)
    allowed_model_preset_ids = _parse_allowed_model_preset_ids(
        name,
        template_data,
    )

    permission_mode = template_data.get("permission_mode")
    if permission_mode is not None:
        if not isinstance(permission_mode, str) or permission_mode not in {
            "default",
            "acceptEdits",
            "plan",
            "bypassPermissions",
        }:
            raise TemplateError(
                f"{name}: permission_mode must be default, acceptEdits, plan, or bypassPermissions"
            )

    try:
        lane_mode = normalize_planspace_mode(
            template_data.get("lane_mode")
            if isinstance(template_data.get("lane_mode"), str)
            else None
        )
    except ValueError as exc:
        raise TemplateError(f"{name}: invalid lane_mode: {exc}") from exc

    nodes = _parse_lane_nodes(name, root, lane_data)
    _validate_lane_graph(name, nodes)
    seed = _parse_seed(name, root, template_data)

    return Template(
        name=name,
        brief=brief,
        allowed_model_preset_ids=allowed_model_preset_ids,
        auto_commit=bool(template_data.get("auto_commit", False)),
        permission_mode=permission_mode,
        lane_mode=lane_mode,
        nodes=nodes,
        seed=seed,
        root=root,
    )


def _parse_allowed_model_preset_ids(
    name: str,
    template_data: dict[str, Any],
) -> list[str]:
    if "providers" in template_data:
        raise TemplateError(
            f"{name}: providers is obsolete; run the schema migration"
        )
    if "model_preset_id" in template_data:
        raise TemplateError(
            f"{name}: model_preset_id is obsolete; use allowed_model_preset_ids"
        )
    raw = template_data.get("allowed_model_preset_ids")
    if not isinstance(raw, list) or not raw:
        raise TemplateError(
            f"{name}: allowed_model_preset_ids must be a non-empty list"
        )
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise TemplateError(
                f"{name}: allowed_model_preset_ids entries must be strings"
            )
        try:
            preset_id = normalize_model_preset_id(item)
        except ValueError as exc:
            raise TemplateError(f"{name}: {exc}") from exc
        if preset_id not in out:
            out.append(preset_id)
    return out


def _read_yaml(path: Path, name: str, label: str) -> dict[str, Any]:
    if not path.exists():
        raise TemplateError(f"{name}: missing {label}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise TemplateError(f"{name}: invalid {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateError(f"{name}: {label} must be a mapping")
    return data


def _brief(root: Path, data: dict[str, Any], name: str) -> str:
    raw = data.get("brief")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    brief_path = root / "brief.md"
    if not brief_path.exists():
        return ""
    return next(
        (
            line.strip()
            for line in brief_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ),
        "",
    )


def _parse_lane_nodes(
    name: str,
    root: Path,
    lane_data: dict[str, Any],
) -> list[TemplateNodeSpec]:
    raw_nodes = lane_data.get("nodes") or []
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise TemplateError(f"{name}: lane.yaml nodes must be a non-empty list")

    nodes: list[TemplateNodeSpec] = []
    seen: set[str] = set()
    for idx, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise TemplateError(f"{name}: lane node #{idx} must be a mapping")
        node_id = str(raw.get("id") or f"step{idx}").strip()
        if not node_id:
            raise TemplateError(f"{name}: lane node #{idx} id must be non-empty")
        if node_id in seen:
            raise TemplateError(f"{name}: duplicate node id {node_id!r}")
        seen.add(node_id)

        kind = _parse_kind(name, node_id, raw.get("kind", "agent"))
        if kind is NodeKind.OP:
            raise TemplateError(f"{name}: lane node {node_id} cannot be kind=op")

        if kind is NodeKind.VERIFIER:
            category = Category.REVIEW
            subtype = ReviewSubtype.PROGRAMMATIC_REVIEW
        else:
            category = _parse_category(name, node_id, raw.get("category", "regular"))
            subtype = _parse_subtype(name, node_id, raw.get("subtype"))

        brief = _parse_brief(name, node_id, raw.get("brief"))
        prompt = ""
        script_ref: Path | None = None
        if kind is NodeKind.AGENT:
            prompt_file = raw.get("prompt_file")
            if not isinstance(prompt_file, str) or not prompt_file:
                raise TemplateError(f"{name}: agent node {node_id} missing prompt_file")
            prompt_path = root / prompt_file
            if not prompt_path.exists():
                raise TemplateError(
                    f"{name}: node {node_id} prompt_file not found: {prompt_file}"
                )
            prompt = prompt_path.read_text(encoding="utf-8")
        else:
            script_raw = raw.get("script_ref")
            if not isinstance(script_raw, str) or not script_raw:
                raise TemplateError(
                    f"{name}: verifier node {node_id} missing script_ref"
                )
            script_ref = (root / script_raw).resolve()
            if not script_ref.exists():
                raise TemplateError(
                    f"{name}: node {node_id} script_ref not found: {script_raw}"
                )

        if category is Category.REVIEW:
            if subtype is None:
                raise TemplateError(f"{name}: review node {node_id} missing subtype")
            if brief is None:
                raise TemplateError(f"{name}: review node {node_id} missing brief")
        else:
            if subtype is not None:
                raise TemplateError(
                    f"{name}: non-review node {node_id} must not carry subtype"
                )
            if brief is not None:
                raise TemplateError(
                    f"{name}: non-review node {node_id} must not carry brief"
                )

        deps = _parse_deps(name, node_id, raw.get("scheduled_deps"))
        resume_from = raw.get("resume_from", "") or ""
        if resume_from and not isinstance(resume_from, str):
            raise TemplateError(f"{name}: node {node_id} resume_from must be a string")

        summary = raw.get("motivation") or raw.get("summary") or ""
        if summary and not isinstance(summary, str):
            raise TemplateError(f"{name}: node {node_id} motivation must be a string")

        nodes.append(
            TemplateNodeSpec(
                id=node_id,
                kind=kind,
                category=category,
                subtype=subtype,
                brief=brief,
                prompt=prompt,
                script_ref=script_ref,
                scheduled_deps=deps,
                resume_from=resume_from,
                summary=summary or _default_summary(kind, brief, prompt),
            )
        )
    return nodes


def _parse_kind(name: str, node_id: str, raw: Any) -> NodeKind:
    if not isinstance(raw, str):
        raise TemplateError(f"{name}: node {node_id} kind must be a string")
    try:
        return NodeKind(raw)
    except ValueError as exc:
        raise TemplateError(
            f"{name}: node {node_id} kind must be agent or verifier"
        ) from exc


def _parse_category(name: str, node_id: str, raw: Any) -> Category:
    if not isinstance(raw, str):
        raise TemplateError(f"{name}: node {node_id} category must be a string")
    try:
        return Category(raw)
    except ValueError as exc:
        raise TemplateError(
            f"{name}: node {node_id} category must be planning, regular, or review"
        ) from exc


def _parse_subtype(name: str, node_id: str, raw: Any) -> ReviewSubtype | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise TemplateError(f"{name}: node {node_id} subtype must be a string")
    try:
        return ReviewSubtype(raw)
    except ValueError as exc:
        raise TemplateError(f"{name}: node {node_id} has unknown subtype") from exc


def _parse_brief(name: str, node_id: str, raw: Any) -> ReviewBrief | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TemplateError(f"{name}: node {node_id} brief must be a mapping")
    try:
        return ReviewBrief.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise TemplateError(f"{name}: node {node_id} invalid brief: {exc}") from exc


def _parse_deps(name: str, node_id: str, raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TemplateError(f"{name}: node {node_id} scheduled_deps must be a list")
    deps: list[str] = []
    seen: set[str] = set()
    for dep_raw in raw:
        if not isinstance(dep_raw, str) or not dep_raw.strip():
            raise TemplateError(
                f"{name}: node {node_id} scheduled_deps entries must be strings"
            )
        dep = dep_raw.strip()
        if dep in seen:
            continue
        seen.add(dep)
        deps.append(dep)
    return deps


def _parse_seed(
    name: str,
    root: Path,
    data: dict[str, Any],
) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for entry in data.get("seed") or []:
        if not isinstance(entry, dict):
            raise TemplateError(f"{name}: seed entries must be mappings")
        src = entry.get("from")
        dst = entry.get("to")
        if not isinstance(src, str) or not isinstance(dst, str):
            raise TemplateError(f"{name}: seed entries need string 'from' and 'to'")
        src_path = root / src
        if not src_path.exists():
            raise TemplateError(f"{name}: seed source missing: {src}")
        out.append((src_path, dst))
    return out


def _validate_lane_graph(name: str, nodes: list[TemplateNodeSpec]) -> None:
    by_slug = {spec.id: spec for spec in nodes}
    order = {spec.id: idx for idx, spec in enumerate(nodes)}
    for spec in nodes:
        for dep in spec.scheduled_deps or []:
            if dep not in by_slug:
                raise TemplateError(
                    f"{name}: node {spec.id} scheduled_dep references unknown node {dep!r}"
                )
            if order[dep] >= order[spec.id]:
                raise TemplateError(
                    f"{name}: node {spec.id} scheduled_dep must reference an earlier node"
                )
        if spec.resume_from:
            if spec.resume_from not in by_slug:
                raise TemplateError(
                    f"{name}: node {spec.id} resume_from references unknown node {spec.resume_from!r}"
                )
            if order[spec.resume_from] >= order[spec.id]:
                raise TemplateError(
                    f"{name}: node {spec.id} resume_from must reference an earlier node"
                )
            if spec.resume_from not in (spec.scheduled_deps or []):
                raise TemplateError(
                    f"{name}: node {spec.id} resume_from must also appear in scheduled_deps"
                )

    # Reuse the same cycle helper over a tiny duck-typed node shape.
    class _Node:
        def __init__(self, deps: list[str]) -> None:
            self.scheduled_deps = deps

    if has_cycle({spec.id: _Node(spec.scheduled_deps or []) for spec in nodes}):  # type: ignore[arg-type]
        raise TemplateError(f"{name}: scheduled_deps introduce a cycle")


def _default_summary(
    kind: NodeKind,
    brief: ReviewBrief | None,
    prompt: str,
) -> str:
    if brief is not None:
        return brief.check_what
    if kind is NodeKind.VERIFIER:
        return "programmatic verifier"
    return prompt.strip().replace("\n", " ")[:160]
