import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

import { getNodePreview } from "../api";
import type {
  Activity,
  ArtifactMode,
  ContextBundle,
  ContextBundleSource,
  EventRecord,
  InteractionRequest,
  ModelPreset,
  NodeDiff,
  NodeInfo,
  ReviewBrief,
  ReviewSubtype,
  SkillSelection,
} from "../types";
import type { PrincipleSummary, SkillSummary, UpdateVirtualPayload } from "../api";
import {
  appendRecordsToTurns,
  buildTurnsFromEvents,
  setTurnsStreaming,
} from "../transcript";
import { ToolActivity } from "../components/ToolActivity";
import { ZoomableText } from "../components/TextZoom";
import { EntryPickerModal } from "../components/EntryPickerModal";
import type { HierarchyEntry } from "../components/HierarchyTree";
import {
  PendingGateInline,
  type ResolveGatePayload,
} from "../components/PendingGateInline";
import {
  artifactModeAvailable,
  canResumeNode,
  categoryForClassification,
  isLibraryOpKind,
  nodeClassification,
  nodeClassificationLabel,
  qaModeAvailable,
  type NodeClassification,
  type NodeMutationLock,
} from "../nodeUtil";
import { GateReviewForm } from "./gateReview";
import { InspectDrawer } from "./InspectDrawer";
import {
  modelPresetDetail,
  modelPresetLabel,
  providerLabel,
  selectableModelPresets,
} from "../modelPresets";
import {
  clearStashedDraft,
  draftShapeMatches,
  draftStashKey,
  readStashedDraft,
  shouldAutosaveDraft,
  stashRestoreDecision,
  writeStashedDraft,
} from "../draftStash";

export type AgentPanelProps = {
  sessionId: string;
  node: NodeInfo;
  nodesById: Map<string, NodeInfo>;
  modelPresets: ModelPreset[];
  events: EventRecord[];
  eventsLoading: boolean;
  diff: NodeDiff | null;
  diffLoading: boolean;
  contextBundle: ContextBundle | null;
  contextBundleLoading: boolean;
  pendingGate: InteractionRequest | null;
  pendingReview: InteractionRequest | null;
  principles?: PrincipleSummary[];
  skills?: SkillSummary[];
  onResolveGate?: (id: string, payload: ResolveGatePayload) => void;
  onResolveReview: (payload: { id: string; judgment: string }) => void;
  onCreateContinuationVirtual: (nodeId: string) => void;
  onPromoteVirtual: (nodeId: string) => Promise<void>;
  onDequeueNode: (nodeId: string) => Promise<void>;
  onUpdateVirtual: (
    nodeId: string,
    payload: UpdateVirtualPayload,
  ) => Promise<NodeInfo | undefined>;
  onInterruptNode: (nodeId: string) => void;
  onRerunNode: (nodeId: string) => void;
  canInterrupt: boolean;
  canRerun: boolean;
  canMutate: boolean;
  mutationLock: NodeMutationLock;
  manualPromotionPlanspaceId: string | null;
  activePlanspaceId: string | null;
  knownPlanspaceIds: string[];
  onActivatePlanspace: (planspaceId: string) => void;
  isManualPlanspace: (planspaceId: string | null | undefined) => boolean;
  focusRequestVersion: number;
  activityFocusRequestVersion: number;
  onSelectArtifact: (
    nodeId: string,
    name: string,
    ext: "md" | "json" | "html",
  ) => void;
};

