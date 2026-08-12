export const LANGUAGE_OPTIONS = [
  { value: "", label: "No preference" },
  { value: "English", label: "English" },
  { value: "Simplified Chinese", label: "Simplified Chinese" },
  { value: "Traditional Chinese", label: "Traditional Chinese" },
  { value: "Japanese", label: "Japanese" },
  { value: "Korean", label: "Korean" },
  { value: "Spanish", label: "Spanish" },
  { value: "French", label: "French" },
  { value: "German", label: "German" },
  { value: "Portuguese", label: "Portuguese" },
  { value: "Italian", label: "Italian" },
  { value: "Russian", label: "Russian" },
  { value: "Arabic", label: "Arabic" },
  { value: "Hindi", label: "Hindi" },
  { value: "Vietnamese", label: "Vietnamese" },
  { value: "Thai", label: "Thai" },
  { value: "Indonesian", label: "Indonesian" },
] as const;

export function languageLabel(language?: string | null): string {
  return language?.trim() || "No preference";
}

/** Google Translate target codes, keyed by the lowercased option value. */
const GOOGLE_TRANSLATE_CODES: Record<string, string> = {
  english: "en",
  "simplified chinese": "zh-CN",
  "traditional chinese": "zh-TW",
  japanese: "ja",
  korean: "ko",
  spanish: "es",
  french: "fr",
  german: "de",
  portuguese: "pt",
  italian: "it",
  russian: "ru",
  arabic: "ar",
  hindi: "hi",
  vietnamese: "vi",
  thai: "th",
  indonesian: "id",
};

/**
 * Resolve a project's preferred language to a Google Translate target code.
 * Projects without a preference translate to Simplified Chinese.
 */
export function googleTranslateCode(language?: string | null): string {
  const key = language?.trim().toLowerCase();
  if (!key) return "zh-CN";
  return GOOGLE_TRANSLATE_CODES[key] ?? "zh-CN";
}
