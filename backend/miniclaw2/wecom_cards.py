"""Pure WeCom (企业微信) message builders and answer handling for ``ask_user``.

An intelligent bot's template cards have no text input and hold at most
three dropdowns or a single checkbox question, so a gate is split:

- one markdown *detail* message with every question, every option and its
  description in full;
- then cards by question shape: a lone single-select question becomes one
  ``button_interaction`` card (one tap answers); other single-select
  questions share ``multiple_interaction`` cards of up to three dropdowns,
  whose first entry is a 「请选择」 placeholder so an untouched dropdown
  reads as unanswered; each multi-select question, and a single-select one
  too long for a dropdown, gets its own ``vote_interaction`` card. Every
  option list ends with 「其他（文字回答）」.

Free text is accepted only from a reply that *quotes* a question message.
The markers survive in the quoted text WeCom sends back: the detail's first
line carries the gate's ``#<ref>`` (all of its questions; first, so a quote
clipped at the end still has it), and each per-question prompt and card
carries ``#<ref>-<n>``. So the bridge knows which question a reply answers;
a message that quotes nothing is never taken as an answer.

A card can be changed only by the response to a click on it, within 5 s of
the click, so a card whose gate is gone is replaced by a closed card the next
time someone clicks it.
"""

from __future__ import annotations

import copy
import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .feishu_cards import card_subtitle, clock, has_secret_question, option_labels, question_key
from .gate_observer import GateNotice

PLACEHOLDER_ID = "_"
OTHER_ID = "o"
OTHER_TEXT = "其他（文字回答）"
PLACEHOLDER_TEXT = "请选择"
SUBMIT_KEY = "submit"

#: Dropdowns per ``multiple_interaction`` card.
SELECTS_PER_CARD = 3
#: Real options that fit a dropdown (10, minus the placeholder and 其他).
SELECT_OPTIONS = 8
#: Real options that fit a checkbox (20, minus 其他).
VOTE_OPTIONS = 19
#: Real options shown as buttons (6, minus 其他).
BUTTON_OPTIONS = 5

#: WeCom's markdown limit is 20480 bytes; leave headroom.
MARKDOWN_LIMIT = 20000
TITLE_LIMIT = 26
DESC_LIMIT = 30
SUBTITLE_TEXT_LIMIT = 112
SOURCE_LIMIT = 13
SELECT_TITLE_LIMIT = 13
SELECT_OPTION_LIMIT = 10
VOTE_OPTION_LIMIT = 11
BUTTON_TEXT_LIMIT = 10
QUESTION_TEXT_LIMIT = 1500
DESCRIPTION_LIMIT = 200
PROMPT_TEXT_LIMIT = 500
ANSWER_ECHO_LIMIT = 60

REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MARKER_RE = re.compile(r"#([A-HJ-NP-Z2-9]{4})(?:-(\d{1,2}))?(?![0-9A-Za-z])")
_TASK_UNSAFE = re.compile(r"[^0-9A-Za-z_\-@]")

#: ``source.desc_color``: 0 grey, 1 black, 2 red, 3 green.
HOST_COLORS = (0, 1, 2, 3)

QUOTE_HOW = "长按本条消息，选择「引用」后回复你的答案。"
QUOTE_RULE = "只有引用了提问消息的回复才会被记录为答案；网页端也可以随时作答，先答先生效。"

CardKind = Literal["button", "select", "vote"]
Choice = Literal["none", "other", "answer"]


@dataclass(frozen=True)
class CardSpec:
    task_id: str
    kind: CardKind
    #: Indices into the gate's questions.
    questions: tuple[int, ...]
    card: dict[str, Any] = field(compare=False)


@dataclass(frozen=True)
class PushPlan:
    detail: str
    cards: tuple[CardSpec, ...] = ()
    #: Questions without options: answered only by a quoted reply.
    text_questions: tuple[int, ...] = ()
    #: The detail itself is the prompt for the only (text) question.
    detail_is_prompt: bool = False
    #: The gate cannot be answered here; the detail says why.
    notice_only: bool = False


