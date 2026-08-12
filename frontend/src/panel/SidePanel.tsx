import { useEffect, useState } from "react";
import type { CanvasSelection } from "../canvas/Canvas";
import type {
  ClientMessage,
  ContextBundle,
  EventRecord,
  InteractionRequest,
  NodeDiff,
  NodeInfo,
  ModelPreset,
  PlanspaceMode,
  SessionContextSpaceInfo,
  SessionInfo,
  CommitDescriptor,
} from "../types";
import type { PrincipleSummary, SkillSummary, UpdateVirtualPayload } from "../api";
import {
  coerceSkillAuditEntries,
  splitSkillAttachments,
  type AttachedSkillDisplay,
  type SkillAttachmentEntry,
} from "../canvas/layout";
import { AgentPanel } from "./AgentPanel";
import { ContextNodePanel } from "./ContextNodePanel";
import { OpPanel } from "./OpPanel";
import { PlanspaceFilePanel } from "./PlanspaceFilePanel";
import { ProjectPanel } from "./ProjectPanel";
import { ArtifactPanel } from "./ArtifactPanel";

type ResolveGatePayload = Omit<
  Extract<ClientMessage, { type: "interaction_response" }>,
  "type" | "id"
>;

export type SidePanelProps = {
  selection: CanvasSelection;
  nodes: NodeInfo[];
  session: SessionInfo | null;
  modelPresets: ModelPreset[];

  /* selected-node data */
  events: EventRecord[];
  eventsLoading: boolean;
  diff: NodeDiff | null;
  diffLoading: boolean;
  contextBundle: ContextBundle | null;
  contextBundleLoading: boolean;

  /* context-node data: bundles aggregated across nodes */
  contextBundlesByNodeId: Record<string, ContextBundle | null | undefined>;

  /* project / context-space data */
  contextSpace: SessionContextSpaceInfo | null;
  contextSpaceLoading: boolean;
  contextSpaceSaving: boolean;
  contextSpaceError: string | null;
  settingsSaving: boolean;
  settingsError: string | null;

  /* gates */
  pendingGate: InteractionRequest | null;
  pendingReview: InteractionRequest | null;

  /* callbacks */
  onResolveGate?: (id: string, payload: ResolveGatePayload) => void;
  onResolveReview: (payload: {
    id: string;
    judgment: string;
  }) => void;
  onSelectNode: (nodeId: string) => void;
  onSelectArtifact: (
    nodeId: string,
    name: string,
    ext: "md" | "json" | "html",
  ) => void;
  onPreferredLanguageChange: (preferredLanguage: string | null) => void;
  onConcurrencyChange: (concurrency: number) => void;
  onActivatePlanspace: (binding_id: string, planspace_id: string) => void;
  onSelectContextBinding: (binding_id: string) => void;
  onNewDirection: (
    userSeed: string,
    mode: PlanspaceMode,
    modelPresetId: string,
  ) => void;
  onStartBlankDirection: (
    userSeed: string,
    mode: PlanspaceMode,
    modelPresetId: string,
  ) => void;
  onNewLibraryEntry?: (userSeed: string) => Promise<void> | void;
  onImportSkill?: (source: string) => Promise<void> | void;
  onCreateContinuationVirtual: (nodeId: string) => void;
  onPromoteVirtual: (nodeId: string) => Promise<void>;
  onClaimVirtual: (nodeId: string) => Promise<void>;
  onDequeueNode: (nodeId: string) => Promise<void>;
  onUpdateVirtual: (
    nodeId: string,
    payload: UpdateVirtualPayload,
  ) => Promise<NodeInfo | undefined>;
  onInterruptNode: (nodeId: string) => void;
  onRerunNode: (nodeId: string) => void;
  canInterrupt: boolean;
  canRerun: boolean;
  manualPromotionPlanspaceId: string | null;
  isManualPlanspace: (planspaceId: string | null | undefined) => boolean;
  onPlanspaceModeChange: (planspaceId: string, mode: PlanspaceMode) => void;
  onContextInit: () => void;
  onContextRefresh: () => void;
  onContextCancel: () => void;
  onTogglePlanspaceVisibility: (planspaceId: string, hidden: boolean) => void;

  /* Bumped each time a CONTEXT.md refresh completes, so PlanspaceFilePanel
     reloads the file content. */
  contextReloadVersion?: number;
  focusRequestVersion: number;
  activityFocusRequestVersion: number;
  newDirectionRequestVersion: number;
  onNewDirectionRequestHandled: () => void;

  /* User-wide principles; used to enrich the context panel when a principle tile is
   * selected. */
  principles?: PrincipleSummary[];
  onDeletePrinciple?: (slug: string) => Promise<void> | void;
  skills?: SkillSummary[];
  onDeleteSkill?: (slug: string) => Promise<void> | void;

  onClose: () => void;
  gitCommits?: CommitDescriptor[];
  gitHead?: string | null;
  gitDirtyCount?: number;
  gitActionPending?: boolean;
  gitReviewPending?: boolean;
  onGitCommit?: (message: string) => Promise<void> | void;
  onGitReview?: () => Promise<void> | void;
};

