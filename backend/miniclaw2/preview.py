"""Preview contract — node.preview.json schemas and helpers.

Every executed node writes ``preview.json`` (or the framework writes a
stub when the agent failed to). Every virtual node carries its preview
shape declaratively. Schemas are strict whitelist (``extra='forbid'``)
so agent-written files surface unknown-field violations at reap.

Per PROPOSAL_VIRTUAL_NODES §3.3.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .domain import Category, Node, NodeKind, NodeState, ReviewBrief, ReviewSubtype


class PreviewValidationError(ValueError):
    """Structured validation error carrying a list of issue strings."""

    def __init__(self, issues: list[str]):
        super().__init__("; ".join(issues))
        self.issues = list(issues)


def _iso(ts: float | None) -> str:
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


class ExecutedPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["agent", "op", "verifier"]
    category: Literal["planning", "regular", "review"] | None = None
    state: Literal["done", "error", "cancelled"]
    ran_at: str
    lane: str
    motivation: str
    summary: str
    next_implications: str
    subtype: Literal[
        "agentic_review",
        "human_interact_review",
        "programmatic_review",
    ] | None = None

    @model_validator(mode="after")
    def _check(self) -> "ExecutedPreview":
        if self.kind in {"agent", "verifier"}:
            if self.category is None:
                raise ValueError("executed agent/verifier previews require a category")
            if self.category == "review" and self.subtype is None:
                raise ValueError("review previews require a subtype")
            if self.category != "review" and self.subtype is not None:
                raise ValueError("non-review previews must not carry a subtype")
            if self.kind == "verifier" and self.subtype != "programmatic_review":
                raise ValueError(
                    "verifier previews require subtype=programmatic_review"
                )
            if self.kind == "agent" and self.subtype == "programmatic_review":
                raise ValueError("programmatic_review previews require kind=verifier")
        else:
            if self.category is not None:
                raise ValueError("op previews must not carry a category")
            if self.subtype is not None:
                raise ValueError("op previews must not carry a subtype")
        return self


class ExecutedPreviewBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_what: str
    expected: str
    abnormal: str


class VirtualPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["agent", "verifier"] = "agent"
    category: Literal["planning", "regular", "review"]
    state: Literal["virtual"]
    lane: str
    proposed_by: str
    motivation: str
    prompt_draft: str = ""
    subtype: Literal[
        "agentic_review",
        "human_interact_review",
        "programmatic_review",
    ] | None = None
    brief: ExecutedPreviewBrief | None = None
    scheduled_deps: list[str] = []
    obsolete_reason: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "VirtualPreview":
        if self.kind == "verifier":
            if self.category != "review":
                raise ValueError("verifier virtuals require category=review")
            if self.subtype != "programmatic_review":
                raise ValueError(
                    "verifier virtuals require subtype=programmatic_review"
                )
            if self.brief is None:
                raise ValueError("verifier virtuals require a brief")
            if self.prompt_draft:
                raise ValueError("verifier virtuals must not carry prompt_draft")
            return self
        if self.subtype == "programmatic_review":
            raise ValueError("programmatic_review virtuals require kind=verifier")
        if self.category == "review":
            if self.subtype is None:
                raise ValueError("review virtuals require a subtype")
            if self.brief is None:
                raise ValueError("review virtuals require a brief")
        else:
            if self.subtype is not None:
                raise ValueError("non-review virtuals must not carry a subtype")
            if self.brief is not None:
                raise ValueError("non-review virtuals must not carry a brief")
        return self


Preview = ExecutedPreview | VirtualPreview


def parse_preview(text: str) -> Preview:
    """Parse ``preview.json`` text. Discriminate on ``state`` field.

    Raises ``PreviewValidationError`` with structured issues on any
    schema violation (including unknown fields).
    """
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PreviewValidationError([f"invalid JSON: {exc}"]) from exc
    if not isinstance(data, dict):
        raise PreviewValidationError(["preview must be a JSON object"])
    state = data.get("state")
    try:
        if state == "virtual":
            return VirtualPreview.model_validate(data)
        return ExecutedPreview.model_validate(data)
    except ValidationError as exc:
        issues = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()))
            issues.append(f"{loc}: {err.get('msg', 'invalid')}")
        raise PreviewValidationError(issues) from exc


def validate_preview_for_node(preview: Preview, node: Node) -> list[str]:
    """Cross-check that a preview claims to describe the given node.

    Returns a list of issue strings (empty = ok). Identity checks only;
    does NOT re-run the strict-whitelist validation (done in parse).
    """
    issues: list[str] = []
    if preview.id != node.id:
        issues.append(f"preview.id {preview.id!r} does not match node.id {node.id!r}")
    if preview.lane != (node.planspace_id or ""):
        issues.append(
            f"preview.lane {preview.lane!r} does not match node lane {node.planspace_id!r}"
        )
    if isinstance(preview, ExecutedPreview):
        if preview.kind != node.kind.value:
            issues.append(f"preview.kind {preview.kind!r} does not match node.kind {node.kind.value!r}")
        if node.kind in {NodeKind.AGENT, NodeKind.VERIFIER}:
            expected = node.category.value if node.category else None
            if preview.category != expected:
                issues.append(
                    f"preview.category {preview.category!r} does not match node.category {expected!r}"
                )
        if node.kind in {NodeKind.AGENT, NodeKind.VERIFIER} and node.category is Category.REVIEW:
            expected_sub = node.subtype.value if node.subtype else None
            if preview.subtype != expected_sub:
                issues.append(
                    f"preview.subtype {preview.subtype!r} does not match node.subtype {expected_sub!r}"
                )
    return issues


def render_executed_preview(node: Node, *, motivation: str, summary: str,
                            next_implications: str) -> str:
    """Render a stub preview for the framework to write on agent failure
    or for the op runner. Maps node fields into the schema.
    """
    if node.state not in (NodeState.DONE, NodeState.ERROR, NodeState.CANCELLED):
        raise ValueError(f"cannot render executed preview for state {node.state}")
    state_value: Literal["done", "error", "cancelled"]
    if node.state is NodeState.DONE:
        state_value = "done"
    elif node.state is NodeState.ERROR:
        state_value = "error"
    else:
        state_value = "cancelled"
    payload: dict[str, Any] = {
        "id": node.id,
        "kind": node.kind.value,
        "state": state_value,
        "ran_at": _iso(node.started_at or node.finished_at),
        "lane": node.planspace_id or "",
        "motivation": motivation,
        "summary": summary,
        "next_implications": next_implications,
    }
    if node.kind in {NodeKind.AGENT, NodeKind.VERIFIER}:
        payload["category"] = (node.category or Category.REGULAR).value
        if node.category is Category.REVIEW and node.subtype is not None:
            payload["subtype"] = node.subtype.value
    preview = ExecutedPreview.model_validate(payload)
    return json.dumps(preview.model_dump(exclude_none=True), ensure_ascii=False, indent=2)


def render_virtual_preview(node: Node) -> str:
    """Render a virtual node into preview.json shape for materialization."""
    if node.state is not NodeState.VIRTUAL:
        raise ValueError(f"cannot render virtual preview for state {node.state}")
    if node.category is None:
        raise ValueError("virtual node must have a category")
    payload: dict[str, Any] = {
        "id": node.id,
        "kind": node.kind.value,
        "category": node.category.value,
        "state": "virtual",
        "lane": node.planspace_id or "",
        "proposed_by": node.proposed_by or "unknown",
        "motivation": node.summary or "",  # virtuals carry motivation in summary slot
        "prompt_draft": node.prompt_draft or "",
        "scheduled_deps": list(node.scheduled_deps),
    }
    if node.category is Category.REVIEW:
        if node.subtype is None or node.brief is None:
            raise ValueError("review virtual missing subtype or brief")
        payload["subtype"] = node.subtype.value
        payload["brief"] = node.brief.model_dump()
    if node.obsolete_reason is not None:
        payload["obsolete_reason"] = node.obsolete_reason
    preview = VirtualPreview.model_validate(payload)
    return json.dumps(preview.model_dump(exclude_none=True), ensure_ascii=False, indent=2)


def virtual_preview_to_node(
    preview: VirtualPreview,
    *,
    project_id: str,
    provider: str,
    canonical_id: str,
    verify_script_ref: str | None = None,
) -> Node:
    """Promote a parsed VirtualPreview into a persistable ``Node`` with
    a framework-assigned canonical id."""
    brief = ReviewBrief.model_validate(preview.brief.model_dump()) if preview.brief else None
    subtype = ReviewSubtype(preview.subtype) if preview.subtype else None
    kind = NodeKind(preview.kind)
    return Node(
        id=canonical_id,
        project_id=project_id,
        kind=kind,
        state=NodeState.VIRTUAL,
        planspace_id=preview.lane or None,
        provider=provider,
        prompt="",
        prompt_draft=preview.prompt_draft,
        category=Category(preview.category),
        subtype=subtype,
        brief=brief,
        scheduled_deps=list(preview.scheduled_deps),
        verify_script_ref=verify_script_ref,
        proposed_by=preview.proposed_by,
        obsolete_reason=preview.obsolete_reason,
        summary=preview.motivation,
    )
