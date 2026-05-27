import { useCallback, useEffect, useState } from "react";

type ThemePref = "light" | "dark" | "system";

const STORAGE_KEY = "miniclaw2:theme";

function readStored(): ThemePref {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* ignore */
  }
  return "light";
}

function systemPrefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    !!window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

function applyDOM(pref: ThemePref) {
  const dark = pref === "dark" || (pref === "system" && systemPrefersDark());
  document.documentElement.classList.toggle("dark", dark);
}

export function ThemeToggle() {
  const [pref, setPref] = useState<ThemePref>(() => readStored());

  // Apply on mount + whenever pref changes.
  useEffect(() => {
    applyDOM(pref);
    try {
      localStorage.setItem(STORAGE_KEY, pref);
    } catch {
      /* ignore */
    }
  }, [pref]);

  // Subscribe to system changes only when pref === "system".
  useEffect(() => {
    if (pref !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyDOM("system");
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
      ? "Theme: light · click for dark"
      : pref === "dark"
        ? "Theme: dark · click for system"
        : "Theme: system · click for light";

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
        {pref === "light" ? <SunIcon /> : pref === "dark" ? <MoonIcon /> : <MonitorIcon />}
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

function MonitorIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="2" y="3" width="12" height="8.5" rx="1.2" />
      <path d="M5.5 14h5M8 11.5v2.5" />
    </svg>
  );
}
