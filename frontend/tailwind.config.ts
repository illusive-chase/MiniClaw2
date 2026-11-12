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
          library: {
            DEFAULT: "rgb(var(--state-library) / <alpha-value>)",
            soft: "rgb(var(--state-library-soft) / <alpha-value>)",
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
        /* Project icon hues — a separate family from `state`, which is
         * reserved for node state. `DEFAULT` strokes the glyph, `soft` tints. */
        hue: {
          coral: {
            DEFAULT: "rgb(var(--hue-coral) / <alpha-value>)",
            soft: "rgb(var(--hue-coral-soft) / <alpha-value>)",
          },
          amber: {
            DEFAULT: "rgb(var(--hue-amber) / <alpha-value>)",
            soft: "rgb(var(--hue-amber-soft) / <alpha-value>)",
          },
          sage: {
            DEFAULT: "rgb(var(--hue-sage) / <alpha-value>)",
            soft: "rgb(var(--hue-sage-soft) / <alpha-value>)",
          },
          teal: {
            DEFAULT: "rgb(var(--hue-teal) / <alpha-value>)",
            soft: "rgb(var(--hue-teal-soft) / <alpha-value>)",
          },
          azure: {
            DEFAULT: "rgb(var(--hue-azure) / <alpha-value>)",
            soft: "rgb(var(--hue-azure-soft) / <alpha-value>)",
          },
          indigo: {
            DEFAULT: "rgb(var(--hue-indigo) / <alpha-value>)",
            soft: "rgb(var(--hue-indigo-soft) / <alpha-value>)",
          },
          plum: {
            DEFAULT: "rgb(var(--hue-plum) / <alpha-value>)",
            soft: "rgb(var(--hue-plum-soft) / <alpha-value>)",
          },
          clay: {
            DEFAULT: "rgb(var(--hue-clay) / <alpha-value>)",
            soft: "rgb(var(--hue-clay-soft) / <alpha-value>)",
          },
        },
      },
      boxShadow: {
        card: "0 1px 2px rgb(var(--shadow) / 0.06), 0 1px 1px rgb(var(--shadow) / 0.04)",
        raised: "0 4px 16px rgb(var(--shadow) / 0.08), 0 1px 2px rgb(var(--shadow) / 0.04)",
        modal: "0 24px 64px rgb(var(--shadow) / 0.18), 0 2px 8px rgb(var(--shadow) / 0.08)",
        rail: "inset 3px 0 0 0 currentColor",
      },
      /* Motion tokens; values live in index.css so reduced motion can
       * rewrite them in one place. */
      transitionDuration: {
        instant: "var(--dur-instant)",
        quick: "var(--dur-quick)",
        base: "var(--dur-base)",
        modal: "var(--dur-modal)",
        emphasis: "var(--dur-emphasis)",
        slow: "var(--dur-slow)",
      },
      transitionTimingFunction: {
        out: "var(--ease-out)",
        in: "var(--ease-in)",
        "in-out": "var(--ease-in-out)",
        spring: "var(--ease-spring)",
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
