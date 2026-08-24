import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "reactflow";
import type { NodeState } from "../../types";
import { stateStroke } from "../nodes/stateMeta";

type EdgeData = {
  childState?: NodeState;
  root?: boolean;
  overlapsContinue?: boolean;
  relation?: "available" | "used" | "declared";
  /** Set by the canvas on the one dependency edge the user clicked, to offer
   * withdrawing it. `confirming` is the second step of that gesture. */
  disconnect?: {
    confirming: boolean;
    onRequest: () => void;
    onConfirm: () => void;
    onCancel: () => void;
  };
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
    />
  );
}

export const TimelineEdge = memo(TimelineEdgeImpl);

/* Width of the invisible strip along a dependency edge that accepts a click.
 *
 * React Flow defaults to 20px and puts `nopan` on every edge, so this strip is
 * also where right-drag stops panning. 10px still beats a 1.7px stroke as a
 * target while halving that cost. */
const DEPENDENCY_INTERACTION_WIDTH = 10;

/** Primary DAG arrow — template/planning dependency from scheduled_deps. */
function DependencyEdgeImpl(props: EdgeProps<EdgeData>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected } =
    props;
  const offsetY = data?.overlapsContinue ? 14 : 0;
  const [path, labelX, labelY] = getBezierPath({
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
  const disconnect = data?.disconnect;
  const stroke = disconnect
    ? "rgb(var(--state-error))"
    : selected
      ? "rgb(var(--brand))"
      : data?.root
        ? "rgb(var(--border-strong))"
        : "rgb(var(--state-review))";

  return (
    <>
      <BaseEdge
        path={path}
        interactionWidth={DEPENDENCY_INTERACTION_WIDTH}
        style={{
          stroke,
          strokeWidth: disconnect || selected ? 2.3 : isActive ? 1.9 : data?.root ? 1.35 : 1.7,
          opacity: disconnect || selected ? 1 : isActive ? 0.95 : data?.root ? 0.6 : 0.82,
          strokeDasharray: isActive ? "3 4" : undefined,
          animation: isActive ? "edge-march 0.9s linear infinite" : undefined,
        }}
        markerEnd={props.markerEnd}
      />
      {disconnect && (
        <EdgeLabelRenderer>
          <DisconnectControl
            x={labelX}
            y={labelY + offsetY}
            confirming={disconnect.confirming}
            onRequest={disconnect.onRequest}
            onConfirm={disconnect.onConfirm}
            onCancel={disconnect.onCancel}
          />
        </EdgeLabelRenderer>
      )}
    </>
  );
}

/* The withdraw affordance for one dependency edge.
 *
 * Two things it must not do. It must not carry `nopan`, and it must not swallow
 * a non-primary mousedown: right-drag is this canvas's main pan gesture and it
 * reaches d3-zoom by bubbling, so either one would turn this button into another
 * patch where panning dies.
 *
 * Note this control is genuinely free of `nopan`, unlike the edge it belongs to.
 * EdgeLabelRenderer portals it into a container outside the edge's own <g>, so
 * the class React Flow puts on every edge is not an ancestor of it — the pan
 * filter's `closest('.nopan')` finds nothing here.
 *
 * `pointer-events: auto` is required — EdgeLabelRenderer's container sets
 * `pointer-events: none`, so without it the control paints but cannot be
 * clicked. */
function DisconnectControl({
  x,
  y,
  confirming,
  onRequest,
  onConfirm,
  onCancel,
}: {
  x: number;
  y: number;
  confirming: boolean;
  onRequest: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const swallowPrimaryOnly = (event: React.MouseEvent) => {
    if (event.button !== 0) return;
    event.stopPropagation();
  };

  return (
    <div
      className="nodrag absolute flex items-center gap-1"
      style={{
        transform: `translate(-50%, -50%) translate(${x}px, ${y}px)`,
        pointerEvents: "auto",
      }}
      onMouseDown={swallowPrimaryOnly}
      onClick={swallowPrimaryOnly}
    >
      {confirming ? (
        <>
          <button
            type="button"
            onClick={onConfirm}
            title="确认断开这条依赖"
            className="rounded-full border border-state-error/60 bg-state-error-soft px-2 py-0.5 text-[10px] font-medium text-state-error shadow-card transition hover:border-state-error"
          >
            断开?
          </button>
          <button
            type="button"
            onClick={onCancel}
            title="取消"
            className="rounded-full border border-line bg-surface-raised px-1.5 py-0.5 text-[10px] text-ink-muted shadow-card transition hover:border-line-strong hover:text-ink"
          >
            ×
          </button>
        </>
      ) : (
        <button
          type="button"
          onClick={onRequest}
          title="断开这条依赖"
          className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-state-error/55 bg-surface-raised text-[11px] font-semibold leading-none text-state-error shadow-card transition hover:border-state-error hover:bg-state-error-soft"
        >
          ×
        </button>
      )}
    </div>
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

/** Loads are always dashed — a derived, hover-gated relation, never at-rest
 *  structure. Consumption rides the dash *pattern*: a tight dash for context
 *  the run actually consumed, a sparse one for merely declared or
 *  available-but-unused entries. Opacity is left to the gate alone. */
function LoadsEdgeImpl(props: EdgeProps<EdgeData>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, selected, style, data } =
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
  const consumed = data?.relation === "used";
  return (
    <BaseEdge
      path={path}
      style={{
        stroke: selected ? "rgb(var(--brand))" : "rgb(var(--ink-subtle))",
        strokeWidth: 1.1,
        strokeDasharray: consumed ? "5 3" : "2 4",
        opacity: visible,
        transition: "opacity 180ms ease",
      }}
    />
  );
}

export const LoadsEdge = memo(LoadsEdgeImpl);

function ProducesEdgeImpl(props: EdgeProps) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, selected, style } = props;
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.32,
  });
  const visible = (style as React.CSSProperties | undefined)?.opacity ?? 0;
  return (
    <BaseEdge
      path={path}
      style={{
        stroke: selected ? "rgb(var(--brand))" : "rgb(var(--ink-subtle))",
        strokeWidth: selected ? 1.8 : 1.1,
        strokeDasharray: "4 4",
        opacity: selected ? 0.95 : visible,
        transition: "opacity 180ms ease",
      }}
      markerEnd={props.markerEnd}
    />
  );
}

export const ProducesEdge = memo(ProducesEdgeImpl);
