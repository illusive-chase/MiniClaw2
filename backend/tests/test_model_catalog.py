from __future__ import annotations

import unittest

from miniclaw2.model_catalog import (
    default_model_preset_id,
    get_model_preset,
    list_model_presets,
    normalize_active_model_preset_id,
)


class ModelCatalogTest(unittest.TestCase):
    def test_active_and_compatibility_presets_are_listed(self) -> None:
        presets = {preset.id: preset for preset in list_model_presets()}

        self.assertEqual(
            {
                preset_id
                for preset_id, preset in presets.items()
                if preset.status == "active"
            },
            {
                "opus-4-8",
                "opus-4-7",
                "gpt-5.6",
                "gpt-5.6-x",
                "gpt-5.6-u",
            },
        )
        self.assertEqual(
            {
                preset_id
                for preset_id, preset in presets.items()
                if preset.status == "compatibility"
            },
            {"gpt-5.5"},
        )
        self.assertEqual(default_model_preset_id(), "gpt-5.6")

    def test_active_preset_settings(self) -> None:
        opus = get_model_preset("opus-4-8")
        self.assertEqual(opus.model, "claude-opus-4-8[1m]")
        self.assertEqual(opus.reasoning_effort, "xhigh")

        opus_4_7 = get_model_preset("opus-4-7")
        self.assertEqual(opus_4_7.model, "claude-opus-4-7[1m]")
        self.assertEqual(opus_4_7.reasoning_effort, "xhigh")
        self.assertEqual(
            normalize_active_model_preset_id("opus-4-7"),
            "opus-4-7",
        )

        expected = {
            "gpt-5.6": ("gpt-5.6-sol", "high"),
            "gpt-5.6-x": ("gpt-5.6-sol", "xhigh"),
            "gpt-5.6-u": ("gpt-5.6-sol", "ultra"),
        }
        for preset_id, (model, effort) in expected.items():
            with self.subTest(preset_id=preset_id):
                preset = get_model_preset(preset_id)
                self.assertEqual(preset.model, model)
                self.assertEqual(preset.reasoning_effort, effort)

    def test_codex_presets_inherit_cli_model_provider(self) -> None:
        for preset in list_model_presets():
            if preset.provider == "codex":
                with self.subTest(preset_id=preset.id):
                    self.assertIsNone(preset.model_provider)

    def test_compatibility_presets_resolve_but_cannot_be_selected(self) -> None:
        self.assertEqual(get_model_preset("gpt-5.5").status, "compatibility")
        with self.assertRaisesRegex(ValueError, "compatibility-only"):
            normalize_active_model_preset_id("gpt-5.5")


if __name__ == "__main__":
    unittest.main()
