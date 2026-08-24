import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { InteractionRequest, ModelPreset, NodeInfo, NodeState } from "../../types";
import {
  PendingGateInline,
  type ResolveGatePayload,
} from "../../components/PendingGateInline";
import type { AgentNodeData } from "../layout";
import { stateMeta } from "./stateMeta";
import {
  canResumeNode,
  nodeClassificationChipLabel,
  nodeClassificationLabel,
  nodeClassificationTone,
} from "../../nodeUtil";
import { modelPresetDetail, modelPresetLabel, providerLabel } from "../../modelPresets";
import { useNodeInHoverGroup } from "../hoverStore";
import { startWiringDrag, useWiringDrag } from "../wiringDragStore";
import { useLongPressWiring } from "../useLongPressWiring";

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
    resumeParent,
    isActive,
    planspaceColor,
    readyToPromote,
    canCreateVirtual,
    templateArguments,
  } = data;
  const hoveredByGroup = useNodeInHoverGroup(node.id);
  /* While a wire is being pulled, every tile that could receive it says so.
   * The drag carries only its source, so eligibility is decided here from the
   * same rule the drop uses: this tile must be an editable virtual that does
   * not already hold the dependency, and cannot be the source itself. */
  const wiringDrag = useWiringDrag();
  const isWiringSource = wiringDrag?.sourceId === node.id;
  const isWiringCandidate =
    Boolean(wiringDrag) &&
    !isWiringSource &&
    agentNodeContext.canAcceptDependency(wiringDrag?.sourceId ?? "", node.id);
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
    if (
      !selected ||
      !isVirtual ||
      !agentNodeContext.canCreateVirtual ||
      !agentNodeContext.canMutateNode(node.id)
    ) return;
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
  }, [selected, isVirtual, node.id]);

  const actionItems = useMemo(() => {
    const items: ActionItem[] = [];
    if (isActiveState(node.state)) {
      items.push({
        key: "interrupt",
        icon: <StopActionIcon />,
        title: "Interrupt this running node",
        disabled:
          !agentNodeContext.canInterrupt ||
          !agentNodeContext.canMutateNode(node.id),
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
        disabled:
          !agentNodeContext.canRerun ||
          !agentNodeContext.canMutateNode(node.id),
        tone: "brand",
        onClick: () => agentNodeContext.onRerunNode(node.id),
      });
    }
    if (
      isVirtual &&
      readyToPromote &&
      !node.obsolete_reason &&
      node.planspace_id === agentNodeContext.manualPromotionPlanspaceId &&
      agentNodeContext.canMutateNode(node.id)
    ) {
      items.push({
        key: "promote",
        icon: <PromoteActionIcon />,
        title: "Promote - run this virtual",
        disabled: !agentNodeContext.canPromoteVirtual,
        tone: "brand",
        onClick: () => agentNodeContext.onPromoteVirtual(node.id),
      });
    }
    /* Dequeue follows the node's own lane mode, mirroring the backend: a
     * queued node in any manual lane stays dequeueable even when another
     * lane is active. */
    if (
      node.state === "queued" &&
      agentNodeContext.isManualPlanspace(node.planspace_id)
    ) {
      items.push({
        key: "dequeue",
        icon: <DequeueActionIcon />,
        title: "Dequeue - return to editable virtual",
        disabled:
          !agentNodeContext.canDequeue ||
          !agentNodeContext.canMutateNode(node.id),
        tone: "neutral",
        alwaysVisible: true,
        onClick: () => agentNodeContext.onDequeueNode(node.id),
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
        disabled:
          removeSaving ||
          !agentNodeContext.canCreateVirtual ||
          !agentNodeContext.canMutateNode(node.id),
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
    <div
      className="group relative w-[224px]"
      title={tooltipForAgent(node, isActive, agentNodeContext.modelPresets)}
    >
      {/* `relative` here so the rail/bar (absolute children below) treat THIS
       * div as their containing block — otherwise they'd anchor to the outer
       * `group relative` ancestor and escape this div's `overflow-hidden`,
       * extending past the rounded corners. */}
      <div
        className={
          "relative select-none overflow-hidden rounded-lg border text-left shadow-card transition " +
          (isWiringCandidate
            ? "border-brand ring-2 ring-brand/45 ring-offset-2 ring-offset-surface-sunken shadow-raised"
            : selected
            ? "border-brand ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
            : hoveredByGroup
              ? isVirtual
                ? "border-brand ring-2 ring-brand/20 ring-offset-2 ring-offset-surface-sunken shadow-raised"
                : "border-line-strong ring-2 ring-line-strong/45 ring-offset-2 ring-offset-surface-sunken shadow-raised"
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
        <span className="font-mono text-[10px] text-ink-subtle" title={node.id}>
          {node.id.slice(0, 6)}
        </span>
      </div>

      {/* Template argument chips. Only an embedded template session supplies
       * these; every ordinary project leaves the row out entirely. The rfNode
       * height in `layout.ts` grows with every two-chip row — the two must stay
       * in step or lane fitting reads a stale height. */}
      {templateArguments && templateArguments.length > 0 ? (
        <div className="flex flex-wrap gap-1 px-3.5 pt-1.5">
          {templateArguments.map((name) => (
            <span
              key={name}
              className="w-24 min-w-0 truncate rounded border border-brand/40 bg-brand-soft px-1 py-0.5 font-mono text-[8.5px] text-brand"
              title={`模板参数 {{${name}}}`}
            >
              {`{{${name}}}`}
            </span>
          ))}
        </div>
      ) : null}

      {/* body — prompt preview */}
      <div className="line-clamp-3 px-3.5 pt-1.5 text-[12.5px] leading-[1.38] text-ink-strong">
        {headline}
      </div>

      {/* footer */}
      <div className="flex items-center justify-between gap-2 px-3.5 pb-1.5 pt-2 text-[10px] text-ink-subtle">
        <span className="flex min-w-0 items-center gap-1">
          <span
            className="max-w-[92px] truncate rounded border border-line bg-surface/70 px-1 py-0.5 text-[8.5px] font-medium uppercase tracking-[0.1em] text-ink-muted"
            title={
              modelPresetDetail(agentNodeContext.modelPresets, node.model_preset_id) ||
              providerLabel(node.provider)
            }
          >
            {modelPresetLabel(agentNodeContext.modelPresets, node.model_preset_id)}
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

      {/* react-flow handles — all six are programmatic anchors only.
        *
        * Dependencies are wired by long-pressing the "↘" action button, not by
        * dragging a handle, so nothing here needs to be pointer-reachable. Every
        * handle is pinned `isConnectableStart`/`isConnectableEnd` false, which
        * drops React Flow's `connectionindicator` class — the class that would
        * otherwise give a handle `pointer-events: all` and a crosshair cursor.
        *
        * Keeping them inert is what removes the six right-drag dead patches
        * these used to punch through each tile: React Flow puts `nopan` on
        * every handle unconditionally, and this canvas pans on right-drag, so a
        * pointer-reachable handle is also a spot where panning dies.
        *
        * The id-less left/right pair must stay FIRST: React Flow resolves an
        * edge with no handle id to index 0 of the matching bounds array, which
        * is how dep/resume/timeline keep their left/right anchors. */}
      <Handle
        type="target"
        position={Position.Left}
        isConnectableStart={false}
        isConnectableEnd={false}
        className={ANCHOR_HANDLE_CLASS}
      />
      <Handle
        type="source"
        position={Position.Right}
        isConnectableStart={false}
        isConnectableEnd={false}
        className={ANCHOR_HANDLE_CLASS}
      />
      <Handle
        type="source"
        id="produces"
        position={Position.Bottom}
        isConnectableStart={false}
        isConnectableEnd={false}
        className={ANCHOR_HANDLE_CLASS}
      />
      <Handle
        type="target"
        id="loads"
        position={Position.Top}
        isConnectableStart={false}
        isConnectableEnd={false}
        className={ANCHOR_HANDLE_CLASS}
      />
      {/* Epoch links run to the vertical trunk, so they enter the top and
        * leave the bottom rather than sharing the horizontal dep/resume axis.
        * Anchored left of centre to stay clear of loads (top) and produces
        * (bottom), and because the trunk column sits to the left. */}
      <Handle
        type="target"
        id="epochIn"
        position={Position.Top}
        style={{ left: "22%" }}
        isConnectableStart={false}
        isConnectableEnd={false}
        className={ANCHOR_HANDLE_CLASS}
      />
      <Handle
        type="source"
        id="epochOut"
        position={Position.Bottom}
        style={{ left: "22%" }}
        isConnectableStart={false}
        isConnectableEnd={false}
        className={ANCHOR_HANDLE_CLASS}
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
        <ActionButton
          key={item.key}
          item={item}
          top={stackTop(actionIndex, actionItems.length)}
          /* Only the dependency button carries the wiring gesture: it is the
           * one whose click already means "attach something downstream of this
           * node", which is exactly what the wire declares in longhand. */
          wiring={
            item.key === "dependency"
              ? { nodeId: node.id, enabled: !item.disabled }
              : null
          }
        />
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
  onDequeueNode: (nodeId: string) => void;
  onCreateContinuationVirtual: (nodeId: string) => void;
  onCreateDependencyVirtual: (nodeId: string) => void;
  onMarkVirtualObsolete: (nodeId: string) => Promise<void>;
  onDeleteVirtual: (nodeId: string) => Promise<void>;
  onInterruptNode: (nodeId: string) => void;
  onRerunNode: (nodeId: string) => void;
  canCreateVirtual: boolean;
  canMutateNode: (nodeId: string) => boolean;
  canAcceptDependency: (sourceNodeId: string, targetNodeId: string) => boolean;
  canPromoteVirtual: boolean;
  canDequeue: boolean;
  manualPromotionPlanspaceId: string | null;
  isManualPlanspace: (planspaceId: string | null | undefined) => boolean;
  canInterrupt: boolean;
  canRerun: boolean;
  pendingGateForNode: (nodeId: string) => InteractionRequest | null;
  onResolveGate: (id: string, payload: ResolveGatePayload) => void;
  modelPresets: ModelPreset[];
};

let agentNodeContext: AgentNodeContext = {
  onPromoteVirtual: () => {},
  onDequeueNode: () => {},
  onCreateContinuationVirtual: () => {},
  onCreateDependencyVirtual: () => {},
  onMarkVirtualObsolete: async () => {},
  onDeleteVirtual: async () => {},
  onInterruptNode: () => {},
  onRerunNode: () => {},
  canCreateVirtual: false,
  canMutateNode: () => false,
  canAcceptDependency: () => false,
  canPromoteVirtual: false,
  canDequeue: false,
  manualPromotionPlanspaceId: null,
  isManualPlanspace: () => false,
  canInterrupt: false,
  canRerun: false,
  pendingGateForNode: () => null,
  onResolveGate: () => {},
  modelPresets: [],
};

export function setAgentNodeContext(ctx: AgentNodeContext): void {
  agentNodeContext = ctx;
}

/* All six handles are invisible programmatic anchors — edges attach to them,
 * pointers never touch them. Dependencies are wired by long-pressing the "↘"
 * button instead, so no handle has to be findable, and keeping them all
 * `opacity-0` is what removes the row of dots that briefly appeared on every
 * tile edge when dragging handles was the wiring gesture. */
const ANCHOR_HANDLE_CLASS =
  "!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0";

/* Vertical offset of one action button, top to bottom in push order.
 *
 * Buttons are centred on the tile's right edge. Nothing else competes for that
 * column now: the handles beneath them are inert, so a button sitting over one
 * costs nothing. */
export function stackTop(index: number, total: number): string {
  if (index < 0) return "50%";
  const step = 30;
  const offset = (index - (total - 1) / 2) * step;
  return `calc(50% + ${offset}px)`;
}

type ActionItem = {
  key: "promote" | "dequeue" | "continuation" | "dependency" | "remove" | "interrupt" | "rerun";
  icon: ReactNode;
  title: string;
  disabled: boolean;
  tone: "brand" | "neutral" | "danger";
  alwaysVisible?: boolean;
  onClick: () => void;
};

/* One button in the tile's right-edge action stack.
 *
 * Its own component because the dependency button needs hooks the others do
 * not: the long-press that pulls out a wire. Keeping that inside the map
 * callback would call hooks conditionally. */
function ActionButton({
  item,
  top,
  wiring,
}: {
  item: ActionItem;
  top: string;
  wiring: { nodeId: string; enabled: boolean } | null;
}) {
  const onBegin = useCallback(
    (origin: { x: number; y: number }) => {
      startWiringDrag(wiring?.nodeId ?? "", origin);
    },
    [wiring?.nodeId],
  );
  const longPress = useLongPressWiring({
    enabled: Boolean(wiring?.enabled),
    onBegin,
  });

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        if (!item.disabled) item.onClick();
      }}
      onClickCapture={wiring ? longPress.onClickCapture : undefined}
      onPointerDown={wiring ? longPress.onPointerDown : undefined}
      onMouseDown={(e) => e.stopPropagation()}
      disabled={item.disabled}
      style={{ top }}
      className={actionButtonClass(item.tone, item.alwaysVisible)}
      title={
        wiring?.enabled
          ? `${item.title}（长按拖出连线）`
          : item.title
      }
      aria-label={item.title}
    >
      {item.icon}
    </button>
  );
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

function DequeueActionIcon() {
  return <ActionGlyph>↩</ActionGlyph>;
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
  return (
    <span
      className={
        "inline-flex items-center rounded border px-1 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] " +
        nodeClassificationTone(node)
      }
      title={nodeClassificationLabel(node)}
    >
      {nodeClassificationChipLabel(node)}
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

function formatStartTime(node: NodeInfo): string {
  const at = node.started_at ?? node.created_at;
  return new Date(at * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function tooltipForAgent(
  node: NodeInfo,
  isActive: boolean,
  modelPresets: ModelPreset[],
): string {
  const promptText = node.prompt_draft || node.prompt;
  const prompt = promptText ? `"${promptText.slice(0, 80)}"` : "(no prompt)";
  const status = isActive ? " · active" : "";
  const category = node.category ? ` · ${node.category}` : "";
  const preset = modelPresetLabel(modelPresets, node.model_preset_id);
  const provider = providerLabel(node.provider);
  return `Agent ${node.state}${category} · ${preset} (${provider})${status}\n${prompt}\n${node.id}`;
}
