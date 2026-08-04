import { memo, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

import { getNodePreview } from "../api";
import type {
  Activity,
  ContextBundle,
  ContextBundleSource,
  EventRecord,
  InteractionRequest,
  ModelPreset,
  NodeCategory,
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
import {
  PendingGateInline,
  type ResolveGatePayload,
} from "../components/PendingGateInline";
import { canResumeNode } from "../nodeUtil";
import { GateReviewForm } from "./gateReview";
import { InspectDrawer } from "./InspectDrawer";
import {
  modelPresetDetail,
  modelPresetLabel,
  providerLabel,
  selectableModelPresets,
} from "../modelPresets";

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
  onPromoteVirtual: (nodeId: string) => void;
  onUpdateVirtual: (nodeId: string, payload: UpdateVirtualPayload) => Promise<void>;
  onInterruptNode: (nodeId: string) => void;
  onRerunNode: (nodeId: string) => void;
  canInterrupt: boolean;
  canRerun: boolean;
  canMutate: boolean;
  manualPromotionPlanspaceId: string | null;
  focusRequestVersion: number;
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
  onUpdateVirtual,
  onInterruptNode,
  onRerunNode,
  canInterrupt,
  canRerun,
  canMutate,
  manualPromotionPlanspaceId,
  focusRequestVersion,
  onSelectArtifact,
}: AgentPanelProps) {
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

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <StatePill state={node.state} />
              {node.agent_op_kind === "library_edit" && (
                <span className="rounded border border-line bg-surface px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] text-ink-muted">
                  librarian
                </span>
              )}
            </div>
            <h2 className="mt-1.5 line-clamp-2 font-display text-[15px] font-semibold leading-snug text-ink-strong">
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
            {node.state === "virtual" &&
            canMutate &&
            node.planspace_id === manualPromotionPlanspaceId ? (
              <button
                type="button"
                onClick={() => onPromoteVirtual(node.id)}
                disabled={!readyToPromote}
                className="rounded-md bg-brand px-2.5 py-1 text-[11px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
                title={readyToPromote ? "Promote virtual node" : "Dependencies are not terminal"}
              >
                Promote
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

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        {!canMutate && (
          <div className="mb-3 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] text-state-waiting">
            This project is read-only on this machine.
          </div>
        )}
        <section className="mb-5">
          <BasicInformationCard node={node} modelPresets={modelPresets} />
        </section>
        {node.state === "virtual" ? (
          <fieldset disabled={!canMutate} className={canMutate ? "contents" : "contents opacity-75"}>
            <VirtualNodeBody
              node={node}
              nodesById={nodesById}
              modelPresets={modelPresets}
              principles={principles}
              skills={skills}
              onUpdateVirtual={onUpdateVirtual}
              focusRequestVersion={canMutate ? focusRequestVersion : 0}
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
                node={node}
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

type VirtualDraft = {
  promptDraft: string;
  motivation: string;
  modelPresetId: string;
  category: NodeCategory;
  subtype: ReviewSubtype;
  brief: ReviewBrief;
  scheduledDeps: string[];
  pendingExtraPrinciples: string[];
  pendingExtraSkills: SkillSelection[];
  obsoleteReason: string;
};

type PendingLocalVirtualUpdate = {
  nodeId: string;
  persistedSignature: string;
  draftAfterAck: VirtualDraft;
};

function VirtualNodeBody({
  node,
  nodesById,
  modelPresets,
  principles,
  skills,
  onUpdateVirtual,
  focusRequestVersion,
}: {
  node: NodeInfo;
  nodesById: Map<string, NodeInfo>;
  modelPresets: ModelPreset[];
  principles?: PrincipleSummary[];
  skills?: SkillSummary[];
  onUpdateVirtual: (nodeId: string, payload: UpdateVirtualPayload) => Promise<void>;
  focusRequestVersion: number;
}) {
  if (node.kind === "verifier") {
    return <VerifierVirtualBody node={node} nodesById={nodesById} />;
  }
  return (
    <EditableVirtualNodeBody
      node={node}
      nodesById={nodesById}
      modelPresets={modelPresets}
      principles={principles}
      skills={skills}
      onUpdateVirtual={onUpdateVirtual}
      focusRequestVersion={focusRequestVersion}
    />
  );
}

function EditableVirtualNodeBody({
  node,
  nodesById,
  modelPresets,
  principles,
  skills,
  onUpdateVirtual,
  focusRequestVersion,
}: {
  node: NodeInfo;
  nodesById: Map<string, NodeInfo>;
  modelPresets: ModelPreset[];
  principles?: PrincipleSummary[];
  skills?: SkillSummary[];
  onUpdateVirtual: (nodeId: string, payload: UpdateVirtualPayload) => Promise<void>;
  focusRequestVersion: number;
}) {

  const persistedDraft = virtualDraftFromNode(node);
  const persistedDraftSignature = JSON.stringify(persistedDraft);
  const persistedDraftRef = useRef({
    nodeId: node.id,
    signature: persistedDraftSignature,
  });
  const pendingLocalUpdateRef = useRef<PendingLocalVirtualUpdate | null>(null);
  const [draft, setDraft] = useState<VirtualDraft>(() => persistedDraft);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const promptDraftRef = useRef<HTMLTextAreaElement | null>(null);
  const activeModelPresets = selectableModelPresets(modelPresets);
  const currentPreset = modelPresets.find((preset) => preset.id === draft.modelPresetId);
  const candidateDeps = useMemo(
    () =>
      Array.from(nodesById.values()).filter(
        (candidate) =>
          candidate.id !== node.id &&
          (candidate.planspace_id ?? "") === (node.planspace_id ?? ""),
      ),
    [nodesById, node.id, node.planspace_id],
  );
  const dirty = useMemo(
    () => JSON.stringify(draft) !== persistedDraftSignature,
    [draft, persistedDraftSignature],
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
    };

    if (
      previous.nodeId === node.id &&
      previous.signature === persistedDraftSignature
    ) {
      return;
    }

    if (previous.nodeId !== node.id) {
      pendingLocalUpdateRef.current = null;
    } else if (acknowledgedLocalUpdate) {
      pendingLocalUpdateRef.current = null;
    }

    setDraft(
      acknowledgedLocalUpdate
        ? pendingLocalUpdate.draftAfterAck
        : persistedDraft,
    );
    setError(null);
    if (previous.nodeId === node.id && !acknowledgedLocalUpdate) {
      window.alert("此 virtual node 的持久化内容已更新，已重新加载。");
    }
  }, [node.id, persistedDraftSignature]);

  useEffect(() => {
    if (focusRequestVersion <= 0) return;
    const timer = window.setTimeout(() => {
      promptDraftRef.current?.focus();
      promptDraftRef.current?.select();
    }, 30);
    return () => window.clearTimeout(timer);
  }, [node.id, focusRequestVersion]);

  const save = async () => {
    const draftAfterAck = virtualDraftAfterSave(draft);
    const pendingLocalUpdate: PendingLocalVirtualUpdate = {
      nodeId: node.id,
      persistedSignature: JSON.stringify(draftAfterAck),
      draftAfterAck,
    };
    pendingLocalUpdateRef.current = pendingLocalUpdate;
    setSaving(true);
    setError(null);
    try {
      await onUpdateVirtual(node.id, virtualPayloadFromDraft(draft, node));
      if (
        pendingLocalUpdateRef.current === pendingLocalUpdate &&
        persistedDraftRef.current.signature === pendingLocalUpdate.persistedSignature
      ) {
        pendingLocalUpdateRef.current = null;
        setDraft(draftAfterAck);
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
      draftAfterAck: nextDraft,
    };
    pendingLocalUpdateRef.current = pendingLocalUpdate;
    setDraft(nextDraft);
    setSaving(true);
    setError(null);
    try {
      await onUpdateVirtual(node.id, {
        obsolete_reason: nextReason || null,
      });
      if (
        pendingLocalUpdateRef.current === pendingLocalUpdate &&
        persistedDraftRef.current.signature === pendingLocalUpdate.persistedSignature
      ) {
        pendingLocalUpdateRef.current = null;
        setDraft(nextDraft);
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
                dirty ? (
                  <span className="text-[10px] font-normal normal-case tracking-normal text-state-waiting">
                    unsaved
                  </span>
                ) : null
              }
            >
              Draft
            </SectionHeading>
          </div>
          <div className="space-y-3 px-3 py-3">
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
                ["regular", "Work"],
                ["planning", "Plan"],
                ["review", "Review"],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() =>
                    setDraft((current) => ({
                      ...current,
                      category: value,
                    }))
                  }
                  className={
                    "rounded px-3 py-1.5 text-[12px] font-medium transition " +
                    (draft.category === value
                      ? "bg-surface-raised text-ink-strong shadow-card"
                      : "text-ink-muted hover:text-ink")
                  }
                >
                  {label}
                </button>
              ))}
            </div>

            {draft.category === "review" && (
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
                No eligible dependencies in this direction.
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
                      <span className="min-w-0">
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
                onClick={save}
                disabled={
                  saving ||
                  !dirty ||
                  (draft.subtype !== "code_review" && !draft.promptDraft.trim()) ||
                  (!node.resume_from_node_id && !draft.modelPresetId)
                }
                className="rounded-md bg-brand px-3 py-1.5 text-[12px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
              >
                {saving ? "Saving..." : "Save changes"}
              </button>
              <button
                type="button"
                onClick={() => setDraft(virtualDraftFromNode(node))}
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
}

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
                  <span className="ml-2 text-ink">
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
  const attachedIds = new Set(attached);
  const attachedPrinciples = attached.map((id) => ({
    id,
    title: available.find((s) => s.id === id)?.title ?? id,
  }));
  const remaining = available.filter((s) => !attachedIds.has(s.id));

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
            <div>
              <select
                value=""
                onChange={(e) => {
                  attach(e.target.value);
                  e.currentTarget.value = "";
                }}
                className={inputClassName + " text-[11.5px]"}
              >
                <option value="">Attach a principle…</option>
                {remaining.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title} ({s.slug})
                  </option>
                ))}
              </select>
            </div>
          )}
          {available.length === 0 && (
            <div className="rounded-md border border-dashed border-line bg-surface px-3 py-2 text-[11px] text-ink-muted">
              No principles yet. Author one with the "New principle / skill" button on the
              project panel.
            </div>
          )}
        </div>
      </div>
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
  const attachedIds = new Set(attached.map((selection) => selection.id));
  const remaining = available.filter((skill) => !attachedIds.has(skill.id));
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
          <select
            value=""
            onChange={(event) => {
              if (!event.target.value) return;
              onChange([...attached, { id: event.target.value, suggest: false }]);
            }}
            className="w-full rounded-md border border-line bg-surface px-2 py-1.5 text-[11px] text-ink"
          >
            <option value="">Attach skill…</option>
            {remaining.map((skill) => (
              <option key={skill.id} value={skill.id}>{skill.title}</option>
            ))}
          </select>
        )}
        {attached.length === 0 && remaining.length === 0 && (
          <div className="text-[11px] text-ink-muted">No skills available.</div>
        )}
      </div>
    </section>
  );
}

