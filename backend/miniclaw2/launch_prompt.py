"""Category-aware launch instruction composer.

Each agent launch gets a system-prompt-shaped block describing the
materialized lane filesystem, the preview contract the node must
satisfy, the category-specific write rights (regular / planning /
review), and — for review nodes — the brief inline plus a pointer to
``human-review.md`` for human-interact reviews. The anti-self-poisoning
guidance footer is appended last by the runner's launch composition.

Templates use ``<<TOKEN>>`` placeholders so literal JSON braces in
the examples render verbatim. Substitution is plain ``str.replace``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .domain import ArtifactMode, Category, Node, NodeKind, ReviewSubtype
from .materialize import GRAPH_DIRNAME
from .model_catalog import list_model_presets

_PROMPT_DIR = Path(__file__).with_name("prompts")

_CATEGORY_REGULAR = "category_regular.md"
_CATEGORY_PLANNING = "category_planning.md"
_CATEGORY_AGENTIC_REVIEW = "category_agentic_review.md"
_CATEGORY_HUMAN_INTERACT_REVIEW = "category_human_interact_review.md"
_ANTI_SELF_POISONING = "anti_self_poisoning.md"
_PRINCIPLE_INIT = "principle_init.md"
_LIBRARY_INIT = "library_init.md"
_QA_MODE = "qa_mode.md"


_ARTIFACT_DEFAULT = """\
Artifacts are **not** expected from this node. Publish one only if the
turn's prompt explicitly asks for a file to show the human.\
"""

_ARTIFACT_MARKDOWN = """\
**This node must publish at least one Markdown artifact.** The user asked
for a written deliverable from this turn, so the "only when explicitly
requested" default does not apply — this is that request. Write it as one
or more `.md` files and declare each one in your preview's `artifacts`.

Write it for a reader who was not in this session: state the outcome
first, then the reasoning. It is a publication, not a session log.\
"""

_ARTIFACT_HTML = """\
**This node must publish an HTML artifact.** The user asked for a
rendered deliverable from this turn, so the "only when explicitly
requested" default does not apply — this is that request. Write a single
self-contained `.html` file — inline all CSS and JS, embed images as data
URIs, reference no external asset — and declare it in your preview's
`artifacts`.

Mind the 2 MiB per-file cap: embedded data URIs count toward it. Reach
for HTML when the content genuinely needs rendering (a diagram, a table
that must be scanned, a layout); otherwise Markdown reads better.\
"""

_ARTIFACT_CUSTOM = """\
**This node must publish an artifact matching the user's specification
below.** The user asked for a deliverable from this turn, so the "only
when explicitly requested" default does not apply — this is that request.

The user's specification, verbatim:

<<artifact_spec_quoted>>

The framework constraints above still bind and outrank the specification:
declared files must end in `.md`, `.json`, or `.html`; at most 16 files;
2 MiB per file; 8 MiB per node. If the specification asks for a format
outside those suffixes, produce the closest allowed one and say so in
your preview's `summary` rather than failing silently.\
"""

_ARTIFACT_REQUIREMENTS: dict[ArtifactMode, str] = {
    ArtifactMode.DEFAULT: _ARTIFACT_DEFAULT,
    ArtifactMode.MARKDOWN: _ARTIFACT_MARKDOWN,
    ArtifactMode.HTML: _ARTIFACT_HTML,
}


