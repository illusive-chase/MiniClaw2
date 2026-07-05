import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { ContextNodeData } from "../layout";

/**
 * Context card: a "layered/stacked" card. Lives above the timeline lane.
 * One node per distinct file pulled into a context bundle.
 */
function ContextNodeImpl({ data, selected }: NodeProps<ContextNodeData>) {
  const {
    filename,
    scope,
    kind,
    chars,
    path,
    loadedByNodeIds,
    title,
    dimmed,
    attachedCount,
    plugId,
  } = data;
  const isProject = scope === "project-root";
  const isSkill = kind === "skill";
  const displayName = isSkill && title ? title : filename;
  const loadedCount = loadedByNodeIds.length;
  const preAttached = attachedCount ?? 0;
  const canDragToAttach = isSkill && !!plugId;
  const tooltipLines = [
    kindLabel(scope, kind),
    isSkill && title ? title : null,
    path,
    dimmed
      ? "on the shelf — not loaded by any live node"
      : `${chars} chars · loaded by ${loadedCount} run${loadedCount === 1 ? "" : "s"}`,
    preAttached > 0
      ? `pre-attached on ${preAttached} pending node${preAttached === 1 ? "" : "s"}`
      : null,
    canDragToAttach ? "drag the ⋮⋮ handle onto a virtual to attach" : null,
  ].filter(Boolean);

  return (
    <div
      title={tooltipLines.join("\n")}
      className={
        "relative select-none transition " +
        (isProject ? "w-[220px] " : "w-[160px] ") +
        (dimmed ? "opacity-60 hover:opacity-90 " : "") +
        (selected
          ? "rounded-md ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
          : "rounded-md hover:ring-2 hover:ring-line-strong/45 hover:ring-offset-2 hover:ring-offset-surface-sunken")
      }
    >
      {/* Project-root CONTEXT renders flat (no stacked-card chrome) so it
       * reads as a neutral top stripe rather than a planspace context tile. */}
      {!isProject && (
        <>
          <span
            aria-hidden="true"
            className="absolute -bottom-1 -right-1 h-full w-full rounded-md border border-line/60 bg-surface-sunken"
          />
          <span
            aria-hidden="true"
            className="absolute -bottom-0.5 -right-0.5 h-full w-full rounded-md border border-line/80 bg-surface-raised/80"
          />
        </>
      )}
      {/* Pre-attached count badge — only rendered for skill tiles with at
       * least one virtual/phantom holding this in pending_extra_skills.
       * Positioned outside the card so it doesn't shift the header text. */}
      {isSkill && preAttached > 0 && (
        <span
          aria-label={`attached to ${preAttached} pending node${preAttached === 1 ? "" : "s"}`}
          className="pointer-events-none absolute -top-1.5 -right-1.5 z-10 inline-flex min-w-[16px] items-center justify-center rounded-full border border-line bg-brand px-1 text-[9.5px] font-semibold leading-none text-white shadow-card"
        >
          {preAttached}
        </span>
      )}
      {/* Skill-attach drag handle — an HTML5-DnD source. ``nodrag`` keeps
       * React Flow from starting a node move when the user grabs the
       * handle. The rest of the tile still moves via RF, so users can
       * still reposition the shelf if they want. */}
      {canDragToAttach && (
        <div
          draggable
          className="nodrag absolute -top-1.5 -left-1.5 z-10 inline-flex h-4 w-4 cursor-grab items-center justify-center rounded-full border border-line bg-surface text-[10px] leading-none text-ink-muted shadow-card hover:text-ink active:cursor-grabbing"
          title="Drag onto a virtual node to attach this skill"
          aria-label="Attach skill by dragging onto a virtual node"
          onDragStart={(event) => {
            event.stopPropagation();
            event.dataTransfer.setData("application/x-miniclaw-skill", plugId!);
            event.dataTransfer.effectAllowed = "copy";
          }}
          onMouseDown={(event) => {
            /* Belt-and-braces: prevent RF from grabbing the tile the
             * moment the user starts a drag from the handle. */
            event.stopPropagation();
          }}
        >
          ⋮⋮
        </div>
      )}
      <div
        className={
          "relative flex h-[70px] flex-col border pl-2.5 pr-2 py-1.5 " +
          (isProject
            ? "rounded-md border-dashed bg-surface-sunken/60 "
            : "rounded-md bg-surface-raised shadow-card ") +
          (dimmed ? "border-dashed " : "") +
          (selected ? "border-brand" : "border-line hover:border-line-strong")
        }
      >
        <div className="flex items-center justify-between text-[9px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          <span>{kindLabel(scope, kind)}</span>
          <span className="font-mono text-[9px] normal-case tracking-normal text-ink-subtle">
            {dimmed ? "shelf" : formatChars(chars)}
          </span>
        </div>
        <div
          className="line-clamp-2 pt-0.5 text-[11px] leading-tight text-ink-strong"
          title={path}
        >
          <span className={isSkill && title ? "font-medium" : "font-mono"}>
            {displayName}
          </span>
        </div>
        <div className="mt-auto text-[9.5px] text-ink-muted">
          {dimmed
            ? preAttached > 0
              ? `pre-attached ×${preAttached}`
              : "not loaded"
            : `loaded by ${loadedCount}`}
        </div>
      </div>

      {/* Read (loads) exits from the left. */}
      <Handle
        type="source"
        id="loads"
        position={Position.Left}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
    </div>
  );
}

export const ContextNode = memo(ContextNodeImpl);

function kindLabel(scope: string, kind: string): string {
  /* Read scope/kind as a single plain-language descriptor so users
   * don't need to know the ontology. */
  if (kind === "skill") return "skill";
  if (kind === "planspace") return "project memory";
  if (kind === "memory") return "memory";
  if (kind === "global") return "global";
  if (kind === "binding") return "memory link";
  if (scope === "system") return "system context";
  if (scope === "session") return "session note";
  if (scope === "project") return "project file";
  return kind || scope || "context";
}

function formatChars(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`;
  return String(n);
}
