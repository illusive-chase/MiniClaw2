import { memo } from "react";
import { BaseEdge, getBezierPath, type EdgeProps } from "reactflow";
import type { NodeState } from "../../types";
import { stateStroke } from "../nodes/stateMeta";

type EdgeData = { childState?: NodeState };

const ACTIVE: NodeState[] = ["running", "waiting", "awaiting_review"];

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

/** Thin solid arrow — "this agent wrote this artifact". */
function ProducesEdgeImpl(props: EdgeProps) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, selected } = props;
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.18,
  });
  return (
    <BaseEdge
      path={path}
      style={{
        stroke: selected ? "rgb(var(--brand))" : "rgb(var(--border-strong))",
        strokeWidth: 1.2,
        opacity: 0.7,
      }}
      markerEnd={props.markerEnd}
    />
  );
}

export const ProducesEdge = memo(ProducesEdgeImpl);

/** Solid arrow into a gate — the brief feeds the gate. */
function ReviewsEdgeImpl(props: EdgeProps) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, selected } = props;
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.22,
  });
  return (
    <BaseEdge
      path={path}
      style={{
        stroke: selected ? "rgb(var(--brand))" : "rgb(var(--state-review))",
        strokeWidth: 1.4,
        opacity: 0.85,
      }}
      markerEnd={props.markerEnd}
    />
  );
}

export const ReviewsEdge = memo(ReviewsEdgeImpl);

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