@lru_cache(maxsize=16)
def _load_template(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


def _template_for_node(node: Node) -> str | None:
    if node.kind is not NodeKind.AGENT:
        return None
    if node.category is Category.PLANNING:
        return _load_template(_CATEGORY_PLANNING)
    if node.category is Category.REVIEW:
        if node.subtype is ReviewSubtype.CODE_REVIEW:
            raise ValueError("code_review nodes must bypass launch prompt templates")
        if node.subtype is ReviewSubtype.HUMAN_INTERACT_REVIEW:
            return _load_template(_CATEGORY_HUMAN_INTERACT_REVIEW)
        return _load_template(_CATEGORY_AGENTIC_REVIEW)
    return _load_template(_CATEGORY_REGULAR)


def _lane_path(node: Node) -> str:
    lane_id = node.planspace_id or ""
    return f"{GRAPH_DIRNAME}/{lane_id}".rstrip("/")


def _substitutions(
    node: Node,
    lane_path: str | None = None,
    outputs_path: str | None = None,
    store_root: Path | None = None,
) -> dict[str, str]:
    subs: dict[str, str] = {
        "lane_path": lane_path or _lane_path(node),
        "node_id": node.id,
        "lane_id": node.planspace_id or "",
        "outputs_path": outputs_path or "",
        "artifact_requirement": build_artifact_requirement(node),
    }
    if node.category is Category.PLANNING:
        subs["planning_model_preset_id"] = node.model_preset_id or ""
        subs["active_model_presets"] = "\n".join(
            "- "
            f"model_preset_id={json.dumps(preset.id)}: "
            f"provider={json.dumps(preset.provider)}, "
            f"model={json.dumps(preset.model)}"
            for preset in list_model_presets(store_root=store_root)
            if preset.status == "active"
        )
    brief = node.brief
    if brief is not None:
        subs["brief_check_what"] = brief.check_what
        subs["brief_expected"] = brief.expected
        subs["brief_abnormal"] = brief.abnormal
    return subs


def build_category_launch_block(
    node: Node,
    *,
    lane_path: str | None = None,
    outputs_path: str | None = None,
    store_root: Path | None = None,
) -> str:
    """Return the category-aware launch instruction block for ``node``.

    Returns an empty string for op nodes (they do not get a launch
    block — ops do not call a provider).
    """
    template = _template_for_node(node)
    if template is None:
        return ""
    rendered = template
    for token, value in _substitutions(
        node,
        lane_path,
        outputs_path,
        store_root,
    ).items():
        rendered = rendered.replace(f"<<{token}>>", value)
    return rendered.strip()


def build_dependency_launch_block(
    node: Node,
    *,
    lane_path: str | None = None,
    foreign_hosts: dict[str, str] | None = None,
) -> str:
    """Return a launch block that names declared dependency previews.

    The graph already carries ``scheduled_deps`` as structured state. This
    block makes that state explicit in the provider prompt so the agent does
    not need to discover its upstream preview paths by enumerating the lane.
    """
    deps = [
        dep.strip()
        for dep in node.scheduled_deps
        if isinstance(dep, str) and dep.strip()
    ]
    if not deps:
        return ""

    lane = lane_path or _lane_path(node)
    lines = [
        "# MiniClaw2 — scheduled dependency index",
        "",
        "This node has declared dependency parents. Read these previews before "
        "using upstream work as context:",
        "",
    ]
    for dep in deps:
        lines.append(f"- `{dep}` -> `{lane}/nodes/{dep}/preview.json`")
        label = (foreign_hosts or {}).get(dep)
        if label:
            lines.append(
                f'  (This dependency ran on another device, "{label}". Its preview '
                "and artifacts are synced here, but any absolute paths and "
                "environment details it mentions belong to that device and do not "
                "apply here.)"
            )
    lines.extend([
        "",
        "If a dependency preview points to transcript details or artifacts, "
        "inspect the sibling files under that same dependency directory.",
    ])
    return "\n".join(lines)


def build_principle_init_block(principles_dir: str) -> str:
    """Return the principle-author preset with its directory substituted.

    Used only for agent nodes with ``agent_op_kind == "principle_edit"``.
    """
    template = _load_template(_PRINCIPLE_INIT)
    return template.replace("<<principles_dir>>", principles_dir).strip()


def build_library_init_block(principles_dir: str, skills_dir: str) -> str:
    """Return the librarian preset with both library roots substituted."""
    template = _load_template(_LIBRARY_INIT)
    return (
        template.replace("<<principles_dir>>", principles_dir)
        .replace("<<skills_dir>>", skills_dir)
        .strip()
    )


def _blockquote(text: str) -> str:
    """Prefix each line with `> ` so arbitrary user text cannot break out."""
    return "\n".join(
        f"> {line}" if line.strip() else ">"
        for line in text.strip().splitlines()
    )


def build_artifact_requirement(node: Node) -> str:
    """Return the artifact-requirement paragraph for this node's mode."""
    mode = node.artifact_mode
    if mode is ArtifactMode.CUSTOM:
        return _ARTIFACT_CUSTOM.replace(
            "<<artifact_spec_quoted>>",
            _blockquote(node.artifact_spec),
        )
    return _ARTIFACT_REQUIREMENTS[mode]


def build_qa_mode_block(node: Node) -> str:
    """Return the ask-user encouragement block for Q/A-enabled nodes."""
    if node.kind is not NodeKind.AGENT or not node.qa_mode:
        return ""
    return _load_template(_QA_MODE).strip()


def anti_self_poisoning_block() -> str:
    """Return the durable-preview guidance footer.

    Appended last in the launch instruction composition so the
    guidance is fresh in the agent's context when it sits down to
    write previews.
    """
    return _load_template(_ANTI_SELF_POISONING).strip()
