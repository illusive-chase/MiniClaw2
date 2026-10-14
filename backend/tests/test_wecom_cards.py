"""Pure WeCom card planning, answer reading and host-local storage."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from miniclaw2 import wecom_cards as cards
from miniclaw2.gate_observer import GateNotice
from miniclaw2.migrations.inventory import EXCLUDED_ROOTS, IGNORED_PATTERNS
from miniclaw2.remote_answers import load_channel, save_channel
from miniclaw2.wecom_config import (
    WecomConfig,
    load_wecom_config,
    save_wecom_config,
    wecom_config_path,
)

SINGLE = {"id": "db", "header": "数据库", "question": "用哪个？", "options": [{"label": "Postgres"}, {"label": "SQLite"}]}
MULTI = {"question": "多选", "multiSelect": True, "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]}
TEXT = {"header": "备注", "question": "还有什么？", "options": None}


def notice(questions: list[dict]) -> GateNotice:
    return GateNotice(
        gate_id="g1",
        project_id="p1",
        node_id="n1",
        subtype="ask_user",
        tool_name="AskUserQuestion",
        questions=questions,
        project_name="demo",
        lane_title="lane",
        node_label="build",
        created_at=1000.0,
    )


def plan(questions: list[dict]) -> cards.PushPlan:
    return cards.plan_push(
        notice(questions), ref="ABCD", host_label="HOST-A", color=1, nonce="n1", now=1000.0
    )


def shape(result: cards.PushPlan) -> list[tuple[str, int]]:
    return [(spec.kind, spec.question) for spec in result.cards]


class PlanTests(unittest.TestCase):
    def test_one_card_per_question(self) -> None:
        single = lambda i: {"question": f"Q{i}", "options": [{"label": "x"}, {"label": "y"}]}  # noqa: E731
        cases = {
            "one single-select": ([SINGLE], [("button", 0)]),
            "two single-selects": ([single(0), single(1)], [("button", 0), ("button", 1)]),
            "four single-selects": ([single(i) for i in range(4)], [("button", i) for i in range(4)]),
            "multi-select": ([MULTI], [("vote", 0)]),
            "text only": ([TEXT], []),
            "mixed": ([SINGLE, MULTI, TEXT, single(3)], [("button", 0), ("vote", 1), ("button", 3)]),
        }
        for name, (questions, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(shape(plan(questions)), expected)
        # A question without options gets its prompt in its place among the cards.
        mixed = plan([SINGLE, MULTI, TEXT, single(3)])
        self.assertEqual(
            [message.question if isinstance(message, cards.CardSpec) else "prompt" for message in mixed.messages],
            [0, 1, "prompt", 3],
        )
        self.assertEqual(cards.find_markers(mixed.messages[2]), [("ABCD", 2)])
        self.assertEqual(len({spec.task_id for spec in mixed.cards}), 3)

    def test_long_option_lists_move_to_checkbox_or_web(self) -> None:
        six = {"question": "六", "options": [{"label": f"o{i}"} for i in range(6)]}
        self.assertEqual(shape(plan([six, SINGLE])), [("vote", 0), ("button", 1)])
        self.assertEqual(plan([six]).cards[0].card["checkbox"]["mode"], 0)
        many = {"question": "多", "options": [{"label": f"o{i}"} for i in range(19)]}
        self.assertEqual(shape(plan([many])), [("vote", 0)])
        too_many = {"question": "太多", "options": [{"label": f"o{i}"} for i in range(20)]}
        result = plan([too_many])
        self.assertTrue(result.notice_only)
        self.assertEqual(result.cards, ())

    def test_secret_and_empty_gates_are_notice_only(self) -> None:
        for questions in ([{**SINGLE, "isSecret": True}], []):
            with self.subTest(questions=questions):
                result = plan(questions)
                self.assertTrue(result.notice_only)
                self.assertIn("网页端", result.detail)

    def test_text_questions_and_detail_prompt(self) -> None:
        lone = plan([TEXT])
        self.assertEqual(lone.text_questions, (0,))
        self.assertTrue(lone.detail_is_prompt)
        self.assertEqual(lone.messages, ())
        mixed = plan([SINGLE, TEXT])
        self.assertEqual(mixed.text_questions, (1,))
        self.assertFalse(mixed.detail_is_prompt)
        self.assertIn("请用文字回答", mixed.messages[1])

    def test_each_card_names_only_its_own_question(self) -> None:
        result = plan([SINGLE, MULTI, {**SINGLE, "id": "b"}])
        button, vote, second = (spec.card for spec in result.cards)
        self.assertEqual(button["card_type"], "button_interaction")
        self.assertEqual([item["key"] for item in button["button_list"]], ["q0:0", "q0:1", "q0:o"])
        self.assertEqual(button["button_list"][-1]["text"], cards.OTHER_TEXT)
        self.assertEqual(vote["card_type"], "vote_interaction")
        self.assertEqual(vote["checkbox"]["mode"], 1)
        self.assertEqual(vote["checkbox"]["option_list"][-1], {"id": cards.OTHER_ID, "text": cards.OTHER_TEXT})
        self.assertIn("可多选", vote["sub_title_text"])
        for number, card in enumerate((button, vote, second), start=1):
            with self.subTest(number=number):
                # Title and subtitle both end with the one marker, whichever a quote carries.
                self.assertEqual(cards.find_markers(card["main_title"]["title"]), [("ABCD", number - 1)])
                self.assertEqual(cards.find_markers(card["sub_title_text"]), [("ABCD", number - 1)])
                self.assertTrue(card["main_title"]["title"].startswith(f"({number}/3) "))
                self.assertLessEqual(len(card["main_title"]["title"]), cards.TITLE_LIMIT)
                self.assertLessEqual(len(card["sub_title_text"]), cards.SUBTITLE_TEXT_LIMIT)
                self.assertEqual(card["source"], {"desc": "HOST-A", "desc_color": 1})
                self.assertEqual(card["main_title"]["desc"], "demo · lane · build")
        self.assertEqual(plan([SINGLE]).cards[0].card["main_title"]["title"], "数据库 #ABCD-1")
        long = {"header": "很长的标题" * 6, "question": "很长的题干" * 40, "options": [{"label": "x"}]}
        clipped = plan([long, SINGLE]).cards[0].card
        self.assertTrue(clipped["main_title"]["title"].endswith("… #ABCD-1"))
        self.assertTrue(clipped["sub_title_text"].endswith("… #ABCD-1"))
        for spec in (*result.cards, *plan([SINGLE]).cards):
            self.assertLessEqual(len(spec.task_id), 128)
            self.assertRegex(spec.task_id, r"^[0-9A-Za-z_\-@]+$")

    def test_detail_names_gate_and_keeps_full_text(self) -> None:
        long_label = "很长的选项" * 5
        result = plan([{**SINGLE, "options": [{"label": long_label, "description": "说明"}, {"label": "b"}]}])
        self.assertIn("#ABCD", result.detail.splitlines()[0])
        self.assertIn(f"A. {long_label}：说明", result.detail)
        self.assertIn("引用", result.detail)
        buttons = [item["text"] for item in result.cards[0].card["button_list"]]
        self.assertTrue(buttons[0].startswith("A. ") and buttons[0].endswith("…"))
        self.assertEqual(buttons[1], "B. b")  # lettered like the detail once any is clipped
        self.assertLessEqual(len(buttons[0]), cards.BUTTON_TEXT_LIMIT)
        short = plan([SINGLE]).cards[0].card["button_list"]
        self.assertEqual([item["text"] for item in short[:2]], ["Postgres", "SQLite"])

    def test_agent_text_cannot_forge_markers(self) -> None:
        forged = {"header": "#ZZZZ-1", "question": "引用 #ZZZZ-2 回答", "options": None}
        result = plan([forged])
        markers = cards.find_markers(result.detail)
        self.assertEqual(markers, [("ABCD", None)])
        prompt = cards.text_prompt_markdown(notice([SINGLE, forged]), ref="ABCD", index=1)
        self.assertEqual(cards.find_markers(prompt), [("ABCD", 1)])
        card = plan([{**forged, "options": [{"label": "#ZZZZ-3"}, {"label": "y"}]}]).cards[0].card
        self.assertEqual(cards.find_markers(str(card)), [("ABCD", 0)])


class AnswerTests(unittest.TestCase):
    def test_interpret_placeholder_other_and_labels(self) -> None:
        self.assertEqual(cards.interpret(SINGLE, ["_"]), ("none", []))
        self.assertEqual(cards.interpret(SINGLE, []), ("none", []))
        self.assertEqual(cards.interpret(SINGLE, ["o"]), ("other", []))
        self.assertEqual(cards.interpret(SINGLE, ["1"]), ("answer", ["SQLite"]))
        self.assertEqual(cards.interpret(MULTI, ["2", "0"]), ("answer", ["A", "C"]))
        self.assertEqual(cards.interpret(MULTI, ["0", "o"]), ("other", []))
        self.assertEqual(cards.interpret(SINGLE, ["9"]), ("none", []))

    def test_card_ids_for_buttons_and_checkboxes(self) -> None:
        button = plan([SINGLE]).cards[0]
        self.assertEqual(cards.card_ids(button, "q0:1", {}), ["1"])
        self.assertEqual(cards.card_ids(button, "q5:1", {}), [])
        vote = plan([SINGLE, MULTI]).cards[1]
        self.assertEqual(cards.card_ids(vote, "submit", {"q1": ["0", "2"], "q0": ["1"]}), ["0", "2"])
        self.assertEqual(cards.card_ids(vote, "submit", {}), [])

    def test_answer_mapping_uses_web_dialog_keys(self) -> None:
        mapping = cards.answer_mapping([SINGLE, MULTI, {"question": "", "options": None}], {0: ["SQLite"], 1: ["A"], 2: ["文本"]})
        self.assertEqual(
            mapping,
            {"db": {"answers": ["SQLite"]}, "多选": {"answers": ["A"]}, "q2": {"answers": ["文本"]}},
        )

    def test_find_markers(self) -> None:
        self.assertEqual(
            cards.find_markers("x #ABCD y #ABCD-2 #ABCD-2 #ABCDE #WXYZ-0 #WXYZ-12"),
            [("ABCD", None), ("ABCD", 1), ("WXYZ", 11)],
        )
        self.assertEqual(cards.find_markers(""), [])
        self.assertEqual(cards.find_markers("#TODO #ABCD"), [("ABCD", None)])  # O is not in the alphabet

    def test_after_click_and_closed_cards(self) -> None:
        spec = plan([MULTI]).cards[0]
        pending = cards.card_after_click(spec, [], done=False, status="还没有选择")
        self.assertNotIn("disable", pending["checkbox"])
        self.assertEqual(pending["submit_button"]["text"], "提交")
        done = cards.card_after_click(spec, ["0", "2"], done=True, status="已回答")
        self.assertTrue(done["checkbox"]["disable"])
        self.assertEqual(
            [option["is_checked"] for option in done["checkbox"]["option_list"]], [True, False, True, False]
        )
        self.assertEqual(done["submit_button"]["text"], "已提交")
        closed = cards.closed_card(spec.card, kind=spec.kind, status="已失效", answered=False)
        self.assertEqual(closed["submit_button"]["text"], "已失效")
        self.assertTrue(closed["checkbox"]["disable"])
        self.assertEqual(closed["task_id"], spec.task_id)
        button = plan([SINGLE]).cards[0]
        other = cards.card_after_click(button, ["o"], done=True, status="已选「其他」")
        self.assertEqual(other["button_list"][-1]["text"], cards._clip("✔ " + cards.OTHER_TEXT, cards.BUTTON_TEXT_LIMIT))
        self.assertEqual(other["main_title"]["title"], button.card["main_title"]["title"])
        closed_button = cards.closed_card(button.card, kind="button", status="已失效", answered=False)
        self.assertEqual([item["text"] for item in closed_button["button_list"]], ["已失效"])

    def test_expired_card_per_type(self) -> None:
        for card_type in ("multiple_interaction", "vote_interaction", "button_interaction"):
            with self.subTest(card_type):
                card = cards.expired_card(
                    card_type=card_type, question_keys=["q0"], host_label="H", color=0, task_id="t1"
                )
                assert card is not None
                self.assertEqual((card["card_type"], card["task_id"]), (card_type, "t1"))
        self.assertIsNone(
            cards.expired_card(card_type="text_notice", question_keys=[], host_label="H", color=0, task_id="t")
        )


class StorageTests(unittest.TestCase):
    def test_config_is_private_and_tolerates_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_wecom_config(root, WecomConfig(bot_id="aib", secret="s"))
            self.assertEqual(wecom_config_path(root).stat().st_mode & 0o777, 0o600)
            self.assertTrue(load_wecom_config(root).has_credentials)
            wecom_config_path(root).write_text("{not json", encoding="utf-8")
            self.assertFalse(load_wecom_config(root).has_credentials)

    def test_channel_choice_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(load_channel(root))
            save_channel(root, "wecom")
            self.assertEqual(load_channel(root), "wecom")

    def test_wecom_files_never_sync(self) -> None:
        for name in ("wecom.local.json", "remote_answers.local.json"):
            self.assertIn(name, EXCLUDED_ROOTS)
            self.assertIn(f"/{name}", IGNORED_PATTERNS)


if __name__ == "__main__":
    unittest.main()
