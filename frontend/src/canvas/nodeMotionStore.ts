/**
 * Transient motion marks for canvas nodes: born, arrived, located, leaving.
 *
 * Written from outside the canvas (App creates and deletes nodes, the canvas
 * centres on them) and read by each node component, which is memoized — so
 * the marks live in a module store, the same shape as `hoverStore`, and a
 * node re-renders only when its own mark changes.
 *
 * Marks are only ever written for things that happen while the user watches.
 * Hydrating a project, switching projects, and panning a node back into view
 * (`onlyRenderVisibleElements` remounts it) must not replay anything, so every
 * mark expires on a timer whether or not its animation ever ran.
 */
import { useCallback, useSyncExternalStore, type AnimationEvent, type CSSProperties } from "react";
import { useUpdateNodeInternals } from "reactflow";

export type NodeMotionKind = "born" | "arrive" | "locate" | "leave";

export type NodeMotion = {
  kind: NodeMotionKind;
  /** Stagger for batch arrivals, in ms. */
  delayMs: number;
  /** `transform-origin` for a birth: where the request came from. */
  origin?: string;
  seq: number;
};

type Listener = () => void;

const listeners = new Set<Listener>();
const marks = new Map<string, NodeMotion>();
const timers = new Map<string, number>();
let seq = 0;

/** How long a mark may wait to be played before it is dropped (spec §6.3). */
const MARK_TTL_MS = 1200;
/** `locate` rings twice; give it room. */
const LOCATE_TTL_MS = 1500;

function emit() {
  for (const listener of listeners) listener();
}

function setMark(id: string, mark: Omit<NodeMotion, "seq">, ttlMs: number | null) {
  const previous = timers.get(id);
  if (previous !== undefined) window.clearTimeout(previous);
  timers.delete(id);
  const next: NodeMotion = { ...mark, seq: ++seq };
  marks.set(id, next);
  if (ttlMs !== null) {
    timers.set(
      id,
      window.setTimeout(() => clearNodeMotion(id, next.seq), ttlMs + mark.delayMs),
    );
  }
  emit();
}

/** A node the user just asked for. Scales in from `origin` with a ring. */
export function markNodeBorn(id: string, origin = "50% 50%"): void {
  setMark(id, { kind: "born", delayMs: 0, origin }, MARK_TTL_MS);
}

/**
 * Nodes that appeared on their own (an agent planned them, a template was
 * stamped). Fade only — no ring — and staggered in creation order so a batch
 * reads as a sequence rather than one flash. A node already marked is left
 * alone, so a birth is never downgraded to an arrival.
 */
export function markNodesArrived(ids: readonly string[]): void {
  let changed = false;
  ids.forEach((id, index) => {
    if (marks.has(id)) return;
    const delayMs = Math.min(index, 8) * 30;
    const next: NodeMotion = { kind: "arrive", delayMs, seq: ++seq };
    marks.set(id, next);
    timers.set(id, window.setTimeout(() => clearNodeMotion(id, next.seq), MARK_TTL_MS + delayMs));
    changed = true;
  });
  if (changed) emit();
}

/**
 * "Here it is" after a jump: the ring plays twice. A node still being born
 * keeps its birth — one emphasis at a time.
 */
export function markNodeLocated(id: string): void {
  if (marks.get(id)?.kind === "born") return;
  setMark(id, { kind: "locate", delayMs: 0 }, LOCATE_TTL_MS);
}

/** On its way out; the caller removes the node after `EXIT_MS.node`. */
export function markNodeLeaving(id: string): void {
  setMark(id, { kind: "leave", delayMs: 0 }, null);
}

export function isNodeLeaving(id: string): boolean {
  return marks.get(id)?.kind === "leave";
}

/** The current mark for `id`, outside React. */
export function peekNodeMotion(id: string): NodeMotion | null {
  return marks.get(id) ?? null;
}

/** The current pending-create placeholder, outside React. */
export function peekPendingCreate(): PendingCreate | null {
  return pendingCreate;
}

/** Drop a mark. With `onlySeq`, only if it is still that mark. */
export function clearNodeMotion(id: string, onlySeq?: number): void {
  const mark = marks.get(id);
  if (!mark || (onlySeq !== undefined && mark.seq !== onlySeq)) return;
  marks.delete(id);
  const timer = timers.get(id);
  if (timer !== undefined) window.clearTimeout(timer);
  timers.delete(id);
  emit();
}

