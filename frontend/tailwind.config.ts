import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        display: [
          "'Fraunces'",
          "Georgia",
          "ui-serif",
          "serif",
        ],
        sans: [
          "'Switzer'",
          "'Inter'",
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
        mono: [
          "'JetBrains Mono'",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      colors: {
        surface: {
          DEFAULT: "rgb(var(--surface) / <alpha-value>)",
          raised: "rgb(var(--surface-raised) / <alpha-value>)",
          sunken: "rgb(var(--surface-sunken) / <alpha-value>)",
          scrim: "rgb(var(--surface-scrim) / <alpha-value>)",
        },
        ink: {
          strong: "rgb(var(--ink-strong) / <alpha-value>)",
          DEFAULT: "rgb(var(--ink) / <alpha-value>)",
          muted: "rgb(var(--ink-muted) / <alpha-value>)",
          subtle: "rgb(var(--ink-subtle) / <alpha-value>)",
        },
        line: {
          DEFAULT: "rgb(var(--border) / <alpha-value>)",
          strong: "rgb(var(--border-strong) / <alpha-value>)",
        },
        brand: {
          DEFAULT: "rgb(var(--brand) / <alpha-value>)",
          soft: "rgb(var(--brand-soft) / <alpha-value>)",
          ink: "rgb(var(--brand-ink) / <alpha-value>)",
        },
        state: {
          queued: {
            DEFAULT: "rgb(var(--state-queued) / <alpha-value>)",
            soft: "rgb(var(--state-queued-soft) / <alpha-value>)",
          },
          running: {
            DEFAULT: "rgb(var(--state-running) / <alpha-value>)",
            soft: "rgb(var(--state-running-soft) / <alpha-value>)",
          },
          waiting: {
            DEFAULT: "rgb(var(--state-waiting) / <alpha-value>)",
            soft: "rgb(var(--state-waiting-soft) / <alpha-value>)",
          },
          review: {
            DEFAULT: "rgb(var(--state-review) / <alpha-value>)",
            soft: "rgb(var(--state-review-soft) / <alpha-value>)",
          },
          done: {
            DEFAULT: "rgb(var(--state-done) / <alpha-value>)",
            soft: "rgb(var(--state-done-soft) / <alpha-value>)",
          },
          error: {
            DEFAULT: "rgb(var(--state-error) / <alpha-value>)",
            soft: "rgb(var(--state-error-soft) / <alpha-value>)",
          },
          cancelled: {
            DEFAULT: "rgb(var(--state-cancelled) / <alpha-value>)",
            soft: "rgb(var(--state-cancelled-soft) / <alpha-value>)",
          },
        },
      },
      boxShadow: {
        card: "0 1px 2px rgb(var(--shadow) / 0.06), 0 1px 1px rgb(var(--shadow) / 0.04)",
        raised: "0 4px 16px rgb(var(--shadow) / 0.08), 0 1px 2px rgb(var(--shadow) / 0.04)",
        modal: "0 24px 64px rgb(var(--shadow) / 0.18), 0 2px 8px rgb(var(--shadow) / 0.08)",
        rail: "inset 3px 0 0 0 currentColor",
      },
      borderRadius: {
        sm: "4px",
        md: "6px",
        lg: "8px",
        xl: "12px",
      },
    },
  },
  plugins: [],
} satisfies Config;
