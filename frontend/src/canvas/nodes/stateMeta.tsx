import type { ComponentType } from "react";
import type { NodeState } from "../../types";

export type StateMeta = {
  label: string;
  Icon: ComponentType;
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
    case "awaiting_review":
      return {
        label: "review",
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
    case "awaiting_review":
      return "rgb(var(--state-review))";
    case "error":
      return "rgb(var(--state-error))";
    case "done":
      return "rgb(var(--state-done))";
    default:
      return "rgb(var(--border-strong))";
  }
}

/* icons */

export function DotIcon() {
  return <span className="block h-1.5 w-1.5 rounded-full bg-current" />;
}

export function DotPulseIcon() {
  return (
    <span className="relative block h-1.5 w-1.5">
      <span className="absolute inset-0 rounded-full bg-current opacity-40 pulse-slow" />
      <span className="absolute inset-[1px] rounded-full bg-current" />
    </span>
  );
}

export function HourglassIcon() {
  return (
    <svg viewBox="0 0 8 8" width="8" height="8" fill="currentColor" aria-hidden="true">
      <path d="M1.5 1h5v.6L4.6 4l1.9 2.4V7h-5v-.6L3.4 4 1.5 1.6V1Z" />
    </svg>
  );
}

export function RingIcon() {
  return (
    <svg
      viewBox="0 0 8 8"
      width="8"
      height="8"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden="true"
    >
      <circle cx="4" cy="4" r="2.4" />
    </svg>
  );
}

export function CheckIcon() {
  return (
    <svg
      viewBox="0 0 8 8"
      width="8"
      height="8"
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

export function CrossIcon() {
  return (
    <svg
      viewBox="0 0 8 8"
      width="8"
      height="8"
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

export function SlashIcon() {
  return (
    <svg
      viewBox="0 0 8 8"
      width="8"
      height="8"
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
