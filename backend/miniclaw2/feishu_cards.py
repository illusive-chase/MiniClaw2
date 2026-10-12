"""Pure Feishu card builders and answer handling for remote ``ask_user``.

Every card is Card 2.0 JSON with ``update_multi`` set, because the bridge
replaces cards in place (callback response or PATCH) and Feishu only allows
that when both versions opt in. The whole question set is one ``form`` with a
single submit button: re-rendering a card clears unsent input, so there is no
per-option toggle round trip.

The submit button's value is the routing key for a multi-host shared app:
``h`` names the owning host and ``g`` its gate, ``n`` is the per-card nonce
that only the owner knows, ``k`` lets a non-owner check completeness without
knowing the questions. A click can land on any connected host; a non-owner
answers it by replacing the card with a receipt that carries the submission,
which the owner later reads back (see ``feishu_bridge``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Iterator
from typing import Any

from .gate_observer import GateNotice

CARD_VALUE_VERSION = 1
ACTION_SUBMIT = "s"
ACTION_TEST = "t"
RECEIPT_ELEMENT_ID = "mc2_rx"
RECEIPT_PREFIX = "MC2RX1."
#: Form input beyond this is not relayed; the owner never sees a clipped answer.
RECEIPT_FORM_LIMIT = 4096
#: Leave headroom under Feishu's 30 KB card limit.
CARD_SIZE_LIMIT = 28 * 1024

QUESTION_TEXT_LIMIT = 1500
DESCRIPTION_LIMIT = 200
OPTION_LABEL_LIMIT = 100
SUBTITLE_LIMIT = 60
ANSWER_SUMMARY_LIMIT = 300
RETRY_HINT = "提交失败（出错了 / 200672）请再点一次，网页端也可随时作答。"

HOST_COLORS = (
    "indigo", "turquoise", "orange", "violet", "carmine", "lime", "purple", "blue",
)


def host_short_id(machine_id: str) -> str:
    """Routing id for this host: 12 hex chars of its machine id."""
    return machine_id.replace("-", "").lower()[:12]


def host_color(host_short: str) -> str:
    """Fixed tag colour per host, derivable from the routing id on any card."""
    digest = hashlib.sha256(host_short.encode("utf-8")).hexdigest()
    return HOST_COLORS[int(digest, 16) % len(HOST_COLORS)]


def question_key(question: dict[str, Any], index: int) -> str:
    """Same key as the web dialog: ``id || question || q{index}``."""
    for candidate in (question.get("id"), question.get("question")):
        if candidate not in (None, ""):
            return str(candidate)
    return f"q{index}"


def option_labels(question: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for option in question.get("options") or []:
        if isinstance(option, dict):
            label = str(option.get("label") or "").strip()
        elif isinstance(option, str):
            label = option.strip()
        else:
            label = ""
        if label:
            labels.append(label)
    return labels


def has_secret_question(questions: list[dict[str, Any]]) -> bool:
    return any(bool(question.get("isSecret")) for question in questions)


def normalize_answers(
    questions: list[dict[str, Any]],
    form_value: dict[str, Any],
) -> tuple[dict[str, dict[str, list[str]]] | None, int | None]:
    """Turn a form submission into the canonical gate answer mapping.

    Mirrors ``AskUserDialog.submit``: non-empty free text overrides the
    selection; otherwise the selected labels, multi-select ordered by option
    index. Returns ``(None, n)`` when question ``n`` (1-based) has no answer,
    the same completeness rule the web dialog enforces before Send.
    """
    answers: dict[str, dict[str, list[str]]] = {}
    for index, question in enumerate(questions):
        text = str(form_value.get(f"o{index}") or "").strip()
        if text:
            values = [text]
        else:
            labels = option_labels(question)
            if question.get("multiSelect"):
                values = [
                    label
                    for position, label in enumerate(labels)
                    if _checked(form_value.get(f"q{index}_{position}"))
                ]
            else:
                selected = _option_index(form_value.get(f"q{index}"))
                values = (
                    [labels[selected]]
                    if selected is not None and selected < len(labels)
                    else []
                )
        if not values:
            return None, index + 1
        answers[question_key(question, index)] = {"answers": values}
    return answers, None


def first_unanswered(form_value: dict[str, Any], count: Any) -> int | None:
    """Completeness check that needs only field names, for non-owner hosts."""
    if not isinstance(count, int) or count < 0:
        return None
    for index in range(count):
        if str(form_value.get(f"o{index}") or "").strip():
            continue
        if form_value.get(f"q{index}") not in (None, "", []):
            continue
        prefix = f"q{index}_"
        if any(
            key.startswith(prefix) and _checked(value)
            for key, value in form_value.items()
        ):
            continue
        return index + 1
    return None


def card_subtitle(notice: GateNotice) -> str:
    parts = [
        part.strip()
        for part in (notice.project_name, notice.lane_title or "", notice.node_label)
        if part and part.strip()
    ]
    return _clip(" · ".join(parts), SUBTITLE_LIMIT)


def host_tag(label: str, color: str) -> dict[str, Any]:
    return {
        "tag": "text_tag",
        "text": {"tag": "plain_text", "content": label},
        "color": color,
    }


def ask_card(
    notice: GateNotice,
    *,
    host_short: str,
    host_label: str,
    color: str,
    nonce: str,
    now: float | None = None,
) -> dict[str, Any]:
    """The pending-question card, or a notice-only card when it cannot be answered here."""
    questions = notice.questions
    subtitle = card_subtitle(notice)
    asked = _asked_line(notice.created_at, now)
    if not questions or has_secret_question(questions):
        return _notice_only_card(
            notice,
            host_label=host_label,
            color=color,
            subtitle=subtitle,
            reason="此提问包含敏感输入，请回到网页端作答。"
            if questions
            else "此提问没有可显示的问题，请回到网页端查看。",
            asked=asked,
        )
    form_elements: list[dict[str, Any]] = []
    for index, question in enumerate(questions):
        form_elements.extend(_question_elements(question, index))
    form_elements.append(
        {
            "tag": "button",
            "name": "submit",
            "text": {"tag": "plain_text", "content": "提交"},
            "type": "primary_filled",
            "width": "fill",
            "form_action_type": "submit",
            "behaviors": [
                {
                    "type": "callback",
                    "value": {
                        "v": CARD_VALUE_VERSION,
                        "a": ACTION_SUBMIT,
                        "h": host_short,
                        "hl": host_label,
                        "g": notice.gate_id,
                        "n": nonce,
                        "k": len(questions),
                    },
                }
            ],
        }
    )
    card = _card(
        "MiniClaw2 需要你的回答",
        "blue",
        [
            _markdown(asked),
            {"tag": "form", "name": "ask", "elements": form_elements},
            _markdown(RETRY_HINT, size="notation"),
        ],
        subtitle=subtitle,
        tag=host_tag(host_label, color),
    )
    card["config"]["summary"] = {"content": _summary_text(notice, host_label)}
    if _card_size(card) > CARD_SIZE_LIMIT:
        return _notice_only_card(
            notice,
            host_label=host_label,
            color=color,
            subtitle=subtitle,
            reason="问题内容过长，请回到网页端作答。",
            asked=asked,
        )
    return card


def final_card(
    *,
    title: str,
    template: str,
    subtitle: str,
    host_label: str,
    color: str,
    lines: list[str],
) -> dict[str, Any]:
    """A terminal card: no interactive components, host tag and subtitle kept."""
    return _card(
        title,
        template,
        [_markdown("\n".join(line for line in lines if line))],
        subtitle=subtitle,
        tag=host_tag(host_label, color),
    )


def answer_lines(
    questions: list[dict[str, Any]],
    answers: dict[str, Any] | None,
) -> list[str]:
    """Readable ``- **Q**：A、B`` lines for a canonical answer mapping."""
    if not isinstance(answers, dict):
        return []
    lines: list[str] = []
    for index, question in enumerate(questions):
        entry = answers.get(question_key(question, index))
        values = entry.get("answers") if isinstance(entry, dict) else None
        if not isinstance(values, list):
            continue
        heading = str(question.get("header") or "").strip() or f"第 {index + 1} 题"
        joined = "、".join(str(value) for value in values)
        lines.append(f"- **{_escape(heading)}**：{_escape(_clip(joined, ANSWER_SUMMARY_LIMIT))}")
    return lines


def test_card(*, host_short: str, host_label: str, color: str) -> dict[str, Any]:
    return _card(
        "MiniClaw2 连通测试",
        "blue",
        [
            _markdown(
                f"这张卡片由 **{_escape(host_label)}** 发出。点击下面的按钮，"
                "弹出的提示会写明是哪台主机收到了点击。"
            ),
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "测试点击"},
                "type": "primary_filled",
                "behaviors": [
                    {
                        "type": "callback",
                        "value": {
                            "v": CARD_VALUE_VERSION,
                            "a": ACTION_TEST,
                            "h": host_short,
                            "hl": host_label,
                        },
                    }
                ],
            },
            _markdown(RETRY_HINT, size="notation"),
        ],
        subtitle="飞书远程回答",
        tag=host_tag(host_label, color),
    )


def welcome_card(*, host_label: str, color: str) -> dict[str, Any]:
    return final_card(
        title=f"已连接 · 本机：{host_label}",
        template="green",
        subtitle="飞书远程回答",
        host_label=host_label,
        color=color,
        lines=[
            "之后这台主机上的 agent 提问，在网页端等待一段时间仍未回答时，会推送到这里。",
            "卡片头部的标签区分不同主机，副标题写明项目、方向和节点。",
        ],
    )


# ---------- receipts (card mailbox) ----------


def encode_receipt(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return RECEIPT_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_receipt(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str) or not text.startswith(RECEIPT_PREFIX):
        return None
    body = text[len(RECEIPT_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def find_receipt(card: Any) -> dict[str, Any] | None:
    """Locate the receipt by ``element_id``, never by searching body text."""
    for element in _walk(card):
        if element.get("element_id") != RECEIPT_ELEMENT_ID:
            continue
        text = element.get("text")
        content = text.get("content") if isinstance(text, dict) else element.get("content")
        if isinstance(content, str):
            return decode_receipt(content.strip())
    return None


def receipt_form_too_long(form_value: dict[str, Any]) -> bool:
    encoded = json.dumps(form_value, ensure_ascii=False, separators=(",", ":"))
    return len(encoded.encode("utf-8")) > RECEIPT_FORM_LIMIT


def receipt_card(
    *,
    value: dict[str, Any],
    form_value: dict[str, Any],
    operator_open_id: str | None,
    via_short: str,
    via_label: str,
    at: float,
) -> dict[str, Any]:
    owner_label = str(value.get("hl") or value.get("h") or "属主主机")
    payload = {
        "v": CARD_VALUE_VERSION,
        "via": via_short,
        "vl": via_label,
        "at": at,
        "value": value,
        "fv": form_value,
        "op": operator_open_id,
    }
    return _card(
        f"已收到，正在交给 {owner_label}",
        "wathet",
        [
            _markdown(
                f"答案已由 **{_escape(via_label)}** 代收，正在等待 "
                f"**{_escape(owner_label)}** 确认……\n"
                "**长时间停在这里说明该主机离线，请回网页端处理。**"
            ),
            _markdown(f"你的回答：{_escape(_form_summary(form_value, value.get('k')))}"),
            {
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {"title": {"tag": "plain_text", "content": "回执数据"}},
                "elements": [
                    {
                        "tag": "div",
                        "element_id": RECEIPT_ELEMENT_ID,
                        "text": {"tag": "plain_text", "content": encode_receipt(payload)},
                    }
                ],
            },
        ],
        subtitle="",
        tag=host_tag(owner_label, host_color(str(value.get("h") or ""))),
    )


def toast(kind: str, content: str) -> dict[str, Any]:
    return {"toast": {"type": kind, "content": content}}


def with_card(response: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    return {**response, "card": {"type": "raw", "data": card}}


def clock(at: float | None) -> str:
    return time.strftime("%H:%M", time.localtime(at if at is not None else time.time()))


# ---------- internals ----------


def _question_elements(question: dict[str, Any], index: int) -> list[dict[str, Any]]:
    heading = str(question.get("header") or "").strip() or f"第 {index + 1} 题"
    text = _clip(str(question.get("question") or ""), QUESTION_TEXT_LIMIT)
    if len(str(question.get("question") or "")) > QUESTION_TEXT_LIMIT:
        text += "\n（完整内容请在网页端查看）"
    body = [f"**{_escape(heading)}**", _escape(text)]
    descriptions = []
    for option in question.get("options") or []:
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or "").strip()
        description = str(option.get("description") or "").strip()
        if label and description:
            descriptions.append(
                f"- **{_escape(_clip(label, OPTION_LABEL_LIMIT))}**："
                f"{_escape(_clip(description, DESCRIPTION_LIMIT))}"
            )
    if descriptions:
        body.append("\n".join(descriptions))
    elements: list[dict[str, Any]] = [_markdown("\n".join(part for part in body if part))]
    labels = option_labels(question)
    if labels and question.get("multiSelect"):
        elements.extend(
            {
                "tag": "checker",
                "name": f"q{index}_{position}",
                "text": {"tag": "plain_text", "content": _clip(label, OPTION_LABEL_LIMIT)},
            }
            for position, label in enumerate(labels)
        )
    elif labels:
        elements.append(
            {
                "tag": "select_static",
                "name": f"q{index}",
                "placeholder": {"tag": "plain_text", "content": "请选择"},
                "options": [
                    {
                        "text": {"tag": "plain_text", "content": _clip(label, OPTION_LABEL_LIMIT)},
                        "value": str(position),
                    }
                    for position, label in enumerate(labels)
                ],
            }
        )
    elements.append(
        {
            "tag": "input",
            "name": f"o{index}",
            "placeholder": {
                "tag": "plain_text",
                "content": "其他（填写后覆盖本题选项）" if labels else "输入你的回答",
            },
        }
    )
    return elements


def _notice_only_card(
    notice: GateNotice,
    *,
    host_label: str,
    color: str,
    subtitle: str,
    reason: str,
    asked: str,
) -> dict[str, Any]:
    lines = [asked]
    for index, question in enumerate(notice.questions):
        heading = str(question.get("header") or "").strip() or f"第 {index + 1} 题"
        text = _clip(str(question.get("question") or ""), 300)
        lines.append(f"**{_escape(heading)}** {_escape(text)}")
    lines.append(f"**{reason}**")
    card = _card(
        "MiniClaw2 需要你的回答",
        "blue",
        [_markdown("\n".join(lines))],
        subtitle=subtitle,
        tag=host_tag(host_label, color),
    )
    card["config"]["summary"] = {"content": _summary_text(notice, host_label)}
    return card


def _summary_text(notice: GateNotice, host_label: str) -> str:
    first = ""
    for question in notice.questions:
        first = str(question.get("question") or question.get("header") or "").strip()
        if first:
            break
    context = " · ".join(
        part for part in (notice.project_name, notice.node_label) if part
    )
    return _clip(f"[{host_label}] {context}：{_clip(first, 30)}", 120)


def _asked_line(created_at: float, now: float | None) -> str:
    moment = time.time() if now is None else now
    waited = max(0, int((moment - created_at) // 60)) if created_at else 0
    suffix = f"（已等待 {waited} 分钟）" if waited else ""
    return f"提问于 {clock(created_at or moment)}{suffix}"


def _form_summary(form_value: dict[str, Any], count: Any) -> str:
    total = count if isinstance(count, int) and count >= 0 else 0
    parts: list[str] = []
    for index in range(total):
        text = str(form_value.get(f"o{index}") or "").strip()
        if text:
            parts.append(f"第 {index + 1} 题 其他：{_clip(text, 60)}")
            continue
        selected = _option_index(form_value.get(f"q{index}"))
        if selected is not None:
            parts.append(f"第 {index + 1} 题 选第 {selected + 1} 项")
            continue
        checked = sorted(
            position + 1
            for key, value in form_value.items()
            if key.startswith(f"q{index}_") and _checked(value)
            for position in [_option_index(key[len(f"q{index}_"):])]
            if position is not None
        )
        if checked:
            parts.append(f"第 {index + 1} 题 选第 {'、'.join(map(str, checked))} 项")
    return "；".join(parts) or "（无）"


def _card(
    title: str,
    template: str,
    elements: list[dict[str, Any]],
    *,
    subtitle: str,
    tag: dict[str, Any],
) -> dict[str, Any]:
    header: dict[str, Any] = {
        "title": {"tag": "plain_text", "content": title},
        "template": template,
        "text_tag_list": [tag],
    }
    if subtitle:
        header["subtitle"] = {"tag": "plain_text", "content": subtitle}
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": header,
        "body": {"elements": elements},
    }


def _markdown(content: str, *, size: str | None = None) -> dict[str, Any]:
    element: dict[str, Any] = {"tag": "markdown", "content": content}
    if size:
        element["text_size"] = size
    return element


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _checked(value: Any) -> bool:
    return value is True or value == "true"


def _option_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _clip(value: str, limit: int) -> str:
    text = " ".join(value.split()) if "\n" not in value else value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _escape(value: str) -> str:
    """Neutralize card markdown that could fake structure (links, mentions)."""
    return value.replace("<", "&lt;").replace(">", "&gt;")


def _card_size(card: dict[str, Any]) -> int:
    return len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
