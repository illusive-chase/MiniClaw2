"""Pure WeCom (企业微信) message builders and answer handling for ``ask_user``.

An intelligent bot's template cards have no text input, so a gate is split:

- one markdown *detail* message with every question, every option and its
  description in full;
- then one message per question, in order: a single-select question with
  few options becomes a ``button_interaction`` card (one tap answers), any
  other question with options a ``vote_interaction`` card (single- or
  multi-select checkbox), and a question without options a markdown prompt.
  Every option list ends with 「其他（文字回答）」.

One question per card is what keeps free text unambiguous: text is accepted
only from a reply that *quotes* a question message, and the marker in the
quoted text WeCom sends back names exactly one question. Each card and
prompt carries ``#<ref>-<n>``; the detail's first line carries the gate's
``#<ref>`` (first, so a quote clipped at the end still has it), which names
a question only when the gate has just one. A message that quotes nothing
is never taken as an answer.

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

OTHER_ID = "o"
OTHER_TEXT = "其他（文字回答）"
SUBMIT_KEY = "submit"

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
QUOTE_CARD = "要用文字回答，请长按该题的卡片，选择「引用」后回复。"
QUOTE_RULE = "只有引用了提问消息的回复才会被记录为答案；网页端也可以随时作答，先答先生效。"

CardKind = Literal["button", "vote"]
Choice = Literal["none", "other", "answer"]


@dataclass(frozen=True)
class CardSpec:
    task_id: str
    kind: CardKind
    #: Index into the gate's questions.
    question: int
    card: dict[str, Any] = field(compare=False)


@dataclass(frozen=True)
class PushPlan:
    detail: str
    #: What follows the detail, one per question in order: its card, or
    #: the markdown prompt of a question without options.
    messages: tuple[str | CardSpec, ...] = ()
    #: Questions without options: answered only by a quoted reply.
    text_questions: tuple[int, ...] = ()
    #: The detail itself is the prompt for the only (text) question.
    detail_is_prompt: bool = False
    #: The gate cannot be answered here; the detail says why.
    notice_only: bool = False

    @property
    def cards(self) -> tuple[CardSpec, ...]:
        return tuple(message for message in self.messages if isinstance(message, CardSpec))


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

    subtitle = card_subtitle(notice)
    gate = _TASK_UNSAFE.sub("", notice.gate_id)[:64] or "gate"
    total = len(questions)
    text_questions = tuple(
        index for index, question in enumerate(questions) if not option_labels(question)
    )
    detail_is_prompt = total == 1 and bool(text_questions)
    messages: list[str | CardSpec] = []
    for index, question in enumerate(questions):
        labels = option_labels(question)
        if not labels:
            if not detail_is_prompt:
                messages.append(text_prompt_markdown(notice, ref=ref, index=index))
            continue
        kind: CardKind = (
            "button"
            if not question.get("multiSelect") and len(labels) <= BUTTON_OPTIONS
            else "vote"
        )
        task_id = f"mc2-{gate}-{index + 1}-{nonce}"
        card = _question_card(
            kind,
            question,
            index,
            ref=ref,
            task_id=task_id,
            prefix=f"({index + 1}/{total}) " if total > 1 else "",
            subtitle=subtitle,
            host_label=host_label,
            color=color,
        )
        messages.append(CardSpec(task_id=task_id, kind=kind, question=index, card=card))
    if detail_is_prompt:
        footer = [f"**{QUOTE_HOW}**"]
    else:
        how = ["下方每道题单独一条消息。"] if total > 1 else []
        if len(text_questions) < total:
            how.append(
                "在卡片中作答；选了「其他（文字回答）」的题目，请长按该题的卡片，选择「引用」后回复文字。"
            )
        if text_questions:
            how.append("没有选项的题目，请长按它的提示消息，选择「引用」后回复文字。")
        footer = ["".join(how)]
    return PushPlan(
        detail=detail_markdown(notice, ref=ref, host_label=host_label, now=now, footer=footer),
        messages=tuple(messages),
        text_questions=text_questions,
        detail_is_prompt=detail_is_prompt,
    )


def card_ids(spec: CardSpec, event_key: str, selections: dict[str, list[str]]) -> list[str]:
    """The option ids a click chose for the card's question."""
    if spec.kind == "button":
        key, _, option = (event_key or "").partition(":")
        return [option] if _question_index(key) == spec.question and option else []
    return [str(item) for item in selections.get(f"q{spec.question}") or []]


