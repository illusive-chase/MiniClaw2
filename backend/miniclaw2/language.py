"""Project language preference helpers."""

from __future__ import annotations

from typing import Any

_CLEAR_VALUES = {"", "auto", "default", "none", "null", "system"}

_ALIASES = {
    "en": "English",
    "english": "English",
    "zh": "Simplified Chinese",
    "zh cn": "Simplified Chinese",
    "zh-cn": "Simplified Chinese",
    "chinese": "Simplified Chinese",
    "simplified chinese": "Simplified Chinese",
    "mandarin": "Simplified Chinese",
    "中文": "Simplified Chinese",
    "简体中文": "Simplified Chinese",
    "zh tw": "Traditional Chinese",
    "zh-tw": "Traditional Chinese",
    "traditional chinese": "Traditional Chinese",
    "繁體中文": "Traditional Chinese",
    "ja": "Japanese",
    "jp": "Japanese",
    "japanese": "Japanese",
    "日本語": "Japanese",
    "ko": "Korean",
    "korean": "Korean",
    "한국어": "Korean",
    "es": "Spanish",
    "spanish": "Spanish",
    "fr": "French",
    "french": "French",
    "de": "German",
    "german": "German",
    "pt": "Portuguese",
    "portuguese": "Portuguese",
    "it": "Italian",
    "italian": "Italian",
    "ru": "Russian",
    "russian": "Russian",
    "ar": "Arabic",
    "arabic": "Arabic",
    "hi": "Hindi",
    "hindi": "Hindi",
    "vi": "Vietnamese",
    "vietnamese": "Vietnamese",
    "th": "Thai",
    "thai": "Thai",
    "id": "Indonesian",
    "indonesian": "Indonesian",
}


def normalize_preferred_language(value: Any) -> str | None:
    """Normalize a user-selected language label.

    Only known aliases are accepted because the normalized label is embedded
    into model instructions.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("preferred_language must be a string or null")
    text = " ".join(value.strip().split())
    if text.lower() in _CLEAR_VALUES:
        return None

    key = " ".join(text.lower().replace("_", " ").split())
    alias = _ALIASES.get(key) or _ALIASES.get(text.lower())
    if alias:
        return alias
    raise ValueError("preferred_language is not a supported language label")


def project_preferred_language(project: Any) -> str | None:
    """Return a normalized language preference from a Project-like object."""

    raw = getattr(project, "preferred_language", None)
    if raw is None:
        settings = getattr(project, "settings_override", {}) or {}
        if isinstance(settings, dict):
            raw = settings.get("preferred_language") or settings.get("language")
    return normalize_preferred_language(raw)


def language_launch_instruction(preferred_language: str | None) -> str:
    """Return per-turn instructions for user-visible agent-authored text."""

    language = normalize_preferred_language(preferred_language)
    if not language:
        return ""
    return (
        "# Language preference\n\n"
        f"The user selected `{language}` as their preferred language. Use "
        f"`{language}` for every natural-language string you write that "
        "MiniClaw2 may show to the user, including assistant replies, "
        "ask-user questions and options, plan-approval text, review handoffs, "
        "summaries, and planspace STATUS/PLAN text fields. Do not translate "
        "code, commands, file paths, identifiers, JSON keys, protocol values, "
        "logs, exact quotes, or externally sourced text that must remain exact."
    )
