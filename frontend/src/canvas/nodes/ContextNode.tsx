import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { ContextNodeData } from "../layout";

/**
 * Context card: a "layered/stacked" card. Lives above the timeline lane.
 * One node per distinct file pulled into a context bundle.
 *
 * Skill tiles are the exception: only explicitly selected skills reach the
 * canvas (auto-attached dependencies fold into their root's tile), so a skill
 * renders as a single flat card with an attachment-count badge.
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
    usedByNodeIds,
    attachedSkills,
  } = data;
  const isProject = scope === "project-root";
  const isPrinciple = kind === "principle";
  const isSkill = kind === "skill";
  const displayName = (isPrinciple || isSkill) && title ? title : filename;
  const loadedCount = loadedByNodeIds.length;
  const attachedCount = attachedSkills?.length ?? 0;
  const tooltipLines = [
    kindLabel(scope, kind),
    (isPrinciple || isSkill) && title ? title : null,
    path,
    loadedCount > 0
      ? `${chars} chars · loaded by ${loadedCount} run${loadedCount === 1 ? "" : "s"}`
      : "declared on a pending node",
    isSkill && loadedCount > 0
      ? `used by ${usedByNodeIds?.length ?? 0} run${usedByNodeIds?.length === 1 ? "" : "s"}`
      : null,
    attachedCount > 0
      ? `brings ${attachedCount} attached skill${attachedCount === 1 ? "" : "s"}: ` +
        `${attachedSkills!.slice(0, 8).map((item) => item.title).join(", ")}` +
        `${attachedCount > 8 ? ", …" : ""}`
      : null,
  ].filter(Boolean);

  if (isSkill) {
    return (
      <div
        title={tooltipLines.join("\n")}
        className={
          "relative w-[160px] select-none transition " +
          (selected
            ? "rounded-md ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
            : "rounded-md hover:ring-2 hover:ring-state-review/35 hover:ring-offset-2 hover:ring-offset-surface-sunken")
        }
      >
        <div
          className={
            "relative flex h-[70px] flex-col rounded-md border bg-surface-raised py-1.5 pl-2.5 pr-2 shadow-card " +
            (selected
              ? "border-brand"
              : "border-state-review/45 hover:border-state-review/75")
          }
        >
          <div className="flex items-center gap-1 text-[9px] font-semibold uppercase tracking-[0.14em] text-state-review">
            <BoltGlyph />
            <span>skill</span>
            {chars > 0 && (
              <span className="ml-auto font-mono text-[9px] font-normal normal-case tracking-normal text-ink-subtle">
                {formatChars(chars)}
              </span>
            )}
          </div>
          <div
            className="line-clamp-2 pt-0.5 text-[11px] font-medium leading-tight text-ink-strong"
            title={path}
          >
            {displayName}
          </div>
          <div className="mt-auto flex items-center gap-1.5 text-[9.5px] text-ink-muted">
            {attachedCount > 0 && (
              <span className="inline-flex items-center gap-0.5 rounded-full border border-state-review/40 bg-state-review/10 px-1.5 py-px text-[8.5px] font-semibold leading-tight text-state-review">
                <StackGlyph />
                {attachedCount}
              </span>
            )}
            <span className="truncate">
              {loadedCount > 0 ? `loaded by ${loadedCount}` : "declared"}
            </span>
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

  return (
    <div
      title={tooltipLines.join("\n")}
      className={
        "relative select-none transition " +
        (isProject ? "w-[220px] " : "w-[160px] ") +
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
      <div
        className={
          "relative flex h-[70px] flex-col border pl-2.5 pr-2 py-1.5 " +
          (isProject
            ? "rounded-md border-dashed bg-surface-sunken/60 "
            : "rounded-md bg-surface-raised shadow-card ") +
          (selected ? "border-brand" : "border-line hover:border-line-strong")
        }
      >
        <div className="flex items-center justify-between text-[9px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          <span>{kindLabel(scope, kind)}</span>
          <span className="font-mono text-[9px] normal-case tracking-normal text-ink-subtle">
            {formatChars(chars)}
          </span>
        </div>
        <div
          className="line-clamp-2 pt-0.5 text-[11px] leading-tight text-ink-strong"
          title={path}
        >
          <span className={isPrinciple && title ? "font-medium" : "font-mono"}>
            {displayName}
          </span>
        </div>
        <div className="mt-auto text-[9.5px] text-ink-muted">
          {loadedCount > 0 ? `loaded by ${loadedCount}` : "declared"}
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

function BoltGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-[9px] w-[9px] shrink-0"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z" />
    </svg>
  );
}

function StackGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-[8px] w-[8px] shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m12 3 9 5-9 5-9-5 9-5z" />
      <path d="m3 13.5 9 5 9-5" />
    </svg>
  );
}

function kindLabel(scope: string, kind: string): string {
  /* Read scope/kind as a single plain-language descriptor so users
   * don't need to know the ontology. */
  if (kind === "principle") return "principle";
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
