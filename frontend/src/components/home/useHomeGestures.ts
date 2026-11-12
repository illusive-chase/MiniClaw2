/* Pointer state machine for the home grid (design §2.1–§2.3, revised).
 *
 * Browse mode: a press that stays put for PRESS_DELAY_MS starts the ring; it
 * fills over the rest of HOLD_MS. A release before the ring starts is a plain
 * click; a release while it is still filling cancels and the ring retracts;
 * holding past HOLD_MS arms it, and the *release* that follows enters edit
 * mode. The arming press itself never drags — dragging needs a fresh press,
 * which is what keeps "pick it up" and "open the editor" from colliding.
 *
 * Edit mode: a press on a draggable slot that travels past SLOP_PX starts a
 * drag at once; a release without travel is a click.
 *
 * While dragging, targets resolve on *dwell*, not on passage: hovering a tile's
 * center for MERGE_MS proposes a merge, resting anywhere else on a slot for
 * REORDER_MS moves the dragged slot there. Sweeping across the grid on the way
 * to a target therefore leaves what it passes over alone.
 */
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";

import {
  findFolder,
  indexOfSlot,
  mergeInto,
  moveInFolder,
  moveSlot,
  newFolderId,
  pullOut,
  type SlotKey,
} from "../../homeLayout";
import type { HomeItem } from "../../types";
import type { FlipHandle } from "./useFlip";
import type { CellCtx, RingPhase } from "./HomeCell";
import type { HomeEdit } from "./useHomeSync";

export const PRESS_DELAY_MS = 150;
export const HOLD_MS = 800;
export const FILL_MS = HOLD_MS - PRESS_DELAY_MS;
const SLOP_PX = 6;
const MERGE_RADIUS_PX = 24;
const MERGE_MS = 380;
const REORDER_MS = 110;
const LEAVE_FOLDER_MS = 450;
const EDGE_PX = 56;
const LAND_MS = 200;
const RETRACT_MS = 160;
/* Tile center within its 96×104 cell: 4px top padding plus half the 64px tile. */
const TILE_CENTER_X = 48;
const TILE_CENTER_Y = 36;

export type GestureTarget = { slot: string; ctx: CellCtx };
/** The drag as the view needs it: what is lifted, and where its ghost starts. */
export type DragView = GestureTarget & { start: string };

type Press = GestureTarget & {
  pointerId: number;
  x: number;
  y: number;
  phase: "waiting" | "pressing" | "armed";
  draggable: boolean;
  timers: number[];
  editing: boolean;
};

type Drag = GestureTarget & {
  folderId: string | null;
  offsetX: number;
  offsetY: number;
  x: number;
  y: number;
  snapshot: HomeItem[];
  current: HomeItem[];
  edits: HomeEdit[];
  mergeSlot: string | null;
  mergeOn: boolean;
  mergeTimer: number;
  reorderTarget: string | null;
  reorderTimer: number;
  rx: number;
  ry: number;
  leaveTimer: number;
};

export type MergeOutcome = { folderId: string; created: boolean; projectId: string };

export type GestureOptions = {
  editing: boolean;
  items: HomeItem[] | null;
  openFolderId: string | null;
  rootGrid: RefObject<HTMLElement>;
  rootFlip: FlipHandle;
  rootScroller: RefObject<HTMLElement>;
  folderGrid: RefObject<HTMLElement>;
  folderFlip: FlipHandle;
  folderPanel: RefObject<HTMLElement>;
  ghost: RefObject<HTMLDivElement>;
  preview: (items: HomeItem[]) => void;
  hold: (held: boolean) => void;
  commit: (edit: HomeEdit) => void;
  onActivate: (target: GestureTarget) => void;
  onArmedRelease: (target: GestureTarget) => void;
  onDragStart: (target: GestureTarget) => void;
  onLeftFolder: () => void;
  onMerged: (outcome: MergeOutcome) => void;
};

