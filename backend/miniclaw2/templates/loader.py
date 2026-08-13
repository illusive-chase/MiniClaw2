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

Templates are *functions*: ``arguments`` are string parameters filled in at
instantiation time, ``inputs`` are named upstream ports each bound to one
existing node. Both are declared in ``template.yaml``; argument placeholders
are additionally discovered by scanning prompt text (see
``_resolve_parameters``). Only ``schema_version: 2`` is accepted — v1
templates must be migrated, not parsed on a second code path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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

SCHEMA_VERSION = 2

#: Legal argument / input identifier.
PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Placeholder occurrences inside prompt text. The inner pattern is
#: deliberately permissive so ``{{Bad-Name}}`` is *matched* here and then
#: discarded by :data:`PARAM_NAME_RE`; unmatched braces stay literal text.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")

#: ``scheduled_deps`` prefix marking a reference to a named input port.
INPUT_DEP_PREFIX = "in:"


def user_templates_root(store_root: Path | None = None) -> Path:
    """Return the on-disk root where user templates live."""
    return contextspace_root(store_root) / "templates"


class TemplateError(Exception):
    """Raised when a template's YAML or referenced files are invalid."""


@dataclass(slots=True)
class TemplateArgument:
    """A string parameter filled in when the template is instantiated.

    ``default is None`` means the argument is required; the instantiation
    dialog refuses to create the instance until the caller supplies a value.
    ``declared`` is False for arguments discovered only by scanning prompt
    text, which is what lets authors use a new ``{{placeholder}}`` without
    first editing ``template.yaml``.
    """

    name: str
    description: str = ""
    default: str | None = None
    declared: bool = True

    @property
    def required(self) -> bool:
        return self.default is None

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "default": self.default,
            "required": self.required,
            "declared": self.declared,
        }


@dataclass(slots=True)
class TemplateInput:
    """A named upstream port bound to exactly one existing node on apply."""

    name: str
    description: str = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
        }


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

    @property
    def input_deps(self) -> list[str]:
        """Port names this node depends on, from ``in:<name>`` deps.

        These are out-of-graph source points: they are bound to real nodes at
        stamp time, so they take no part in the template's internal topology.
        """
        return [
            dep[len(INPUT_DEP_PREFIX) :]
            for dep in (self.scheduled_deps or [])
            if dep.startswith(INPUT_DEP_PREFIX)
        ]

    @property
    def internal_deps(self) -> list[str]:
        """Deps naming another node in this template."""
        return [
            dep
            for dep in (self.scheduled_deps or [])
            if not dep.startswith(INPUT_DEP_PREFIX)
        ]

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
    slug: str
    name: str
    brief: str
    allowed_model_preset_ids: list[str]
    auto_commit: bool
    permission_mode: str | None
    lane_mode: PlanspaceMode
    nodes: list[TemplateNodeSpec]
    seed: list[tuple[Path, str]]
    root: Path
    arguments: list[TemplateArgument] = field(default_factory=list)
    inputs: list[TemplateInput] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "brief": self.brief,
            "allowed_model_preset_ids": list(self.allowed_model_preset_ids),
            "auto_commit": self.auto_commit,
            "node_count": len(self.nodes),
            "nodes": [node.metadata() for node in self.nodes],
            "schema_version": SCHEMA_VERSION,
            "arguments": [arg.metadata() for arg in self.arguments],
            "inputs": [inp.metadata() for inp in self.inputs],
            "warnings": [dict(warning) for warning in self.warnings],
        }


def list_templates(store_root: Path | None = None) -> list[Template]:
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
        out.append(load_template(child.name, store_root=store_root))
    return out


def load_template(name: str, store_root: Path | None = None) -> Template:
    root = TEMPLATES_DIR / name
    if not root.is_dir():
        raise TemplateError(f"template not found: {name}")
    return _load_from_root(root, name, store_root=store_root)


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
            out.append(_load_from_root(child, child.name, store_root=store_root))
        except TemplateError:
            # Skip malformed user templates rather than fail the whole list.
            continue
    return out


def load_user_template(slug: str, store_root: Path | None = None) -> Template:
    root = user_templates_root(store_root) / slug
    if not root.is_dir():
        raise TemplateError(f"user template not found: {slug}")
    return _load_from_root(root, slug, store_root=store_root)


