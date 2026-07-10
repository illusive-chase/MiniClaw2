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
            {preset_id for preset_id, preset in presets.items() if preset.status == "active"},
            {"opus-4-8", "gpt-5.6", "gpt-5.6-x", "gpt-5.6-u"},
        )
        self.assertEqual(
            {
                preset_id
                for preset_id, preset in presets.items()
                if preset.status == "compatibility"
            },
            {"gpt-5.5", "opus-4-7"},
        )
        self.assertEqual(default_model_preset_id(), "gpt-5.6")

    def test_active_preset_settings(self) -> None:
        opus = get_model_preset("opus-4-8")
        self.assertEqual(opus.model, "claude-opus-4-8[1m]")
        self.assertEqual(opus.reasoning_effort, "xhigh")

        expected = {
            "gpt-5.6": ("gpt-5.6-sol", "high"),
            "gpt-5.6-x": ("gpt-5.6", "xhigh"),
            "gpt-5.6-u": ("gpt-5.6", "ultra"),
        }
        for preset_id, (model, effort) in expected.items():
            with self.subTest(preset_id=preset_id):
                preset = get_model_preset(preset_id)
                self.assertEqual(preset.model, model)
                self.assertEqual(preset.reasoning_effort, effort)

    def test_compatibility_presets_resolve_but_cannot_be_selected(self) -> None:
        self.assertEqual(get_model_preset("gpt-5.5").status, "compatibility")
        with self.assertRaisesRegex(ValueError, "compatibility-only"):
            normalize_active_model_preset_id("gpt-5.5")


if __name__ == "__main__":
    unittest.main()
