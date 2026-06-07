import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { ArtifactNodeData } from "../layout";

/**
 * Artifact card: a document with a folded corner.
 * Selecting it opens the file content in the side panel.
 */
function ArtifactNodeImpl({ data, selected }: NodeProps<ArtifactNodeData>) {
  const { filename, artifactKind, path } = data;
  const label = labelFor(artifactKind);

  return (
    <div
      title={`${label}\n${path}`}
      className={
        "relative w-[180px] select-none transition " +
        (selected
          ? "rounded ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
          : "rounded hover:ring-2 hover:ring-line-strong/45 hover:ring-offset-2 hover:ring-offset-surface-sunken")
      }
    >
      <div
        className={
          "relative flex h-[90px] flex-col overflow-hidden border bg-surface-raised pl-3 pr-4 pt-2 shadow-card " +
          (selected ? "border-brand" : "border-line hover:border-line-strong")
        }
        style={{ clipPath: DOC_CLIP }}
      >
        {/* folded corner */}
        <span
          aria-hidden="true"
          className="absolute right-0 top-0 h-3.5 w-3.5 border-b border-l border-line bg-surface-sunken"
          style={{ clipPath: "polygon(0 0, 100% 100%, 0 100%)" }}
        />
        <div className="text-[9px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          {label}
        </div>
        <div className="line-clamp-2 pt-1 font-mono text-[11.5px] leading-tight text-ink-strong">
          {filename}
        </div>
        <div className="mt-auto truncate pt-1 text-[10px] text-ink-muted" title={path}>
          {path}
        </div>
      </div>

      <Handle
        type="target"
        position={Position.Top}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      <Handle
        type="target"
        id="produces"
        position={Position.Left}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      <Handle
        type="source"
        id="reviews"
        position={Position.Right}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      <Handle
        type="source"
        id="loads"
        position={Position.Bottom}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
    </div>
  );
}

export const ArtifactNode = memo(ArtifactNodeImpl);

function labelFor(k: ArtifactNodeData["artifactKind"]): string {
  switch (k) {
    case "summary":
      return "result.md";
    case "interface":
      return "result.json";
    case "review_brief":
      return "brief.md";
    case "review_response":
      return "review.json";
    default:
      return "artifact";
  }
}

const DOC_CLIP = "polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 0 100%)";