def _load_from_root(
    root: Path,
    name: str,
    *,
    store_root: Path | None = None,
) -> Template:
    template_data = _read_yaml(root / "template.yaml", name, "template.yaml")
    lane_data = _read_yaml(root / "lane.yaml", name, "lane.yaml")
    slug = name

    _require_schema_version(name, template_data)

    raw_name = template_data.get("name") or name
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise TemplateError(f"{name}: template name must be a string")
    name = raw_name.strip()

    brief = _brief(root, template_data, name)
    allowed_model_preset_ids = _parse_allowed_model_preset_ids(
        name,
        template_data,
        store_root=store_root,
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
    declared_arguments = _parse_arguments(name, template_data)
    inputs = _parse_inputs(name, template_data)
    arguments, warnings = _resolve_parameters(name, nodes, declared_arguments, inputs)
    _validate_lane_graph(name, nodes, inputs)
    seed = _parse_seed(name, root, template_data)

    return Template(
        slug=slug,
        name=name,
        brief=brief,
        allowed_model_preset_ids=allowed_model_preset_ids,
        auto_commit=bool(template_data.get("auto_commit", False)),
        permission_mode=permission_mode,
        lane_mode=lane_mode,
        nodes=nodes,
        seed=seed,
        root=root,
        arguments=arguments,
        inputs=inputs,
        warnings=warnings,
    )


def _require_schema_version(name: str, template_data: dict[str, Any]) -> None:
    """Reject anything that is not schema v2.

    Strictly one parsing path: a template without ``schema_version: 2`` is a
    migration task, not a compatibility mode.
    """
    if "schema_version" not in template_data:
        raise TemplateError(
            f"{name}: template.yaml is missing schema_version; this template"
            f" predates schema v{SCHEMA_VERSION} — run the template migration"
            " to upgrade it"
        )
    raw = template_data.get("schema_version")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TemplateError(
            f"{name}: schema_version must be the integer {SCHEMA_VERSION}"
        )
    if raw != SCHEMA_VERSION:
        raise TemplateError(
            f"{name}: unsupported schema_version {raw}; expected"
            f" {SCHEMA_VERSION} — run the template migration to upgrade it"
        )


def _parse_arguments(
    name: str,
    template_data: dict[str, Any],
) -> list[TemplateArgument]:
    raw = template_data.get("arguments")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TemplateError(f"{name}: arguments must be a list")

    out: list[TemplateArgument] = []
    seen: set[str] = set()
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TemplateError(f"{name}: argument #{idx} must be a mapping")
        arg_name = _parse_param_name(name, "argument", idx, entry.get("name"))
        if arg_name in seen:
            raise TemplateError(f"{name}: duplicate argument name {arg_name!r}")
        seen.add(arg_name)

        description = entry.get("description") or ""
        if not isinstance(description, str):
            raise TemplateError(
                f"{name}: argument {arg_name!r} description must be a string"
            )

        # Absent key and explicit ``default: null`` both mean "required".
        default = entry.get("default")
        if default is not None and not isinstance(default, str):
            raise TemplateError(
                f"{name}: argument {arg_name!r} default must be a string or null"
            )

        out.append(
            TemplateArgument(
                name=arg_name,
                description=description.strip(),
                default=default,
                declared=True,
            )
        )
    return out


def _parse_inputs(name: str, template_data: dict[str, Any]) -> list[TemplateInput]:
    raw = template_data.get("inputs")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TemplateError(f"{name}: inputs must be a list")

    out: list[TemplateInput] = []
    seen: set[str] = set()
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TemplateError(f"{name}: input #{idx} must be a mapping")
        input_name = _parse_param_name(name, "input", idx, entry.get("name"))
        if input_name in seen:
            raise TemplateError(f"{name}: duplicate input name {input_name!r}")
        seen.add(input_name)

        description = entry.get("description") or ""
        if not isinstance(description, str):
            raise TemplateError(
                f"{name}: input {input_name!r} description must be a string"
            )
        out.append(
            TemplateInput(name=input_name, description=description.strip())
        )
    return out


def _parse_param_name(name: str, label: str, idx: int, raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise TemplateError(f"{name}: {label} #{idx} name must be a non-empty string")
    value = raw.strip()
    if not PARAM_NAME_RE.match(value):
        raise TemplateError(
            f"{name}: {label} name {value!r} must match [a-z][a-z0-9_]*"
        )
    return value


def _scan_placeholders(text: str) -> tuple[list[str], list[str]]:
    """Return ``(argument_names, input_ports)`` referenced by ``text``.

    Placeholders whose body matches neither an argument name nor
    ``input.<port>`` are ignored — they stay literal text so hand-written
    braces are never rewritten by mistake.
    """
    args: list[str] = []
    ports: list[str] = []
    for body in _PLACEHOLDER_RE.findall(text):
        if body.startswith("input."):
            port = body[len("input.") :]
            if PARAM_NAME_RE.match(port) and port not in ports:
                ports.append(port)
        elif PARAM_NAME_RE.match(body) and body not in args:
            args.append(body)
    return args, ports


def _resolve_parameters(
    name: str,
    nodes: list[TemplateNodeSpec],
    declared: list[TemplateArgument],
    inputs: list[TemplateInput],
) -> tuple[list[TemplateArgument], list[dict[str, str]]]:
    """Merge declared arguments with those scanned out of prompt text.

    Scanned-but-undeclared arguments are appended in memory (the editor
    persists them on save). Declared-but-absent ones are kept and reported as
    ``dangling_argument`` warnings so the editor can offer a one-click
    cleanup — the loader does not treat them as errors, since a template may
    legitimately be a work in progress.
    """
    input_names = {inp.name for inp in inputs}

    used_args: list[str] = []
    referenced_ports: set[str] = set()
    for spec in nodes:
        scanned_args, scanned_ports = _scan_placeholders(spec.prompt)
        for arg_name in scanned_args:
            if arg_name not in used_args:
                used_args.append(arg_name)
        for port in scanned_ports:
            if port not in input_names:
                raise TemplateError(
                    f"{name}: node {spec.id} references undeclared input port"
                    f" {port!r} via {{{{input.{port}}}}} — declare it under inputs"
                )
            referenced_ports.add(port)
        referenced_ports.update(spec.input_deps)

    arguments = list(declared)
    declared_names = {arg.name for arg in arguments}
    for arg_name in used_args:
        if arg_name not in declared_names:
            arguments.append(
                TemplateArgument(name=arg_name, description="", default=None, declared=False)
            )
            declared_names.add(arg_name)

    warnings: list[dict[str, str]] = []
    used = set(used_args)
    for arg in declared:
        if arg.name not in used:
            warnings.append(
                {
                    "code": "dangling_argument",
                    "name": arg.name,
                    "message": (
                        f"argument {arg.name!r} is declared but no prompt uses"
                        f" {{{{{arg.name}}}}}"
                    ),
                }
            )
    for inp in inputs:
        if inp.name not in referenced_ports:
            warnings.append(
                {
                    "code": "unreferenced_input",
                    "name": inp.name,
                    "message": (
                        f"input {inp.name!r} is declared but no node references it"
                        f" via scheduled_deps 'in:{inp.name}' or"
                        f" {{{{input.{inp.name}}}}}"
                    ),
                }
            )
    return arguments, warnings


def _parse_allowed_model_preset_ids(
    name: str,
    template_data: dict[str, Any],
    *,
    store_root: Path | None = None,
) -> list[str]:
    if "providers" in template_data:
        raise TemplateError(
            f"{name}: providers is obsolete; use allowed_model_preset_ids"
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
        preset_id = item.strip()
        if not preset_id:
            raise TemplateError(
                f"{name}: allowed_model_preset_ids entries must be non-empty"
            )
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


def _validate_lane_graph(
    name: str,
    nodes: list[TemplateNodeSpec],
    inputs: list[TemplateInput] | None = None,
) -> None:
    by_slug = {spec.id: spec for spec in nodes}
    order = {spec.id: idx for idx, spec in enumerate(nodes)}
    input_names = {inp.name for inp in (inputs or [])}

    for spec in nodes:
        # ``in:<port>`` deps are out-of-graph source points: they are bound to
        # real nodes at stamp time, so they are checked against the declared
        # ports here and then excluded from ordering and cycle analysis.
        for port in spec.input_deps:
            if port not in input_names:
                raise TemplateError(
                    f"{name}: node {spec.id} scheduled_dep references undeclared"
                    f" input port {port!r}"
                )
        for dep in spec.internal_deps:
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
            if spec.resume_from not in spec.internal_deps:
                raise TemplateError(
                    f"{name}: node {spec.id} resume_from must also appear in scheduled_deps"
                )

    # Reuse the same cycle helper over a tiny duck-typed node shape.
    class _Node:
        def __init__(self, deps: list[str]) -> None:
            self.scheduled_deps = deps

    if has_cycle({spec.id: _Node(spec.internal_deps) for spec in nodes}):  # type: ignore[arg-type]
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