export function subscribeNodeMotion(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useNodeMotion(id: string): NodeMotion | null {
  const read = useCallback(() => marks.get(id) ?? null, [id]);
  return useSyncExternalStore(subscribeNodeMotion, read, read);
}

/**
 * The motion class for an edge between `source` and `target`: drawn in
 * alongside a node being born, faded in with an arriving one, faded out with
 * a leaving one. Returns a string, so an edge re-renders only when its own
 * class changes.
 */
export function useEdgeMotionClass(source: string, target: string): string {
  const read = useCallback(() => {
    const a = marks.get(source)?.kind;
    const b = marks.get(target)?.kind;
    if (a === "leave" || b === "leave") return "edge-leave";
    if (a === "born" || b === "born") return "edge-draw";
    if (a === "arrive" || b === "arrive") return "edge-arrive";
    return "";
  }, [source, target]);
  return useSyncExternalStore(subscribeNodeMotion, read, read);
}

const KIND_CLASS: Record<NodeMotionKind, string> = {
  born: "node-birth",
  arrive: "node-arrive",
  locate: "",
  leave: "node-leave",
};

/**
 * Everything a node component needs to play its own mark: classes and style
 * for its root element, a ring flag for the one-shot halo, and the
 * animation-end handler that retires the mark.
 *
 * The scale-in also scales the tile's handles, and React Flow measures handle
 * offsets with `getBoundingClientRect` — a measurement taken mid-birth would
 * pin edge endpoints a few percent short, and nothing re-measures until the
 * node resizes. So the end of the scale forces one re-measure.
 */
export function useNodeMotionProps(id: string): {
  className: string;
  style: CSSProperties | undefined;
  ring: "once" | "twice" | null;
  onAnimationEnd: (event: AnimationEvent<HTMLElement>) => void;
} {
  const motion = useNodeMotion(id);
  const updateNodeInternals = useUpdateNodeInternals();
  const onAnimationEnd = useCallback(
    (event: AnimationEvent<HTMLElement>) => {
      if (!motion || motion.kind === "leave") return;
      const name = event.animationName;
      if (name === "mc-node-birth") updateNodeInternals(id);
      /* The ring outlasts the scale; retire the mark when the last
       * animation for this kind finishes. */
      const last = motion.kind === "arrive" ? "mc-fade-in" : "mc-ring-once";
      if (name === last) clearNodeMotion(id, motion.seq);
    },
    [id, motion, updateNodeInternals],
  );
  if (!motion) return { className: "", style: undefined, ring: null, onAnimationEnd };
  const style: CSSProperties = {};
  if (motion.delayMs > 0) style.animationDelay = `${motion.delayMs}ms`;
  if (motion.origin) (style as Record<string, string>)["--birth-origin"] = motion.origin;
  return {
    className: KIND_CLASS[motion.kind],
    style,
    ring: motion.kind === "born" ? "once" : motion.kind === "locate" ? "twice" : null,
    onAnimationEnd,
  };
}

/* ───────── pending creation ─────────
 * Between a double-click (or a wire released on empty canvas) and the create
 * request returning there is a network round-trip in which nothing would
 * happen. The canvas draws a ripple and a dashed placeholder at the spot in
 * that window, so the click is answered at once. Screen coordinates: the
 * overlay sits outside React Flow's node list and never enters layout. */

export type PendingCreate = { x: number; y: number; seq: number };

const pendingListeners = new Set<Listener>();
let pendingCreate: PendingCreate | null = null;
let pendingTimer: number | null = null;
/** Safety net if the request neither resolves nor fails promptly. */
const PENDING_MAX_MS = 2000;

function emitPending() {
  for (const listener of pendingListeners) listener();
}

export function showPendingCreate(x: number, y: number): number {
  if (pendingTimer !== null) window.clearTimeout(pendingTimer);
  const next = { x, y, seq: ++seq };
  pendingCreate = next;
  pendingTimer = window.setTimeout(() => clearPendingCreate(next.seq), PENDING_MAX_MS);
  emitPending();
  return next.seq;
}

/** Retire the placeholder; with `onlySeq`, only if it is still that one. */
export function clearPendingCreate(onlySeq?: number): void {
  if (!pendingCreate || (onlySeq !== undefined && pendingCreate.seq !== onlySeq)) return;
  pendingCreate = null;
  if (pendingTimer !== null) window.clearTimeout(pendingTimer);
  pendingTimer = null;
  emitPending();
}

function subscribePending(listener: Listener): () => void {
  pendingListeners.add(listener);
  return () => pendingListeners.delete(listener);
}

function readPending(): PendingCreate | null {
  return pendingCreate;
}

export function usePendingCreate(): PendingCreate | null {
  return useSyncExternalStore(subscribePending, readPending, readPending);
}
