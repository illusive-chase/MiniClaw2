"""Parse ``AskUserQuestion`` hook payloads and format the response directive.

The payload shape mirrors what botmux's ``parseQuestions`` expects
(``src/core/ask-hook/claude-code.ts``): the tool_input contains a
``questions`` list; each question has ``question``, ``header``, optional
``multiSelect`` flag, and an ``options`` list of ``{label, description}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ParsedOption:
    label: str
    description: str = ""


@dataclass(slots=True)
class ParsedQuestion:
    question: str
    header: str = ""
    multi_select: bool = False
    options: list[ParsedOption] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedAsk:
    questions: list[ParsedQuestion]
    raw_questions: list[dict[str, Any]]


def parse_ask_payload(payload: dict[str, Any]) -> ParsedAsk | None:
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("questions")
    if not isinstance(raw, list) or not raw:
        return None

    questions: list[ParsedQuestion] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        question_text = str(entry.get("question") or "").strip()
        if not question_text:
            continue
        options_raw = entry.get("options")
        options: list[ParsedOption] = []
        if isinstance(options_raw, list):
            for opt in options_raw:
                if isinstance(opt, dict):
                    label = str(opt.get("label") or "").strip()
                    description = str(opt.get("description") or "")
                    if label:
                        options.append(ParsedOption(label=label, description=description))
                elif isinstance(opt, str) and opt.strip():
                    options.append(ParsedOption(label=opt.strip()))
        questions.append(
            ParsedQuestion(
                question=question_text,
                header=str(entry.get("header") or "").strip(),
                multi_select=bool(entry.get("multiSelect")),
                options=options,
                raw=entry,
            )
        )

    if not questions:
        return None

    return ParsedAsk(questions=questions, raw_questions=[q.raw for q in questions])


def format_ask_directive(
    response: dict[str, Any],
    parsed: ParsedAsk,
) -> dict[str, Any]:
    """Format the InteractionResponse dict as a Claude PreToolUse directive.

    Claude Code's ``AskUserQuestion`` tool natively accepts arbitrary text —
    if the user typed a free-form response, put that string in
    ``answers[q.question]``. When the user chose from the button list,
    join their selected labels with ``, ``.
    """
    answers = _extract_answers(response, parsed)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {
                "questions": parsed.raw_questions,
                "answers": answers,
            },
        },
    }


def _extract_answers(
    response: dict[str, Any],
    parsed: ParsedAsk,
) -> dict[str, str]:
    """Pull per-question answers out of an InteractionResponse-shaped dict.

    We accept several shapes because the frontend has evolved:

    - ``response.updated_input.answers`` — direct dict[question, str]
    - ``response.response.answers`` — same, nested
    - ``response.decision`` — string or dict; single-question shortcut
    - free-text ``response.message`` — applied to the first question
    """
    out: dict[str, str] = {}
    candidates: list[dict[str, Any]] = []
    updated = response.get("updated_input")
    if isinstance(updated, dict):
        raw = updated.get("answers")
        if isinstance(raw, dict):
            candidates.append(raw)
    nested = response.get("response")
    if isinstance(nested, dict):
        raw = nested.get("answers")
        if isinstance(raw, dict):
            candidates.append(raw)
        if isinstance(nested.get("value"), dict):
            candidates.append(nested["value"])

    for question in parsed.questions:
        text: str | None = None
        for source in candidates:
            val = source.get(question.question)
            if val is None:
                val = source.get(question.header)
            if val is None:
                continue
            text = _stringify_answer(val)
            if text is not None:
                break
        if text is not None:
            out[question.question] = text

    if not out and len(parsed.questions) == 1:
        decision = response.get("decision")
        if isinstance(decision, str) and decision.strip():
            out[parsed.questions[0].question] = decision.strip()
        elif isinstance(decision, dict):
            text = _stringify_answer(decision)
            if text is not None:
                out[parsed.questions[0].question] = text
        else:
            message = response.get("message")
            if isinstance(message, str) and message.strip():
                out[parsed.questions[0].question] = message.strip()

    return out


def _stringify_answer(val: Any) -> str | None:
    if isinstance(val, str):
        text = val.strip()
        return text or None
    if isinstance(val, list):
        parts = [str(v).strip() for v in val if str(v).strip()]
        return ", ".join(parts) if parts else None
    if isinstance(val, dict):
        for key in ("label", "answer", "text", "value"):
            inner = val.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
            if isinstance(inner, list):
                parts = [str(v).strip() for v in inner if str(v).strip()]
                if parts:
                    return ", ".join(parts)
        return None
    return None
