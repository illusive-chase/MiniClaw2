import { memo } from "react";
import {
  BaseEdge,
  getBezierPath,
  type EdgeProps,
} from "reactflow";
import type { NodeState } from "../../types";
import { stateStroke } from "../nodes/stateMeta";

type EdgeData = {
  childState?: NodeState;
  root?: boolean;
  overlapsContinue?: boolean;
  dashed?: boolean;
  relation?: "available" | "used";
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
  return (
    <BaseEdge
      path={path}
      style={{
        stroke: selected ? "rgb(var(--brand))" : "rgb(var(--ink-subtle))",
        strokeWidth: 1.1,
        strokeDasharray: data?.dashed ? "4 4" : undefined,
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
