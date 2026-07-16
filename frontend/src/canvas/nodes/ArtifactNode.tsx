import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";

import type { ArtifactNodeData } from "../layout";

function ArtifactNodeImpl({ data, selected }: NodeProps<ArtifactNodeData>) {
  const { artifact, overflowCount } = data;
  const label = artifact ? artifact.name.split(".").pop()?.toUpperCase() : "FILES";
  const title = artifact
    ? `${artifact.name}\n${formatBytes(artifact.bytes)}`
    : `${overflowCount} more published artifacts`;

  return (
    <div
      title={title}
      className={
        "relative w-[160px] select-none transition " +
        (selected
          ? "rounded-md ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
          : "rounded-md hover:ring-2 hover:ring-line-strong/45 hover:ring-offset-2 hover:ring-offset-surface-sunken")
      }
    >
      <div
        className={
          "relative flex h-[70px] flex-col rounded-md border border-dashed bg-surface-sunken/60 px-2.5 py-1.5 transition " +
          (selected ? "border-brand" : "border-line hover:border-line-strong")
        }
      >
        <div className="text-[9px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          {label || "FILE"}
        </div>
        <div className="line-clamp-2 pt-0.5 font-mono text-[11px] leading-tight text-ink-strong">
          {artifact?.name ?? `+${overflowCount} more`}
        </div>
        <div className="mt-auto text-[9.5px] text-ink-muted">
          {artifact ? formatBytes(artifact.bytes) : "open artifact list"}
        </div>
      </div>
      <Handle
        type="target"
        id="produces"
        position={Position.Top}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
    </div>
  );
}

export const ArtifactNode = memo(ArtifactNodeImpl);

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(bytes >= 10 * 1024 ? 0 : 1)} KiB`;
  return `${bytes} B`;
}