def host_color(host_short: str) -> int:
    digest = hashlib.sha256(host_short.encode("utf-8")).hexdigest()
    return HOST_COLORS[int(digest, 16) % len(HOST_COLORS)]


def new_ref(taken: set[str]) -> str:
    while True:
        ref = "".join(secrets.choice(REF_ALPHABET) for _ in range(4))
        if ref not in taken:
            return ref


def marker(ref: str, index: int | None = None) -> str:
    """``#REF`` names a whole gate, ``#REF-n`` its question ``n``."""
    return f"#{ref}" if index is None else f"#{ref}-{index + 1}"


def find_markers(text: str) -> list[tuple[str, int | None]]:
    """``(ref, question index or None for the whole gate)`` in quoted text."""
    found: list[tuple[str, int | None]] = []
    for match in MARKER_RE.finditer(text or ""):
        number = match.group(2)
        if number is not None and int(number) < 1:
            continue
        entry = (match.group(1), int(number) - 1 if number is not None else None)
        if entry not in found:
            found.append(entry)
    return found


def plan_push(
    notice: GateNotice,
    *,
    ref: str,
    host_label: str,
    color: int,
    nonce: str,
    now: float | None = None,
) -> PushPlan:
    """Split a gate into the messages that present it in WeCom."""
    questions = notice.questions
    if not questions:
        return PushPlan(
            detail=notice_only_markdown(
                notice, host_label=host_label, reason="此提问没有可显示的问题，请回到网页端查看。", now=now
            ),
            notice_only=True,
        )
    if has_secret_question(questions):
        return PushPlan(
            detail=notice_only_markdown(
                notice, host_label=host_label, reason="此提问包含敏感输入，请回到网页端作答。", now=now
            ),
            notice_only=True,
        )
    if any(len(option_labels(question)) > VOTE_OPTIONS for question in questions):
        return PushPlan(
            detail=notice_only_markdown(
                notice,
                host_label=host_label,
                reason=f"有题目的选项超过 {VOTE_OPTIONS} 个，企业微信卡片放不下，请回到网页端作答。",
                now=now,
            ),
            notice_only=True,
        )

    groups: list[tuple[CardKind, tuple[int, ...]]] = []
    first = questions[0]
    if (
        len(questions) == 1
        and not first.get("multiSelect")
        and 0 < len(option_labels(first)) <= BUTTON_OPTIONS
    ):
        groups.append(("button", (0,)))
    else:
        selects: list[int] = []
        for index, question in enumerate(questions):
            labels = option_labels(question)
            if not labels:
                continue
            if question.get("multiSelect") or len(labels) > SELECT_OPTIONS:
                groups.append(("vote", (index,)))
                continue
            selects.append(index)
            if len(selects) == SELECTS_PER_CARD:
                groups.append(("select", tuple(selects)))
                selects = []
        if selects:
            groups.append(("select", tuple(selects)))
        groups.sort(key=lambda group: group[1][0])

    text_questions = tuple(
        index for index, question in enumerate(questions) if not option_labels(question)
    )
    detail_is_prompt = len(questions) == 1 and bool(text_questions)
    subtitle = card_subtitle(notice)
    gate = _TASK_UNSAFE.sub("", notice.gate_id)[:64] or "gate"
    specs: list[CardSpec] = []
    for position, (kind, indices) in enumerate(groups):
        task_id = f"mc2-{gate}-{position + 1}-{nonce}"
        card = _question_card(
            kind,
            questions,
            indices,
            ref=ref,
            task_id=task_id,
            prefix=f"({position + 1}/{len(groups)}) " if len(groups) > 1 else "",
            subtitle=subtitle,
            host_label=host_label,
            color=color,
        )
        specs.append(CardSpec(task_id=task_id, kind=kind, questions=indices, card=card))
    if detail_is_prompt:
        footer = [f"**{QUOTE_HOW}**"]
    elif not specs:
        footer = ["每道题下方会单独发一条提示消息，请分别长按它们，选择「引用」后回复文字。"]
    else:
        footer = [
            "在下方卡片中作答。选了「其他（文字回答）」或没有选项的题目，"
            "请长按对应的提示消息，选择「引用」后回复文字。"
        ]
    return PushPlan(
        detail=detail_markdown(notice, ref=ref, host_label=host_label, now=now, footer=footer),
        cards=tuple(specs),
        text_questions=text_questions,
        detail_is_prompt=detail_is_prompt,
    )


