/**
 * Motion primitives shared across the UI.
 *
 * Every duration and curve lives as a CSS custom property in `index.css`
 * (`--dur-*`, `--ease-*`, `--rise-*`, `--scale-*`); components reference those
 * tokens through classes rather than hard-coding milliseconds. The few numbers
 * JavaScript must know — how long to keep an element mounted for its exit —
 * are mirrored here and must track the CSS.
 *
 * Reduced motion is handled at the token level: under
 * `prefers-reduced-motion: reduce` the distance and scale tokens collapse and
 * durations shrink, so every keyframe degrades to a short fade without any
 * component branching on the preference.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";

/** Exit durations in ms. Each matches a `*-exit` rule in `index.css`. */
export const EXIT_MS = {
  menu: 100,
  popover: 140,
  modal: 160,
  panel: 160,
  node: 160,
  /** Home folder / archive overlay; `.home-overlay[data-phase="exit"]`. */
  overlay: 140,
} as const;

/** How long a "fresh" mark lives; matches `--dur-emphasis` plus the ring tail. */
export const FRESH_HOLD_MS = 900;

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export type PresencePhase = "enter" | "exit";

/**
 * Keep an element mounted for `exitMs` after `open` turns false, so its exit
 * animation can play. Entry needs no help: a CSS keyframe runs on mount.
 *
 * The "still present" flag is derived during render rather than set from an
 * effect. Setting it from an effect would leave one committed frame where
 * `open` is false and nothing is mounted — the element would blink out and
 * then replay its entry.
 *
 * The removal timer lives in an effect (never in a state updater), so the
 * StrictMode double-invoke cannot schedule it twice, and reopening mid-exit
 * cancels it through the cleanup.
 */
export function usePresence(
  open: boolean,
  exitMs: number,
): { mounted: boolean; phase: PresencePhase } {
  const [present, setPresent] = useState(open);
  if (open && !present) setPresent(true);
  useEffect(() => {
    if (open || !present) return;
    const timer = window.setTimeout(() => setPresent(false), exitMs);
    return () => window.clearTimeout(timer);
  }, [open, present, exitMs]);
  return { mounted: open || present, phase: open ? "enter" : "exit" };
}

/**
 * The last non-null value seen. A modal whose payload is cleared on close
 * still has something to render while it animates out.
 */
export function useLatest<T>(value: T | null | undefined): T | null {
  const ref = useRef<T | null>(value ?? null);
  if (value !== null && value !== undefined) ref.current = value;
  return ref.current;
}

/**
 * Ids in `ids` that `seen` has not recorded. `seen` is updated in place.
 * Pure apart from that mutation, so the hook below stays a thin shell.
 */
export function collectFreshIds(seen: Set<string>, ids: readonly string[]): string[] {
  const added: string[] = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    seen.add(id);
    added.push(id);
  }
  return added;
}

const EMPTY_SET: ReadonlySet<string> = new Set();

/**
 * Ids that appeared since the previous commit, held for `holdMs`.
 *
 * The first commit marks nothing: hydrating a page must not make every row
 * animate in at once — motion is only for what arrives while the user watches.
 * Runs as a layout effect so a new row's first paint already carries its mark
 * and the entry plays from frame zero.
 */
export function useFreshIds(
  ids: readonly string[],
  holdMs: number = FRESH_HOLD_MS,
): ReadonlySet<string> {
  const seenRef = useRef<Set<string> | null>(null);
  const timersRef = useRef(new Set<number>());
  const [fresh, setFresh] = useState<ReadonlySet<string>>(EMPTY_SET);
  const key = ids.join("\u0000");

  useLayoutEffect(() => {
    if (seenRef.current === null) {
      seenRef.current = new Set(ids);
      return;
    }
    const added = collectFreshIds(seenRef.current, ids);
    if (added.length === 0) return;
    setFresh((prev) => new Set([...prev, ...added]));
    const timers = timersRef.current;
    const timer = window.setTimeout(() => {
      timers.delete(timer);
      setFresh((prev) => {
        const next = new Set(prev);
        for (const id of added) next.delete(id);
        return next.size > 0 ? next : EMPTY_SET;
      });
    }, holdMs);
    timers.add(timer);
    // `key` stands in for `ids`; a new array identity with the same members
    // must not re-run the diff.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, holdMs]);

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const timer of timers) window.clearTimeout(timer);
      timers.clear();
    };
  }, []);

  return fresh;
}

/**
 * Motion for a count badge. Returns a React `key` that changes with the
 * count, so the badge remounts and replays its keyframe, and the class to
 * play: a spring-in when the badge appears from zero, a bump when an already
 * visible count moves. The first render plays nothing — a page load is not
 * news. A layout effect, so the badge never paints one frame un-animated.
 */
export function useCountMotion(count: number): { key: number; className: string; grew: boolean } {
  const [state, setState] = useState({ tick: 0, entering: false, grew: false });
  const prev = useRef(count);
  useLayoutEffect(() => {
    if (prev.current === count) return;
    const entering = prev.current === 0;
    const grew = count > prev.current;
    prev.current = count;
    setState((current) => ({ tick: current.tick + 1, entering, grew }));
  }, [count]);
  return {
    key: state.tick,
    className: state.tick === 0 ? "" : state.entering ? "mc-badge-in" : "mc-bump",
    grew: state.tick > 0 && state.grew,
  };
}

function cssDurationMs(name: string, fallback: number): number {
  if (typeof window === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const value = Number.parseFloat(raw);
  if (!Number.isFinite(value)) return fallback;
  return raw.endsWith("ms") ? value : raw.endsWith("s") ? value * 1000 : value;
}

/**
 * Play a short rise-in on `ref`'s element whenever `swapKey` changes after
 * the first render — "the content here is different now" — without
 * remounting anything. A keyed remount would replay a CSS animation just as
 * well, but it would also reset every piece of local state below it (drafts,
 * scroll, open sections), which a selection change must not do.
 *
 * Uses the Web Animations API so nothing is left behind on the element once
 * the animation ends. `enabled` false skips a swap (e.g. while the container
 * is itself sliding in).
 */
export function useSwapAnimation(
  ref: React.RefObject<HTMLElement>,
  swapKey: string,
  enabled = true,
): void {
  const previous = useRef(swapKey);
  useLayoutEffect(() => {
    if (previous.current === swapKey) return;
    previous.current = swapKey;
    const element = ref.current;
    if (!enabled || !element || typeof element.animate !== "function") return;
    const reduced = prefersReducedMotion();
    const rise = reduced ? 0 : 4;
    element.animate(
      [
        { opacity: 0, transform: `translateY(${rise}px)` },
        { opacity: 1, transform: "none" },
      ],
      {
        duration: cssDurationMs("--dur-base", 200),
        easing: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
    );
  }, [ref, swapKey, enabled]);
}

/**
 * Animation-delay for the `index`-th item of a staggered group, capped so a
 * long list never makes the last row wait.
 */
export function staggerDelay(index: number, stepMs = 30, maxSteps = 6): string {
  return `${Math.min(index, maxSteps) * stepMs}ms`;
}