/**
 * Polymorphic side panel. The selected node's `kind` decides the shape.
 *
 * Per PRD §2.3, this collapses the legacy six-tab NodeDetail; selection drives
 * the panel's structure rather than a tab label.
 */
export function SidePanel(props: SidePanelProps) {
  const nodesById = new Map(props.nodes.map((n) => [n.id, n]));

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-ink-subtle">
          Details
        </div>
        <button
          type="button"
          onClick={props.onClose}
          className="flex h-6 w-6 items-center justify-center rounded text-[13px] leading-none text-ink-muted transition hover:bg-surface-raised hover:text-ink"
          title="Close panel"
          aria-label="Close panel"
        >
          ×
        </button>
      </div>
      <div className="min-h-0 flex-1">
      <Inner {...props} nodesById={nodesById} />
      </div>
    </div>
  );
}

function Inner(props: SidePanelProps & { nodesById: Map<string, NodeInfo> }) {
  const {
    selection,
    session,
    modelPresets,
    events,
    eventsLoading,
    diff,
    diffLoading,
    contextBundle,
    contextBundleLoading,
    contextBundlesByNodeId,
    contextSpace,
    contextSpaceLoading,
    contextSpaceSaving,
    contextSpaceError,
    settingsSaving,
    settingsError,
    pendingGate,
    pendingReview,
    onResolveGate,
    onResolveReview,
    onSelectNode,
    onSelectArtifact,
    onPreferredLanguageChange,
    onConcurrencyChange,
    onActivatePlanspace,
    onSelectContextBinding,
    onNewDirection,
    onStartBlankDirection,
    onNewLibraryEntry,
    onImportSkill,
    onCreateContinuationVirtual,
    onPromoteVirtual,
    onClaimVirtual,
    onDequeueNode,
    onUpdateVirtual,
    onInterruptNode,
    onRerunNode,
    canInterrupt,
    canRerun,
    manualPromotionPlanspaceId,
    isManualPlanspace,
    onPlanspaceModeChange,
    onContextInit,
    onContextRefresh,
    onContextCancel,
    onTogglePlanspaceVisibility,
    contextReloadVersion,
    focusRequestVersion,
    activityFocusRequestVersion,
    newDirectionRequestVersion,
    onNewDirectionRequestHandled,
    principles,
    onDeletePrinciple,
    skills,
    onDeleteSkill,
    nodesById,
    gitCommits = [],
  } = props;

  if (selection.kind === "none") {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-ink-muted">
        Select a node on the canvas to inspect it.
      </div>
    );
  }

  if (selection.kind === "projectRoot") {
    return (
      <ProjectPanel
        session={session}
        modelPresets={modelPresets}
        contextSpace={contextSpace}
        contextSpaceLoading={contextSpaceLoading}
        contextSpaceSaving={contextSpaceSaving}
        contextSpaceError={contextSpaceError}
        settingsSaving={settingsSaving}
        settingsError={settingsError}
        onActivatePlanspace={onActivatePlanspace}
        onSelectContextBinding={onSelectContextBinding}
        onPreferredLanguageChange={onPreferredLanguageChange}
        onConcurrencyChange={onConcurrencyChange}
        onNewDirection={onNewDirection}
        onStartBlankDirection={onStartBlankDirection}
        onNewLibraryEntry={onNewLibraryEntry}
        onImportSkill={onImportSkill}
        onContextInit={onContextInit}
        onContextRefresh={onContextRefresh}
        onContextCancel={onContextCancel}
        onTogglePlanspaceVisibility={onTogglePlanspaceVisibility}
        newDirectionRequestVersion={newDirectionRequestVersion}
        onNewDirectionRequestHandled={onNewDirectionRequestHandled}
      />
    );
  }

  if (selection.kind === "commit") {
    const commit = selection.sha ? gitCommits.find((item) => item.sha === selection.sha) : null;
    const headCommit = props.gitHead
      ? gitCommits.find((item) => item.sha === props.gitHead)
      : [...gitCommits].reverse().find((item) => item.live);
    const currentEpochSummaries = headCommit
      ? props.nodes
          .filter(
            (node) =>
              node.kind !== "op" &&
              !!node.commit_before &&
              (node.commit_before === headCommit.sha ||
                headCommit.aliases.includes(node.commit_before)),
          )
          .map((node) => node.summary?.trim())
          .filter((summary): summary is string => !!summary)
      : [];
    if (!selection.sha) {
      return <GitCommitPanel dirtyCount={props.gitDirtyCount ?? 0} pending={props.gitActionPending ?? false} reviewPending={props.gitReviewPending ?? false} readOnly={!!session?.read_only} onCommit={props.onGitCommit} onReview={props.onGitReview} suggestedMessage={currentEpochSummaries.join("; ") || "Changes from MiniClaw2"} />;
    }
    if (!commit) {
      return (
        <div className="flex h-full flex-col overflow-y-auto bg-surface px-4 py-4 text-sm">
          <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">Git commit</div>
          <h2 className="mt-2 font-mono text-[14px] font-semibold text-ink-strong">{selection.sha.slice(0, 12)}</h2>
          <p className="mt-3 text-[13px] leading-relaxed text-ink-muted">Commit metadata unavailable.</p>
        </div>
      );
    }
    const members = props.nodes.filter(
      (node) =>
        node.kind !== "op" &&
        !!node.commit_before &&
        (node.commit_before === commit.sha || commit.aliases.includes(node.commit_before)),
    );
    const associatedOps = props.nodes.filter(
      (node) =>
        node.kind === "op" &&
        !!node.commit_after &&
        (node.commit_after === commit.sha || commit.aliases.includes(node.commit_after)),
    );
    const isHead = commit.sha === props.gitHead;
    return (
      <div className="flex h-full flex-col overflow-y-auto bg-surface px-4 py-4 text-sm">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">Git commit</div>
        <h2 className="mt-2 font-mono text-[14px] font-semibold text-ink-strong">{commit.sha.slice(0, 12)}</h2>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {isHead && <span className="rounded-full bg-brand-soft px-2 py-0.5 text-[10px] font-medium text-brand-ink">HEAD</span>}
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${commit.live ? "bg-state-done-soft text-state-done" : "bg-state-waiting-soft text-state-waiting"}`}>{commit.live ? "live" : "stale"}</span>
        </div>
        <p className="mt-3 text-[13px] leading-relaxed text-ink">{commit.message}</p>
        {commit.ts && <div className="mt-2 text-[11px] text-ink-subtle">{new Date(commit.ts * 1000).toLocaleString()}</div>}
        <div className="mt-5 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">Epoch members</div>
        <div className="mt-2 space-y-1.5">
          {members.length === 0 && <div className="text-[11px] text-ink-subtle">No recorded members.</div>}
          {members.map((node) => (
            <button key={node.id} type="button" onClick={() => onSelectNode(node.id)} className="block w-full rounded-md border border-line bg-surface-raised px-3 py-2 text-left transition hover:border-line-strong">
              <span className="block font-mono text-[10px] text-ink-muted">{node.id.slice(0, 8)}</span>
              <span className="mt-0.5 block truncate text-[11px] text-ink">{node.summary || node.prompt || "Agent node"}</span>
            </button>
          ))}
        </div>
        {associatedOps.length > 0 && (
          <>
            <div className="mt-5 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">Commit operations</div>
            <div className="mt-2 space-y-1.5">
              {associatedOps.map((op) => (
                <button key={op.id} type="button" onClick={() => onSelectNode(op.id)} className="block w-full rounded-md border border-line bg-surface-raised px-3 py-2 text-left transition hover:border-line-strong">
                  <span className="block text-[11px] font-medium text-ink">{op.parent_node_id ? "Automatic commit" : "Manual commit"}</span>
                  <span className="mt-0.5 block font-mono text-[10px] text-ink-muted">{op.id.slice(0, 8)} · {op.state}</span>
                  {op.parent_node_id && <span className="mt-0.5 block font-mono text-[10px] text-brand">agent {op.parent_node_id.slice(0, 8)}</span>}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    );
  }

  if (selection.kind === "agent") {
    const node = nodesById.get(selection.nodeId);
    if (!node) return <Missing />;
    if (!session) return <Missing />;
    const canMutateNode =
      !session.read_only &&
      (session.sharing !== "shared" ||
        node.owner_host_id === session.local_machine_id);
    return (
      <AgentPanel
        sessionId={session.id}
        node={node}
        nodesById={nodesById}
        modelPresets={modelPresets}
        events={events}
        eventsLoading={eventsLoading}
        diff={diff}
        diffLoading={diffLoading}
        contextBundle={contextBundle}
        contextBundleLoading={contextBundleLoading}
        pendingGate={canMutateNode ? pendingGate : null}
        pendingReview={canMutateNode ? pendingReview : null}
        principles={principles}
        skills={skills}
        onResolveGate={onResolveGate}
        onResolveReview={onResolveReview}
        onCreateContinuationVirtual={onCreateContinuationVirtual}
        onPromoteVirtual={onPromoteVirtual}
        onClaimVirtual={onClaimVirtual}
        onDequeueNode={onDequeueNode}
        onUpdateVirtual={onUpdateVirtual}
        onInterruptNode={onInterruptNode}
        onRerunNode={onRerunNode}
        canInterrupt={canInterrupt && canMutateNode}
        canRerun={canRerun && canMutateNode}
        canMutate={canMutateNode}
        canClaim={
          !session.read_only &&
          session.is_native &&
          session.sharing === "shared" &&
          !canMutateNode
        }
        manualPromotionPlanspaceId={manualPromotionPlanspaceId}
        activePlanspaceId={contextSpace?.active_planspace_id ?? null}
        knownPlanspaceIds={
          contextSpace?.bindings
            .find(
              (binding) => binding.id === contextSpace.resolved_binding_id,
            )
            ?.plugs.filter((plug) => plug.kind === "planspace")
            .map((plug) => plug.id) ?? []
        }
        onActivatePlanspace={(planspaceId) => {
          const bindingId = contextSpace?.resolved_binding_id;
          if (bindingId) onActivatePlanspace(bindingId, planspaceId);
        }}
        isManualPlanspace={isManualPlanspace}
        focusRequestVersion={focusRequestVersion}
        activityFocusRequestVersion={activityFocusRequestVersion}
        onSelectArtifact={onSelectArtifact}
      />
    );
  }

  if (selection.kind === "op") {
    const node = nodesById.get(selection.nodeId);
    if (!node) return <Missing />;
    return <OpPanel node={node} diff={diff} diffLoading={diffLoading} />;
  }

  if (selection.kind === "artifact") {
    const node = nodesById.get(selection.nodeId);
    if (!node || !session) return <Missing />;
    const artifact = (node.artifacts ?? []).find(
      (candidate) => candidate.status === "published" && candidate.name === selection.name,
    );
    if (!artifact) return <Missing />;
    return (
      <ArtifactPanel
        sessionId={session.id}
        nodeId={node.id}
        artifact={artifact}
        ext={selection.ext}
      />
    );
  }

  if (selection.kind === "planspace") {
    return (
      <PlanspaceLanePanel
        planspaceId={selection.planspaceId}
        contextSpace={contextSpace}
        saving={contextSpaceSaving || !!session?.read_only}
        onModeChange={onPlanspaceModeChange}
      />
    );
  }

  if (selection.kind === "context") {
    /* Find all nodes whose bundle includes this path. */
    const loadedByNodeIds: string[] = [];
    let sample: ContextBundle | null = null;
    for (const [ownerId, bundle] of Object.entries(contextBundlesByNodeId)) {
      if (!bundle) continue;
      if (bundle.sources.some((s) => s.path === selection.path)) {
        loadedByNodeIds.push(ownerId);
        if (!sample) sample = bundle;
      }
    }
    if (selection.plugId?.startsWith("skills.")) {
      /* Auto-attached materializations don't count as direct loads — the
       * canvas folds those under the explicitly selected skill. */
      for (const node of nodesById.values()) {
        const audit = node.settings_snapshot?.skill_audit;
        if (!Array.isArray(audit)) continue;
        if (audit.some((raw) => {
          if (!raw || typeof raw !== "object") return false;
          const item = raw as Record<string, unknown>;
          return (
            item.id === selection.plugId &&
            item.missing !== true &&
            item.failed !== true &&
            item.auto_attached !== true
          );
        })) {
          loadedByNodeIds.push(node.id);
        }
      }
    }
    if (session && isPlanspaceFileSelection(selection)) {
      return (
        <PlanspaceFilePanel
          sessionId={session.id}
          loadedByNodeIds={loadedByNodeIds}
          nodesById={nodesById}
          onSelectConsumer={onSelectNode}
          reloadVersion={contextReloadVersion}
        />
      );
    }
    const principle =
      selection.plugId && selection.plugId.startsWith("principles.") && principles
        ? principles.find((s) => s.id === selection.plugId) ?? null
        : null;
    const skill =
      selection.plugId && selection.plugId.startsWith("skills.") && skills
        ? skills.find((item) => item.id === selection.plugId) ?? null
        : null;
    const attachedSkills = skill
      ? collectAttachedSkills(skill.id, nodesById, skills)
      : undefined;
    return (
      <ContextNodePanel
        identityKey={selection.identityKey}
        path={selection.path}
        loadedByNodeIds={loadedByNodeIds}
        nodesById={nodesById}
        sampleBundle={sample}
        onSelectConsumer={onSelectNode}
        principle={principle}
        onDeletePrinciple={principle ? onDeletePrinciple : undefined}
        skill={skill}
        onDeleteSkill={skill ? onDeleteSkill : undefined}
        attachedSkills={attachedSkills}
      />
    );
  }

  return <Missing />;
}

function GitCommitPanel({ dirtyCount, pending, reviewPending, readOnly, onCommit, onReview, suggestedMessage }: { dirtyCount: number; pending: boolean; reviewPending: boolean; readOnly: boolean; onCommit?: (message: string) => Promise<void> | void; onReview?: () => Promise<void> | void; suggestedMessage: string }) {
  const [message, setMessage] = useState(suggestedMessage);
  const [edited, setEdited] = useState(false);
  useEffect(() => {
    if (!edited) setMessage(suggestedMessage);
  }, [edited, suggestedMessage]);
  const busy = pending || reviewPending;
  const disabled = readOnly || busy || dirtyCount === 0 || !message.trim();
  return (
    <div className="flex h-full flex-col bg-surface px-4 py-4">
      <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">Commit changes</div>
      <p className="mt-2 text-[12px] text-ink-muted">{dirtyCount === 0 ? "Working tree clean." : `${dirtyCount} changed ${dirtyCount === 1 ? "file" : "files"}.`}</p>
      <textarea value={message} onChange={(event) => { setEdited(true); setMessage(event.target.value); }} disabled={readOnly || busy} rows={5} className="mt-4 resize-none rounded-md border border-line bg-surface-raised px-3 py-2 text-[13px] text-ink outline-none focus:border-brand" placeholder="Commit message" />
      <div className="mt-3 grid grid-cols-2 gap-2">
        <button type="button" disabled={readOnly || busy || dirtyCount === 0} onClick={() => void onReview?.()} className="inline-flex h-9 items-center justify-center rounded-md border border-line bg-surface-raised px-3 text-sm font-medium text-ink disabled:cursor-not-allowed disabled:opacity-40">{reviewPending ? "Reviewing…" : "Review"}</button>
        <button type="button" disabled={disabled} onClick={() => void onCommit?.(message.trim())} className="inline-flex h-9 items-center justify-center rounded-md bg-brand px-3 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40">{pending ? "Committing…" : "Commit"}</button>
      </div>
    </div>
  );
}

function Missing() {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center text-sm text-ink-muted">
      Node no longer exists.
    </div>
  );
}

function PlanspaceLanePanel({
  planspaceId,
  contextSpace,
  saving,
  onModeChange,
}: {
  planspaceId: string;
  contextSpace: SessionContextSpaceInfo | null;
  saving: boolean;
  onModeChange: (planspaceId: string, mode: PlanspaceMode) => void;
}) {
  const plug = contextSpace?.bindings
    .flatMap((binding) => binding.plugs)
    .find((candidate) => candidate.id === planspaceId);
  const mode = plug?.mode ?? "manual";
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Direction
        </div>
        <h2 className="mt-1 truncate font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {plug?.title || planspaceId}
        </h2>
        <div className="mt-1 font-mono text-[10.5px] text-ink-muted">
          {planspaceId}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        <section className="mb-5">
          <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            Promotion mode
          </div>
          <div className="mt-2 inline-flex rounded-md border border-line bg-surface-sunken p-0.5">
            {(["manual", "auto"] as const).map((option) => (
              <button
                key={option}
                type="button"
                disabled={saving || mode === option}
                onClick={() => onModeChange(planspaceId, option)}
                className={
                  "rounded px-3 py-1.5 text-[12px] font-medium transition disabled:cursor-default " +
                  (mode === option
                    ? "bg-surface-raised text-ink-strong shadow-card"
                    : "text-ink-muted hover:text-ink")
                }
              >
                {option}
              </button>
            ))}
          </div>
        </section>

        <dl className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px]">
          <div className="grid grid-cols-[120px_1fr] gap-3">
            <dt className="text-ink-subtle">Current mode</dt>
            <dd className="font-mono text-ink">{mode}</dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

function isPlanspaceFileSelection(
  selection: Extract<CanvasSelection, { kind: "context" }>,
): boolean {
  return selection.scope === "project-root";
}

/**
 * Aggregate the skills that were auto-attached under `rootSkillId` across
 * every node's observed audit and pending declaration, mirroring the folding
 * the canvas applies when it collapses dependencies into the root's tile.
 */
function collectAttachedSkills(
  rootSkillId: string,
  nodesById: Map<string, NodeInfo>,
  skills: SkillSummary[] | undefined,
): AttachedSkillDisplay[] {
  const skillById = new Map((skills ?? []).map((item) => [item.id, item]));
  const merged = new Map<
    string,
    { title: string; reason: "dependency" | "package"; usedBy: Set<string> }
  >();
  const record = (
    entry: SkillAttachmentEntry,
    usedByNodeId: string | null,
  ): void => {
    let info = merged.get(entry.id);
    if (!info) {
      info = {
        title:
          skillById.get(entry.id)?.title ??
          entry.name ??
          entry.id.replace(/^skills\./, ""),
        reason: entry.attachment_reason === "package" ? "package" : "dependency",
        usedBy: new Set<string>(),
      };
      merged.set(entry.id, info);
    }
    if (usedByNodeId) info.usedBy.add(usedByNodeId);
  };
  for (const node of nodesById.values()) {
    const audited = splitSkillAttachments(
      coerceSkillAuditEntries(node.settings_snapshot?.skill_audit),
    );
    for (const dep of audited.attachedByRoot.get(rootSkillId) ?? []) {
      record(dep, dep.used === true ? node.id : null);
    }
    if (node.state !== "virtual") continue;
    const declared = splitSkillAttachments(
      (node.pending_extra_skills ?? [])
        .filter((selection) => typeof selection?.id === "string")
        .map((selection) => ({
          id: selection.id,
          auto_attached: selection.auto_attached === true,
          required_by: selection.required_by,
          attachment_reason: selection.attachment_reason,
        })),
    );
    for (const dep of declared.attachedByRoot.get(rootSkillId) ?? []) {
      record(dep, null);
    }
  }
  return Array.from(merged.entries())
    .map(([id, info]) => ({
      id,
      title: info.title,
      reason: info.reason,
      usedByNodeIds: Array.from(info.usedBy),
    }))
    .sort((a, b) => a.title.localeCompare(b.title));
}