export function useHomeGestures(options: GestureOptions) {
  const opts = useRef(options);
  opts.current = options;

  const [pressView, setPressView] = useState<{ slot: string; ctx: CellCtx; phase: RingPhase } | null>(null);
  const [dragView, setDragView] = useState<DragView | null>(null);
  const [mergeView, setMergeView] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [landing, setLanding] = useState(false);

  const press = useRef<Press | null>(null);
  const drag = useRef<Drag | null>(null);
  const retractTimer = useRef(0);
  const frame = useRef(0);
  const listening = useRef(false);

  const clearPressTimers = (current: Press | null) => {
    if (!current) return;
    for (const timer of current.timers) window.clearTimeout(timer);
    current.timers = [];
  };

  const retract = useCallback((target: GestureTarget) => {
    window.clearTimeout(retractTimer.current);
    setPressView({ ...target, phase: "cancel" });
    retractTimer.current = window.setTimeout(() => setPressView(null), RETRACT_MS);
  }, []);

  const endPress = useCallback((how: "cancel" | "silent") => {
    const current = press.current;
    press.current = null;
    clearPressTimers(current);
    if (!current) return;
    if (how === "cancel" && current.phase === "pressing") retract(current);
    else setPressView(null);
  }, [retract]);

  const moveGhost = (x: number, y: number) => {
    const d = drag.current;
    const ghost = opts.current.ghost.current;
    if (!d || !ghost) return;
    ghost.style.transform = `translate(${x - d.offsetX}px, ${y - d.offsetY}px)`;
  };

  const step = (edit: HomeEdit) => {
    const d = drag.current;
    if (!d) return;
    const next = edit(d.current);
    if (next === d.current) return;
    d.current = next;
    d.edits.push(edit);
    opts.current.preview(next);
  };

  const clearMerge = (d: Drag) => {
    window.clearTimeout(d.mergeTimer);
    d.mergeTimer = 0;
    if (d.mergeOn) setMergeView(null);
    d.mergeSlot = null;
    d.mergeOn = false;
  };

  const clearReorder = (d: Drag) => {
    window.clearTimeout(d.reorderTimer);
    d.reorderTimer = 0;
    d.reorderTarget = null;
  };

  type Hit = { slot: string; dist: number } | { slot: "end"; dist: number };

  const hitTest = (grid: HTMLElement | null, flip: FlipHandle, x: number, y: number): Hit | null => {
    if (!grid) return null;
    let last: DOMRect | null = null;
    for (const cell of grid.querySelectorAll<HTMLElement>(":scope > [data-slot]")) {
      const slot = cell.dataset.slot!;
      const rect = flip.layoutRect(slot) ?? cell.getBoundingClientRect();
      if (!slot.startsWith("sys:")) last = rect;
      if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
        const dist = Math.hypot(x - (rect.left + TILE_CENTER_X), y - (rect.top + TILE_CENTER_Y));
        return slot.startsWith("sys:") ? { slot: "end", dist } : { slot, dist };
      }
    }
    const bounds = grid.getBoundingClientRect();
    if (
      last
      && x >= bounds.left && x <= bounds.right
      && (y > last.bottom + 4 || (y >= last.top && y <= last.bottom && x > last.right))
    ) {
      return { slot: "end", dist: Infinity };
    }
    return null;
  };

  const reorderTo = (target: string) => {
    const d = drag.current;
    if (!d) return;
    d.reorderTimer = 0;
    if (d.ctx === "folder" && d.folderId) {
      const folderId = d.folderId;
      const projectId = d.slot.slice(2);
      const targetId = target === "end" ? null : target.slice(2);
      step((items) => {
        const folder = findFolder(items, folderId);
        if (!folder) return items;
        const to = targetId === null ? folder.projects.length - 1 : folder.projects.indexOf(targetId);
        return to < 0 ? items : moveInFolder(items, folderId, projectId, to);
      });
      return;
    }
    const key = d.slot as SlotKey;
    step((items) => {
      const to = target === "end" ? items.length - 1 : indexOfSlot(items, target as SlotKey);
      return to < 0 ? items : moveSlot(items, key, to);
    });
  };

  const leaveFolder = () => {
    const d = drag.current;
    if (!d || d.ctx !== "folder") return;
    const projectId = d.slot.slice(2);
    d.leaveTimer = 0;
    step((items) => pullOut(items, projectId));
    d.ctx = "root";
    d.folderId = null;
    setLeaving(false);
    setDragView((view) => (view ? { ...view, ctx: "root" } : view));
    opts.current.onLeftFolder();
  };

  const dragMove = (x: number, y: number) => {
    const d = drag.current;
    if (!d) return;
    d.x = x;
    d.y = y;
    moveGhost(x, y);
    const o = opts.current;
    if (d.ctx === "folder") {
      const panel = o.folderPanel.current?.getBoundingClientRect();
      const outside = !panel
        || x < panel.left - 4 || x > panel.right + 4 || y < panel.top - 4 || y > panel.bottom + 4;
      if (outside) {
        clearReorder(d);
        if (!d.leaveTimer) {
          setLeaving(true);
          d.leaveTimer = window.setTimeout(leaveFolder, LEAVE_FOLDER_MS);
        }
        return;
      }
      if (d.leaveTimer) {
        window.clearTimeout(d.leaveTimer);
        d.leaveTimer = 0;
        setLeaving(false);
      }
    }
    const hit = d.ctx === "folder"
      ? hitTest(o.folderGrid.current, o.folderFlip, x, y)
      : hitTest(o.rootGrid.current, o.rootFlip, x, y);
    if (!hit || hit.slot === d.slot) {
      clearMerge(d);
      clearReorder(d);
      return;
    }
    const canMerge = d.ctx === "root" && d.slot.startsWith("p:") && hit.slot !== "end";
    if (canMerge && hit.dist < MERGE_RADIUS_PX) {
      clearReorder(d);
      if (d.mergeSlot !== hit.slot) {
        clearMerge(d);
        d.mergeSlot = hit.slot;
        d.mergeTimer = window.setTimeout(() => {
          d.mergeOn = true;
          setMergeView(hit.slot);
        }, MERGE_MS);
      }
      return;
    }
    clearMerge(d);
    if (d.reorderTarget !== hit.slot || Math.hypot(x - d.rx, y - d.ry) > SLOP_PX) {
      window.clearTimeout(d.reorderTimer);
      d.reorderTarget = hit.slot;
      d.rx = x;
      d.ry = y;
      d.reorderTimer = window.setTimeout(() => reorderTo(hit.slot), REORDER_MS);
    }
  };

  const autoScroll = () => {
    frame.current = 0;
    const d = drag.current;
    if (!d) return;
    const o = opts.current;
    const box = d.ctx === "folder" ? o.folderPanel.current : o.rootScroller.current;
    if (box) {
      const rect = box.getBoundingClientRect();
      let velocity = 0;
      if (d.y < rect.top + EDGE_PX) velocity = -Math.ceil((rect.top + EDGE_PX - d.y) / 4);
      else if (d.y > rect.bottom - EDGE_PX) velocity = Math.ceil((d.y - (rect.bottom - EDGE_PX)) / 4);
      if (velocity) {
        const before = box.scrollTop;
        box.scrollTop += velocity;
        if (box.scrollTop !== before) dragMove(d.x, d.y);
      }
    }
    frame.current = window.requestAnimationFrame(autoScroll);
  };

  const beginDrag = (p: Press, x: number, y: number) => {
    const o = opts.current;
    if (!o.items) return;
    const grid = p.ctx === "folder" ? o.folderGrid.current : o.rootGrid.current;
    const cell = grid?.querySelector<HTMLElement>(`:scope > [data-slot="${CSS.escape(p.slot)}"]`);
    const rect = cell?.getBoundingClientRect();
    if (!rect) return;
    drag.current = {
      slot: p.slot,
      ctx: p.ctx,
      folderId: p.ctx === "folder" ? o.openFolderId : null,
      offsetX: p.x - rect.left,
      offsetY: p.y - rect.top,
      x,
      y,
      snapshot: o.items,
      current: o.items,
      edits: [],
      mergeSlot: null,
      mergeOn: false,
      mergeTimer: 0,
      reorderTarget: null,
      reorderTimer: 0,
      rx: x,
      ry: y,
      leaveTimer: 0,
    };
    o.hold(true);
    /* The ghost's first frame is placed by render, before any pointermove
     * reaches `moveGhost`; otherwise it would flash at the viewport origin. */
    setDragView({
      slot: p.slot,
      ctx: p.ctx,
      start: `translate(${x - (p.x - rect.left)}px, ${y - (p.y - rect.top)}px)`,
    });
    document.body.classList.add("home-dragging");
    o.onDragStart({ slot: p.slot, ctx: p.ctx });
    frame.current = window.requestAnimationFrame(autoScroll);
  };

  const finishDrag = (land: { rect: DOMRect | null; merge: boolean }) => {
    const ghost = opts.current.ghost.current;
    const done = () => {
      setLanding(false);
      setDragView(null);
      setMergeView(null);
      setLeaving(false);
    };
    if (!ghost || !land.rect) {
      done();
      return;
    }
    setLanding(true);
    ghost.dataset.landing = "true";
    if (land.merge) {
      const x = land.rect.left + TILE_CENTER_X - 48;
      const y = land.rect.top + 4;
      ghost.style.transform = `translate(${x}px, ${y}px) scale(0.55)`;
      ghost.style.opacity = "0";
    } else {
      ghost.style.transform = `translate(${land.rect.left}px, ${land.rect.top}px)`;
    }
    window.setTimeout(done, LAND_MS);
  };

  const endDrag = (cancel: boolean) => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    window.clearTimeout(d.mergeTimer);
    window.clearTimeout(d.reorderTimer);
    window.clearTimeout(d.leaveTimer);
    if (frame.current) window.cancelAnimationFrame(frame.current);
    frame.current = 0;
    document.body.classList.remove("home-dragging");
    const o = opts.current;
    const flipFor = (ctx: CellCtx) => (ctx === "folder" ? o.folderFlip : o.rootFlip);

    if (cancel) {
      o.preview(d.snapshot);
      o.hold(false);
      window.requestAnimationFrame(() => finishDrag({ rect: flipFor(d.ctx).layoutRect(d.slot), merge: false }));
      return;
    }

    if (d.mergeOn && d.mergeSlot && d.ctx === "root") {
      const target = d.mergeSlot as SlotKey;
      const projectId = d.slot.slice(2);
      const folderId = target.startsWith("f:") ? target.slice(2) : newFolderId(d.current);
      const result = mergeInto(d.current, target, projectId, folderId);
      if (result) {
        const targetRect = o.rootFlip.layoutRect(target);
        d.edits.push((items) => mergeInto(items, target, projectId, folderId)?.items ?? items);
        d.current = result.items;
        o.preview(result.items);
        const edits = d.edits;
        o.commit((items) => edits.reduce((acc, edit) => edit(acc), items));
        o.hold(false);
        finishDrag({ rect: targetRect, merge: true });
        o.onMerged({ folderId: result.folderId, created: result.created, projectId });
        return;
      }
    }

    if (d.edits.length && JSON.stringify(d.current) !== JSON.stringify(d.snapshot)) {
      const edits = d.edits;
      o.commit((items) => edits.reduce((acc, edit) => edit(acc), items));
    }
    o.hold(false);
    window.requestAnimationFrame(() => finishDrag({ rect: flipFor(d.ctx).layoutRect(d.slot), merge: false }));
  };

  const onMove = (event: PointerEvent) => {
    if (drag.current) {
      event.preventDefault();
      dragMove(event.clientX, event.clientY);
      return;
    }
    const p = press.current;
    if (!p || event.pointerId !== p.pointerId) return;
    if (Math.hypot(event.clientX - p.x, event.clientY - p.y) <= SLOP_PX) return;
    if (p.phase === "armed") return;
    if (p.editing && p.draggable) {
      endPress("silent");
      beginDrag(p, event.clientX, event.clientY);
      return;
    }
    /* Browse mode, moved before the hold completed: a scroll, not a press. */
    endPress("cancel");
  };

  const onUp = (event: PointerEvent) => {
    if (drag.current) {
      endDrag(false);
      detach();
      return;
    }
    const p = press.current;
    if (!p || event.pointerId !== p.pointerId) return;
    const phase = p.phase;
    if (phase === "armed") {
      press.current = null;
      clearPressTimers(p);
      setPressView({ slot: p.slot, ctx: p.ctx, phase: "armed" });
      opts.current.onArmedRelease({ slot: p.slot, ctx: p.ctx });
      /* The armed ring stays drawn until the selection ring takes over. */
      window.requestAnimationFrame(() => setPressView(null));
    } else if (phase === "pressing") {
      endPress("cancel");
    } else {
      endPress("silent");
      opts.current.onActivate({ slot: p.slot, ctx: p.ctx });
    }
    detach();
  };

  const onCancel = () => {
    if (drag.current) endDrag(false);
    else endPress("cancel");
    detach();
  };

  const onAbort = () => {
    endDrag(true);
    detach();
  };

  /* Once a hold has armed, or a drag is under way, the same finger must not
   * scroll the page. Pointer events alone cannot stop a touch pan. */
  const onTouchMove = (event: TouchEvent) => {
    if (drag.current || press.current?.phase === "armed") event.preventDefault();
  };

  const detach = () => {
    if (!listening.current) return;
    listening.current = false;
    window.removeEventListener("pointermove", onMoveRef);
    window.removeEventListener("pointerup", onUpRef);
    window.removeEventListener("pointercancel", onCancelRef);
    document.removeEventListener("touchmove", onTouchMoveRef);
  };

  /* Stable listener identities that always call the latest closures. */
  const handlers = useRef({ onMove, onUp, onCancel, onAbort, onTouchMove, attach: () => {}, detach: () => {} });
  const onMoveRef = useRef((event: PointerEvent) => handlers.current.onMove(event)).current;
  const onUpRef = useRef((event: PointerEvent) => handlers.current.onUp(event)).current;
  const onCancelRef = useRef(() => handlers.current.onCancel()).current;
  const onTouchMoveRef = useRef((event: TouchEvent) => handlers.current.onTouchMove(event)).current;

  const attach = () => {
    if (listening.current) return;
    listening.current = true;
    window.addEventListener("pointermove", onMoveRef, { passive: false });
    window.addEventListener("pointerup", onUpRef);
    window.addEventListener("pointercancel", onCancelRef);
    document.addEventListener("touchmove", onTouchMoveRef, { passive: false });
  };
  handlers.current = { onMove, onUp, onCancel, onAbort, onTouchMove, attach, detach };

  /** `holdable` is false where a long press means nothing (the archive tile):
   * no ring is drawn, and the press is only ever a click. */
  const onPointerDown = useCallback(
    (
      target: GestureTarget,
      draggable: boolean,
      event: ReactPointerEvent<HTMLElement>,
      holdable = true,
    ) => {
      if (event.button !== 0 || drag.current || press.current) return;
      const editing = opts.current.editing;
      const p: Press = {
        ...target,
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        phase: "waiting",
        draggable,
        timers: [],
        editing,
      };
      press.current = p;
      window.clearTimeout(retractTimer.current);
      if (!editing && holdable) {
        p.timers.push(window.setTimeout(() => {
          if (press.current !== p) return;
          p.phase = "pressing";
          setPressView({ ...target, phase: "pressing" });
        }, PRESS_DELAY_MS));
        p.timers.push(window.setTimeout(() => {
          if (press.current !== p) return;
          p.phase = "armed";
          setPressView({ ...target, phase: "armed" });
          if (navigator.vibrate) {
            try {
              navigator.vibrate(8);
            } catch {
              /* unsupported */
            }
          }
        }, HOLD_MS));
      }
      handlers.current.attach();
    },
    [],
  );

  /** Esc during a drag puts everything back. Returns whether it consumed the key. */
  const cancelDrag = useCallback(() => {
    if (!drag.current) return false;
    handlers.current.onAbort();
    return true;
  }, []);

  useEffect(() => () => {
    handlers.current.detach();
    window.clearTimeout(retractTimer.current);
    if (frame.current) window.cancelAnimationFrame(frame.current);
    document.body.classList.remove("home-dragging");
  }, []);

  const ringFor = useCallback(
    (slot: string, ctx: CellCtx): RingPhase | null =>
      pressView && pressView.slot === slot && pressView.ctx === ctx ? pressView.phase : null,
    [pressView],
  );

  return {
    onPointerDown,
    cancelDrag,
    ringFor,
    drag: dragView,
    mergeSlot: mergeView,
    leavingFolder: leaving,
    landing,
  };
}