def interpret(question: dict[str, Any], ids: list[str]) -> tuple[Choice, list[str]]:
    """Read one question's chosen ids: nothing, 「其他」, or labels."""
    chosen = [item for item in ids if item]
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


def card_after_click(spec: CardSpec, ids: list[str], *, done: bool, status: str) -> dict[str, Any]:
    """The clicked card again, showing what was chosen; ``done`` locks a checkbox."""
    card = copy.deepcopy(spec.card)
    card["main_title"]["desc"] = _clip(status, DESC_LIMIT)
    if spec.kind == "vote":
        chosen = set(ids)
        for option in card["checkbox"]["option_list"]:
            option["is_checked"] = option["id"] in chosen
        if done:
            card["checkbox"]["disable"] = True
            card["submit_button"]["text"] = "已提交"
    else:
        # Buttons stay live, so a choice (「其他」 too) can be changed by another tap.
        chosen_id = ids[0] if ids else ""
        for button in card["button_list"]:
            if button["key"].partition(":")[2] == chosen_id:
                button["text"] = _clip("✔ " + button["text"], BUTTON_TEXT_LIMIT)
    return card


def closed_card(
    shown: dict[str, Any], *, kind: CardKind, status: str, answered: bool
) -> dict[str, Any]:
    """The card as last shown, locked, for any click after its gate closed."""
    card = copy.deepcopy(shown)
    card["main_title"]["desc"] = _clip(status, DESC_LIMIT)
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
    """A closed card for a click whose card this process no longer knows.

    Dropdown cards are no longer sent, but ones pushed by an older version
    can still be clicked.
    """
    keys = question_keys or ["q0"]
    card: dict[str, Any] = {
        "card_type": card_type,
        "source": _source(host_label, color),
        "main_title": {"title": "提问已失效", "desc": "请回网页端作答"},
        "task_id": task_id,
    }
    closed = [{"id": "_", "text": "已失效"}]
    if card_type == "multiple_interaction":
        card["select_list"] = [
            {"question_key": key, "title": "已失效", "option_list": closed, "disable": True}
            for key in keys[:3]
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
        "要用文字回答，请长按该题的卡片（没有选项的题目是它的提示消息），选择「引用」后回复。"
        "没有引用提问的消息不会被记录为答案。"
    )


# ---------- internals ----------


def _question_card(
    kind: CardKind,
    question: dict[str, Any],
    index: int,
    *,
    ref: str,
    task_id: str,
    prefix: str,
    subtitle: str,
    host_label: str,
    color: int,
) -> dict[str, Any]:
    # The marker ends both the title and the subtitle, kept whole, so a reply
    # quoting the card names its question whichever field the quote carries.
    tag = marker(ref, index)
    title = f"{_clip(prefix + _heading(question, index), TITLE_LIMIT - len(tag) - 1)} {tag}"
    text = _question_text(question)
    if question.get("multiSelect"):
        text += "（可多选）"
    card: dict[str, Any] = {
        "card_type": {"button": "button_interaction", "vote": "vote_interaction"}[kind],
        "source": _source(host_label, color),
        "main_title": {"title": title, "desc": _clip(subtitle, DESC_LIMIT)},
        "sub_title_text": f"{_clip(text, SUBTITLE_TEXT_LIMIT - len(tag) - 1)} {tag}",
        "task_id": task_id,
    }
    if kind == "button":
        texts = _option_texts(option_labels(question), BUTTON_TEXT_LIMIT)
        card["button_list"] = [
            {"text": text, "style": 1, "key": f"q{index}:{position}"}
            for position, text in enumerate(texts)
        ] + [{"text": OTHER_TEXT, "style": 2, "key": f"q{index}:{OTHER_ID}"}]
    else:
        card["checkbox"] = {
            "question_key": f"q{index}",
            "mode": 1 if question.get("multiSelect") else 0,
            "option_list": [
                *(
                    {"id": str(position), "text": text}
                    for position, text in enumerate(
                        _option_texts(option_labels(question), VOTE_OPTION_LIMIT)
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
