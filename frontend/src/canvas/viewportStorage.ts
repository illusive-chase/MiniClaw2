import type { CanvasViewport } from "../types";

type ViewportStorage = Pick<Storage, "getItem" | "setItem">;

export function readCanvasViewport(
  projectId: string,
  storage: ViewportStorage | null,
): CanvasViewport | null {
  try {
    const value = JSON.parse(storage?.getItem(`miniclaw2.canvas-viewport.v1:${projectId}`) ?? "null");
    return value && Number.isFinite(value.x) && Number.isFinite(value.y) && Number.isFinite(value.zoom) && value.zoom > 0
      ? { x: value.x, y: value.y, zoom: value.zoom }
      : null;
  } catch {
    return null;
  }
}

export function saveCanvasViewport(
  projectId: string,
  viewport: CanvasViewport,
  storage: ViewportStorage | null,
  userInitiated: boolean,
): void {
  if (!userInitiated || !Number.isFinite(viewport.x) || !Number.isFinite(viewport.y) || !Number.isFinite(viewport.zoom) || viewport.zoom <= 0) return;
  try {
    storage?.setItem(`miniclaw2.canvas-viewport.v1:${projectId}`, JSON.stringify(viewport));
  } catch {
    return;
  }
}

export function browserViewportStorage(): ViewportStorage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
