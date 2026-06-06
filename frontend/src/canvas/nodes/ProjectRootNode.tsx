import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { ProjectRootNodeData } from "../layout";

function ProjectRootNodeImpl({ data }: NodeProps<ProjectRootNodeData>) {
  return (
    <div
      title={`Project · ${data.title}`}
      className="relative flex h-[64px] w-[64px] select-none items-center justify-center rounded-full border border-line bg-surface-raised shadow-card"
    >
      <svg
        viewBox="0 0 24 24"
        width="22"
        height="22"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-ink-muted"
        aria-hidden="true"
      >
        <path d="M3 12 12 4l9 8" />
        <path d="M5 10v9a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-9" />
        <path d="M10 20v-6h4v6" />
      </svg>
      <Handle
        type="source"
        position={Position.Right}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
    </div>
  );
}

export const ProjectRootNode = memo(ProjectRootNodeImpl);