def card_ids(spec: CardSpec, event_key: str, selections: dict[str, list[str]]) -> dict[int, list[str]]:
    """The option ids a click chose, per question on the card."""
    if spec.kind == "button":
        key, _, option = (event_key or "").partition(":")
        index = _question_index(key)
        return {index: [option]} if index in spec.questions and option else {}
    return {
        index: [str(item) for item in selections.get(f"q{index}") or []]
        for index in spec.questions
    }


def interpret(question: dict[str, Any], ids: list[str]) -> tuple[Choice, list[str]]:
    """Read one question's chosen ids: nothing, 「其他」, or labels."""
    chosen = [item for item in ids if item != PLACEHOLDER_ID]
    if not chosen:
        return "none", []
    if OTHER_ID in chosen:
        return "other", []
    labels = option_labels(question)
    positions = sorted(
        {int(item) for item in chosen if item.isdigit() and int(item) < len(labels)}
    )
    if not positions:
        return "none", []
    if not question.get("multiSelect"):
        positions = positions[:1]
    return "answer", [labels[position] for position in positions]


def answer_mapping(
    questions: list[dict[str, Any]], answers: dict[int, list[str]]
) -> dict[str, dict[str, list[str]]]:
    """The canonical gate answer, keyed like ``AskUserDialog.submit``."""
    return {
        question_key(question, index): {"answers": list(answers[index])}
        for index, question in enumerate(questions)
        if index in answers
    }


def card_after_click(
    spec: CardSpec, ids: dict[int, list[str]], *, done: bool, status: str
) -> dict[str, Any]:
    """The clicked card again, showing what was chosen; ``done`` locks it."""
    card = copy.deepcopy(spec.card)
    card["main_title"]["desc"] = _clip(status, DESC_LIMIT)
    if spec.kind == "select":
        for item in card["select_list"]:
            chosen = ids.get(_question_index(item["question_key"])) or []
            item["selected_id"] = chosen[0] if chosen else PLACEHOLDER_ID
            if done:
                item["disable"] = True
    elif spec.kind == "vote":
        chosen_set = set(ids.get(spec.questions[0]) or [])
        for option in card["checkbox"]["option_list"]:
            option["is_checked"] = option["id"] in chosen_set
        if done:
            card["checkbox"]["disable"] = True
    else:
        chosen = (ids.get(spec.questions[0]) or [""])[0]
        for button in card["button_list"]:
            if button["key"].partition(":")[2] == chosen:
                button["text"] = _clip("✔ " + button["text"], BUTTON_TEXT_LIMIT)
    if done and "submit_button" in card:
        card["submit_button"]["text"] = "已提交"
    return card


def closed_card(
    shown: dict[str, Any], *, kind: CardKind, status: str, answered: bool
) -> dict[str, Any]:
    """The card as last shown, locked, for any click after its gate closed."""
    card = copy.deepcopy(shown)
    card["main_title"]["desc"] = _clip(status, DESC_LIMIT)
    for item in card.get("select_list") or []:
        item["disable"] = True
    if "checkbox" in card:
        card["checkbox"]["disable"] = True
    if "submit_button" in card:
        card["submit_button"]["text"] = "已提交" if answered else "已失效"
    if kind == "button" and not answered:
        card["button_list"] = [{"text": "已失效", "style": 2, "key": "closed"}]
    return card


