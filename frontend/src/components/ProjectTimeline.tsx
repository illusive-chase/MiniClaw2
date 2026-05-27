import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { NodeInfo, NodeState } from "../types";

type EdgePath = {
  d: string;
  key: string;
  selected: boolean;
  childState: NodeState;
};

const ACTIVE_STATES = new Set<NodeState>(["running", "waiting", "awaiting_review"]);

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
        childState: node.state,
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
    <section className="border-b border-line bg-surface-sunken px-6 py-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted">
          <span className="inline-block h-1 w-1 rounded-full bg-brand" />
          Project timeline
        </div>
        <div className="font-mono text-[10px] text-ink-subtle">
          {nodes.length} node{nodes.length === 1 ? "" : "s"}
        </div>
      </div>

      {nodes.length === 0 ? (
        <div className="rounded-md border border-dashed border-line bg-surface-raised/40 px-3 py-3 text-xs text-ink-muted">
          No nodes yet — launch one with{" "}
          <span className="font-mono text-ink">+ Node</span>.
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="relative flex gap-3 overflow-x-auto bg-grid rounded-md border border-line p-3"
        >
          {paths.length > 0 && (
            <svg
              className="pointer-events-none absolute inset-0"
              width={overlay.width}
              height={overlay.height}
              aria-hidden="true"
            >
              {paths.map((path) => {
                const active = ACTIVE_STATES.has(path.childState);
                const stroke = path.selected
                  ? "rgb(var(--brand))"
                  : edgeStroke(path.childState);
                const width = path.selected ? 2 : active ? 1.5 : 1;
                return (
                  <path
                    key={path.key}
                    d={path.d}
                    fill="none"
                    stroke={stroke}
                    strokeWidth={width}
                    strokeDasharray={active ? "3 4" : undefined}
                    className={active ? "edge-marching" : undefined}
                    opacity={path.selected ? 1 : active ? 0.85 : 0.55}
                  />
                );
              })}
            </svg>
          )}

          {nodes.map((node, index) => (
            <NodeTile
              key={node.id}
              node={node}
              index={index}
              selected={selectedNodeId === node.id}
              resumeParent={node.kind === "op" ? null : findResumeParent(node, nodeById)}
              onSelect={onSelect}
              tileRef={(el) => {
                if (el) tileRefs.current.set(node.id, el);
                else tileRefs.current.delete(node.id);
              }}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function NodeTile({
  node,
  index,
  selected,
  resumeParent,
  onSelect,
  tileRef,
}: {
  node: NodeInfo;
  index: number;
  selected: boolean;
  resumeParent: NodeInfo | null;
  onSelect: (id: string) => void;
  tileRef: (el: HTMLButtonElement | null) => void;
}) {
  const isOp = node.kind === "op";
  const meta = stateMeta(node.state);
  const kindLabel = isOp && node.op_kind ? `op · ${node.op_kind}` : node.kind;
  const body = isOp
    ? node.summary || "(running)"
    : node.summary || node.prompt || "(empty prompt)";

  return (
    <div
      className={
        "relative flex-none rounded-md transition " +
        (selected ? "ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken" : "")
      }
    >
      <button
        type="button"
        ref={tileRef}
        onClick={() => onSelect(node.id)}
        className={
          "group relative z-10 grid h-[104px] grid-rows-[auto_1fr_auto] overflow-hidden rounded-md border border-line text-left transition hover:border-line-strong hover:shadow-card " +
          (isOp ? "w-32" : "w-52") + " " +
          meta.tileBg
        }
      >
        {/* state rail */}
        <span
          className={"pointer-events-none absolute inset-y-0 left-0 w-[3px] " + meta.railBg}
          aria-hidden="true"
        />

        {/* tile body */}
        <div className="flex items-center justify-between gap-2 pl-3.5 pr-2.5 pt-2">
          <StateChip meta={meta} />
          <span className="font-mono text-[10px] text-ink-subtle">
            {index + 1}
            <span className="text-ink-subtle/70"> · {kindLabel}</span>
          </span>
        </div>

        <div className="line-clamp-2 pl-3.5 pr-2.5 pt-1 text-[12.5px] leading-[1.35] text-ink-strong">
          {body}
        </div>

        <div className="flex items-center justify-between gap-2 pb-1.5 pl-3.5 pr-2.5 pt-1.5 text-[10px] text-ink-subtle">
          <span className="font-mono">{node.id.slice(0, 8)}</span>
          <span className="font-mono">{formatNodeTime(node.started_at ?? node.created_at)}</span>
        </div>

        {/* bottom progress / state bar */}
        <span
          className={"pointer-events-none absolute bottom-0 left-0 h-[2px] w-full " + meta.barTrack}
          aria-hidden="true"
        >
          <span
            className={"absolute inset-y-0 " + meta.barFill}
            style={meta.barStyle}
          />
        </span>

        {/* awaiting_review: pulsing ring */}
        {meta.ring && (
          <span
            className="pointer-events-none absolute inset-0 rounded-md review-ring"
            aria-hidden="true"
          />
        )}
      </button>

      {/* resume-parent badge */}
      {resumeParent && (
        <span
          className="pointer-events-none absolute -top-2 left-3 z-20 rounded-full border border-brand/40 bg-surface-raised px-1.5 py-0.5 font-mono text-[9px] text-brand-ink shadow-card"
          title={`Resumed from ${resumeParent.id}`}
        >
          ↻ {resumeParent.id.slice(0, 6)}
        </span>
      )}
    </div>
  );
}

type StateMeta = {
  label: string;
  icon: JSX.Element;
  chipBg: string;
  chipText: string;
  railBg: string;
  tileBg: string;
  barTrack: string;
  barFill: string;
  barStyle: React.CSSProperties;
  ring: boolean;
};

function stateMeta(state: NodeState): StateMeta {
  switch (state) {
    case "queued":
      return {
        label: "queued",
        icon: <DotIcon />,
        chipBg: "bg-state-queued-soft",
        chipText: "text-ink-muted",
        railBg: "bg-state-queued",
        tileBg: "bg-surface-raised",
        barTrack: "bg-transparent",
        barFill: "w-0 bg-state-queued",
        barStyle: {},
        ring: false,
      };
    case "running":
      return {
        label: "running",
        icon: <DotPulseIcon />,
        chipBg: "bg-state-running-soft",
        chipText: "text-brand-ink dark:text-brand",
        railBg: "bg-state-running",
        tileBg: "bg-state-running-soft/40",
        barTrack: "bg-state-running-soft",
        barFill: "node-sweep w-1/3 bg-gradient-to-r from-transparent via-state-running to-transparent",
        barStyle: {},
        ring: false,
      };
    case "waiting":
      return {
        label: "waiting",
        icon: <HourglassIcon />,
        chipBg: "bg-state-waiting-soft",
        chipText: "text-state-waiting dark:text-state-waiting",
        railBg: "bg-state-waiting pulse-slow",
        tileBg: "bg-state-waiting-soft/35",
        barTrack: "bg-state-waiting-soft",
        barFill: "w-1/2 bg-state-waiting/70 pulse-slow",
        barStyle: {},
        ring: false,
      };
    case "awaiting_review":
      return {
        label: "review",
        icon: <RingIcon />,
        chipBg: "bg-state-review-soft",
        chipText: "text-state-review dark:text-state-review",
        railBg: "bg-state-review",
        tileBg: "bg-state-review-soft/35",
        barTrack: "bg-state-review-soft",
        barFill: "w-full bg-state-review/55 pulse-slow",
        barStyle: {},
        ring: true,
      };
    case "done":
      return {
        label: "done",
        icon: <CheckIcon />,
        chipBg: "bg-state-done-soft",
        chipText: "text-ink-muted",
        railBg: "bg-state-done",
        tileBg: "bg-surface-raised",
        barTrack: "bg-transparent",
        barFill: "w-full bg-state-done/40",
        barStyle: {},
        ring: false,
      };
    case "error":
      return {
        label: "error",
        icon: <CrossIcon />,
        chipBg: "bg-state-error-soft",
        chipText: "text-state-error",
        railBg: "bg-state-error",
        tileBg: "bg-state-error-soft/35",
        barTrack: "bg-transparent",
        barFill: "w-full bg-state-error/55",
        barStyle: {},
        ring: false,
      };
    case "cancelled":
      return {
        label: "cancelled",
        icon: <SlashIcon />,
        chipBg: "bg-state-cancelled-soft",
        chipText: "text-ink-subtle",
        railBg: "bg-state-cancelled",
        tileBg: "bg-surface-raised",
        barTrack: "bg-transparent",
        barFill: "w-full bg-state-cancelled/40",
        barStyle: {},
        ring: false,
      };
    default:
      return stateMeta("queued");
  }
}

function StateChip({ meta }: { meta: StateMeta }) {
  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] " +
        meta.chipBg + " " + meta.chipText
      }
    >
      <span className="inline-flex h-2 w-2 items-center justify-center">
        {meta.icon}
      </span>
      {meta.label}
    </span>
  );
}

function edgeStroke(state: NodeState): string {
  switch (state) {
    case "running":
      return "rgb(var(--state-running))";
    case "waiting":
      return "rgb(var(--state-waiting))";
    case "awaiting_review":
      return "rgb(var(--state-review))";
    case "error":
      return "rgb(var(--state-error))";
    default:
      return "rgb(var(--border-strong))";
  }
}

/* ───────── icons ───────── */

function DotIcon() {
  return <span className="block h-1.5 w-1.5 rounded-full bg-current" />;
}

function DotPulseIcon() {
  return (
    <span className="relative block h-1.5 w-1.5">
      <span className="absolute inset-0 rounded-full bg-current opacity-40 pulse-slow" />
      <span className="absolute inset-[1px] rounded-full bg-current" />
    </span>
  );
}

function HourglassIcon() {
  return (
    <svg viewBox="0 0 8 8" width="8" height="8" fill="currentColor" aria-hidden="true">
      <path d="M1.5 1h5v.6L4.6 4l1.9 2.4V7h-5v-.6L3.4 4 1.5 1.6V1Z" />
    </svg>
  );
}

function RingIcon() {
  return (
    <svg viewBox="0 0 8 8" width="8" height="8" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <circle cx="4" cy="4" r="2.4" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 8 8" width="8" height="8" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1.4 4.4 3 6l3.6-4" />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg viewBox="0 0 8 8" width="8" height="8" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <path d="M2 2 6 6M6 2 2 6" />
    </svg>
  );
}

function SlashIcon() {
  return (
    <svg viewBox="0 0 8 8" width="8" height="8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
      <path d="M1.5 6.5 6.5 1.5" />
    </svg>
  );
}

/* ───────── helpers ───────── */

function samePaths(a: EdgePath[], b: EdgePath[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (
      a[i].key !== b[i].key ||
      a[i].d !== b[i].d ||
      a[i].selected !== b[i].selected ||
      a[i].childState !== b[i].childState
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

function formatNodeTime(value: number): string {
  return new Date(value * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}