function virtualDraftFromNode(node: NodeInfo): VirtualDraft {
  return {
    promptDraft: node.prompt_draft || node.prompt || "",
    motivation: node.summary || "",
    modelPresetId: node.model_preset_id || "",
    category: node.category || "regular",
    subtype: node.subtype || "agentic_review",
    brief: node.brief || {
      check_what: "",
      expected: "",
      abnormal: "",
    },
    scheduledDeps: [...(node.scheduled_deps ?? [])],
    pendingExtraPrinciples: [...(node.pending_extra_principles ?? [])],
    pendingExtraSkills: [...(node.pending_extra_skills ?? [])],
    obsoleteReason: node.obsolete_reason || "",
  };
}

function virtualDraftAfterSave(draft: VirtualDraft): VirtualDraft {
  if (draft.category === "review") {
    return {
      ...draft,
      obsoleteReason: draft.obsoleteReason.trim(),
    };
  }
  return {
    ...draft,
    subtype: "agentic_review",
    brief: {
      check_what: "",
      expected: "",
      abnormal: "",
    },
    obsoleteReason: draft.obsoleteReason.trim(),
  };
}

function virtualPayloadFromDraft(
  draft: VirtualDraft,
  node: NodeInfo,
): UpdateVirtualPayload {
  const payload: UpdateVirtualPayload = {
    prompt_draft: draft.promptDraft,
    motivation: draft.motivation,
    category: draft.category,
    scheduled_deps: draft.scheduledDeps,
    pending_extra_principles: draft.pendingExtraPrinciples,
    pending_extra_skills: draft.pendingExtraSkills,
    obsolete_reason: draft.obsoleteReason.trim() || null,
  };
  // Model preset is locked to the resume source for continuation virtuals;
  // omit it from the payload so the backend doesn't have to re-check equality.
  if (!node.resume_from_node_id && draft.modelPresetId !== node.model_preset_id) {
    payload.model_preset_id = draft.modelPresetId;
  }
  if (draft.category === "review") {
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
  const systemText =
    contextBundle?.system_text?.trim() || node.system_context_snapshot?.trim() || "";
  const turnText = contextBundle?.turn_text?.trim() || "";
  const userPrompt = node.prompt?.trim() || "";

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
          text={systemText}
          sources={systemSources}
        />
        <PromptBlock
          label="Input prompt"
          text={userPrompt}
          extras={turnText ? [{ label: "turn injection", text: turnText }] : []}
          sources={turnSources}
        />
      </div>
    </div>
  );
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
  extras = [],
  sources = [],
}: {
  label: string;
  text: string;
  extras?: Array<{ label: string; text: string }>;
  sources?: ContextBundleSource[];
}) {
  const totalChars = text.length + extras.reduce((n, e) => n + e.text.length, 0);
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
        {extras.map((extra, i) => (
          <div key={i} className="mt-2 border-t border-line/60 pt-2">
            <div className="mb-1 text-[10px] uppercase tracking-[0.12em] text-ink-subtle">
              {extra.label}
            </div>
            <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink">
              {extra.text}
            </pre>
          </div>
        ))}
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
  node,
}: {
  preview: string | null;
  loading: boolean;
  node: NodeInfo;
}) {
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
      <div className="px-3 py-3">
        {node.error ? (
          <pre className="whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft p-3 text-xs text-state-error">
            {node.error}
          </pre>
        ) : preview ? (
          <pre className="max-h-[42vh] overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-surface px-3 py-2 font-mono text-[11px] leading-relaxed text-ink">
            {preview}
          </pre>
        ) : (
          <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
            No preview recorded.
          </div>
        )}
      </div>
    </div>
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
      <div className="md-prose rounded-md border border-line bg-surface-raised px-3 py-2 text-[13px] leading-relaxed text-ink-strong">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        >
          {item.text}
        </ReactMarkdown>
      </div>
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
  return (
    node.kind === "verifier"
      ? "programmatic"
      : node.category === "planning"
      ? "planning"
      : node.category === "review"
        ? node.subtype === "human_interact_review"
          ? "human review"
          : node.subtype === "code_review"
            ? "code review"
            : "review"
        : "regular"
  );
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
    <dl className={"grid grid-cols-[120px_1fr] gap-x-3 gap-y-1.5 text-[11.5px] " + (className ?? "")}>
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