def expired_card(
    *,
    card_type: str,
    question_keys: list[str],
    host_label: str,
    color: int,
    task_id: str,
) -> dict[str, Any] | None:
    """A closed card for a click whose card this process no longer knows."""
    keys = question_keys or ["q0"]
    card: dict[str, Any] = {
        "card_type": card_type,
        "source": _source(host_label, color),
        "main_title": {"title": "提问已失效", "desc": "请回网页端作答"},
        "task_id": task_id,
    }
    closed = [{"id": PLACEHOLDER_ID, "text": "已失效"}]
    if card_type == "multiple_interaction":
        card["select_list"] = [
            {"question_key": key, "title": "已失效", "option_list": closed, "disable": True}
            for key in keys[:SELECTS_PER_CARD]
        ]
        card["submit_button"] = {"text": "已失效", "key": SUBMIT_KEY}
    elif card_type == "vote_interaction":
        card["checkbox"] = {"question_key": keys[0], "mode": 0, "option_list": closed, "disable": True}
        card["submit_button"] = {"text": "已失效", "key": SUBMIT_KEY}
    elif card_type == "button_interaction":
        card["button_list"] = [{"text": "已失效", "style": 2, "key": "closed"}]
    else:
        return None
    return card


def test_card(*, task_id: str, host_label: str, color: int) -> dict[str, Any]:
    return {
        "card_type": "button_interaction",
        "source": _source(host_label, color),
        "main_title": {"title": "MiniClaw2 连通测试", "desc": "企业微信远程回答"},
        "sub_title_text": _clip(
            f"这张卡片由 {host_label} 发出。点击下面的按钮，卡片会写明是哪台主机收到了点击。",
            SUBTITLE_TEXT_LIMIT,
        ),
        "button_list": [{"text": "测试点击", "style": 1, "key": "test"}],
        "task_id": task_id,
    }


def test_clicked_card(card: dict[str, Any], *, host_label: str, at: float) -> dict[str, Any]:
    clicked = copy.deepcopy(card)
    clicked["main_title"]["desc"] = _clip(
        f"✔ {host_label} 收到 {time.strftime('%H:%M:%S', time.localtime(at))}", DESC_LIMIT
    )
    return clicked


# ---------- markdown ----------


def detail_markdown(
    notice: GateNotice,
    *,
    ref: str,
    host_label: str,
    now: float | None = None,
    footer: list[str] | None = None,
) -> str:
    lines = [
        f"**MiniClaw2 需要你的回答** {marker(ref)}",
        f"本机：{plain(host_label)} · {_asked_line(notice.created_at, now)}",
    ]
    subtitle = card_subtitle(notice)
    if subtitle:
        lines.append(f"> {plain(subtitle)}")
    for index, question in enumerate(notice.questions):
        lines.append("")
        lines.extend(_question_lines(question, index))
    lines.append("")
    lines.extend(footer or [])
    lines.append(QUOTE_RULE)
    return _fit("\n".join(lines))


def notice_only_markdown(
    notice: GateNotice, *, host_label: str, reason: str, now: float | None = None
) -> str:
    lines = [
        "**MiniClaw2 需要你的回答**",
        f"本机：{plain(host_label)} · {_asked_line(notice.created_at, now)}",
    ]
    subtitle = card_subtitle(notice)
    if subtitle:
        lines.append(f"> {plain(subtitle)}")
    for index, question in enumerate(notice.questions):
        text = _clip(str(question.get("question") or ""), 300)
        lines.append(f"**{plain(_heading(question, index))}** {plain(text)}")
    lines.append(f"**{reason}**")
    return _fit("\n".join(lines))


def text_prompt_markdown(notice: GateNotice, *, ref: str, index: int) -> str:
    question = notice.questions[index]
    text = _clip(str(question.get("question") or ""), PROMPT_TEXT_LIMIT)
    subtitle = card_subtitle(notice)
    lines = [
        f"**第 {index + 1} 题 · 请用文字回答** {marker(ref, index)}",
        f"> {plain(subtitle)}" if subtitle else "",
        plain(text) if text else plain(_heading(question, index)),
        QUOTE_HOW,
    ]
    return _fit("\n".join(line for line in lines if line))


