import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "reactflow";

type CommitEdgeData = {
  externalCount?: number;
  dashed?: boolean;
};

function CommitEdgeImpl(props: EdgeProps<CommitEdgeData>) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    targetX: props.targetX,
    targetY: props.targetY,
    sourcePosition: props.sourcePosition,
    targetPosition: props.targetPosition,
    curvature: 0.2,
  });
  const externalCount = props.data?.externalCount ?? 0;
  const stroke = props.selected
    ? "rgb(var(--brand))"
    : "rgb(var(--border-strong))";
  const gatedOpacity = (props.style as React.CSSProperties | undefined)?.opacity;

  return (
    <>
      <BaseEdge
        path={path}
        style={{
          stroke,
          strokeWidth: props.selected ? 2 : 1.2,
          opacity: props.selected ? 1 : gatedOpacity ?? 0.65,
          strokeDasharray: props.data?.dashed ? "4 4" : undefined,
          transition: gatedOpacity === undefined ? undefined : "opacity 180ms ease",
        }}
      />
      {externalCount > 0 && (
        <EdgeLabelRenderer>
          <span
            className="pointer-events-none absolute rounded-full border border-line bg-surface-raised px-1.5 py-0.5 font-mono text-[9px] text-ink-muted shadow-sm"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
          >
            +{externalCount}
          </span>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const CommitEdge = memo(CommitEdgeImpl);
