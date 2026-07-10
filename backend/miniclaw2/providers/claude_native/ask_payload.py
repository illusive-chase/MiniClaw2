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
    """Convert canonical ``response.answers`` to Claude's answer mapping."""
    out: dict[str, str] = {}
    nested = response.get("response")
    answers = nested.get("answers") if isinstance(nested, dict) else None
    if not isinstance(answers, dict):
        return out

    for question in parsed.questions:
        keys = [question.raw.get("id"), question.question, question.header]
        for key in keys:
            if not isinstance(key, str) or not key:
                continue
            value = answers.get(key)
            if not isinstance(value, dict):
                continue
            selected = value.get("answers")
            if isinstance(selected, list):
                parts = [str(item).strip() for item in selected if str(item).strip()]
                if parts:
                    out[question.question] = ", ".join(parts)
                break

    return out
