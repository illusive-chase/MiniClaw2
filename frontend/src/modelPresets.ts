import type { ModelPreset } from "./types";

export function defaultModelPresetId(
  presets: ModelPreset[],
  fallback?: string | null,
): string {
  if (fallback && presets.some((preset) => preset.id === fallback)) return fallback;
  return presets.find((preset) => preset.is_default)?.id ?? presets[0]?.id ?? "";
}

export function modelPresetLabel(
  presets: ModelPreset[],
  modelPresetId?: string | null,
): string {
  if (!modelPresetId) return "Model preset";
  return presets.find((preset) => preset.id === modelPresetId)?.label ?? modelPresetId;
}

export function modelPresetDetail(
  presets: ModelPreset[],
  modelPresetId?: string | null,
): string {
  const preset = presets.find((item) => item.id === modelPresetId);
  if (!preset) return "";
  const parts = [preset.provider, preset.model];
  if (preset.reasoning_effort) parts.push(`effort ${preset.reasoning_effort}`);
  return parts.join(" · ");
}

export function providerLabel(provider?: string | null): string {
  if (provider === "codex") return "Codex";
  if (provider === "claude") return "Claude";
  return provider || "-";
}
