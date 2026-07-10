"""Central model preset catalog.

Runtime code selects a model preset id. The provider name, concrete model,
and provider-specific reasoning parameters are derived here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KNOWN_PROVIDERS: frozenset[str] = frozenset({"claude", "codex"})


@dataclass(frozen=True, slots=True)
class ModelPreset:
    id: str
    label: str
    provider: str
    model: str
    description: str = ""
    model_provider: str | None = None
    service_tier: str | None = None
    reasoning_effort: str | None = None
    is_default: bool = False
    status: str = "active"

    def metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
            "model_provider": self.model_provider,
            "service_tier": self.service_tier,
            "reasoning_effort": self.reasoning_effort,
            "description": self.description,
            "is_default": self.is_default,
            "status": self.status,
        }

    def settings_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "model_preset_id": self.id,
            "provider": self.provider,
            "model": self.model,
        }
        if self.model_provider is not None:
            snapshot["model_provider"] = self.model_provider
        if self.service_tier is not None:
            snapshot["service_tier"] = self.service_tier
        if self.reasoning_effort is not None:
            snapshot["reasoning_effort"] = self.reasoning_effort
        return snapshot


MODEL_PRESETS: tuple[ModelPreset, ...] = (
    ModelPreset(
        id="opus-4-8",
        label="Claude Opus 4.8 (1M)",
        provider="claude",
        model="claude-opus-4-8[1m]",
        reasoning_effort="xhigh",
        description="Claude Opus 4.8 preset with a 1M context window and xhigh effort.",
    ),
    ModelPreset(
        id="gpt-5.6",
        label="GPT-5.6 SOL (High)",
        provider="codex",
        model="gpt-5.6-sol",
        model_provider="openai",
        reasoning_effort="high",
        description="Codex preset for GPT-5.6 SOL with high reasoning effort.",
        is_default=True,
    ),
    ModelPreset(
        id="gpt-5.6-x",
        label="GPT-5.6 (XHigh)",
        provider="codex",
        model="gpt-5.6-sol",
        model_provider="openai",
        reasoning_effort="xhigh",
        description="Codex preset for GPT-5.6 SOL with xhigh reasoning effort.",
    ),
    ModelPreset(
        id="gpt-5.6-u",
        label="GPT-5.6 (Ultra)",
        provider="codex",
        model="gpt-5.6-sol",
        model_provider="openai",
        reasoning_effort="ultra",
        description="Codex preset for GPT-5.6 SOL with ultra reasoning effort.",
    ),
    ModelPreset(
        id="gpt-5.5",
        label="GPT-5.5",
        provider="codex",
        model="gpt-5.5",
        model_provider="openai",
        reasoning_effort="medium",
        description="Compatibility preset for existing GPT-5.5 data.",
        status="compatibility",
    ),
    ModelPreset(
        id="opus-4-7",
        label="Claude Opus 4.7",
        provider="claude",
        model="opus-4-7",
        description="Compatibility preset for existing Claude Opus 4.7 data.",
        status="compatibility",
    ),
)

_PRESETS_BY_ID: dict[str, ModelPreset] = {preset.id: preset for preset in MODEL_PRESETS}
_DEFAULT_PRESET_ID = next(
    (preset.id for preset in MODEL_PRESETS if preset.is_default),
    MODEL_PRESETS[0].id,
)

# Migration-only defaults for legacy provider-only data. Runtime request paths
# must not use this map as a fallback.
LEGACY_PROVIDER_DEFAULT_PRESETS: dict[str, str] = {
    "codex": "gpt-5.5",
    "claude": "opus-4-7",
}


def default_model_preset_id() -> str:
    return _DEFAULT_PRESET_ID


def list_model_presets() -> list[ModelPreset]:
    return list(MODEL_PRESETS)


def get_model_preset(model_preset_id: str | None) -> ModelPreset:
    normalized = normalize_model_preset_id(model_preset_id)
    return _PRESETS_BY_ID[normalized]


def normalize_model_preset_id(model_preset_id: str | None) -> str:
    normalized = (model_preset_id or "").strip()
    if not normalized:
        raise ValueError("model_preset_id is required")
    if normalized not in _PRESETS_BY_ID:
        raise ValueError(f"unknown model_preset_id: {model_preset_id!r}")
    return normalized


def normalize_active_model_preset_id(model_preset_id: str | None) -> str:
    normalized = normalize_model_preset_id(model_preset_id)
    if _PRESETS_BY_ID[normalized].status != "active":
        raise ValueError(
            f"model_preset_id {normalized!r} is compatibility-only and cannot "
            "be selected for new or edited work"
        )
    return normalized


def provider_for_model_preset(model_preset_id: str | None) -> str:
    return get_model_preset(model_preset_id).provider


def legacy_provider_to_model_preset_id(provider: str | None) -> str:
    normalized = (provider or "").strip().lower()
    if normalized not in LEGACY_PROVIDER_DEFAULT_PRESETS:
        raise ValueError(f"unknown legacy provider: {provider!r}")
    return LEGACY_PROVIDER_DEFAULT_PRESETS[normalized]


def legacy_settings_to_model_preset_id(
    *,
    provider: str | None,
    model: str | None = None,
    model_provider: str | None = None,
    service_tier: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """Resolve a legacy provider/model tuple during migration only.

    Provider-only legacy rows map through ``LEGACY_PROVIDER_DEFAULT_PRESETS``.
    If legacy model fields are present, they must match exactly one preset.
    """

    normalized_provider = (provider or "").strip().lower()
    if normalized_provider not in KNOWN_PROVIDERS:
        raise ValueError(f"unknown legacy provider: {provider!r}")

    has_model_fields = any(
        isinstance(value, str) and value.strip()
        for value in (model, model_provider, service_tier, reasoning_effort)
    )
    if not has_model_fields:
        return legacy_provider_to_model_preset_id(normalized_provider)

    model_value = model.strip() if isinstance(model, str) and model.strip() else None
    model_provider_value = (
        model_provider.strip()
        if isinstance(model_provider, str) and model_provider.strip()
        else None
    )
    service_tier_value = (
        service_tier.strip()
        if isinstance(service_tier, str) and service_tier.strip()
        else None
    )
    reasoning_effort_value = (
        reasoning_effort.strip()
        if isinstance(reasoning_effort, str) and reasoning_effort.strip()
        else None
    )
    matches = [
        preset
        for preset in MODEL_PRESETS
        if preset.provider == normalized_provider
        and (model_value is None or preset.model == model_value)
        and (
            model_provider_value is None
            or preset.model_provider == model_provider_value
        )
        and (service_tier_value is None or preset.service_tier == service_tier_value)
        and (
            reasoning_effort_value is None
            or preset.reasoning_effort == reasoning_effort_value
        )
    ]
    if len(matches) != 1:
        combo = {
            "provider": normalized_provider,
            "model": model_value,
            "model_provider": model_provider_value,
            "service_tier": service_tier_value,
            "reasoning_effort": reasoning_effort_value,
        }
        raise ValueError(
            "cannot map legacy provider/model settings to a unique "
            f"model preset: {combo!r}"
        )
    return matches[0].id
