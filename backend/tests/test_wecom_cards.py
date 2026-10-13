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


def shape(result: cards.PushPlan) -> list[tuple[str, tuple[int, ...]]]:
    return [(spec.kind, spec.questions) for spec in result.cards]


class PlanTests(unittest.TestCase):
    def test_split_by_question_shape(self) -> None:
        single = lambda i: {"question": f"Q{i}", "options": [{"label": "x"}, {"label": "y"}]}  # noqa: E731
        cases = {
            "one single-select": ([SINGLE], [("button", (0,))]),
            "two single-selects": ([single(0), single(1)], [("select", (0, 1))]),
            "three single-selects": ([single(0), single(1), single(2)], [("select", (0, 1, 2))]),
            "four single-selects": (
                [single(i) for i in range(4)],
                [("select", (0, 1, 2)), ("select", (3,))],
            ),
            "multi-select": ([MULTI], [("vote", (0,))]),
            "text only": ([TEXT], []),
            "mixed": ([SINGLE, MULTI, TEXT, single(3)], [("select", (0, 3)), ("vote", (1,))]),
        }
        for name, (questions, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(shape(plan(questions)), expected)

    def test_long_option_lists_move_to_checkbox_or_web(self) -> None:
        many = {"question": "多", "options": [{"label": f"o{i}"} for i in range(9)]}
        self.assertEqual(shape(plan([many, SINGLE])), [("vote", (0,)), ("select", (1,))])
        self.assertEqual(shape(plan([many])), [("vote", (0,))])
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
        mixed = plan([SINGLE, TEXT])
        self.assertEqual(mixed.text_questions, (1,))
        self.assertFalse(mixed.detail_is_prompt)

    def test_cards_carry_placeholder_other_and_markers(self) -> None:
        result = plan([SINGLE, MULTI, {**SINGLE, "id": "b"}])
        select = result.cards[0].card
        self.assertEqual(select["card_type"], "multiple_interaction")
        first = select["select_list"][0]["option_list"]
        self.assertEqual(first[0]["id"], cards.PLACEHOLDER_ID)
        self.assertEqual(first[-1], {"id": cards.OTHER_ID, "text": cards.OTHER_TEXT})
        self.assertTrue(select["sub_title_text"].endswith("#ABCD-1 #ABCD-3"))
        vote = result.cards[1].card
        self.assertEqual(vote["checkbox"]["mode"], 1)
        self.assertEqual(vote["checkbox"]["option_list"][-1]["id"], cards.OTHER_ID)
        self.assertEqual(select["source"], {"desc": "HOST-A", "desc_color": 1})
        self.assertEqual(select["main_title"]["desc"], "demo · lane · build")
        self.assertTrue(select["main_title"]["title"].startswith("(1/2) "))
        button = plan([SINGLE]).cards[0].card
        self.assertEqual([item["key"] for item in button["button_list"]], ["q0:0", "q0:1", "q0:o"])
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


class AnswerTests(unittest.TestCase):
    def test_interpret_placeholder_other_and_labels(self) -> None:
        self.assertEqual(cards.interpret(SINGLE, ["_"]), ("none", []))
        self.assertEqual(cards.interpret(SINGLE, []), ("none", []))
        self.assertEqual(cards.interpret(SINGLE, ["o"]), ("other", []))
        self.assertEqual(cards.interpret(SINGLE, ["1"]), ("answer", ["SQLite"]))
        self.assertEqual(cards.interpret(MULTI, ["2", "0"]), ("answer", ["A", "C"]))
        self.assertEqual(cards.interpret(MULTI, ["0", "o"]), ("other", []))
        self.assertEqual(cards.interpret(SINGLE, ["9"]), ("none", []))

    def test_card_ids_for_buttons_and_selections(self) -> None:
        button = plan([SINGLE]).cards[0]
        self.assertEqual(cards.card_ids(button, "q0:1", {}), {0: ["1"]})
        self.assertEqual(cards.card_ids(button, "q5:1", {}), {})
        select = plan([SINGLE, {**SINGLE, "id": "b"}]).cards[0]
        self.assertEqual(
            cards.card_ids(select, "submit", {"q0": ["1"], "q1": ["_"]}), {0: ["1"], 1: ["_"]}
        )

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
        spec = plan([SINGLE, {**SINGLE, "id": "b"}]).cards[0]
        pending = cards.card_after_click(spec, {0: ["1"], 1: ["_"]}, done=False, status="第 2 题还没选")
        self.assertEqual([item["selected_id"] for item in pending["select_list"]], ["1", "_"])
        self.assertNotIn("disable", pending["select_list"][0])
        done = cards.card_after_click(spec, {0: ["1"], 1: ["0"]}, done=True, status="已回答")
        self.assertTrue(all(item["disable"] for item in done["select_list"]))
        self.assertEqual(done["submit_button"]["text"], "已提交")
        closed = cards.closed_card(spec.card, kind=spec.kind, status="已失效", answered=False)
        self.assertEqual(closed["submit_button"]["text"], "已失效")
        self.assertEqual(closed["task_id"], spec.task_id)
        button = plan([SINGLE]).cards[0]
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