export function AgentPanel({
  sessionId,
  node,
  nodesById,
  modelPresets,
  events,
  eventsLoading,
  diff,
  diffLoading,
  contextBundle,
  contextBundleLoading,
  pendingGate,
  pendingReview,
  principles,
  skills,
  onResolveGate,
  onResolveReview,
  onCreateContinuationVirtual,
  onPromoteVirtual,
  onDequeueNode,
  onUpdateVirtual,
  onInterruptNode,
  onRerunNode,
  canInterrupt,
  canRerun,
  canMutate,
  mutationLock,
  manualPromotionPlanspaceId,
  activePlanspaceId,
  knownPlanspaceIds,
  onActivatePlanspace,
  isManualPlanspace,
  focusRequestVersion,
  activityFocusRequestVersion,
  onSelectArtifact,
}: AgentPanelProps) {
  const inactiveKnownPlanspace =
    node.state === "virtual" &&
    !!node.planspace_id &&
    node.planspace_id !== activePlanspaceId &&
    knownPlanspaceIds.includes(node.planspace_id);
  const headline = (
    node.summary ||
    node.prompt_draft ||
    node.prompt ||
    "(no prompt)"
  ).trim();
  const turns = useIncrementalTurns(node, events);
  const transcriptItems = useMemo(() => flattenTranscript(turns), [turns]);
  const readyToPromote = useMemo(
    () => virtualReady(node, nodesById),
    [node, nodesById],
  );
  const toolCallCount = useMemo(
    () => transcriptItems.reduce((n, item) => n + (item.kind === "tools" ? item.items.length : 0), 0),
    [transcriptItems],
  );
  const activityDefaultOpen = !isTerminal(node.state) || transcriptItems.length > 0;

  const [preview, setPreview] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [dequeueing, setDequeueing] = useState(false);
  const [draftPromotability, setDraftPromotability] = useState<{
    nodeId: string;
    ready: boolean;
  } | null>(null);
  const virtualNodeBodyRef = useRef<VirtualNodeBodyHandle | null>(null);
  const panelScrollRef = useRef<HTMLDivElement | null>(null);
  const activityDetailsRef = useRef<HTMLDetailsElement | null>(null);
  const latestActivityRef = useRef<HTMLDivElement | null>(null);
  const handledActivityFocusRef = useRef(0);
  const eventsLoadingRef = useRef(eventsLoading);
  eventsLoadingRef.current = eventsLoading;
  const currentReadyToPromote =
    draftPromotability?.nodeId === node.id
      ? draftPromotability.ready
      : readyToPromote;
  const handleDraftPromotabilityChange = useCallback(
    (nodeId: string, ready: boolean) => {
      setDraftPromotability({ nodeId, ready });
    },
    [],
  );

  /* Autosaving to the server is only safe on a manual lane. `update_virtual`
   * finishes by running an auto-promotion pass, so on an auto lane a saved
   * prompt is a launched agent — a periodic push there would start the run
   * mid-sentence, which is not undoable. On those lanes the local stash is the
   * whole protection.
   *
   * Deliberately wider than the backend's own gate, which only promotes when
   * the lane is also the active one: an inactive auto lane would be safe to
   * push to, but it can be activated from elsewhere at any moment and the
   * stash already loses nothing there. */
  const autosaveToServer =
    canMutate && node.state === "virtual" && isManualPlanspace(node.planspace_id);

  const promote = async () => {
    if (promoting) return;
    setPromoting(true);
    try {
      const saved = await virtualNodeBodyRef.current?.saveChanges();
      if (saved === false) return;
      await onPromoteVirtual(node.id);
    } finally {
      setPromoting(false);
    }
  };

  const dequeue = async () => {
    if (dequeueing) return;
    setDequeueing(true);
    try {
      await onDequeueNode(node.id);
    } finally {
      setDequeueing(false);
    }
  };

  useEffect(() => {
    if (node.state === "virtual") {
      setPreview(null);
      setPreviewLoading(false);
      return;
    }
    let cancelled = false;
    setPreviewLoading(true);
    getNodePreview(sessionId, node.id)
      .then((next) => {
        if (!cancelled) setPreview(next?.text ?? null);
      })
      .catch((err) => {
        if (!cancelled) {
          console.warn("get node preview failed:", err);
          setPreview(null);
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, node.id, node.state, node.finished_at]);

  const scrollToLatestActivity = useCallback(() => {
    const container = panelScrollRef.current;
    const details = activityDetailsRef.current;
    const marker = latestActivityRef.current;
    if (!container || !details || !marker) return;

    details.open = true;
    const containerRect = container.getBoundingClientRect();
    const markerRect = marker.getBoundingClientRect();
    container.scrollTo({
      top: Math.max(
        0,
        container.scrollTop + markerRect.bottom - containerRect.bottom + 12,
      ),
      behavior: "smooth",
    });
  }, []);

  useEffect(() => {
    if (
      activityFocusRequestVersion === 0 ||
      handledActivityFocusRef.current === activityFocusRequestVersion ||
      node.state !== "running" ||
      eventsLoading
    ) {
      return;
    }
    const requestVersion = activityFocusRequestVersion;
    const frame = window.requestAnimationFrame(() => {
      if (
        handledActivityFocusRef.current === requestVersion ||
        eventsLoadingRef.current
      ) {
        return;
      }
      scrollToLatestActivity();
      handledActivityFocusRef.current = requestVersion;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [
    activityFocusRequestVersion,
    eventsLoading,
    node.id,
    node.state,
    scrollToLatestActivity,
    transcriptItems.length,
  ]);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <StatePill state={node.state} />
              {isLibraryOpKind(node.agent_op_kind) && (
                <span className="rounded border border-state-library/30 bg-state-library-soft px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] text-state-library">
                  librarian
                </span>
              )}
            </div>
            {/* `break-words` so a headline whose first word is a long path
                wraps within the clamp instead of being cut mid-token. */}
            <h2 className="mt-1.5 line-clamp-2 break-words font-display text-[15px] font-semibold leading-snug text-ink-strong">
              {headline}
            </h2>
          </div>
          <div className="flex flex-none items-center gap-1.5">
            {(node.state === "running" ||
              node.state === "waiting" ||
              node.state === "awaiting_human_input") && (
              <button
                type="button"
                onClick={() => onInterruptNode(node.id)}
                disabled={!canInterrupt}
                className="rounded-md border border-state-error/50 bg-surface px-2.5 py-1 text-[11px] font-medium text-state-error transition hover:bg-state-error-soft disabled:cursor-not-allowed disabled:opacity-40"
                title="Interrupt this running node"
              >
                Stop
              </button>
            )}
            {node.kind === "agent" &&
              (node.state === "error" || node.state === "cancelled") && (
                <button
                  type="button"
                  onClick={() => onRerunNode(node.id)}
                  disabled={!canRerun}
                  className="rounded-md bg-brand px-2.5 py-1 text-[11px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
                  title="Rerun - create a fresh virtual with the same prompt"
                >
                  ↻ Rerun
                </button>
              )}
            {/* Dequeue follows the node's own lane mode (like the backend),
                not the active lane, so queued nodes in inactive manual lanes
                stay dequeueable. */}
            {node.state === "queued" &&
              canMutate &&
              isManualPlanspace(node.planspace_id) && (
              <button
                type="button"
                onClick={() => void dequeue()}
                disabled={dequeueing}
                className="rounded-md border border-line-strong bg-surface px-2.5 py-1 text-[11px] font-medium text-ink-muted transition hover:border-brand/55 hover:bg-brand-soft hover:text-brand disabled:cursor-not-allowed disabled:opacity-40"
                title="Dequeue - return this node to editable virtual state"
              >
                {dequeueing ? "Dequeuing..." : "Dequeue"}
              </button>
            )}
            {node.state === "virtual" &&
            canMutate &&
            node.planspace_id === manualPromotionPlanspaceId ? (
              <button
                type="button"
                onClick={() => void promote()}
                disabled={!currentReadyToPromote || promoting}
                className="rounded-md bg-brand px-2.5 py-1 text-[11px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
                title={currentReadyToPromote ? "Promote virtual node" : "Virtual node is not ready"}
              >
                {promoting ? "Promoting..." : "Promote"}
              </button>
            ) : canMutate && canResumeNode(node) ? (
              <button
                type="button"
                onClick={() => onCreateContinuationVirtual(node.id)}
                className="rounded-md border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-muted transition hover:border-line-strong hover:text-ink"
                title="Create a continuation virtual that resumes this conversation"
              >
                Continuation
              </button>
            ) : null}
          </div>
        </div>
      </div>

      <div
        ref={panelScrollRef}
        className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm"
      >
        {mutationLock === "project_unbound" && (
          <div className="mb-3 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] text-state-waiting">
            只读 · 此设备尚未配置项目路径。配置后可在本机创建新节点。
          </div>
        )}
        {mutationLock === "store_read_only" && (
          <div className="mb-3 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] text-state-waiting">
            只读 · 当前项目存储在本机不可写。
          </div>
        )}
        {mutationLock === "foreign_host" && (
          <div className="mb-3 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] text-state-waiting">
            此节点的记录保存在另一台设备的分区中，本机仅可查看。
          </div>
        )}
        {canMutate && inactiveKnownPlanspace && node.planspace_id && (
          <div className="mb-3 flex items-center justify-between gap-3 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] text-state-waiting">
            <span>该方向未激活，节点无法推进。激活后可 Promote。</span>
            <button
              type="button"
              onClick={() => onActivatePlanspace(node.planspace_id!)}
              className="flex-none rounded border border-state-waiting/40 bg-surface-raised px-2 py-1 font-medium transition hover:border-state-waiting/70"
            >
              激活此方向
            </button>
          </div>
        )}
        <section className="mb-5">
          <BasicInformationCard node={node} modelPresets={modelPresets} />
        </section>
        {node.state === "virtual" ? (
          <fieldset disabled={!canMutate} className={canMutate ? "contents" : "contents opacity-75"}>
            <VirtualNodeBody
              ref={virtualNodeBodyRef}
              node={node}
              nodesById={nodesById}
              modelPresets={modelPresets}
              principles={principles}
              skills={skills}
              onUpdateVirtual={onUpdateVirtual}
              onPromotabilityChange={handleDraftPromotabilityChange}
              focusRequestVersion={canMutate ? focusRequestVersion : 0}
              sessionId={sessionId}
              autosaveToServer={autosaveToServer}
            />
          </fieldset>
        ) : (
          <>
            {pendingReview && (
              <section className="mb-4 rounded-md border border-state-review/40 bg-state-review-soft/35 p-3">
                <SectionHeading tone="review">Human review</SectionHeading>
                <div className="mt-2">
                  <GateReviewForm
                    node={node}
                    pending={pendingReview}
                    onSubmit={onResolveReview}
                    variant="panel"
                  />
                </div>
              </section>
            )}

            {pendingGate && (
              <section className="mb-4 rounded-md border border-state-waiting/40 bg-state-waiting-soft/40 p-3">
                <SectionHeading tone="waiting">Pending response</SectionHeading>
                <div className="mt-2">
                  <PendingGateInline
                    node={node}
                    pending={pendingGate}
                    onResolve={(payload) => onResolveGate?.(pendingGate.id, payload)}
                  />
                </div>
              </section>
            )}

            <section className="mb-5">
              <AgentInputCard
                node={node}
                contextBundle={contextBundle}
                loading={contextBundleLoading}
              />
            </section>

            <section className="mb-5">
              <PreviewCard
                preview={preview}
                loading={previewLoading}
              />
            </section>

            {node.subtype === "code_review" && (
              <section className="mb-5">
                <details className="overflow-hidden rounded-md border border-line bg-surface-sunken">
                  <summary className="cursor-pointer px-3 py-2">
                    <SectionHeading>Reviewed snapshot</SectionHeading>
                  </summary>
                  <div className="border-t border-line p-3">
                    {diffLoading ? (
                      <p className="text-[11px] text-ink-muted">Loading reviewed diff…</p>
                    ) : diff?.error ? (
                      <p className="text-[11px] text-state-error">{diff.error}</p>
                    ) : (
                      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-mono text-[10.5px] leading-relaxed text-ink-muted">
                        {diff?.text || "Snapshot not available yet."}
                      </pre>
                    )}
                  </div>
                </details>
              </section>
            )}

            {(node.artifacts?.length ?? 0) > 0 && (
              <section className="mb-5">
                <SectionHeading>Artifacts</SectionHeading>
                <ul className="mt-2 space-y-1.5">
                  {(node.artifacts ?? []).map((artifact, index) => {
                    const ext = artifact.name.split(".").pop() as "md" | "json" | "html";
                    return (
                      <li key={`${artifact.name}:${artifact.status}:${index}`}>
                        {artifact.status === "published" ? (
                          <button
                            type="button"
                            onClick={() => onSelectArtifact(node.id, artifact.name, ext)}
                            className="flex w-full items-center justify-between gap-3 rounded-md border border-line bg-surface-raised px-3 py-2 text-left transition hover:border-line-strong"
                          >
                            <span className="min-w-0 truncate font-mono text-[11.5px] text-ink-strong">
                              {artifact.name}
                            </span>
                            <span className="flex-none text-[10px] uppercase text-ink-subtle">
                              {formatArtifactBytes(artifact.bytes)}
                            </span>
                          </button>
                        ) : (
                          <div className="rounded-md border border-dashed border-line bg-surface-sunken px-3 py-2 opacity-70">
                            <div className="truncate font-mono text-[11.5px] text-ink-muted">
                              {artifact.name}
                            </div>
                            <div className="mt-0.5 text-[10.5px] text-ink-subtle">
                              Dropped: {artifact.reason || "invalid artifact"}
                            </div>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}

            <section className="mb-5">
              <details
                ref={activityDetailsRef}
                key={node.id}
                open={activityDefaultOpen}
                className="overflow-hidden rounded-md border border-line bg-surface-sunken"
              >
                <summary className="cursor-pointer px-3 py-2">
                  <SectionHeading
                    right={
                      <span className="text-[10px] font-normal normal-case tracking-normal text-ink-subtle">
                        {eventsLoading
                          ? "loading..."
                          : `${toolCallCount} tool ${toolCallCount === 1 ? "call" : "calls"} · ${events.length} events`}
                      </span>
                    }
                  >
                    Activity
                  </SectionHeading>
                </summary>
                <div className="border-t border-line px-3 py-3">
                  <ActivityTranscript
                    items={transcriptItems}
                    streaming={node.state === "running"}
                  />
                  <div ref={latestActivityRef} aria-hidden="true" />
                </div>
              </details>
            </section>

            <ThinkingSection turns={turns} />

            <section className="mb-2">
              <InspectDrawer
                node={node}
                modelPresets={modelPresets}
                contextBundle={contextBundle}
                contextBundleLoading={contextBundleLoading}
                eventCount={events.length}
              />
            </section>
          </>
        )}
      </div>
    </div>
  );
}

function SectionHeading({
  children,
  right,
  tone,
}: {
  children: React.ReactNode;
  right?: React.ReactNode;
  tone?: "waiting" | "review" | "error";
}) {
  const color =
    tone === "waiting"
      ? "text-state-waiting"
      : tone === "review"
        ? "text-state-review"
        : tone === "error"
          ? "text-state-error"
          : "text-ink-subtle";
  return (
    <div
      className={
        "flex items-center justify-between text-[10px] font-medium uppercase tracking-[0.14em] " +
        color
      }
    >
      <span>{children}</span>
      {right}
    </div>
  );
}

function formatArtifactBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${bytes} B`;
}

const ARTIFACT_MODE_HINTS: Record<ArtifactMode, string> = {
  default: "不要求产出物。只有 Prompt 里明确要求时才会产出文件。",
  markdown: "要求本轮产出至少一份 Markdown 文件给你看。",
  html: "要求本轮产出一份自包含的 HTML 文件——内联样式与脚本，不引用外部资源。",
  custom: "在下方描述你想要的产出物。这段文字会原样进入 agent 的提示。",
};

/* 10s: the interval the user asked for. Short enough that a lost tick costs
 * one sentence, long enough that a sustained edit is a handful of writes rather
 * than one per keystroke — each save is a node-file write plus a debounced git
 * commit on the metadata store. */
const AUTOSAVE_INTERVAL_MS = 10_000;

export type VirtualDraft = {
  promptDraft: string;
  motivation: string;
  modelPresetId: string;
  /* The single source of truth for the four mutually exclusive kinds. It maps
   * onto the wire's (category, agent_op_kind) pair at save time, so the two
   * fields can never disagree the way separate draft state would allow. */
  classification: NodeClassification;
  subtype: ReviewSubtype;
  brief: ReviewBrief;
  scheduledDeps: string[];
  pendingExtraPrinciples: string[];
  pendingExtraSkills: SkillSelection[];
  qaMode: boolean;
  artifactMode: ArtifactMode;
  artifactSpec: string;
  obsoleteReason: string;
};

/* Tracks one in-flight local write. The backend normalizes saved drafts
 * (trims, category resets, auto-attached skill expansion) instead of echoing
 * them byte-for-byte, so `persistedSignature`/`draftAfterAck` start as a
 * client-side prediction and are replaced with the server's actual response
 * once it arrives. `sentSignature` stays the prediction of the draft that was
 * sent, and is only used to detect user edits made while the write was in
 * flight (those must not be clobbered by the acknowledgement). */
type PendingLocalVirtualUpdate = {
  nodeId: string;
  persistedSignature: string;
  sentSignature: string;
  draftAfterAck: VirtualDraft;
};

type VirtualNodeBodyHandle = {
  saveChanges: () => Promise<boolean>;
};

type VirtualNodeBodyProps = {
  node: NodeInfo;
  nodesById: Map<string, NodeInfo>;
  modelPresets: ModelPreset[];
  principles?: PrincipleSummary[];
  skills?: SkillSummary[];
  onUpdateVirtual: (
    nodeId: string,
    payload: UpdateVirtualPayload,
  ) => Promise<NodeInfo | undefined>;
  onPromotabilityChange: (nodeId: string, ready: boolean) => void;
  focusRequestVersion: number;
  sessionId: string;
  /* False on auto lanes and read-only nodes: the draft is stashed locally but
   * never pushed on a timer. See `autosaveToServer` in AgentPanel. */
  autosaveToServer: boolean;
};

const VirtualNodeBody = forwardRef<VirtualNodeBodyHandle, VirtualNodeBodyProps>(function VirtualNodeBody({
  node,
  nodesById,
  modelPresets,
  principles,
  skills,
  onUpdateVirtual,
  onPromotabilityChange,
  focusRequestVersion,
  sessionId,
  autosaveToServer,
}, ref) {
  if (node.kind === "verifier") {
    return <VerifierVirtualBody node={node} nodesById={nodesById} />;
  }
  return (
    <EditableVirtualNodeBody
      ref={ref}
      node={node}
      nodesById={nodesById}
      modelPresets={modelPresets}
      principles={principles}
      skills={skills}
      onUpdateVirtual={onUpdateVirtual}
      onPromotabilityChange={onPromotabilityChange}
      focusRequestVersion={focusRequestVersion}
      sessionId={sessionId}
      autosaveToServer={autosaveToServer}
    />
  );
});

const EditableVirtualNodeBody = forwardRef<VirtualNodeBodyHandle, VirtualNodeBodyProps>(function EditableVirtualNodeBody({
  node,
  nodesById,
  modelPresets,
  principles,
  skills,
  onUpdateVirtual,
  onPromotabilityChange,
  focusRequestVersion,
  sessionId,
  autosaveToServer,
}, ref) {

  const persistedDraft = virtualDraftFromNode(node);
  const persistedDraftSignature = JSON.stringify(persistedDraft);
  const persistedDraftRef = useRef({
    nodeId: node.id,
    signature: persistedDraftSignature,
    draft: persistedDraft,
  });
  const pendingLocalUpdateRef = useRef<PendingLocalVirtualUpdate | null>(null);
  const savePromiseRef = useRef<Promise<boolean> | null>(null);
  const [draft, setDraft] = useState<VirtualDraft>(() => persistedDraft);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [autosavedAt, setAutosavedAt] = useState<number | null>(null);
  const [stashWriteState, setStashWriteState] = useState<
    "idle" | "saved" | "failed"
  >("idle");
  const lastAutosaveAttemptRef = useRef<string | null>(null);
  const drainSaveRequestedRef = useRef(false);
  /* Which node this instance has already run its stash restore for. A ref, not
   * state: the restore must happen inside the same reconcile effect that owns
   * `draft`, before any other branch can reset it. */
  const restoredNodeIdRef = useRef<string | null>(null);
  /* Signature the restore branch decided on. Effects run before the state it
   * queued is rendered, so the stash writer would otherwise see the
   * pre-restore draft, read it as clean, and delete the very entry the restore
   * just read. Holding the signature lets the writer wait one render for the
   * draft it is supposed to persist. */
  const pendingRestoreSignatureRef = useRef<string | null>(null);
  const promptDraftRef = useRef<HTMLTextAreaElement | null>(null);
  const activeModelPresets = selectableModelPresets(modelPresets);
  const currentPreset = modelPresets.find((preset) => preset.id === draft.modelPresetId);
  const candidateDeps = useMemo(
    () => candidateDependencies(node, nodesById, draft.scheduledDeps),
    [nodesById, node, draft.scheduledDeps],
  );
  const dirty = useMemo(
    () => JSON.stringify(draft) !== persistedDraftSignature,
    [draft, persistedDraftSignature],
  );
  const draftValidationError = virtualDraftValidationError(draft, node);

  useEffect(() => {
    onPromotabilityChange(
      node.id,
      virtualDraftReadyToPromote(draft, node, nodesById),
    );
  }, [draft, node, nodesById, onPromotabilityChange]);

  /* Selecting this node again — after a switch, a panel unmount, or a reload —
   * reads back whatever local draft was stashed for it. `drop` means the stash
   * has nothing the saved node lacks. `adopt` means nobody moved the node
   * since, so the draft is restored as typed. `merge` means the node did move,
   * and the stash is reconciled against it field by field exactly the way a
   * live external write is, so a remote change is never silently undone by a
   * draft that predates it. */
  const restoreStashedDraft = useCallback(
    (persisted: VirtualDraft): { draft: VirtualDraft; notice: string } | null => {
      const key = draftStashKey(sessionId, node.id);
      const record = readStashedDraft(key);
      if (!record) return null;
      /* A stash written by an older build can disagree with today's draft
       * shape, and sending that back would post fields the API rejects. */
      if (
        !draftShapeMatches(record.draft, persisted as unknown as Record<string, unknown>) ||
        !draftShapeMatches(record.baseline, persisted as unknown as Record<string, unknown>)
      ) {
        clearStashedDraft(key);
        return null;
      }
      const stashed = record.draft as VirtualDraft;
      const baseline = record.baseline as VirtualDraft;
      const persistedSignature = JSON.stringify(persisted);
      const decision = stashRestoreDecision({
        stashedSignature: JSON.stringify(stashed),
        baselineSignature: JSON.stringify(baseline),
        persistedSignature,
      });
      if (decision === "drop") {
        clearStashedDraft(key);
        return null;
      }
      if (decision === "adopt") {
        return { draft: stashed, notice: stashedDraftRestoredMessage(record.savedAt) };
      }
      const { draft: merged, conflicts } = mergeVirtualDraft(
        stashed,
        baseline,
        persisted,
      );
      if (JSON.stringify(merged) === persistedSignature) {
        clearStashedDraft(key);
        return null;
      }
      return {
        draft: merged,
        notice:
          stashedDraftRestoredMessage(record.savedAt) +
          (conflicts.length ? ` ${stashedDraftConflictMessage(conflicts)}` : ""),
      };
    },
    [node.id, sessionId],
  );

  useEffect(() => {
    const previous = persistedDraftRef.current;
    const pendingLocalUpdate = pendingLocalUpdateRef.current;
    const acknowledgedLocalUpdate =
      pendingLocalUpdate?.nodeId === node.id &&
      pendingLocalUpdate.persistedSignature === persistedDraftSignature;
    persistedDraftRef.current = {
      nodeId: node.id,
      signature: persistedDraftSignature,
      draft: persistedDraft,
    };

    /* Ahead of every other branch, and exactly once per node: a first mount
     * takes this path too, which is what makes a restore survive the panel
     * being unmounted or the page reloaded — neither of those looks like a
     * node switch from here. */
    if (restoredNodeIdRef.current !== node.id) {
      restoredNodeIdRef.current = node.id;
      pendingLocalUpdateRef.current = null;
      lastAutosaveAttemptRef.current = null;
      setAutosavedAt(null);
      setError(null);
      const restored = restoreStashedDraft(persistedDraft);
      const nextDraft = restored?.draft ?? persistedDraft;
      pendingRestoreSignatureRef.current = JSON.stringify(nextDraft);
      draftRef.current = nextDraft;
      setDraft(nextDraft);
      setNotice(restored?.notice ?? null);
      return;
    }

    if (
      previous.nodeId === node.id &&
      previous.signature === persistedDraftSignature
    ) {
      return;
    }

    if (pendingLocalUpdate?.nodeId === node.id) {
      if (acknowledgedLocalUpdate) {
        pendingLocalUpdateRef.current = null;
        const latestSignature = JSON.stringify(
          virtualDraftAfterSave(draftRef.current),
        );
        if (latestSignature === pendingLocalUpdate.sentSignature) {
          draftRef.current = pendingLocalUpdate.draftAfterAck;
          setDraft(pendingLocalUpdate.draftAfterAck);
        }
      }
      return;
    }

    const { draft: mergedDraft, conflicts } = mergeVirtualDraft(
      draftRef.current,
      previous.draft,
      persistedDraft,
    );
    draftRef.current = mergedDraft;
    setDraft(mergedDraft);
    setError(conflicts.length ? externalDraftConflictMessage(conflicts) : null);
  }, [node.id, persistedDraftSignature, restoreStashedDraft]);

  useEffect(() => {
    if (focusRequestVersion <= 0) return;
    const timer = window.setTimeout(() => {
      promptDraftRef.current?.focus();
      promptDraftRef.current?.select();
    }, 30);
    return () => window.clearTimeout(timer);
  }, [node.id, focusRequestVersion]);

  /* Every keystroke lands in browser storage, keyed on (session, node), paired
   * with the persisted draft it was based on. Cheap enough to do per edit, and
   * it means the protected window is the keystroke rather than the autosave
   * tick. Cleared once the draft matches what is saved, so a reopened node does
   * not announce a restore of nothing. */
  useEffect(() => {
    if (restoredNodeIdRef.current !== node.id) return;
    const pendingRestore = pendingRestoreSignatureRef.current;
    if (pendingRestore !== null) {
      if (JSON.stringify(draft) !== pendingRestore) return;
      pendingRestoreSignatureRef.current = null;
    }
    const key = draftStashKey(sessionId, node.id);
    if (!dirty) {
      clearStashedDraft(key);
      setStashWriteState("idle");
      return;
    }
    const written = writeStashedDraft(
      key,
      {
        savedAt: Date.now(),
        baseline: persistedDraftRef.current.draft,
        draft,
      },
    );
    setStashWriteState(written ? "saved" : "failed");
    /* `persistedDraftSignature` is a dependency so the stored baseline tracks
     * external writes that leave the local draft untouched. A stale baseline
     * still restores safely — it just forces the merge path and can report a
     * conflict the user does not have — so keeping it current is what makes an
     * ordinary reopen adopt the draft verbatim. */
  }, [dirty, draft, node.id, persistedDraftSignature, sessionId]);

  const save = (
    options: {
      singleSnapshot?: boolean;
      expectedPlanspaceMode?: "manual";
    } = {},
  ): Promise<boolean> => {
    if (savePromiseRef.current) {
      /* Promote and explicit Save must include edits made during an autosave
       * already in flight. Upgrade that operation to its normal draining
       * behavior instead of starting a concurrent PATCH. */
      if (!options.singleSnapshot) drainSaveRequestedRef.current = true;
      return savePromiseRef.current;
    }
    if (JSON.stringify(draftRef.current) === persistedDraftSignature) {
      return Promise.resolve(true);
    }
    drainSaveRequestedRef.current = false;
    const operation = (async () => {
      setSaving(true);
      setError(null);
      setNotice(null);
      try {
        while (true) {
          const draftToSave = draftRef.current;
          const validationError = virtualDraftValidationError(draftToSave, node);
          if (validationError) {
            setError(validationError);
            return false;
          }

          const predictedDraft = virtualDraftAfterSave(draftToSave);
          const sentSignature = JSON.stringify(predictedDraft);
          const pendingLocalUpdate: PendingLocalVirtualUpdate = {
            nodeId: node.id,
            persistedSignature: sentSignature,
            sentSignature,
            draftAfterAck: predictedDraft,
          };
          pendingLocalUpdateRef.current = pendingLocalUpdate;
          const payload = virtualPayloadFromDraft(draftToSave, node);
          if (options.expectedPlanspaceMode) {
            payload.expected_planspace_mode = options.expectedPlanspaceMode;
          }
          const updatedNode = await onUpdateVirtual(node.id, payload);
          if (updatedNode) {
            const serverDraft = virtualDraftFromNode(updatedNode);
            pendingLocalUpdate.persistedSignature = JSON.stringify(serverDraft);
            pendingLocalUpdate.draftAfterAck = serverDraft;
          }

          const latestSignature = JSON.stringify(
            virtualDraftAfterSave(draftRef.current),
          );
          if (
            latestSignature !== pendingLocalUpdate.sentSignature &&
            (!options.singleSnapshot || drainSaveRequestedRef.current)
          ) {
            continue;
          }
          if (
            latestSignature === pendingLocalUpdate.sentSignature &&
            pendingLocalUpdateRef.current === pendingLocalUpdate &&
            persistedDraftRef.current.signature === pendingLocalUpdate.persistedSignature
          ) {
            pendingLocalUpdateRef.current = null;
            draftRef.current = pendingLocalUpdate.draftAfterAck;
            setDraft(pendingLocalUpdate.draftAfterAck);
          }
          return true;
        }
      } catch (err) {
        pendingLocalUpdateRef.current = null;
        setError(errorMessage(err));
        return false;
      } finally {
        setSaving(false);
        drainSaveRequestedRef.current = false;
        savePromiseRef.current = null;
      }
    })();
    savePromiseRef.current = operation;
    return operation;
  };

  useImperativeHandle(ref, () => ({ saveChanges: () => save() }));

  const autosaveRef = useRef(() =>
    save({ singleSnapshot: true, expectedPlanspaceMode: "manual" }),
  );
  autosaveRef.current = () =>
    save({ singleSnapshot: true, expectedPlanspaceMode: "manual" });
  const nodeRef = useRef(node);
  nodeRef.current = node;

  /* Periodic push to the server, on manual lanes only. The interval is fixed
   * rather than debounced per keystroke so a long uninterrupted burst of typing
   * still reaches the server: a debounce that resets on every character never
   * fires while someone is actually writing, which is exactly when the work is
   * worth protecting.
   *
   * The tick re-reads eligibility itself instead of restarting the timer on
   * every edit — a timer with `draft` in its dependencies would be cancelled
   * and recreated per keystroke and, again, never fire mid-burst. */
  useEffect(() => {
    if (!autosaveToServer) return;
    const timer = window.setInterval(() => {
      const currentDraft = draftRef.current;
      const signature = JSON.stringify(currentDraft);
      if (
        !shouldAutosaveDraft({
          enabled: true,
          dirty: signature !== persistedDraftRef.current.signature,
          saving: savePromiseRef.current !== null,
          incomplete: Boolean(
            virtualDraftValidationError(currentDraft, nodeRef.current),
          ),
          signature,
          lastAttemptSignature: lastAutosaveAttemptRef.current,
        })
      ) {
        return;
      }
      /* Recorded before the await so a write that fails is not retried every
       * tick against unchanged text; the next edit clears it and re-arms. */
      lastAutosaveAttemptRef.current = signature;
      void autosaveRef.current().then((ok) => {
        if (ok) {
          setAutosavedAt(Date.now());
          setNotice(null);
        }
      });
    }, AUTOSAVE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [autosaveToServer, node.id]);

  /* An edit after a failed autosave re-arms the timer for the new text. */
  useEffect(() => {
    if (lastAutosaveAttemptRef.current === null) return;
    if (JSON.stringify(draft) !== lastAutosaveAttemptRef.current) {
      lastAutosaveAttemptRef.current = null;
    }
  }, [draft]);

  const toggleObsolete = async () => {
    const nextReason = draft.obsoleteReason.trim()
      ? ""
      : "Obsoleted by user";
    const nextDraft = { ...draft, obsoleteReason: nextReason };
    const expectedPersistedDraft = {
      ...persistedDraft,
      obsoleteReason: nextReason,
    };
    const pendingLocalUpdate: PendingLocalVirtualUpdate = {
      nodeId: node.id,
      persistedSignature: JSON.stringify(expectedPersistedDraft),
      sentSignature: JSON.stringify(virtualDraftAfterSave(nextDraft)),
      draftAfterAck: nextDraft,
    };
    pendingLocalUpdateRef.current = pendingLocalUpdate;
    setDraft(nextDraft);
    setSaving(true);
    setError(null);
    try {
      const updatedNode = await onUpdateVirtual(node.id, {
        obsolete_reason: nextReason || null,
      });
      if (updatedNode) {
        // Only obsolete_reason was written; keep any other unsaved local
        // edits and adopt just the server-normalized reason.
        const serverDraft = virtualDraftFromNode(updatedNode);
        pendingLocalUpdate.persistedSignature = JSON.stringify(serverDraft);
        pendingLocalUpdate.draftAfterAck = {
          ...nextDraft,
          obsoleteReason: serverDraft.obsoleteReason,
        };
      }
      if (
        pendingLocalUpdateRef.current === pendingLocalUpdate &&
        persistedDraftRef.current.signature === pendingLocalUpdate.persistedSignature
      ) {
        pendingLocalUpdateRef.current = null;
        setDraft(pendingLocalUpdate.draftAfterAck);
      }
    } catch (err) {
      if (pendingLocalUpdateRef.current === pendingLocalUpdate) {
        pendingLocalUpdateRef.current = null;
      }
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <section className="mb-5">
        <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
          <div className="border-b border-line px-3 py-2">
            <SectionHeading
              right={
                <DraftSaveStatus
                  dirty={dirty}
                  saving={saving}
                  autosaveToServer={autosaveToServer}
                  autosavedAt={autosavedAt}
                  stashWriteState={stashWriteState}
                />
              }
            >
              Draft
            </SectionHeading>
          </div>
          <div className="space-y-3 px-3 py-3">
            {/* Sits with the fields it restored rather than in the State card
                below: a restore the user scrolls past is a restore they will
                mistake for their own typing. */}
            {notice && (
              <div className="rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11.5px] leading-relaxed text-state-waiting">
                {notice}
              </div>
            )}
            <KVGrid
              rows={[
                ["Proposed by", node.proposed_by ?? "-"],
                ["Lane", node.planspace_id ?? "-"],
              ]}
            />
            <FieldLabel label="Motivation">
              <textarea
                value={draft.motivation}
                onChange={(e) =>
                  setDraft((current) => ({
                    ...current,
                    motivation: e.target.value,
                  }))
                }
                rows={3}
                className={fieldClassName}
              />
            </FieldLabel>
            <FieldLabel label="Prompt draft">
              <textarea
                ref={promptDraftRef}
                value={draft.promptDraft}
                onChange={(e) =>
                  setDraft((current) => ({
                    ...current,
                    promptDraft: e.target.value,
                  }))
                }
                rows={8}
                className={fieldClassName + " font-mono text-[11.5px]"}
              />
            </FieldLabel>
            <FieldLabel label="模型档位">
              <select
                value={draft.modelPresetId}
                disabled={Boolean(node.resume_from_node_id) || activeModelPresets.length === 0}
                onChange={(e) =>
                  setDraft((current) => ({
                    ...current,
                    modelPresetId: e.target.value,
                  }))
                }
                className={inputClassName + " disabled:opacity-50"}
                title={
                  node.resume_from_node_id
                    ? "延续节点继承源节点的模型档位。"
                    : "此 virtual 提升运行时使用的模型档位。"
                }
              >
                {activeModelPresets.length === 0 && (
                  <option value="">没有可用模型档位</option>
                )}
                {draft.modelPresetId && currentPreset?.status === "compatibility" && (
                  <option value={draft.modelPresetId} disabled>
                    {modelPresetLabel(modelPresets, draft.modelPresetId)}
                  </option>
                )}
                {draft.modelPresetId && !currentPreset && (
                  <option value={draft.modelPresetId} disabled>{draft.modelPresetId}</option>
                )}
                {activeModelPresets.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.label}
                  </option>
                ))}
              </select>
            </FieldLabel>
            {draft.modelPresetId && (
              <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11px] text-ink-muted">
                {modelPresetDetail(modelPresets, draft.modelPresetId) ||
                  draft.modelPresetId}
              </div>
            )}
            {node.resume_from_node_id && (
              <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11px] text-ink-muted">
                延续节点复用源节点的底层会话，因此模型档位不可切换。
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="mb-5">
        <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
          <div className="border-b border-line px-3 py-2">
            <SectionHeading>Classification</SectionHeading>
          </div>
          <div className="space-y-3 px-3 py-3">
            <div className="inline-flex rounded-md border border-line bg-surface p-0.5">
              {([
                ["work", "Work"],
                ["planning", "Plan"],
                ["review", "Review"],
                ["library", "Library"],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() =>
                    setDraft((current) => ({
                      ...current,
                      classification: value,
                    }))
                  }
                  className={
                    "rounded px-3 py-1.5 text-[12px] font-medium transition " +
                    (draft.classification === value
                      ? "bg-surface-raised text-ink-strong shadow-card"
                      : "text-ink-muted hover:text-ink")
                  }
                >
                  {label}
                </button>
              ))}
            </div>

            {draft.classification === "library" && (
              <div className="space-y-2 rounded-md border border-state-library/25 bg-state-library-soft/30 p-3">
                <p className="text-[11px] leading-relaxed text-ink-muted">
                  图书管理员节点。它会依据你的描述判断该写成 principle（预先注入的行为准则）还是
                  Agent Skill（按需加载的工具/流程知识），然后在用户级 library 中新建或精炼
                  <span className="font-medium text-ink"> 恰好一个</span> 条目。
                </p>
                <p className="text-[11px] leading-relaxed text-ink-muted">
                  在下方 Prompt 里描述这个可复用的准则或流程即可，无需自己先分类。
                </p>
              </div>
            )}

            {draft.classification === "review" && (
              <div className="space-y-3 rounded-md border border-state-review/25 bg-state-review-soft/20 p-3">
                <div className="inline-flex rounded-md border border-state-review/25 bg-surface p-0.5">
                  {([
                    ["agentic_review", "Agentic"],
                    ["human_interact_review", "Human"],
                    ["code_review", "Code"],
                  ] as const).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() =>
                        setDraft((current) => ({
                          ...current,
                          subtype: value,
                        }))
                      }
                      className={
                        "rounded px-3 py-1.5 text-[12px] font-medium transition " +
                        (draft.subtype === value
                          ? "bg-surface-raised text-state-review shadow-card"
                          : "text-ink-muted hover:text-ink")
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <FieldLabel
                  label={
                    draft.subtype === "code_review"
                      ? currentPreset?.provider === "codex"
                        ? "Focus (optional; unsupported by Codex)"
                        : "Focus (optional)"
                      : "Check"
                  }
                >
                  <textarea
                    value={draft.brief.check_what}
                    onChange={(e) =>
                      setDraft((current) => ({
                        ...current,
                        brief: {
                          ...current.brief,
                          check_what: e.target.value,
                        },
                      }))
                    }
                    rows={2}
                    className={fieldClassName}
                  />
                </FieldLabel>
                {draft.subtype !== "code_review" && <FieldLabel label="Expected">
                  <textarea
                    value={draft.brief.expected}
                    onChange={(e) =>
                      setDraft((current) => ({
                        ...current,
                        brief: {
                          ...current.brief,
                          expected: e.target.value,
                        },
                      }))
                    }
                    rows={2}
                    className={fieldClassName}
                  />
                </FieldLabel>}
                {draft.subtype !== "code_review" && <FieldLabel label="Abnormal">
                  <textarea
                    value={draft.brief.abnormal}
                    onChange={(e) =>
                      setDraft((current) => ({
                        ...current,
                        brief: {
                          ...current.brief,
                          abnormal: e.target.value,
                        },
                      }))
                    }
                    rows={2}
                    className={fieldClassName}
                  />
                </FieldLabel>}
                {draft.subtype === "code_review" && (
                  <p className="text-[11px] leading-relaxed text-ink-muted">
                    Reviews staged, unstaged, and untracked changes with the selected provider's native reviewer. The prompt and focus may be empty.
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      </section>

      {artifactModeAvailable(draft.classification) && (
        <section className="mb-5">
          <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
            <div className="border-b border-line px-3 py-2">
              <SectionHeading>Artifact</SectionHeading>
            </div>
            <div className="space-y-3 px-3 py-3">
              <div className="inline-flex rounded-md border border-line bg-surface p-0.5">
                {([
                  ["default", "Default"],
                  ["markdown", "Markdown"],
                  ["html", "HTML"],
                  ["custom", "Custom"],
                ] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() =>
                      setDraft((current) => ({
                        ...current,
                        artifactMode: value,
                      }))
                    }
                    className={
                      "rounded px-3 py-1.5 text-[12px] font-medium transition " +
                      (draft.artifactMode === value
                        ? "bg-surface-raised text-ink-strong shadow-card"
                        : "text-ink-muted hover:text-ink")
                    }
                  >
                    {label}
                  </button>
                ))}
              </div>

              <p className="text-[11px] leading-relaxed text-ink-muted">
                {ARTIFACT_MODE_HINTS[draft.artifactMode]}
              </p>

              {draft.artifactMode === "custom" && (
                <FieldLabel label="期望的产出物">
                  <textarea
                    value={draft.artifactSpec}
                    onChange={(e) =>
                      setDraft((current) => ({
                        ...current,
                        artifactSpec: e.target.value,
                      }))
                    }
                    rows={4}
                    placeholder="例如：产出三份 markdown——一份面向决策者的摘要、一份实现细节、一份风险清单。"
                    className={fieldClassName}
                  />
                </FieldLabel>
              )}
            </div>
          </div>
        </section>
      )}

      {qaModeAvailable(draft.classification) && (
        <section className="mb-5">
          <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
            <div className="border-b border-line px-3 py-2">
              <SectionHeading>Q/A 模式</SectionHeading>
            </div>
            <div className="space-y-2 px-3 py-3">
              <label className="flex cursor-pointer items-start gap-2">
                <input
                  type="checkbox"
                  checked={draft.qaMode}
                  onChange={(e) =>
                    setDraft((current) => ({
                      ...current,
                      qaMode: e.target.checked,
                    }))
                  }
                  className="mt-0.5"
                />
                <span className="text-[11.5px] leading-relaxed text-ink">
                  遇到影响结果的歧义时，先向你提问再继续。适合需求还没完全定死的节点。
                </span>
              </label>
              <p className="text-[11px] leading-relaxed text-ink-muted">
                提问期间节点会阻塞等待你的回答，并占用项目的并发额度。
              </p>
            </div>
          </div>
        </section>
      )}

      <PrinciplesAttachSection
        principles={principles}
        attached={draft.pendingExtraPrinciples}
        onChange={(next) =>
          setDraft((current) => ({ ...current, pendingExtraPrinciples: next }))
        }
      />

      <SkillsAttachSection
        skills={skills}
        attached={draft.pendingExtraSkills}
        onChange={(next) =>
          setDraft((current) => ({ ...current, pendingExtraSkills: next }))
        }
      />

      <section className="mb-5">
        <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
          <div className="border-b border-line px-3 py-2">
            <SectionHeading>Dependencies</SectionHeading>
          </div>
          <div className="space-y-2 px-3 py-3">
            {candidateDeps.length === 0 ? (
              <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
                本方向内没有其它可依赖的节点。
              </div>
            ) : (
              <div className="max-h-44 space-y-1 overflow-auto rounded-md border border-line bg-surface p-2">
                {candidateDeps.map((candidate) => {
                  const checked = draft.scheduledDeps.includes(candidate.id);
                  return (
                    <label
                      key={candidate.id}
                      className="flex cursor-pointer items-start gap-2 rounded px-2 py-1 text-[11.5px] hover:bg-surface-sunken"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() =>
                          setDraft((current) => ({
                            ...current,
                            scheduledDeps: checked
                              ? current.scheduledDeps.filter((id) => id !== candidate.id)
                              : [...current.scheduledDeps, candidate.id],
                          }))
                        }
                        className="mt-0.5"
                      />
                      {/* `min-w-0` lets the flex item shrink below its
                          min-content width; `break-words` is what then wraps
                          an unbreakable token (a path, a slug) inside that
                          reduced width. Without the pair, a dependency
                          summary would force this list to scroll sideways. */}
                      <span className="min-w-0 break-words">
                        <span className="font-mono text-ink-muted">
                          {candidate.id.slice(0, 8)}
                        </span>
                        <span className="ml-2 text-ink">
                          {oneLine(candidate.summary || candidate.prompt_draft || candidate.prompt || candidate.state).slice(0, 96)}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="mb-2">
        <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
          <div className="border-b border-line px-3 py-2">
            <SectionHeading>State</SectionHeading>
          </div>
          <div className="space-y-3 px-3 py-3">
            <FieldLabel label="Obsolete reason">
              <input
                value={draft.obsoleteReason}
                onChange={(e) =>
                  setDraft((current) => ({
                    ...current,
                    obsoleteReason: e.target.value,
                  }))
                }
                className={inputClassName}
                placeholder="Leave blank to keep promotable"
              />
            </FieldLabel>
            {error && (
              <div className="rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-[11.5px] text-state-error">
                {error}
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void save()}
                disabled={saving || !dirty || Boolean(draftValidationError)}
                className="rounded-md bg-brand px-3 py-1.5 text-[12px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
              >
                {saving ? "Saving..." : "Save changes"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setDraft(virtualDraftFromNode(node));
                  clearStashedDraft(draftStashKey(sessionId, node.id));
                  lastAutosaveAttemptRef.current = null;
                  setStashWriteState("idle");
                  setNotice(null);
                  setError(null);
                }}
                disabled={saving || !dirty}
                className="rounded-md border border-line bg-surface px-3 py-1.5 text-[12px] font-medium text-ink-muted transition hover:border-line-strong hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              >
                Revert
              </button>
              <button
                type="button"
                onClick={toggleObsolete}
                disabled={saving}
                className={
                  "rounded-md border px-3 py-1.5 text-[12px] font-medium transition disabled:cursor-not-allowed disabled:opacity-40 " +
                  (node.obsolete_reason
                    ? "border-state-review/40 bg-state-review-soft text-state-review hover:border-state-review/70"
                    : "border-state-error/40 text-state-error hover:bg-state-error-soft")
                }
              >
                {node.obsolete_reason ? "Restore" : "Mark obsolete"}
              </button>
            </div>
          </div>
        </div>
      </section>
    </>
  );
});

function VerifierVirtualBody({
  node,
  nodesById,
}: {
  node: NodeInfo;
  nodesById: Map<string, NodeInfo>;
}) {
  const deps = (node.scheduled_deps ?? [])
    .map((id) => nodesById.get(id))
    .filter((dep): dep is NodeInfo => Boolean(dep));
  return (
    <>
      <section className="mb-5">
        <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
          <div className="border-b border-line px-3 py-2">
            <SectionHeading>Programmatic Review</SectionHeading>
          </div>
          <div className="space-y-3 px-3 py-3">
            <KVGrid
              rows={[
                ["Proposed by", node.proposed_by ?? "-"],
                ["Lane", node.planspace_id ?? "-"],
                ["Script", node.verify_script_ref ?? "-"],
              ]}
            />
            {node.brief && (
              <div className="space-y-2 rounded-md border border-state-review/25 bg-state-review-soft/20 p-3">
                <BriefBlock label="Check" text={node.brief.check_what} />
                <BriefBlock label="Expected" text={node.brief.expected} />
                <BriefBlock label="Abnormal" text={node.brief.abnormal} />
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="mb-5">
        <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
          <div className="border-b border-line px-3 py-2">
            <SectionHeading>Dependencies</SectionHeading>
          </div>
          <div className="space-y-2 px-3 py-3">
            {deps.length === 0 ? (
              <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
                No dependencies.
              </div>
            ) : (
              deps.map((dep) => (
                <div
                  key={dep.id}
                  className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px]"
                >
                  <span className="font-mono text-ink-muted">{dep.id.slice(0, 8)}</span>
                  {/* A dependency summary is agent-authored and often cites a
                      path or slug with no break opportunity; without
                      `break-words` it overflows this card. */}
                  <span className="ml-2 break-words text-ink">
                    {oneLine(dep.summary || dep.prompt_draft || dep.prompt || dep.state).slice(0, 120)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </section>
    </>
  );
}

/* One line telling the user which of the three states the draft is in: written
 * to the server, held only in this browser, or clean. The distinction matters
 * because on an auto lane the draft is deliberately never pushed — leaving that
 * as a bare "unsaved" would read as a bug. */
function DraftSaveStatus({
  dirty,
  saving,
  autosaveToServer,
  autosavedAt,
  stashWriteState,
}: {
  dirty: boolean;
  saving: boolean;
  autosaveToServer: boolean;
  autosavedAt: number | null;
  stashWriteState: "idle" | "saved" | "failed";
}) {
  const baseClassName = "text-[10px] font-normal normal-case tracking-normal";
  const className = `${baseClassName} ${
    stashWriteState === "failed"
      ? "text-state-error"
      : dirty
        ? "text-state-waiting"
        : "text-ink-subtle"
  }`;
  if (saving) {
    return <span className={className}>保存中…</span>;
  }
  if (dirty) {
    if (stashWriteState === "failed") {
      return (
        <span
          className={className}
          title="浏览器未能写入本地草稿。请保持页面开启，或点击 Save changes 提交到服务端。"
        >
          未保存 · 本地留存失败
        </span>
      );
    }
    if (stashWriteState === "idle") {
      return <span className={className}>未保存</span>;
    }
    return (
      <span
        className={className}
        title={
          autosaveToServer
            ? "每 10 秒自动保存到服务端；期间的改动也实时留存在本浏览器。"
            : "此方向为 auto 模式，保存即会启动节点，因此不自动上传；改动实时留存在本浏览器，由你点击 Save changes 决定何时提交。"
        }
      >
        {autosaveToServer ? "未保存 · 本地已留存" : "未保存 · 仅本地留存"}
      </span>
    );
  }
  if (autosavedAt !== null) {
    return (
      <span className={className}>
        已自动保存{" "}
        {new Date(autosavedAt).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        })}
      </span>
    );
  }
  return null;
}

function BriefBlock({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-state-review">
        {label}
      </div>
      <div className="whitespace-pre-wrap text-[12px] leading-relaxed text-ink">
        {text}
      </div>
    </div>
  );
}

const inputClassName =
  "w-full rounded-md border border-line bg-surface px-2 py-1.5 text-[12px] text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none";

const fieldClassName =
  "w-full resize-y rounded-md border border-line bg-surface px-2 py-1.5 text-[12px] leading-relaxed text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none";

function FieldLabel({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-ink-subtle">
        {label}
      </div>
      {children}
    </label>
  );
}

function PrinciplesAttachSection({
  principles,
  attached,
  onChange,
}: {
  principles?: PrincipleSummary[];
  attached: string[];
  onChange: (next: string[]) => void;
}) {
  const available = principles ?? [];
  const [pickerOpen, setPickerOpen] = useState(false);
  const attachedIds = new Set(attached);
  const attachedPrinciples = attached.map((id) => ({
    id,
    title: available.find((s) => s.id === id)?.title ?? id,
  }));
  const remaining = available.filter((s) => !attachedIds.has(s.id));

  /* The tree is keyed on the hyphen-segmented slug, while the draft stores the
   * prefixed plug id (`principles.foo`). Already-attached entries stay visible
   * but disabled so the hierarchy does not reshuffle as items are picked. */
  const pickerEntries: HierarchyEntry[] = available.map((principle) => ({
    id: principle.id,
    name: principle.slug,
    description: principle.description,
    disabled: attachedIds.has(principle.id),
  }));

  const attach = (id: string) => {
    if (!id || attachedIds.has(id)) return;
    onChange([...attached, id]);
  };
  const detach = (id: string) => {
    onChange(attached.filter((x) => x !== id));
  };

  return (
    <section className="mb-5">
      <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
        <div className="border-b border-line px-3 py-2">
          <SectionHeading
            right={
              attached.length > 0 ? (
                <span className="text-[10px] font-normal normal-case tracking-normal text-ink-subtle">
                  {attached.length} attached
                </span>
              ) : null
            }
          >
            Principles
          </SectionHeading>
        </div>
        <div className="space-y-2 px-3 py-3">
          {attached.length === 0 ? (
            <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
              No principles attached.
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {attachedPrinciples.map((s) => (
                <span
                  key={s.id}
                  className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2 py-0.5 text-[11px] text-ink"
                >
                  <span className="font-medium">{s.title}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${s.title}`}
                    onClick={() => detach(s.id)}
                    className="text-ink-muted hover:text-state-error"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          {remaining.length > 0 && (
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              className="w-full rounded-md border border-dashed border-line bg-surface px-2 py-1.5 text-[11.5px] text-ink-muted transition hover:border-brand/60 hover:text-ink"
            >
              + 添加 principle
            </button>
          )}
          {available.length === 0 && (
            <div className="rounded-md border border-dashed border-line bg-surface px-3 py-2 text-[11px] text-ink-muted">
              No principles yet. Author one with the "New principle / skill" button on the
              project panel.
            </div>
          )}
        </div>
      </div>
      <EntryPickerModal
        open={pickerOpen}
        kind="principle"
        title="选择 principle"
        entries={pickerEntries}
        emptyLabel="还没有 principle。可在项目面板用「New principle / skill」创建。"
        onCancel={() => setPickerOpen(false)}
        onPick={(entry) => {
          attach(entry.id);
          setPickerOpen(false);
        }}
      />
    </section>
  );
}

function SkillsAttachSection({
  skills,
  attached,
  onChange,
}: {
  skills?: SkillSummary[];
  attached: SkillSelection[];
  onChange: (next: SkillSelection[]) => void;
}) {
  const available = skills ?? [];
  const [pickerOpen, setPickerOpen] = useState(false);
  const attachedIds = new Set(attached.map((selection) => selection.id));
  const remaining = available.filter((skill) => !attachedIds.has(skill.id));
  /* `slug` drives the hierarchy; `id` (`skills.<slug>`) is what the draft and
   * the backend speak. Entries already in the draft — including the ones the
   * backend auto-attached — are shown disabled rather than removed. */
  const pickerEntries: HierarchyEntry[] = available.map((skill) => ({
    id: skill.id,
    name: skill.slug,
    description: skill.description,
    disabled: attachedIds.has(skill.id),
  }));
  return (
    <section className="mb-5 overflow-hidden rounded-md border border-state-review/35 bg-state-review/5">
      <div className="border-b border-state-review/25 px-3 py-2">
        <SectionHeading
          right={attached.length ? <span className="font-mono">{attached.length}</span> : null}
        >
          Skills
        </SectionHeading>
      </div>
      <div className="space-y-2 px-3 py-3">
        {attached.map((selection) => {
          const skill = available.find((candidate) => candidate.id === selection.id);
          const autoLabel =
            selection.attachment_reason === "package"
              ? "Pack"
              : selection.attachment_reason === "dependency"
                ? "Dependency"
                : null;
          return (
            <div key={selection.id} className="flex items-center gap-2 rounded border border-line bg-surface px-2 py-1">
              <span className="min-w-0 flex-1 truncate text-[11px] text-ink" title={selection.id}>
                {skill?.title ?? selection.id}
              </span>
              {autoLabel ? (
                <span
                  className="rounded border border-line px-1 py-0.5 text-[9px] uppercase text-ink-subtle"
                  title={selection.required_by ? `Required by ${selection.required_by}` : undefined}
                >
                  {autoLabel}
                </span>
              ) : (
                <>
                  <label className="flex items-center gap-1 text-[10px] text-ink-muted">
                    <input
                      type="checkbox"
                      checked={selection.suggest}
                      onChange={(event) =>
                        onChange(attached.map((item) =>
                          item.id === selection.id
                            ? { ...item, suggest: event.target.checked }
                            : item,
                        ))
                      }
                    />
                    Suggest
                  </label>
                  <button
                    type="button"
                    onClick={() => onChange(attached.filter((item) => item.id !== selection.id))}
                    className="text-[13px] text-ink-muted hover:text-state-error"
                    title="Remove skill"
                    aria-label={`Remove ${skill?.title ?? selection.id}`}
                  >
                    ×
                  </button>
                </>
              )}
            </div>
          );
        })}
        {remaining.length > 0 && (
          <button
            type="button"
            onClick={() => setPickerOpen(true)}
            className="w-full rounded-md border border-dashed border-line bg-surface px-2 py-1.5 text-[11px] text-ink-muted transition hover:border-brand/60 hover:text-ink"
          >
            + 添加 skill
          </button>
        )}
        {attached.length === 0 && remaining.length === 0 && (
          <div className="text-[11px] text-ink-muted">No skills available.</div>
        )}
      </div>
      <EntryPickerModal
        open={pickerOpen}
        kind="skill"
        title="选择 skill"
        entries={pickerEntries}
        emptyLabel="还没有 skill。"
        onCancel={() => setPickerOpen(false)}
        onPick={(entry) => {
          /* Only `{id, suggest:false}` — the backend's `expand_skill_selections`
           * owns package members and frontmatter dependencies, and the panel's
           * PendingLocalVirtualUpdate absorbs the wider list it returns. */
          if (attachedIds.has(entry.id)) return;
          onChange([...attached, { id: entry.id, suggest: false }]);
          setPickerOpen(false);
        }}
      />
    </section>
  );
}

export function virtualDraftFromNode(node: NodeInfo): VirtualDraft {
  return {
    promptDraft: node.prompt_draft || node.prompt || "",
    motivation: node.summary || "",
    modelPresetId: node.model_preset_id || "",
    classification: nodeClassification(node),
    subtype: node.subtype || "agentic_review",
    brief: node.brief || {
      check_what: "",
      expected: "",
      abnormal: "",
    },
    scheduledDeps: [...(node.scheduled_deps ?? [])],
    pendingExtraPrinciples: [...(node.pending_extra_principles ?? [])],
    pendingExtraSkills: [...(node.pending_extra_skills ?? [])],
    qaMode: node.qa_mode ?? false,
    artifactMode: node.artifact_mode || "default",
    artifactSpec: node.artifact_spec || "",
    obsoleteReason: node.obsolete_reason || "",
  };
}

const DRAFT_FIELD_LABELS: Record<keyof VirtualDraft, string> = {
  promptDraft: "Prompt",
  motivation: "动机",
  modelPresetId: "模型",
  classification: "节点类型",
  subtype: "评审子类",
  brief: "评审说明",
  scheduledDeps: "依赖",
  pendingExtraPrinciples: "附加准则",
  pendingExtraSkills: "附加技能",
  qaMode: "允许提问",
  artifactMode: "产出物",
  artifactSpec: "产出物描述",
  obsoleteReason: "作废原因",
};

/* Reconciles an external write to this virtual against the user's unsaved
 * edits, one field at a time. A whole-object reset would be correct only if
 * every write touched every field, but the canvas writes `scheduled_deps`
 * alone — resetting on it would silently discard a prompt the user is still
 * typing.
 *
 * A field the remote left alone keeps the local edit. A field the remote moved
 * adopts the persisted value: it is the only authoritative copy, and letting a
 * local edit win silently would leave the user believing they were looking at
 * saved state. Those adoptions that overwrite a real local edit come back in
 * `conflicts` so the caller can say so rather than swallow it. */
export function mergeVirtualDraft(
  local: VirtualDraft,
  previousPersisted: VirtualDraft,
  nextPersisted: VirtualDraft,
): { draft: VirtualDraft; conflicts: (keyof VirtualDraft)[] } {
  const merged: VirtualDraft = { ...nextPersisted };
  const conflicts: (keyof VirtualDraft)[] = [];
  for (const key of Object.keys(nextPersisted) as (keyof VirtualDraft)[]) {
    const remoteBefore = JSON.stringify(previousPersisted[key]);
    const remoteAfter = JSON.stringify(nextPersisted[key]);
    if (remoteBefore === remoteAfter) {
      (merged as Record<string, unknown>)[key] = local[key];
      continue;
    }
    if (JSON.stringify(local[key]) !== remoteBefore) {
      conflicts.push(key);
    }
  }
  return { draft: merged, conflicts };
}

export function stashedDraftRestoredMessage(savedAt: number): string {
  const at = new Date(savedAt).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  return `已恢复 ${at} 的未保存草稿（保存在本浏览器）。`;
}

export function stashedDraftConflictMessage(
  conflicts: readonly (keyof VirtualDraft)[],
): string {
  const names = conflicts.map((key) => DRAFT_FIELD_LABELS[key]).join("、");
  return `其中这些字段在别处已被改动，已采用最新的持久化值：${names}。`;
}

export function externalDraftConflictMessage(
  conflicts: readonly (keyof VirtualDraft)[],
): string {
  const names = conflicts.map((key) => DRAFT_FIELD_LABELS[key]).join("、");
  return `此 virtual node 已在别处更新，以下字段已采用最新的持久化值，覆盖了你未保存的改动：${names}。其余未保存的改动仍保留。`;
}

/* Nodes that may be picked as dependencies of `node`. Obsolete nodes are kept
 * out so they are not newly chosen, but one that is already selected must stay
 * listed — otherwise it vanishes from the panel while its id remains in the
 * draft, leaving the user unable to uncheck it. */
export function candidateDependencies(
  node: NodeInfo,
  nodesById: Map<string, NodeInfo>,
  selectedDepIds: readonly string[],
): NodeInfo[] {
  return Array.from(nodesById.values()).filter(
    (candidate) =>
      candidate.id !== node.id &&
      (candidate.planspace_id ?? "") === (node.planspace_id ?? "") &&
      (!candidate.obsolete_reason || selectedDepIds.includes(candidate.id)),
  );
}

export function virtualDraftAfterSave(draft: VirtualDraft): VirtualDraft {
  const artifactMode = artifactModeAvailable(draft.classification)
    ? draft.artifactMode
    : "default";
  const normalized: VirtualDraft = {
    ...draft,
    qaMode: qaModeAvailable(draft.classification) ? draft.qaMode : false,
    artifactMode,
    artifactSpec: artifactMode === "custom" ? draft.artifactSpec.trim() : "",
    obsoleteReason: draft.obsoleteReason.trim(),
  };
  if (draft.classification === "review") {
    return normalized;
  }
  return {
    ...normalized,
    subtype: "agentic_review",
    brief: {
      check_what: "",
      expected: "",
      abnormal: "",
    },
  };
}

export function virtualDraftValidationError(
  draft: VirtualDraft,
  node: NodeInfo,
): string | null {
  const allowsEmptyPrompt =
    draft.classification === "review" && draft.subtype === "code_review";
  if (!allowsEmptyPrompt && !draft.promptDraft.trim()) {
    return "请先填写 Prompt draft。";
  }
  if (!node.resume_from_node_id && !draft.modelPresetId) {
    return "请先选择模型档位。";
  }
  if (
    artifactModeAvailable(draft.classification) &&
    draft.artifactMode === "custom" &&
    !draft.artifactSpec.trim()
  ) {
    return "选择 custom 时请先描述期望的产出物。";
  }
  return null;
}

function virtualDraftReadyToPromote(
  draft: VirtualDraft,
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
): boolean {
  if (draft.obsoleteReason.trim() || virtualDraftValidationError(draft, node)) {
    return false;
  }
  for (const depId of draft.scheduledDeps) {
    const dep = byId.get(depId);
    if (!dep) continue;
    if (isTerminal(dep.state)) continue;
    if (dep.state === "virtual" && dep.obsolete_reason) continue;
    return false;
  }
  return true;
}

export function virtualPayloadFromDraft(
  draft: VirtualDraft,
  node: NodeInfo,
): UpdateVirtualPayload {
  const isLibrary = draft.classification === "library";
  const payload: UpdateVirtualPayload = {
    prompt_draft: draft.promptDraft,
    motivation: draft.motivation,
    category: categoryForClassification(draft.classification),
    scheduled_deps: draft.scheduledDeps,
    pending_extra_principles: draft.pendingExtraPrinciples,
    pending_extra_skills: draft.pendingExtraSkills,
    obsolete_reason: draft.obsoleteReason.trim() || null,
  };
  /* Only send agent_op_kind when it actually changes. Leaving a historical
   * principle_edit node untouched keeps it replaying under its original op
   * kind rather than silently migrating it to library_edit. */
  const nextOpKind = isLibrary
    ? isLibraryOpKind(node.agent_op_kind)
      ? node.agent_op_kind ?? "library_edit"
      : "library_edit"
    : null;
  if (nextOpKind !== (node.agent_op_kind ?? null)) {
    payload.agent_op_kind = nextOpKind;
  }
  // Model preset is locked to the resume source for continuation virtuals;
  // omit it from the payload so the backend doesn't have to re-check equality.
  if (!node.resume_from_node_id && draft.modelPresetId !== node.model_preset_id) {
    payload.model_preset_id = draft.modelPresetId;
  }
  if (draft.classification === "review") {
    payload.subtype = draft.subtype;
    payload.brief =
      draft.subtype === "code_review" &&
      !draft.brief.check_what.trim() &&
      !draft.brief.expected.trim() &&
      !draft.brief.abnormal.trim()
        ? null
        : draft.brief;
    payload.review_target =
      draft.subtype === "code_review" ? { type: "uncommitted" } : null;
  } else {
    payload.subtype = null;
    payload.brief = null;
    payload.review_target = null;
  }
  /* Both branches send explicitly. Switching a work node that carried an
   * artifact intent over to review would otherwise post a review category
   * alongside a stale artifact_mode, and the backend's paired invariant
   * rejects that with a 400 the user cannot connect to what they just did. */
  payload.qa_mode = qaModeAvailable(draft.classification) ? draft.qaMode : false;
  if (artifactModeAvailable(draft.classification)) {
    payload.artifact_mode = draft.artifactMode;
    payload.artifact_spec =
      draft.artifactMode === "custom" ? draft.artifactSpec.trim() : "";
  } else {
    payload.artifact_mode = "default";
    payload.artifact_spec = "";
  }
  return payload;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function oneLine(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

function AgentInputCard({
  node,
  contextBundle,
  loading,
}: {
  node: NodeInfo;
  contextBundle: ContextBundle | null;
  loading: boolean;
}) {
  const systemSources = useMemo(
    () => (contextBundle?.sources ?? []).filter((s) => s.injection === "system"),
    [contextBundle],
  );
  const turnSources = useMemo(
    () => (contextBundle?.sources ?? []).filter((s) => s.injection === "turn"),
    [contextBundle],
  );
  const input = agentInputText(node, contextBundle);

  return (
    <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
      <div className="border-b border-line px-3 py-2">
        <SectionHeading
          right={
            loading ? (
              <span className="text-[10px] font-normal normal-case tracking-normal text-ink-subtle">
                loading...
              </span>
            ) : null
          }
        >
          Agent input
        </SectionHeading>
      </div>
      <div className="space-y-2 px-3 py-3">
        <PromptBlock
          label="System prompt"
          text={input.systemText}
          sources={systemSources}
        />
        <PromptBlock
          label="Node instructions"
          text={input.nodeInstructions}
          sources={turnSources}
        />
        <PromptBlock label="Input prompt" text={input.userPrompt} />
      </div>
    </div>
  );
}

export function agentInputText(
  node: NodeInfo,
  contextBundle: ContextBundle | null,
): {
  systemText: string;
  nodeInstructions: string;
  userPrompt: string;
} {
  const turnText = contextBundle?.turn_text?.trim() || "";
  return {
    systemText:
      contextBundle?.system_text?.trim() ||
      node.system_context_snapshot?.trim() ||
      "",
    // Historical nodes predate the exact launch snapshot. Their ContextSpace
    // turn injection is still useful and was the only turn-level material the
    // previous inspector exposed.
    nodeInstructions:
      node.launch_instructions_snapshot?.trim() || turnText,
    userPrompt: node.prompt?.trim() || "",
  };
}

function BasicInformationCard({
  node,
  modelPresets,
}: {
  node: NodeInfo;
  modelPresets: ModelPreset[];
}) {
  const presetLabel = modelPresetLabel(modelPresets, node.model_preset_id);
  const presetDetail = modelPresetDetail(modelPresets, node.model_preset_id);
  const resumeSource = node.resume_from_node_id || node.parent_node_id || "-";
  return (
    <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
      <div className="border-b border-line px-3 py-2">
        <SectionHeading>Basic information</SectionHeading>
      </div>
      <div className="px-3 py-3">
        <KVGrid
          rows={[
            ["Node ID", node.id],
            ["Type", node.kind === "verifier" ? "programmatic verifier" : node.kind],
            ["Category", nodeCategoryLabel(node)],
            ["Model", presetDetail ? `${presetLabel} · ${presetDetail}` : presetLabel],
            ["Provider", providerLabel(node.provider)],
            ["Planspace", node.planspace_id || "-"],
            ["Continues from", resumeSource],
            ["Created", formatTimestamp(node.created_at)],
            ["Started", formatTimestamp(node.started_at)],
            ["Finished", formatTimestamp(node.finished_at)],
          ]}
        />
      </div>
    </div>
  );
}

function PromptBlock({
  label,
  text,
  sources = [],
}: {
  label: string;
  text: string;
  sources?: ContextBundleSource[];
}) {
  const totalChars = text.length;
  const fileSources = sources.filter((s) => s.path);
  return (
    <details className="overflow-hidden rounded border border-line bg-surface">
      <summary className="flex cursor-pointer items-center justify-between gap-2 px-3 py-1.5">
        <span className="text-[11px] font-medium text-ink">{label}</span>
        <span className="font-mono text-[10.5px] text-ink-subtle">
          {fileSources.length > 0 ? `${fileSources.length} file${fileSources.length === 1 ? "" : "s"} · ` : ""}
          {totalChars} chars
        </span>
      </summary>
      <div className="border-t border-line px-3 py-2">
        {text ? (
          <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink">
            {renderWithFootnotes(text, fileSources)}
          </pre>
        ) : (
          <div className="text-[11px] text-ink-muted">No content.</div>
        )}
        {fileSources.length > 0 && (
          <ol className="mt-3 list-none space-y-0.5 border-t border-line/60 pt-2 font-mono text-[10.5px] text-ink-muted">
            {fileSources.map((src, i) => (
              <li key={`${src.path}-${i}`} className="flex gap-2">
                <span className="flex-none text-ink-subtle">[^{i + 1}]</span>
                <span className="min-w-0 truncate" title={src.path}>
                  {src.path}
                  <span className="ml-1 text-ink-subtle">
                    · {src.scope}/{src.kind}
                    {src.plug_id ? ` · ${src.plug_id}` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </details>
  );
}

function renderWithFootnotes(text: string, sources: ContextBundleSource[]): string {
  if (sources.length === 0) return text;
  let out = text;
  sources.forEach((src, i) => {
    const marker = `[^${i + 1}]`;
    if (out.includes(marker)) return;
    for (const candidate of [src.plug_id, src.path].filter(Boolean) as string[]) {
      const re = new RegExp(
        `^(# Loaded Context:\\s+${escapeRegExp(candidate)}[^\\n]*)$`,
        "m",
      );
      if (re.test(out)) {
        out = out.replace(re, `$1 ${marker}`);
        break;
      }
    }
  });
  return out;
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function PreviewCard({
  preview,
  loading,
}: {
  preview: string | null;
  loading: boolean;
}) {
  const fields = useMemo(() => parsePreviewFields(preview), [preview]);

  return (
    <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
      <div className="border-b border-line px-3 py-2">
        <SectionHeading
          right={
            loading ? (
              <span className="text-[10px] font-normal normal-case tracking-normal text-ink-subtle">
                loading...
              </span>
            ) : null
          }
        >
          Preview
        </SectionHeading>
      </div>
      <div className="p-2.5">
        {fields ? (
          /* An implicit column is sized `auto`, whose floor is the widest
             unbreakable token in the field text — agents routinely cite full
             paths like `.miniclaw2/graph/runs/<id>/lanes/<lane>/preview.json`,
             which is far wider than the 380px panel. Pinning the single track
             to `minmax(0,1fr)` caps it at the card so `break-words` below can
             actually wrap those tokens instead of overflowing into the card's
             `overflow-hidden`. */
          <dl className="grid grid-cols-[minmax(0,1fr)] gap-2">
            <PreviewField
              label="运行原因"
              value={fields.motivation}
              tone="motivation"
            />
            <PreviewField
              label="结果摘要"
              value={fields.summary}
              tone="summary"
            />
            <PreviewField
              label="后续影响"
              value={fields.nextImplications}
              tone="implications"
            />
          </dl>
        ) : (
          <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
            {preview ? "Preview 格式不可用。" : "暂无 Preview。"}
          </div>
        )}
      </div>
    </div>
  );
}

type PreviewFields = {
  motivation: string;
  summary: string;
  nextImplications: string;
};

type PreviewFieldTone = "motivation" | "summary" | "implications";

function parsePreviewFields(preview: string | null): PreviewFields | null {
  if (!preview) return null;
  try {
    const parsed: unknown = JSON.parse(preview);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }
    const record = parsed as Record<string, unknown>;
    return {
      motivation: previewFieldValue(record.motivation),
      summary: previewFieldValue(record.summary),
      nextImplications: previewFieldValue(record.next_implications),
    };
  } catch {
    return null;
  }
}

const PREVIEW_FIELD_EMPTY = "未记录";

function previewFieldValue(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : PREVIEW_FIELD_EMPTY;
}

function PreviewField({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: PreviewFieldTone;
}) {
  const styles = {
    motivation: {
      panel: "border-brand/25 bg-brand-soft/45",
      icon: "bg-brand/10 text-brand dark:text-brand",
    },
    summary: {
      panel: "border-state-done/25 bg-state-done-soft/45",
      icon: "bg-state-done/10 text-state-done",
    },
    implications: {
      panel: "border-state-review/25 bg-state-review-soft/40",
      icon: "bg-state-review/10 text-state-review",
    },
  }[tone];

  return (
    <ZoomableText
      title={label}
      text={value === PREVIEW_FIELD_EMPTY ? "" : value}
      defaultView="markdown"
      className={`flex items-start gap-2.5 rounded-md border px-2.5 py-2 ${styles.panel}`}
    >
      <span
        className={`mt-px flex h-6 w-6 flex-none items-center justify-center rounded ${styles.icon}`}
        aria-hidden="true"
      >
        <PreviewFieldIcon tone={tone} />
      </span>
      <div className="min-w-0 flex-1">
        <dt className="text-[9.5px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
          {label}
        </dt>
        <dd className="mt-0.5 whitespace-pre-wrap break-words text-[11.5px] leading-[1.45] text-ink-strong">
          {value}
        </dd>
      </div>
    </ZoomableText>
  );
}

function PreviewFieldIcon({ tone }: { tone: PreviewFieldTone }) {
  if (tone === "motivation") {
    return (
      <svg
        viewBox="0 0 16 16"
        className="h-3.5 w-3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      >
        <path d="M8 1.75a4.25 4.25 0 0 0-2.6 7.61c.55.43.85.94.85 1.49v.4h3.5v-.4c0-.55.3-1.06.85-1.49A4.25 4.25 0 0 0 8 1.75Z" />
        <path d="M6.25 13.25h3.5M7 11.25V8.5h2v2.75" />
      </svg>
    );
  }
  if (tone === "summary") {
    return (
      <svg
        viewBox="0 0 16 16"
        className="h-3.5 w-3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      >
        <path d="m3 8.25 3 3 7-7" />
      </svg>
    );
  }
  return (
    <svg
      viewBox="0 0 16 16"
      className="h-3.5 w-3.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="M2.5 4.5h4a2 2 0 0 1 2 2v5M8.5 8.5l3 3-3 3M2.5 11.5h2" />
    </svg>
  );
}

type TranscriptItem =
  | { kind: "user"; id: string; text: string }
  | { kind: "text"; id: string; text: string }
  | { kind: "error"; id: string; text: string }
  | { kind: "tools"; id: string; items: Activity[] };

function useIncrementalTurns(
  node: NodeInfo,
  records: EventRecord[],
): ReturnType<typeof buildTurnsFromEvents> {
  const cacheRef = useRef<{
    nodeId: string;
    nodeState: NodeInfo["state"];
    prompt: string;
    records: EventRecord[];
    turns: ReturnType<typeof buildTurnsFromEvents>;
  } | null>(null);

  return useMemo(() => {
    const cached = cacheRef.current;
    const cachedLength = cached?.records.length ?? 0;
    const extendsCachedRecords =
      cached !== null &&
      cached.nodeId === node.id &&
      cached.prompt === node.prompt &&
      records.length >= cachedLength &&
      (cachedLength === 0 || cached.records.at(-1) === records[cachedLength - 1]);

    let turns = extendsCachedRecords
      ? appendRecordsToTurns(cached.turns, records.slice(cachedLength))
      : buildTurnsFromEvents(node, records);
    const active = node.state === "running" || node.state === "waiting";
    const wasActive =
      cached?.nodeState === "running" || cached?.nodeState === "waiting";
    if (!active || (extendsCachedRecords && !wasActive)) {
      turns = setTurnsStreaming(turns, active);
    }
    cacheRef.current = {
      nodeId: node.id,
      nodeState: node.state,
      prompt: node.prompt,
      records,
      turns,
    };
    return turns;
  }, [node.id, node.prompt, node.state, records]);
}

function flattenTranscript(turns: ReturnType<typeof buildTurnsFromEvents>): TranscriptItem[] {
  const out: TranscriptItem[] = [];
  for (const turn of turns) {
    if (turn.role === "user") {
      const trimmed = turn.text.trim();
      if (trimmed) out.push({ kind: "user", id: turn.id, text: trimmed });
      continue;
    }
    for (const block of turn.blocks) {
      if (block.kind === "text") {
        if (block.text.trim()) out.push({ kind: "text", id: block.id, text: block.text });
      } else if (block.kind === "activity") {
        if (block.items.length > 0) out.push({ kind: "tools", id: block.id, items: block.items });
      } else if (block.kind === "error") {
        out.push({ kind: "error", id: block.id, text: block.text });
      }
    }
  }
  return out;
}

function ActivityTranscript({
  items,
  streaming,
}: {
  items: TranscriptItem[];
  streaming: boolean;
}) {
  if (items.length === 0) {
    return (
      <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
        {streaming ? (
          <span className="inline-flex items-center gap-1 text-ink-subtle">
            <span className="stream-dot inline-block h-1.5 w-1.5 rounded-full bg-current" />
            <span
              className="stream-dot inline-block h-1.5 w-1.5 rounded-full bg-current"
              style={{ animationDelay: "0.18s" }}
            />
            <span
              className="stream-dot inline-block h-1.5 w-1.5 rounded-full bg-current"
              style={{ animationDelay: "0.36s" }}
            />
          </span>
        ) : (
          "No activity yet."
        )}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {items.map((item) => (
        <TranscriptItemView key={item.id} item={item} />
      ))}
    </div>
  );
}

const TranscriptItemView = memo(function TranscriptItemView({
  item,
}: {
  item: TranscriptItem;
}) {
  if (item.kind === "user") {
    return (
      <div className="rounded-md border border-line bg-surface px-3 py-2 text-[12px] text-ink-muted">
        <div className="mb-1 text-[10px] uppercase tracking-[0.12em] text-ink-subtle">
          user
        </div>
        <pre className="whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-ink">
          {item.text}
        </pre>
      </div>
    );
  }
  if (item.kind === "text") {
    return (
      <ZoomableText
        title="Agent output"
        text={item.text}
        defaultView="markdown"
        className="rounded-md border border-line bg-surface-raised"
      >
        <div className="md-prose px-3 py-2 text-[13px] leading-relaxed text-ink-strong">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
          >
            {item.text}
          </ReactMarkdown>
        </div>
      </ZoomableText>
    );
  }
  if (item.kind === "error") {
    return (
      <pre className="whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft p-2 text-[11.5px] text-state-error">
        {item.text}
      </pre>
    );
  }
  return <ToolActivity items={item.items} />;
}, areTranscriptItemsEqual);

function areTranscriptItemsEqual(
  previous: { item: TranscriptItem },
  next: { item: TranscriptItem },
): boolean {
  const left = previous.item;
  const right = next.item;
  if (left === right) return true;
  if (left.kind !== right.kind || left.id !== right.id) return false;
  if (left.kind !== "tools" && right.kind !== "tools") {
    return left.text === right.text;
  }
  if (left.kind !== "tools" || right.kind !== "tools") return false;
  if (left.items.length !== right.items.length) return false;
  return left.items.every((activity, index) => {
    const candidate = right.items[index];
    return (
      activity.id === candidate.id &&
      activity.kind === candidate.kind &&
      activity.status === candidate.status &&
      activity.name === candidate.name &&
      activity.summary === candidate.summary &&
      activity.parameters === candidate.parameters &&
      activity.command === candidate.command &&
      activity.result === candidate.result &&
      activity.result_kind === candidate.result_kind
    );
  });
}

function ThinkingSection({ turns }: { turns: ReturnType<typeof buildTurnsFromEvents> }) {
  const thinking = useMemo(() => {
    const out: string[] = [];
    for (const t of turns) {
      if (t.role !== "assistant") continue;
      for (const b of t.blocks) {
        if (b.kind === "thinking") out.push(b.text);
      }
    }
    return out;
  }, [turns]);
  if (thinking.length === 0) return null;
  return (
    <section className="mb-5">
      <details className="overflow-hidden rounded-md border border-line bg-surface-sunken">
        <summary className="cursor-pointer px-3 py-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted hover:text-ink">
          Thinking ({thinking.length} block{thinking.length === 1 ? "" : "s"})
        </summary>
        <div className="space-y-2 border-t border-line px-3 py-2">
          {thinking.map((text, i) => (
            <pre
              key={i}
              className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink-muted"
            >
              {text}
            </pre>
          ))}
        </div>
      </details>
    </section>
  );
}

function StatePill({ state }: { state: NodeInfo["state"] }) {
  const map: Record<NodeInfo["state"], { bg: string; text: string; label: string }> = {
    virtual: { bg: "bg-surface-sunken", text: "text-ink-muted", label: "virtual" },
    queued: { bg: "bg-state-queued-soft", text: "text-ink-muted", label: "queued" },
    running: {
      bg: "bg-state-running-soft",
      text: "text-brand-ink dark:text-brand",
      label: "running",
    },
    waiting: {
      bg: "bg-state-waiting-soft",
      text: "text-state-waiting",
      label: "waiting",
    },
    awaiting_human_input: {
      bg: "bg-state-review-soft",
      text: "text-state-review",
      label: "human input",
    },
    done: { bg: "bg-state-done-soft", text: "text-ink-muted", label: "done" },
    error: { bg: "bg-state-error-soft", text: "text-state-error", label: "error" },
    cancelled: {
      bg: "bg-state-cancelled-soft",
      text: "text-ink-subtle",
      label: "cancelled",
    },
  };
  const m = map[state];
  return (
    <span
      className={
        "inline-block rounded px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em] " +
        m.bg +
        " " +
        m.text
      }
    >
      {m.label}
    </span>
  );
}

function nodeCategoryLabel(node: NodeInfo): string {
  return nodeClassificationLabel(node);
}

function formatTimestamp(value?: number | null): string {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString();
}

function KVGrid({
  rows,
  className,
}: {
  rows: Array<[string, string]>;
  className?: string;
}) {
  return (
    /* `minmax(0,1fr)`, not `1fr`: a bare `1fr` is `minmax(auto,1fr)`, whose
       auto floor is the value's min-content width. Values here are single
       unbreakable tokens (node ids, planspace ids, model slugs) wider than
       the 380px panel, so the track would outgrow the card and the card's
       `overflow-hidden` would clip it. `break-words` alone cannot rescue
       that — it wraps visually without lowering min-content. */
    <dl className={"grid grid-cols-[120px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[11.5px] " + (className ?? "")}>
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-subtle">{k}</dt>
          <dd className="whitespace-pre-wrap break-words font-mono text-ink">{v || "-"}</dd>
        </div>
      ))}
    </dl>
  );
}

function isTerminal(state: NodeInfo["state"]): boolean {
  return state === "done" || state === "error" || state === "cancelled";
}

function virtualReady(node: NodeInfo, byId: Map<string, NodeInfo>): boolean {
  if (node.state !== "virtual" || node.obsolete_reason) return false;
  if (node.subtype !== "code_review" && !(node.prompt_draft || "").trim()) return false;
  for (const depId of node.scheduled_deps ?? []) {
    const dep = byId.get(depId);
    if (!dep) continue;
    if (isTerminal(dep.state)) continue;
    if (dep.state === "virtual" && dep.obsolete_reason) continue;
    return false;
  }
  return true;
}
