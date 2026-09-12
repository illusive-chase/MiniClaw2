import { useCallback, useEffect, useState } from "react";

import type { ThemePref } from "../theme";
import { THEME_STORAGE_KEY, applyTheme, readStoredTheme } from "../theme";

export function ThemeToggle() {
  const [pref, setPref] = useState<ThemePref>(() => readStoredTheme());

  // Apply on mount + whenever pref changes.
  useEffect(() => {
    applyTheme(pref);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, pref);
    } catch {
      /* ignore */
    }
  }, [pref]);

  // Subscribe to system changes only when pref === "system".
  useEffect(() => {
    if (pref !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [pref]);

  const cycle = useCallback(() => {
    setPref((current) =>
      current === "light" ? "dark" : current === "dark" ? "system" : "light",
    );
  }, []);

  const tooltip =
    pref === "light"
      ? "主题：亮色 · 点击切换到暗色"
      : pref === "dark"
        ? "主题：暗色 · 点击切换到跟随系统"
        : "主题：跟随系统 · 点击切换到亮色";

  return (
    <button
      type="button"
      onClick={cycle}
      title={tooltip}
      aria-label={tooltip}
      className="group inline-flex h-8 w-8 items-center justify-center rounded-md border border-line text-ink-muted transition hover:border-line-strong hover:bg-surface-raised hover:text-ink"
    >
      <span
        key={pref}
        className="inline-flex h-4 w-4 items-center justify-center transition-transform duration-300 ease-out"
        style={{ transform: pref === "system" ? "rotate(0deg)" : pref === "dark" ? "rotate(-30deg)" : "rotate(20deg)" }}
      >
        {pref === "light" ? <SunIcon /> : pref === "dark" ? <MoonIcon /> : <AutoThemeIcon />}
      </span>
    </button>
  );
}

function SunIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1.5v1.5M8 13v1.5M1.5 8h1.5M13 8h1.5M3.4 3.4l1.05 1.05M11.55 11.55l1.05 1.05M3.4 12.6l1.05-1.05M11.55 4.45l1.05-1.05" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M6.1 2.5a5.7 5.7 0 0 0 7.4 7.4 6.2 6.2 0 1 1-7.4-7.4Z" />
    </svg>
  );
}

/* "Follow the system" drawn as light-vs-dark rather than as a device: the
 * button's subject is the theme, so all three faces have to stay inside the
 * sun/moon vocabulary. A monitor made the third state read as a hardware
 * setting instead of the midpoint between the other two.
 *
 * The dark half is hatched rather than filled, so the glyph is pure stroke
 * like the rest of the top bar. Two hatches, not three: below ~3 units of
 * pitch they merge into a solid wedge at 16px and the icon is back to being
 * a filled half-circle. */
function AutoThemeIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="8" cy="8" r="5.6" />
      <path d="M8 2.4V13.6" />
      <path d="M9.07 7.38 11.13 5.32M9.07 10.68 11.13 8.62" />
    </svg>
  );
}
