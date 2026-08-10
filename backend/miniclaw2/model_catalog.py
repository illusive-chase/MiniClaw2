"""Model preset resolution backed entirely by the global configuration."""

from __future__ import annotations

from pathlib import Path

from .global_config import ModelPreset, load_global_config


def default_model_preset_id(*, store_root: Path | None = None) -> str:
    return load_global_config(store_root).defaults.default_model_preset_id


def default_code_review_model_preset_id(*, store_root: Path | None = None) -> str:
    return load_global_config(store_root).code_review.model_preset_id


def list_model_presets(*, store_root: Path | None = None) -> list[ModelPreset]:
    config = load_global_config(store_root)
    default_id = config.defaults.default_model_preset_id
    return [
        preset.model_copy(update={"is_default": preset.id == default_id})
        for preset in config.model_presets
    ]


def get_model_preset(
    model_preset_id: str | None,
    *,
    store_root: Path | None = None,
) -> ModelPreset:
    normalized = normalize_model_preset_id(model_preset_id, store_root=store_root)
    return next(
        preset
        for preset in list_model_presets(store_root=store_root)
        if preset.id == normalized
    )


def normalize_model_preset_id(
    model_preset_id: str | None,
    *,
    store_root: Path | None = None,
) -> str:
    normalized = (model_preset_id or "").strip()
    if not normalized:
        raise ValueError("model_preset_id is required")
    preset_ids = {
        preset.id for preset in list_model_presets(store_root=store_root)
    }
    if normalized not in preset_ids:
        raise ValueError(f"unknown model_preset_id: {model_preset_id!r}")
    return normalized


def normalize_active_model_preset_id(
    model_preset_id: str | None,
    *,
    store_root: Path | None = None,
) -> str:
    normalized = normalize_model_preset_id(
        model_preset_id,
        store_root=store_root,
    )
    preset = get_model_preset(normalized, store_root=store_root)
    if preset.status != "active":
        raise ValueError(
            f"model_preset_id {normalized!r} is compatibility-only and cannot "
            "be selected for new or edited work"
        )
    return normalized


def provider_for_model_preset(
    model_preset_id: str | None,
    *,
    store_root: Path | None = None,
) -> str:
    return get_model_preset(model_preset_id, store_root=store_root).provider
