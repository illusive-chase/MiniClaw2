import type { ComponentType } from "react";
import type { NodeState } from "../../types";

/**
 * Icons are drawn on an 8×8 grid and default to that size.
 *
 * `size` exists because the notice rail leans on the icon far harder than a
 * node tile does: every banner there shares one frame, so shape and color are
 * the only thing separating a completion from a failure, and an 8px glyph in a
 * 28px chip reads as a colored dot rather than as a check or a cross.
 */
export type StateIconProps = { size?: number };
export type StateIcon = ComponentType<StateIconProps>;

export type StateMeta = {
  label: string;
  Icon: StateIcon;
  chipBg: string;
  chipText: string;
  railBg: string;
  tileBg: string;
  barTrack: string;
  barFill: string;
  ring: boolean;
};

export function stateMeta(state: NodeState): StateMeta {
  switch (state) {
    case "virtual":
      return {
        label: "virtual",
        Icon: DotIcon,
        chipBg: "bg-surface-sunken",
        chipText: "text-ink-muted",
        railBg: "bg-line-strong",
        tileBg: "bg-surface-raised/70",
        barTrack: "bg-transparent",
        barFill: "w-0 bg-transparent",
        ring: false,
      };
    case "queued":
      return {
        label: "queued",
        Icon: DotIcon,
        chipBg: "bg-state-queued-soft",
        chipText: "text-ink-muted",
        railBg: "bg-state-queued",
        tileBg: "bg-surface-raised",
        barTrack: "bg-transparent",
        barFill: "w-0 bg-state-queued",
        ring: false,
      };
    case "running":
      return {
        label: "running",
        Icon: DotPulseIcon,
        chipBg: "bg-state-running-soft",
        chipText: "text-brand-ink dark:text-brand",
        railBg: "bg-state-running",
        tileBg: "bg-state-running-soft/40",
        barTrack: "bg-state-running-soft",
        barFill:
          "node-sweep w-1/3 bg-gradient-to-r from-transparent via-state-running to-transparent",
        ring: false,
      };
    case "waiting":
      return {
        label: "waiting",
        Icon: HourglassIcon,
        chipBg: "bg-state-waiting-soft",
        chipText: "text-state-waiting dark:text-state-waiting",
        railBg: "bg-state-waiting pulse-slow",
        tileBg: "bg-state-waiting-soft/35",
        barTrack: "bg-state-waiting-soft",
        barFill: "w-1/2 bg-state-waiting/70 pulse-slow",
        ring: false,
      };
    case "awaiting_human_input":
      return {
        label: "human input",
        Icon: RingIcon,
        chipBg: "bg-state-review-soft",
        chipText: "text-state-review dark:text-state-review",
        railBg: "bg-state-review",
        tileBg: "bg-state-review-soft/35",
        barTrack: "bg-state-review-soft",
        barFill: "w-full bg-state-review/55 pulse-slow",
        ring: true,
      };
    case "done":
      return {
        label: "done",
        Icon: CheckIcon,
        chipBg: "bg-state-done-soft",
        chipText: "text-ink-muted",
        railBg: "bg-state-done",
        tileBg: "bg-surface-raised",
        barTrack: "bg-transparent",
        barFill: "w-full bg-state-done/40",
        ring: false,
      };
    case "error":
      return {
        label: "error",
        Icon: CrossIcon,
        chipBg: "bg-state-error-soft",
        chipText: "text-state-error",
        railBg: "bg-state-error",
        tileBg: "bg-state-error-soft/35",
        barTrack: "bg-transparent",
        barFill: "w-full bg-state-error/55",
        ring: false,
      };
    case "cancelled":
      return {
        label: "cancelled",
        Icon: SlashIcon,
        chipBg: "bg-state-cancelled-soft",
        chipText: "text-ink-subtle",
        railBg: "bg-state-cancelled",
        tileBg: "bg-surface-raised",
        barTrack: "bg-transparent",
        barFill: "w-full bg-state-cancelled/40",
        ring: false,
      };
    default:
      return stateMeta("queued");
  }
}

export function stateStroke(state: NodeState): string {
  switch (state) {
    case "running":
      return "rgb(var(--state-running))";
    case "waiting":
      return "rgb(var(--state-waiting))";
    case "awaiting_human_input":
      return "rgb(var(--state-review))";
    case "error":
      return "rgb(var(--state-error))";
    case "done":
      return "rgb(var(--state-done))";
    default:
      return "rgb(var(--border-strong))";
  }
}

/* icons
 *
 * Every icon takes the same `size` (default 8, the design grid), and at that
 * default each one renders exactly as it did before the prop existed — the
 * node tile's chip is unchanged. The SVGs keep an 8-unit viewBox and scale
 * whole, stroke weight included, so a glyph enlarged for the notice rail is
 * the same drawing rather than a thinner one. The two dot icons carry no
 * stroke, so they size off the box: 3/4 of it, which is the 6px dot in an 8px
 * slot the tile has always drawn.
 */

export function DotIcon({ size = 8 }: StateIconProps) {
  return (
    <span
      className="block rounded-full bg-current"
      style={{ width: size * 0.75, height: size * 0.75 }}
    />
  );
}

export function DotPulseIcon({ size = 8 }: StateIconProps) {
  const dot = size * 0.75;
  return (
    <span className="relative block" style={{ width: dot, height: dot }}>
      <span className="absolute inset-0 rounded-full bg-current opacity-40 pulse-slow" />
      <span
        className="absolute rounded-full bg-current"
        style={{ inset: size / 8 }}
      />
    </span>
  );
}

export function HourglassIcon({ size = 8 }: StateIconProps) {
  return (
    <svg
      viewBox="0 0 8 8"
      width={size}
      height={size}
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M1.5 1h5v.6L4.6 4l1.9 2.4V7h-5v-.6L3.4 4 1.5 1.6V1Z" />
    </svg>
  );
}

export function RingIcon({ size = 8 }: StateIconProps) {
  return (
    <svg
      viewBox="0 0 8 8"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden="true"
    >
      <circle cx="4" cy="4" r="2.4" />
    </svg>
  );
}

export function CheckIcon({ size = 8 }: StateIconProps) {
  return (
    <svg
      viewBox="0 0 8 8"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M1.4 4.4 3 6l3.6-4" />
    </svg>
  );
}

export function CrossIcon({ size = 8 }: StateIconProps) {
  return (
    <svg
      viewBox="0 0 8 8"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M2 2 6 6M6 2 2 6" />
    </svg>
  );
}

export function SlashIcon({ size = 8 }: StateIconProps) {
  return (
    <svg
      viewBox="0 0 8 8"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M1.5 6.5 6.5 1.5" />
    </svg>
  );
}