def welcome_markdown(*, host_label: str) -> str:
    return "\n".join(
        [
            f"**已连接 · 本机：{plain(host_label)}**",
            "之后这台主机上的 agent 提问，在网页端等待一段时间仍未回答时，会推送到这里。",
            "卡片左上角写明来自哪台主机，标题下方写明项目、方向和节点。",
            "需要文字回答时，请长按提问消息选择「引用」后回复；没有引用提问的消息不会被记录。",
        ]
    )


def closed_markdown(notice: GateNotice, *, answered_on_web: bool) -> str:
    what = "已在网页端回答" if answered_on_web else "已结束"
    return f"『{plain(card_subtitle(notice))}』的提问{what}，之前推送的卡片已失效。"


def recorded_markdown(
    notice: GateNotice, *, index: int, text: str, remaining: list[int], resolved: bool
) -> str:
    head = (
        f"已记录『{plain(card_subtitle(notice))}』第 {index + 1} 题："
        f"{plain(_clip(text, ANSWER_ECHO_LIMIT))}"
    )
    if resolved:
        return f"{head}\n全部题目已回答，答案已交给 agent。"
    if remaining:
        return f"{head}\n还差第 {'、'.join(str(item + 1) for item in remaining)} 题。"
    return head


def hint_markdown() -> str:
    return (
        "要用文字回答，请长按对应的提问消息，选择「引用」后回复。"
        "没有引用提问的消息不会被记录为答案。"
    )


# ---------- internals ----------


