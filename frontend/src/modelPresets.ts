import type { ModelPreset } from "./types";

export function selectableModelPresets(presets: ModelPreset[]): ModelPreset[] {
  return presets.filter((preset) => preset.status === "active");
}

export function defaultModelPresetId(
  presets: ModelPreset[],
  fallback?: string | null,
): string {
  const selectable = selectableModelPresets(presets);
  if (fallback && selectable.some((preset) => preset.id === fallback)) return fallback;
  return selectable.find((preset) => preset.is_default)?.id ?? selectable[0]?.id ?? "";
}

export function modelPresetLabel(
  presets: ModelPreset[],
  modelPresetId?: string | null,
): string {
  if (!modelPresetId) return "Model preset";
  const preset = presets.find((item) => item.id === modelPresetId);
  if (!preset) return modelPresetId;
  return preset.status === "compatibility" ? `${preset.label}（兼容）` : preset.label;
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
