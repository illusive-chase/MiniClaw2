import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { InteractionRequest, NodeInfo, NodeState } from "../../types";
import {
  PendingGateInline,
  type ResolveGatePayload,
} from "../../components/PendingGateInline";
import type { AgentNodeData } from "../layout";
import { stateMeta } from "./stateMeta";
import { canResumeNode } from "../../nodeUtil";

/**
 * Agent tile: rounded rectangle, ~224x130. The primary work unit.
 *
 * Color encodes `state`; shape encodes `kind`. Shows a one-line prompt preview
 * plus an active sweep bar when the agent is running. A hover-only
 * right-edge action stack exposes promote, continuation, dependency, and
 * removal affordances when each operation is valid.
 */
function AgentNodeImpl({ data, selected }: NodeProps<AgentNodeData>) {
  const {
    node,
    index,
    resumeParent,
    isActive,
    planspaceColor,
    readyToPromote,
    canCreateVirtual,
  } = data;
  const meta = stateMeta(node.state);
  const pendingGate = agentNodeContext.pendingGateForNode(node.id);
  const headline = oneLine(
    node.summary ||
      node.prompt_draft ||
      node.prompt ||
      (node.state === "virtual" ? "(draft prompt missing)" : "(empty prompt)"),
  );
  const isVirtual = node.state === "virtual";
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removeSaving, setRemoveSaving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [deleteBlockers, setDeleteBlockers] = useState<string[] | null>(null);
  const removeRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!removeOpen) return;
    const handler = (event: MouseEvent) => {
      if (removeRef.current && !removeRef.current.contains(event.target as Node)) {
        setRemoveOpen(false);
        setRemoveError(null);
        setDeleteBlockers(null);
      }
    };
    window.addEventListener("mousedown", handler);
    return () => window.removeEventListener("mousedown", handler);
  }, [removeOpen]);

  useEffect(() => {
    if (!selected || !isVirtual || !agentNodeContext.canCreateVirtual) return;
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
      ) {
        return;
      }
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      event.preventDefault();
      setRemoveOpen(true);
      setRemoveError(null);
      setDeleteBlockers(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [selected, isVirtual]);

  const actionItems = useMemo(() => {
    const items: Array<{
      key: "promote" | "continuation" | "dependency" | "remove" | "interrupt" | "rerun";
      icon: ReactNode;
      title: string;
      disabled: boolean;
      tone: "brand" | "neutral" | "danger";
      alwaysVisible?: boolean;
      onClick: () => void;
    }> = [];
    if (isActiveState(node.state)) {
      items.push({
        key: "interrupt",
        icon: <StopActionIcon />,
        title: "Interrupt this running node",
        disabled: !agentNodeContext.canInterrupt,
        tone: "danger",
        alwaysVisible: true,
        onClick: () => agentNodeContext.onInterruptNode(node.id),
      });
    }
    if (
      !isVirtual &&
      node.kind === "agent" &&
      (node.state === "error" || node.state === "cancelled")
    ) {
      items.push({
        key: "rerun",
        icon: <ActionGlyph>↻</ActionGlyph>,
        title: "Rerun - fresh virtual with the same prompt",
        disabled: !agentNodeContext.canRerun,
        tone: "brand",
        onClick: () => agentNodeContext.onRerunNode(node.id),
      });
    }
    if (isVirtual && readyToPromote && !node.obsolete_reason) {
      items.push({
        key: "promote",
        icon: <PromoteActionIcon />,
        title: "Promote - run this virtual",
        disabled: !agentNodeContext.canPromoteVirtual,
        tone: "brand",
        onClick: () => agentNodeContext.onPromoteVirtual(node.id),
      });
    }
    if (!isVirtual && isTerminal(node.state) && canResumeNode(node)) {
      items.push({
        key: "continuation",
        icon: <ActionGlyph>↪</ActionGlyph>,
        title: "Continuation - new virtual that resumes this conversation",
        disabled: !canCreateVirtual || !agentNodeContext.canCreateVirtual,
        tone: "neutral",
        onClick: () => agentNodeContext.onCreateContinuationVirtual(node.id),
      });
    }
    if (isVirtual || (!isVirtual && isTerminal(node.state))) {
      items.push({
        key: "dependency",
        icon: <ActionGlyph>↘</ActionGlyph>,
        title: "Dependency - new virtual that waits for this",
        disabled: !canCreateVirtual || !agentNodeContext.canCreateVirtual,
        tone: "neutral",
        onClick: () => agentNodeContext.onCreateDependencyVirtual(node.id),
      });
    }
    if (isVirtual) {
      items.push({
        key: "remove",
        icon: <ActionGlyph>×</ActionGlyph>,
        title: "Remove",
        disabled: removeSaving || !agentNodeContext.canCreateVirtual,
        tone: "danger",
        onClick: () => {
          setRemoveOpen((open) => !open);
          setRemoveError(null);
          setDeleteBlockers(null);
        },
      });
    }
    return items;
  }, [
    canCreateVirtual,
    isVirtual,
    node,
    readyToPromote,
    removeSaving,
  ]);

  const removeIndex = actionItems.findIndex((item) => item.key === "remove");
  const removeTop = stackTop(removeIndex, actionItems.length);

  const markObsolete = async () => {
    setRemoveSaving(true);
    setRemoveError(null);
    try {
      await agentNodeContext.onMarkVirtualObsolete(node.id);
      setRemoveOpen(false);
      setDeleteBlockers(null);
    } catch (err) {
      setRemoveError(errorMessage(err));
    } finally {
      setRemoveSaving(false);
    }
  };

  const hardDelete = async () => {
    setRemoveSaving(true);
    setRemoveError(null);
    setDeleteBlockers(null);
    try {
      await agentNodeContext.onDeleteVirtual(node.id);
      setRemoveOpen(false);
    } catch (err) {
      const blockers = blockersFromError(err);
      if (blockers.length > 0) {
        setDeleteBlockers(blockers);
      } else {
        setRemoveError(errorMessage(err));
      }
    } finally {
      setRemoveSaving(false);
    }
  };

  return (
    <div className="group relative w-[224px]" title={tooltipForAgent(node, isActive)}>
      {/* `relative` here so the rail/bar (absolute children below) treat THIS
       * div as their containing block — otherwise they'd anchor to the outer
       * `group relative` ancestor and escape this div's `overflow-hidden`,
       * extending past the rounded corners. */}
      <div
        className={
          "relative select-none overflow-hidden rounded-lg border text-left shadow-card transition " +
          (selected
            ? "border-brand ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
            : isVirtual
              ? "border-dashed border-line-strong hover:border-brand hover:ring-2 hover:ring-brand/20 hover:ring-offset-2 hover:ring-offset-surface-sunken hover:shadow-raised"
              : "border-line hover:border-line-strong hover:ring-2 hover:ring-line-strong/45 hover:ring-offset-2 hover:ring-offset-surface-sunken hover:shadow-raised") +
          " " +
          meta.tileBg
        }
      >
      {/* state rail */}
      <span
        className={
          "pointer-events-none absolute inset-y-0 left-0 w-[3px] " +
          (planspaceColor ? "" : meta.railBg)
        }
        style={planspaceColor ? { background: planspaceColor.accent } : undefined}
        aria-hidden="true"
      />

      {/* header row */}
      <div className="flex items-center justify-between gap-2 pl-3.5 pr-2.5 pt-2">
        <div className="flex min-w-0 items-center gap-1">
          <StateChip state={node.state} />
          <CategoryChip node={node} />
        </div>
        <span className="font-mono text-[10px] text-ink-subtle">
          {index + 1}
          <span className="text-ink-subtle/70"> · {isVirtual ? "plan" : "run"}</span>
        </span>
      </div>

      {/* body — prompt preview */}
      <div className="line-clamp-3 px-3.5 pt-1.5 text-[12.5px] leading-[1.38] text-ink-strong">
        {headline}
      </div>

      {/* footer */}
      <div className="flex items-center justify-between gap-2 px-3.5 pb-1.5 pt-2 text-[10px] text-ink-subtle">
        <span className="flex min-w-0 items-center gap-1">
          <span className="font-mono">{node.id.slice(0, 8)}</span>
          <span className="rounded border border-line bg-surface/70 px-1 py-0.5 text-[8.5px] font-medium uppercase tracking-[0.1em] text-ink-muted">
            {providerLabel(node.provider)}
          </span>
        </span>
        {isVirtual ? (
          <span className={readyToPromote ? "font-mono text-brand" : "font-mono text-ink-muted"}>
            {node.obsolete_reason ? "obsolete" : readyToPromote ? "ready" : `${node.scheduled_deps?.length ?? 0} deps`}
          </span>
        ) : node.usage ? (
          <span className="font-mono text-ink-muted">
            ↑{compactTokens(node.usage.input_tokens)} ↓
            {compactTokens(node.usage.output_tokens)}
          </span>
        ) : (
          <span className="font-mono">{formatStartTime(node)}</span>
        )}
      </div>

      {/* state bar */}
      <span
        className={"pointer-events-none absolute bottom-0 left-0 h-[2px] w-full " + meta.barTrack}
        aria-hidden="true"
      >
        <span className={"absolute inset-y-0 " + meta.barFill} />
      </span>

      {/* human-input halo */}
      {meta.ring && (
        <span
          className="pointer-events-none absolute inset-0 rounded-lg review-ring"
          aria-hidden="true"
        />
      )}

      {/* live runner dot */}
      {isActive && (
        <span
          className="pointer-events-none absolute right-2 top-2 inline-block h-1.5 w-1.5 rounded-full bg-state-running shadow-[0_0_0_3px_rgb(var(--state-running)/0.25)]"
          aria-hidden="true"
        />
      )}

      {/* react-flow handles */}
      <Handle
        type="target"
        position={Position.Left}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      <Handle
        type="source"
        id="produces"
        position={Position.Bottom}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      <Handle
        type="target"
        id="loads"
        position={Position.Top}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      </div>

      {pendingGate && (
        <div
          className="nodrag absolute left-0 top-[calc(100%+8px)] z-30 w-[300px] rounded-md border border-state-waiting/45 bg-surface-raised p-2 shadow-modal"
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-state-waiting">
              Waiting for response
            </span>
            <span className="font-mono text-[9px] text-ink-subtle">
              {pendingGate.interaction_type}
            </span>
          </div>
          <PendingGateInline
            node={node}
            pending={pendingGate}
            compact
            onResolve={(payload) => agentNodeContext.onResolveGate(pendingGate.id, payload)}
          />
        </div>
      )}

      {/* resumed-from badge — sits OUTSIDE the inner overflow-hidden div so
       * `-top-2` pokes above the tile instead of being clipped. */}
      {resumeParent && (
        <span
          className="pointer-events-none absolute -top-2 left-3 rounded-full border border-brand/40 bg-surface-raised px-1.5 py-0.5 font-mono text-[9px] text-brand-ink shadow-card"
          title={`Resumed from ${resumeParent.id}`}
        >
          ↻ {resumeParent.id.slice(0, 6)}
        </span>
      )}

      {actionItems.map((item, actionIndex) => (
        <button
          key={item.key}
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            if (!item.disabled) item.onClick();
          }}
          onMouseDown={(e) => e.stopPropagation()}
          disabled={item.disabled}
          style={{ top: stackTop(actionIndex, actionItems.length) }}
          className={actionButtonClass(item.tone, item.alwaysVisible)}
          title={item.title}
          aria-label={item.title}
        >
          {item.icon}
        </button>
      ))}

      {removeOpen && removeIndex >= 0 && (
        <div
          ref={removeRef}
          className="nodrag absolute -right-[178px] z-40 w-40 -translate-y-1/2 rounded-md border border-line bg-surface-raised p-1 shadow-modal"
          style={{ top: removeTop }}
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
        >
          {deleteBlockers ? (
            <div className="space-y-1.5 p-1">
              <div className="break-words text-[11px] leading-snug text-state-error">
                Blocked by: {deleteBlockers.join(", ")}
              </div>
              <PopoverButton
                disabled={removeSaving}
                onClick={markObsolete}
              >
                Mark obsolete instead
              </PopoverButton>
            </div>
          ) : (
            <>
              <PopoverButton disabled={removeSaving} onClick={markObsolete}>
                Mark obsolete
              </PopoverButton>
              <PopoverButton
                disabled={removeSaving}
                danger
                onClick={hardDelete}
              >
                Delete
              </PopoverButton>
              {removeError && (
                <div className="px-2 py-1 text-[10.5px] leading-snug text-state-error">
                  {removeError}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export const AgentNode = memo(AgentNodeImpl);

/* Module-level singleton: App.tsx writes active action callbacks here so the
 * memoized AgentNode always reads the latest handlers without stale closures. */
export type AgentNodeContext = {
  onPromoteVirtual: (nodeId: string) => void;
  onCreateContinuationVirtual: (nodeId: string) => void;
  onCreateDependencyVirtual: (nodeId: string) => void;
  onMarkVirtualObsolete: (nodeId: string) => Promise<void>;
  onDeleteVirtual: (nodeId: string) => Promise<void>;
  onInterruptNode: (nodeId: string) => void;
  onRerunNode: (nodeId: string) => void;
  canCreateVirtual: boolean;
  canPromoteVirtual: boolean;
  canInterrupt: boolean;
  canRerun: boolean;
  pendingGateForNode: (nodeId: string) => InteractionRequest | null;
  onResolveGate: (id: string, payload: ResolveGatePayload) => void;
};

let agentNodeContext: AgentNodeContext = {
  onPromoteVirtual: () => {},
  onCreateContinuationVirtual: () => {},
  onCreateDependencyVirtual: () => {},
  onMarkVirtualObsolete: async () => {},
  onDeleteVirtual: async () => {},
  onInterruptNode: () => {},
  onRerunNode: () => {},
  canCreateVirtual: false,
  canPromoteVirtual: false,
  canInterrupt: false,
  canRerun: false,
  pendingGateForNode: () => null,
  onResolveGate: () => {},
};

export function setAgentNodeContext(ctx: AgentNodeContext): void {
  agentNodeContext = ctx;
}

function stackTop(index: number, total: number): string {
  if (index < 0) return "50%";
  const step = 30;
  const offset = (index - (total - 1) / 2) * step;
  return `calc(50% + ${offset}px)`;
}

function actionButtonClass(
  tone: "brand" | "neutral" | "danger",
  alwaysVisible?: boolean,
): string {
  const toneClass =
    tone === "brand"
      ? "border-brand/45 bg-surface-raised text-brand hover:border-brand hover:bg-brand-soft"
      : tone === "danger"
        ? "border-state-error/50 bg-surface-raised text-state-error hover:border-state-error hover:bg-state-error-soft"
        : "border-line-strong bg-surface-raised text-ink-muted hover:border-brand/55 hover:bg-brand-soft hover:text-brand";
  const visibility = alwaysVisible
    ? "opacity-100 disabled:opacity-45"
    : "opacity-0 group-hover:opacity-100 hover:opacity-100 disabled:opacity-0 group-hover:disabled:opacity-45";
  return (
    "nodrag absolute -right-3 z-20 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full border text-[12px] font-semibold leading-none shadow-card transition disabled:cursor-not-allowed disabled:border-line disabled:bg-surface-sunken disabled:text-ink-subtle " +
    visibility +
    " " +
    toneClass
  );
}

function ActionGlyph({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-3.5 w-3.5 items-center justify-center leading-none">
      {children}
    </span>
  );
}

function StopActionIcon() {
  return (
    <svg
      viewBox="0 0 14 14"
      width="14"
      height="14"
      fill="currentColor"
      aria-hidden="true"
    >
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.2" />
    </svg>
  );
}

function PromoteActionIcon() {
  return (
    <svg
      viewBox="0 0 14 14"
      width="14"
      height="14"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M4.5 3 10.5 7 4.5 11Z" />
    </svg>
  );
}

function PopoverButton({
  children,
  disabled,
  danger,
  onClick,
}: {
  children: ReactNode;
  disabled?: boolean;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => {
        if (!disabled) onClick();
      }}
      className={
        "block w-full rounded px-2 py-1.5 text-left text-[11.5px] transition disabled:cursor-not-allowed disabled:opacity-45 " +
        (danger
          ? "text-state-error hover:bg-state-error-soft"
          : "text-ink hover:bg-surface-sunken")
      }
    >
      {children}
    </button>
  );
}

function blockersFromError(err: unknown): string[] {
  if (!err || typeof err !== "object") return [];
  const blockers = (err as { blockers?: unknown }).blockers;
  return Array.isArray(blockers)
    ? blockers.filter((value): value is string => typeof value === "string")
    : [];
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function isTerminal(state: NodeInfo["state"]): boolean {
  return state === "done" || state === "error" || state === "cancelled";
}

function isActiveState(state: NodeInfo["state"]): boolean {
  return (
    state === "running" ||
    state === "waiting" ||
    state === "awaiting_human_input"
  );
}

function StateChip({ state }: { state: NodeState }) {
  const meta = stateMeta(state);
  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] " +
        meta.chipBg +
        " " +
        meta.chipText
      }
    >
      <span className="inline-flex h-2 w-2 items-center justify-center">
        <meta.Icon />
      </span>
      {meta.label}
    </span>
  );
}

function CategoryChip({ node }: { node: NodeInfo }) {
  const label =
    node.kind === "verifier"
      ? "verify"
      : node.category === "planning"
      ? "plan"
      : node.category === "review"
        ? node.subtype === "human_interact_review"
          ? "human"
          : "review"
        : "work";
  const tone =
    node.category === "planning"
      ? "border-brand/30 bg-brand-soft text-brand-ink"
      : node.category === "review"
        ? "border-state-review/30 bg-state-review-soft text-state-review"
        : "border-line bg-surface text-ink-muted";
  return (
    <span
      className={"inline-flex items-center rounded border px-1 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] " + tone}
      title={node.subtype ?? node.category ?? "regular"}
    >
      {label}
    </span>
  );
}

function oneLine(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

function compactTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`;
  return String(n);
}

function providerLabel(provider: NodeInfo["provider"]): string {
  return provider === "codex" ? "codex" : "claude";
}

function formatStartTime(node: NodeInfo): string {
  const at = node.started_at ?? node.created_at;
  return new Date(at * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function tooltipForAgent(node: NodeInfo, isActive: boolean): string {
  const promptText = node.prompt_draft || node.prompt;
  const prompt = promptText ? `"${promptText.slice(0, 80)}"` : "(no prompt)";
  const status = isActive ? " · active" : "";
  const category = node.category ? ` · ${node.category}` : "";
  return `Agent ${node.state}${category} · ${node.provider}${status}\n${prompt}\n${node.id}`;
}
