import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { NodeInfo, NodeState } from "../types";

type EdgePath = { d: string; key: string; selected: boolean };

export function ProjectTimeline({
  nodes,
  selectedNodeId,
  onSelect,
}: {
  nodes: NodeInfo[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const tileRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const [paths, setPaths] = useState<EdgePath[]>([]);
  const [overlay, setOverlay] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });

  const nodeById = useMemo(() => {
    const map = new Map<string, NodeInfo>();
    for (const node of nodes) map.set(node.id, node);
    return map;
  }, [nodes]);

  const recompute = useCallback(() => {
    const container = scrollRef.current;
    if (!container) return;
    const containerRect = container.getBoundingClientRect();
    const scrollLeft = container.scrollLeft;
    const next: EdgePath[] = [];
    for (const node of nodes) {
      if (!node.parent_node_id) continue;
      if (node.kind === "op") continue;
      const parent = nodeById.get(node.parent_node_id);
      if (!parent || parent.kind === "op") continue;
      const childTile = tileRefs.current.get(node.id);
      const parentTile = tileRefs.current.get(node.parent_node_id);
      if (!childTile || !parentTile) continue;
      const parentRect = parentTile.getBoundingClientRect();
      const childRect = childTile.getBoundingClientRect();
      const x1 = parentRect.right - containerRect.left + scrollLeft;
      const y1 = parentRect.bottom - containerRect.top - 6;
      const x2 = childRect.left - containerRect.left + scrollLeft;
      const y2 = childRect.bottom - containerRect.top - 6;
      const dx = Math.max(40, Math.abs(x2 - x1) * 0.4);
      const d = `M ${x1} ${y1} C ${x1 + dx} ${y1 + 16}, ${x2 - dx} ${y2 + 16}, ${x2} ${y2}`;
      next.push({
        d,
        key: `${node.parent_node_id}->${node.id}`,
        selected:
          node.id === selectedNodeId || node.parent_node_id === selectedNodeId,
      });
    }
    setPaths((prev) => (samePaths(prev, next) ? prev : next));
    const nextWidth = container.scrollWidth;
    const nextHeight = container.clientHeight;
    setOverlay((prev) =>
      prev.width === nextWidth && prev.height === nextHeight
        ? prev
        : { width: nextWidth, height: nextHeight },
    );
  }, [nodes, nodeById, selectedNodeId]);

  useLayoutEffect(() => {
    recompute();
  }, [recompute]);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const ro = new ResizeObserver(() => recompute());
    ro.observe(container);
    const onScroll = () => recompute();
    container.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", recompute);
    return () => {
      ro.disconnect();
      container.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", recompute);
    };
  }, [recompute]);

  return (
    <section className="border-b border-slate-800 bg-slate-950/40 px-6 py-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
          Project timeline
        </div>
        <div className="text-[11px] text-slate-600">{nodes.length} nodes</div>
      </div>
      {nodes.length === 0 ? (
        <div className="rounded-md border border-dashed border-slate-800 px-3 py-3 text-xs text-slate-600">
          No nodes yet.
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="relative flex gap-2 overflow-x-auto pb-1"
        >
          {paths.length > 0 && (
            <svg
              className="pointer-events-none absolute inset-0"
              width={overlay.width}
              height={overlay.height}
              aria-hidden="true"
            >
              {paths.map((path) => (
                <path
                  key={path.key}
                  d={path.d}
                  fill="none"
                  stroke={path.selected ? "rgb(125 211 252)" : "rgb(71 85 105)"}
                  strokeWidth={path.selected ? 2 : 1.5}
                  strokeDasharray="4 3"
                />
              ))}
            </svg>
          )}
          {nodes.map((node, index) => {
            const isOp = node.kind === "op";
            const kindLabel = isOp && node.op_kind ? `op · ${node.op_kind}` : node.kind;
            const body =
              isOp
                ? node.summary || "(running)"
                : node.summary || node.prompt || "(empty prompt)";
            const resumeParent = !isOp
              ? findResumeParent(node, nodeById)
              : null;
            return (
              <button
                key={node.id}
                type="button"
                ref={(el) => {
                  if (el) tileRefs.current.set(node.id, el);
                  else tileRefs.current.delete(node.id);
                }}
                onClick={() => onSelect(node.id)}
                className={
                  "group relative z-10 grid h-24 flex-none grid-rows-[auto_1fr_auto] rounded-md border px-3 py-2 text-left transition " +
                  (isOp ? "w-32 " : "w-48 ") +
                  (selectedNodeId === node.id
                    ? "border-sky-500/80 bg-sky-950/30"
                    : isOp
                      ? "border-slate-800/60 bg-slate-900/30 hover:border-slate-700"
                      : "border-slate-800 bg-slate-900/50 hover:border-slate-700")
                }
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] text-slate-400">
                    {index + 1}. {kindLabel}
                  </span>
                  <span className={"h-2 w-2 rounded-full " + stateDot(node.state)} />
                </div>
                <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-200">
                  {body}
                </div>
                <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-slate-500">
                  <span className="font-mono">{node.id.slice(0, 8)}</span>
                  <span>{formatNodeTime(node.started_at ?? node.created_at)}</span>
                </div>
                {resumeParent && (
                  <span
                    className="absolute -top-2 left-2 rounded-full border border-sky-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[9px] text-sky-300"
                    title={`Resumed from ${resumeParent.id}`}
                  >
                    ↻ {resumeParent.id.slice(0, 6)}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function samePaths(a: EdgePath[], b: EdgePath[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (
      a[i].key !== b[i].key ||
      a[i].d !== b[i].d ||
      a[i].selected !== b[i].selected
    ) {
      return false;
    }
  }
  return true;
}

function findResumeParent(
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
): NodeInfo | null {
  if (!node.parent_node_id) return null;
  const parent = byId.get(node.parent_node_id);
  if (!parent || parent.kind === "op") return null;
  return parent;
}

function stateDot(state: NodeState): string {
  switch (state) {
    case "queued":
      return "bg-slate-500";
    case "running":
      return "bg-sky-400 animate-pulse";
    case "waiting":
    case "awaiting_review":
      return "bg-emerald-400 animate-pulse";
    case "done":
      return "bg-slate-400";
    case "error":
      return "bg-rose-500";
    case "cancelled":
      return "bg-zinc-600";
    default:
      return "bg-slate-500";
  }
}

function formatNodeTime(value: number): string {
  return new Date(value * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}
