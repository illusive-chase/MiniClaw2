import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "reactflow";
import type { NodeInfo, NodeState } from "../../types";
import { stateStroke } from "../nodes/stateMeta";

type EdgeData = {
  childState?: NodeState;
  root?: boolean;
  overlapsContinue?: boolean;
};

const ACTIVE: NodeState[] = [
  "running",
  "waiting",
  "awaiting_human_input",
];

/** Solid spine — FS ordering between adjacent timeline nodes. */
function TimelineEdgeImpl(props: EdgeProps<EdgeData>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected } =
    props;
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.2,
  });
  const state = data?.childState;
  const isActive = state ? ACTIVE.includes(state) : false;
  const stroke = selected
    ? "rgb(var(--brand))"
    : state
      ? stateStroke(state)
      : "rgb(var(--border-strong))";

  return (
    <BaseEdge
      path={path}
      style={{
        stroke,
        strokeWidth: selected ? 2.2 : isActive ? 1.8 : 1.4,
        opacity: selected ? 1 : isActive ? 0.9 : 0.55,
        strokeDasharray: isActive ? "3 4" : undefined,
        animation: isActive
          ? "edge-march 0.9s linear infinite"
          : undefined,
      }}
      markerEnd={props.markerEnd}
    />
  );
}

export const TimelineEdge = memo(TimelineEdgeImpl);

/** Primary DAG arrow — template/planning dependency from scheduled_deps. */
function DependencyEdgeImpl(props: EdgeProps<EdgeData>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected } =
    props;
  const offsetY = data?.overlapsContinue ? 14 : 0;
  const [path] = getBezierPath({
    sourceX,
    sourceY: sourceY + offsetY,
    targetX,
    targetY: targetY + offsetY,
    sourcePosition,
    targetPosition,
    curvature: data?.root ? 0.18 : 0.24,
  });
  const state = data?.childState;
  const isActive = state ? ACTIVE.includes(state) : false;
  const stroke = selected
    ? "rgb(var(--brand))"
    : data?.root
      ? "rgb(var(--border-strong))"
      : "rgb(var(--state-review))";

  return (
    <BaseEdge
      path={path}
      style={{
        stroke,
        strokeWidth: selected ? 2.3 : isActive ? 1.9 : data?.root ? 1.35 : 1.7,
        opacity: selected ? 1 : isActive ? 0.95 : data?.root ? 0.6 : 0.82,
        strokeDasharray: isActive ? "3 4" : undefined,
        animation: isActive ? "edge-march 0.9s linear infinite" : undefined,
      }}
      markerEnd={props.markerEnd}
    />
  );
}

export const DependencyEdge = memo(DependencyEdgeImpl);

/** Solid arrow with ↻ glyph mid-edge — provider conversation continuation. */
function ResumeEdgeImpl(props: EdgeProps<EdgeData>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected } =
    props;
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.25,
  });
  const state = data?.childState;
  const isActive = state ? ACTIVE.includes(state) : false;
  const stroke = selected
    ? "rgb(var(--brand))"
    : state
      ? stateStroke(state)
      : "rgb(var(--border-strong))";

  return (
    <>
      <BaseEdge
        path={path}
        style={{
          stroke,
          strokeWidth: selected ? 2.2 : isActive ? 1.8 : 1.3,
          opacity: selected ? 1 : isActive ? 0.9 : 0.6,
          strokeDasharray: isActive ? "3 4" : undefined,
          animation: isActive
            ? "edge-march 0.9s linear infinite"
            : undefined,
        }}
        markerEnd={props.markerEnd}
      />
      <g transform={`translate(${labelX - 8} ${labelY - 8})`}>
        <rect
          width="16"
          height="16"
          rx="8"
          fill="rgb(var(--surface-raised))"
          stroke={stroke}
          strokeWidth="1.2"
        />
        <text
          x="8"
          y="11.5"
          textAnchor="middle"
          fontSize="10"
          fill={stroke}
          fontFamily="'JetBrains Mono', monospace"
        >
          ↻
        </text>
      </g>
    </>
  );
}

export const ResumeEdge = memo(ResumeEdgeImpl);

/** Dashed — acausal carryover; auto-hidden unless endpoint hovered/selected. */
function LoadsEdgeImpl(props: EdgeProps) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, selected, style } =
    props;
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.4,
  });
  const visible = (style as React.CSSProperties | undefined)?.opacity ?? 0;
  return (
    <BaseEdge
      path={path}
      style={{
        stroke: selected ? "rgb(var(--brand))" : "rgb(var(--ink-subtle))",
        strokeWidth: 1.1,
        strokeDasharray: "4 4",
        opacity: visible,
        transition: "opacity 180ms ease",
      }}
    />
  );
}

export const LoadsEdge = memo(LoadsEdgeImpl);

/* ───────── op chevron ─────────
 *
 * Edge type that *carries* an op: a timeline transition from one timeline node
 * to the next, with a clickable chevron mid-path showing the op's commit hash.
 * Click → selects the op (parent state shows OpPanel). Hover → reveals the SHA
 * and a short summary; the side panel still owns the full diff view.
 */

type OpEdgeData = {
  childState?: NodeState;
  op: NodeInfo;
  /** true when the op's id matches the selected node id */
  opSelected?: boolean;
};

export type OpChevronContext = {
  onSelectOp: (nodeId: string) => void;
};

let opChevronContext: OpChevronContext = {
  onSelectOp: () => {},
};

export function setOpChevronContext(ctx: OpChevronContext): void {
  opChevronContext = ctx;
}

function OpChevronEdgeImpl(props: EdgeProps<OpEdgeData>) {
  const {
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    data,
    markerEnd,
  } = props;
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.2,
  });
  const state = data?.childState;
  const isActive = state ? ACTIVE.includes(state) : false;
  const op = data?.op;
  const selected = data?.opSelected ?? false;
  const stroke = selected
    ? "rgb(var(--brand))"
    : state
      ? stateStroke(state)
      : "rgb(var(--border-strong))";
  const sha = (op?.commit_after ?? op?.commit_before ?? "").slice(0, 7);
  const summary = (op?.summary || "").replace(/\s+/g, " ").trim();
  const opKind = op?.op_kind ?? "op";

  return (
    <>
      <BaseEdge
        path={path}
        style={{
          stroke,
          strokeWidth: selected ? 2.2 : isActive ? 1.8 : 1.4,
          opacity: selected ? 1 : isActive ? 0.9 : 0.6,
          strokeDasharray: isActive ? "3 4" : undefined,
          animation: isActive ? "edge-march 0.9s linear infinite" : undefined,
        }}
        markerEnd={markerEnd}
      />
      <EdgeLabelRenderer>
        <div
          className="nodrag nopan absolute"
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            pointerEvents: "all",
          }}
          title={`${opKind}${sha ? ` · ${sha}` : ""}${summary ? `\n${summary}` : ""}`}
        >
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (op) opChevronContext.onSelectOp(op.id);
            }}
            className={
              "group inline-flex h-[22px] items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.1em] shadow-card transition " +
              (selected
                ? "border-brand bg-brand-soft text-brand-ink"
                : "border-line bg-surface-raised text-ink-muted hover:border-line-strong hover:text-ink")
            }
          >
            <ChevronGlyph />
            {sha ? (
              <span className="font-mono normal-case tracking-normal">{sha}</span>
            ) : (
              <span className="normal-case tracking-normal">{opKind}</span>
            )}
          </button>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

export const OpChevronEdge = memo(OpChevronEdgeImpl);

function ChevronGlyph() {
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
      <path d="M2.5 1.5 5.5 4 2.5 6.5" />
    </svg>
  );
}
