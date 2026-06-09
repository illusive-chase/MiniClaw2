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
  { value: "Vietnamese", label: "Vietnamese" },
  { value: "Thai", label: "Thai" },
] as const;

export function languageLabel(language?: string | null): string {
  return language?.trim() || "No preference";
}
