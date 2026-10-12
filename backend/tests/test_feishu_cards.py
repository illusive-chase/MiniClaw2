"""Pure Feishu card builders, answer normalization and host-local storage."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from miniclaw2 import feishu_cards as cards
from miniclaw2.feishu_config import (
    LEDGER_RETENTION_SECONDS,
    FeishuConfig,
    feishu_config_path,
    load_feishu_config,
    load_usage,
    purge_relay_ledger,
    record_usage,
    save_feishu_config,
)
from miniclaw2.gate_observer import GateNotice
from miniclaw2.migrations.inventory import EXCLUDED_ROOTS, IGNORED_PATTERNS

QUESTIONS = [
    {"id": "db", "header": "数据库", "question": "用哪个？", "options": [{"label": "Postgres"}, {"label": "SQLite"}]},
    {"question": "多选", "multiSelect": True, "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]},
    {"header": "备注", "question": "", "options": None},
]


def notice(questions: list[dict] | None = None) -> GateNotice:
    return GateNotice(
        gate_id="g1",
        project_id="p1",
        node_id="n1",
        subtype="ask_user",
        tool_name="AskUserQuestion",
        questions=QUESTIONS if questions is None else questions,
        project_name="demo",
        lane_title="lane",
        node_label="x" * 200,
        created_at=1000.0,
    )


class NormalizeAnswersTests(unittest.TestCase):
    """Mirrors ``AskUserDialog.submit`` / ``allAnswered``."""

    def test_selection_text_override_and_keys(self) -> None:
        answers, missing = cards.normalize_answers(
            QUESTIONS,
            {"q0": "1", "q1_2": True, "q1_0": True, "q1_1": False, "o1": "", "o2": " 自由文本 "},
        )
        self.assertIsNone(missing)
        self.assertEqual(
            answers,
            {
                "db": {"answers": ["SQLite"]},
                "多选": {"answers": ["A", "C"]},  # option order, not click order
                "q2": {"answers": ["自由文本"]},  # id || question || q{index}
            },
        )

    def test_free_text_overrides_selection(self) -> None:
        answers, _ = cards.normalize_answers(
            QUESTIONS, {"q0": "0", "o0": "MySQL", "q1_1": True, "o2": "x"}
        )
        self.assertEqual(answers["db"], {"answers": ["MySQL"]})

    def test_missing_answer_reports_one_based_index(self) -> None:
        self.assertEqual(cards.normalize_answers(QUESTIONS, {"q0": "0", "o2": "x"}), (None, 2))
        self.assertEqual(cards.normalize_answers(QUESTIONS, {"q0": "9", "q1_0": True, "o2": "x"}), (None, 1))

    def test_non_owner_completeness_uses_field_names_only(self) -> None:
        self.assertIsNone(cards.first_unanswered({"q0": "1", "q1_0": True, "o2": "x"}, 3))
        self.assertEqual(cards.first_unanswered({"q0": "1", "q1_0": False, "o2": "x"}, 3), 2)
        self.assertEqual(cards.first_unanswered({"q0": "1", "q1_0": "true"}, 3), 3)
        self.assertIsNone(cards.first_unanswered({}, "bogus"))


class CardTests(unittest.TestCase):
    def build(self, questions: list[dict] | None = None) -> dict:
        return cards.ask_card(
            notice(questions), host_short="abc123def456", host_label="MBP", color="blue", nonce="n0nce", now=1000.0 + 180
        )

    def test_ask_card_is_one_form_with_routing_value(self) -> None:
        card = self.build()
        self.assertTrue(card["config"]["update_multi"])
        self.assertEqual(card["header"]["text_tag_list"][0]["text"]["content"], "MBP")
        self.assertLessEqual(len(card["header"]["subtitle"]["content"]), cards.SUBTITLE_LIMIT)
        forms = [element for element in cards._walk(card) if element.get("tag") == "form"]
        self.assertEqual(len(forms), 1)
        names = [element.get("name") for element in cards._walk(forms[0]) if element.get("name")]
        self.assertEqual(names, ["ask", "q0", "o0", "q1_0", "q1_1", "q1_2", "o1", "o2", "submit"])
        button = next(element for element in cards._walk(card) if element.get("name") == "submit")
        self.assertEqual(
            button["behaviors"][0]["value"],
            {"v": 1, "a": "s", "h": "abc123def456", "hl": "MBP", "g": "g1", "n": "n0nce", "k": 3},
        )
        self.assertIn("已等待 3 分钟", json.dumps(card, ensure_ascii=False))
        self.assertTrue(card["config"]["summary"]["content"].startswith("[MBP] demo"))

    def test_secret_or_empty_or_oversized_questions_become_notice_cards(self) -> None:
        secret = self.build([{"question": "token", "isSecret": True}])
        empty = self.build([])
        huge = self.build([
            {"question": f"q{i}", "options": [{"label": f"选项{i}-{k}" * 5, "description": "说明" * 90} for k in range(12)]}
            for i in range(12)
        ])
        for card, phrase in ((secret, "敏感输入"), (empty, "没有可显示的问题"), (huge, "过长")):
            text = json.dumps(card, ensure_ascii=False)
            self.assertIn(phrase, text)
            self.assertFalse(any(element.get("tag") in {"form", "button"} for element in cards._walk(card)))

    def test_markup_in_questions_is_neutralized(self) -> None:
        card = self.build([{"question": "<at id=all></at> [link](http://x)", "options": None}])
        text = json.dumps(card, ensure_ascii=False)
        self.assertNotIn("<at", text)

    def test_receipt_round_trip_by_element_id(self) -> None:
        value = {"v": 1, "a": "s", "h": "abc123def456", "hl": "MBP", "g": "g1", "n": "nonce", "k": 1}
        form = {"o0": "含\"引号\"、中文和 **markdown**\n换行", "q0": "1"}
        card = cards.receipt_card(
            value=value, form_value=form, operator_open_id="ou_x", via_short="fff000fff000", via_label="Mini", at=12.5
        )
        self.assertFalse(any(element.get("tag") in {"form", "button"} for element in cards._walk(card)))
        decoded = cards.find_receipt(json.loads(json.dumps(card)))
        self.assertEqual(decoded, {"v": 1, "via": "fff000fff000", "vl": "Mini", "at": 12.5, "value": value, "fv": form, "op": "ou_x"})
        # A body containing the prefix outside the receipt element is ignored.
        fake = cards.final_card(title="t", template="grey", subtitle="", host_label="h", color="blue", lines=[cards.RECEIPT_PREFIX + "eyJ9"])
        self.assertIsNone(cards.find_receipt(fake))
        self.assertIsNone(cards.decode_receipt("MC2RX1.!!!"))

    def test_host_identity_is_stable(self) -> None:
        self.assertEqual(cards.host_short_id("8BFAB724-94A2-4C1D-9E0F-112233445566"), "8bfab72494a2")
        self.assertEqual(cards.host_color("8bfab72494a2"), cards.host_color("8bfab72494a2"))
        self.assertIn(cards.host_color("x"), cards.HOST_COLORS)

    def test_answer_lines(self) -> None:
        lines = cards.answer_lines(QUESTIONS, {"db": {"answers": ["SQLite"]}, "多选": {"answers": ["A", "C"]}})
        self.assertEqual(lines, ["- **数据库**：SQLite", "- **第 2 题**：A、C"])


class StorageTests(unittest.TestCase):
    def test_config_is_private_and_tolerates_garbage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(load_feishu_config(root).has_credentials)
            save_feishu_config(root, FeishuConfig(app_id="cli_x", app_secret="s"))
            self.assertEqual(feishu_config_path(root).stat().st_mode & 0o777, 0o600)
            self.assertTrue(load_feishu_config(root).has_credentials)
            feishu_config_path(root).write_text("{not json", encoding="utf-8")
            self.assertFalse(load_feishu_config(root).has_credentials)

    def test_usage_counts_per_month_and_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_usage(root, "send")
            record_usage(root, "mget", 2)
            usage = load_usage(root)
            self.assertEqual(usage["calls"], 3)
            self.assertEqual(usage["by_kind"], {"send": 1, "mget": 2})

    def test_ledger_purge_keeps_live_and_recent_records(self) -> None:
        now = 10 * LEDGER_RETENTION_SECONDS
        ledger = {
            "live": {"state": "sent", "sent_at": 0},
            "old": {"state": "settled", "closed_at": now - LEDGER_RETENTION_SECONDS - 1},
            "recent": {"state": "stale", "closed_at": now - 10},
        }
        self.assertEqual(set(purge_relay_ledger(ledger, now=now)), {"live", "recent"})

    def test_feishu_files_never_sync(self) -> None:
        self.assertIn("feishu.local.json", EXCLUDED_ROOTS)
        self.assertIn("feishu", EXCLUDED_ROOTS)
        self.assertIn("/feishu.local.json", IGNORED_PATTERNS)
        self.assertIn("/feishu/", IGNORED_PATTERNS)


if __name__ == "__main__":
    unittest.main()