def _question_card(
    kind: CardKind,
    questions: list[dict[str, Any]],
    indices: tuple[int, ...],
    *,
    ref: str,
    task_id: str,
    prefix: str,
    subtitle: str,
    host_label: str,
    color: int,
) -> dict[str, Any]:
    first = questions[indices[0]]
    # Markers stay whole at the end, so a reply quoting the card still names its questions.
    markers = " ".join(marker(ref, index) for index in indices)
    budget = SUBTITLE_TEXT_LIMIT - len(markers) - 1
    if kind == "select" and len(indices) > 1:
        title = "请选择"
        share = max(8, budget // len(indices) - 4)
        sub_title = "；".join(
            f"{index + 1}. {_clip(_question_text(questions[index]), share)}" for index in indices
        )
    else:
        title = _heading(first, indices[0])
        sub_title = _question_text(first)
        if kind == "vote" and first.get("multiSelect"):
            sub_title += "（可多选）"
    sub_title = f"{_clip(sub_title, budget)} {markers}"
    card: dict[str, Any] = {
        "card_type": {
            "button": "button_interaction",
            "select": "multiple_interaction",
            "vote": "vote_interaction",
        }[kind],
        "source": _source(host_label, color),
        "main_title": {
            "title": _clip(prefix + title, TITLE_LIMIT),
            "desc": _clip(subtitle, DESC_LIMIT),
        },
        "sub_title_text": sub_title,
        "task_id": task_id,
    }
    if kind == "button":
        texts = _option_texts(option_labels(first), BUTTON_TEXT_LIMIT)
        card["button_list"] = [
            {"text": text, "style": 1, "key": f"q{indices[0]}:{position}"}
            for position, text in enumerate(texts)
        ] + [{"text": OTHER_TEXT, "style": 2, "key": f"q{indices[0]}:{OTHER_ID}"}]
    elif kind == "select":
        card["select_list"] = [
            {
                "question_key": f"q{index}",
                "title": _clip(f"{index + 1}. {_heading(questions[index], index)}", SELECT_TITLE_LIMIT),
                "option_list": [
                    {"id": PLACEHOLDER_ID, "text": PLACEHOLDER_TEXT},
                    *(
                        {"id": str(position), "text": text}
                        for position, text in enumerate(
                            _option_texts(option_labels(questions[index]), SELECT_OPTION_LIMIT)
                        )
                    ),
                    {"id": OTHER_ID, "text": OTHER_TEXT},
                ],
            }
            for index in indices
        ]
        card["submit_button"] = {"text": "提交", "key": SUBMIT_KEY}
    else:
        card["checkbox"] = {
            "question_key": f"q{indices[0]}",
            "mode": 1 if first.get("multiSelect") else 0,
            "option_list": [
                *(
                    {"id": str(position), "text": text}
                    for position, text in enumerate(
                        _option_texts(option_labels(first), VOTE_OPTION_LIMIT)
                    )
                ),
                {"id": OTHER_ID, "text": OTHER_TEXT},
            ],
        }
        card["submit_button"] = {"text": "提交", "key": SUBMIT_KEY}
    return card


def _question_lines(question: dict[str, Any], index: int) -> list[str]:
    raw = str(question.get("question") or "")
    text = _clip(raw, QUESTION_TEXT_LIMIT)
    if len(raw) > QUESTION_TEXT_LIMIT:
        text += "\n（完整内容请在网页端查看）"
    kind = "可多选" if question.get("multiSelect") else ("单选" if option_labels(question) else "文字回答")
    header = plain(str(question.get("header") or "").strip())
    title = f"第 {index + 1} 题 · {header}" if header else f"第 {index + 1} 题"
    lines = [f"**{title}**（{kind}）"]
    if text:
        lines.append(plain(text))
    position = 0
    for option in question.get("options") or []:
        if isinstance(option, dict):
            label = str(option.get("label") or "").strip()
            description = str(option.get("description") or "").strip()
        elif isinstance(option, str):
            label, description = option.strip(), ""
        else:
            continue
        if not label:
            continue
        line = f"{_letter(position)}. {plain(label)}"
        if description:
            line += f"：{plain(_clip(description, DESCRIPTION_LIMIT))}"
        lines.append(line)
        position += 1
    return lines


def _heading(question: dict[str, Any], index: int) -> str:
    return _unmark(str(question.get("header") or "").strip()) or f"第 {index + 1} 题"


def _question_text(question: dict[str, Any]) -> str:
    return _unmark(" ".join(str(question.get("question") or "").split())) or "（无题干）"


def _question_index(key: str) -> int | None:
    if isinstance(key, str) and key.startswith("q") and key[1:].isdigit():
        return int(key[1:])
    return None


def _option_texts(labels: list[str], limit: int) -> list[str]:
    """Labels as they fit on a card.

    When any of them has to be clipped, all are lettered like the detail
    message, which carries every option in full.
    """
    texts = [_unmark(" ".join(label.split())) for label in labels]
    if all(len(text) <= limit for text in texts):
        return texts
    return [_clip(f"{_letter(position)}. {text}", limit) for position, text in enumerate(texts)]


def _letter(position: int) -> str:
    return chr(ord("A") + position) if position < 26 else str(position + 1)


def _source(host_label: str, color: int) -> dict[str, Any]:
    return {"desc": _clip(host_label, SOURCE_LIMIT), "desc_color": color}


def _asked_line(created_at: float, now: float | None) -> str:
    moment = time.time() if now is None else now
    waited = max(0, int((moment - created_at) // 60)) if created_at else 0
    suffix = f"（已等待 {waited} 分钟）" if waited else ""
    return f"提问于 {clock(created_at or moment)}{suffix}"


def _clip(value: str, limit: int) -> str:
    text = " ".join(str(value).split()) if "\n" not in str(value) else str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def plain(value: str) -> str:
    """Neutralize markup that could fake structure, and markers in agent text."""
    return _unmark(str(value).replace("<", "＜").replace(">", "＞"))


def _unmark(text: str) -> str:
    """Agent text must never look like a question marker."""
    return MARKER_RE.sub(lambda match: "＃" + match.group(0)[1:], text)


def _fit(content: str) -> str:
    if len(content.encode("utf-8")) <= MARKDOWN_LIMIT:
        return content
    tail = "\n（内容过长，完整内容请在网页端查看）"
    budget = MARKDOWN_LIMIT - len(tail.encode("utf-8"))
    clipped = content.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return clipped + tail
