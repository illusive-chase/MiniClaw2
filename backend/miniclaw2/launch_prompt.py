"""Category-aware launch instruction composer.

Per PROPOSAL_VIRTUAL_NODES §3.2 + §3.8, each agent launch gets a
system-prompt-shaped block describing:

  - The materialized lane filesystem and what files live where.
  - The preview contract this node must satisfy.
  - The category-specific write rights (regular vs planning vs review).
  - For review nodes, the brief inline, and (for human-interact
    reviews) a pointer to ``human-review.md``.

A separate anti-self-poisoning footer is appended last in the
runner's launch instruction composition.

Templates use ``<<TOKEN>>`` placeholders (chosen so the literal JSON
braces in the prompt examples render verbatim). Substitution is a
straight ``str.replace`` pass — no format / no template engine.
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


def anti_self_poisoning_block() -> str:
    """Return the durable-preview filter footer.

    Appended last in the launch instruction composition so the
    constraint is fresh in the agent's context when it sits down to
    write previews.
    """
    return _load_template(_ANTI_SELF_POISONING).strip()
