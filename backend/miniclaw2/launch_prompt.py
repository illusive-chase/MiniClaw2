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

from functools import lru_cache
from pathlib import Path

from .domain import Category, Node, NodeKind, ReviewSubtype
from .materialize import GRAPH_DIRNAME

_PROMPT_DIR = Path(__file__).with_name("prompts")

_CATEGORY_REGULAR = "category_regular.md"
_CATEGORY_PLANNING = "category_planning.md"
_CATEGORY_AGENTIC_REVIEW = "category_agentic_review.md"
_CATEGORY_HUMAN_INTERACT_REVIEW = "category_human_interact_review.md"
_ANTI_SELF_POISONING = "anti_self_poisoning.md"


@lru_cache(maxsize=16)
def _load_template(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


def _template_for_node(node: Node) -> str | None:
    if node.kind is not NodeKind.AGENT:
        return None
    if node.category is Category.PLANNING:
        return _load_template(_CATEGORY_PLANNING)
    if node.category is Category.REVIEW:
        if node.subtype is ReviewSubtype.HUMAN_INTERACT_REVIEW:
            return _load_template(_CATEGORY_HUMAN_INTERACT_REVIEW)
        return _load_template(_CATEGORY_AGENTIC_REVIEW)
    return _load_template(_CATEGORY_REGULAR)


def _lane_path(node: Node) -> str:
    lane_id = node.planspace_id or ""
    return f"{GRAPH_DIRNAME}/{lane_id}".rstrip("/")


def _substitutions(node: Node) -> dict[str, str]:
    subs: dict[str, str] = {
        "lane_path": _lane_path(node),
        "node_id": node.id,
        "lane_id": node.planspace_id or "",
    }
    brief = node.brief
    if brief is not None:
        subs["brief_check_what"] = brief.check_what
        subs["brief_expected"] = brief.expected
        subs["brief_abnormal"] = brief.abnormal
    return subs


def build_category_launch_block(node: Node) -> str:
    """Return the category-aware launch instruction block for ``node``.

    Returns an empty string for op nodes (they do not get a launch
    block — ops do not call a provider).
    """
    template = _template_for_node(node)
    if template is None:
        return ""
    rendered = template
    for token, value in _substitutions(node).items():
        rendered = rendered.replace(f"<<{token}>>", value)
    return rendered.strip()


def build_dependency_launch_block(node: Node) -> str:
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

    lane = _lane_path(node)
    lines = [
        "# MiniClaw2 — scheduled dependency index",
        "",
        "This node has declared dependency parents. Read these previews before "
        "using upstream work as context:",
        "",
    ]
    for dep in deps:
        lines.append(f"- `{dep}` -> `{lane}/nodes/{dep}/preview.json`")
    lines.extend([
        "",
        "If a dependency preview points to transcript details or artifacts, "
        "inspect the sibling files under that same dependency directory.",
    ])
    return "\n".join(lines)


def anti_self_poisoning_block() -> str:
    """Return the durable-preview guidance footer.

    Appended last in the launch instruction composition so the
    guidance is fresh in the agent's context when it sits down to
    write previews.
    """
    return _load_template(_ANTI_SELF_POISONING).strip()
