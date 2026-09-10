/* The stored light/dark preference, shared by the app and the reading page.
 *
 * Both surfaces are served from the same origin, so they read the same
 * localStorage key. Extracting it here is what lets a Markdown tab open in
 * the theme the user already chose without mounting `App` or duplicating the
 * media-query logic.
 */

export type ThemePref = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "miniclaw2:theme";

export function readStoredTheme(): ThemePref {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    if (value === "light" || value === "dark" || value === "system") return value;
  } catch {
    /* Site data blocked; light is the documented default. */
  }
  return "light";
}

export function systemPrefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    !!window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

export function applyTheme(pref: ThemePref): void {
  const dark = pref === "dark" || (pref === "system" && systemPrefersDark());
  document.documentElement.classList.toggle("dark", dark);
}

/** Apply whatever the user last chose. Used by surfaces that only read it. */
export function applyStoredTheme(): void {
  applyTheme(readStoredTheme());
}
