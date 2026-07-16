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
import type { SkillSummary, UpdateVirtualPayload } from "../api";
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
  onNewSkill?: (userSeed: string) => Promise<void> | void;
  onCreateContinuationVirtual: (nodeId: string) => void;
  onPromoteVirtual: (nodeId: string) => void;
  onUpdateVirtual: (nodeId: string, payload: UpdateVirtualPayload) => Promise<void>;
  onInterruptNode: (nodeId: string) => void;
  onRerunNode: (nodeId: string) => void;
  canInterrupt: boolean;
  canRerun: boolean;
  onPlanspaceModeChange: (planspaceId: string, mode: PlanspaceMode) => void;
  onContextInit: () => void;
  onContextRefresh: () => void;
  onContextCancel: () => void;
  onTogglePlanspaceVisibility: (planspaceId: string, hidden: boolean) => void;

  /* Bumped each time a CONTEXT.md refresh completes, so PlanspaceFilePanel
     reloads the file content. */
  contextReloadVersion?: number;
  focusRequestVersion: number;
  newDirectionRequestVersion: number;
  onNewDirectionRequestHandled: () => void;

  /* User-wide skills; used to enrich the context panel when a skill tile is
   * selected. */
  skills?: SkillSummary[];
  onDeleteSkill?: (slug: string) => Promise<void> | void;

  onClose: () => void;
  gitCommits?: CommitDescriptor[];
  gitHead?: string | null;
  gitDirtyCount?: number;
  gitActionPending?: boolean;
  onGitCommit?: (message: string) => Promise<void> | void;
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
    onNewSkill,
    onCreateContinuationVirtual,
    onPromoteVirtual,
    onUpdateVirtual,
    onInterruptNode,
    onRerunNode,
    canInterrupt,
    canRerun,
    onPlanspaceModeChange,
    onContextInit,
    onContextRefresh,
    onContextCancel,
    onTogglePlanspaceVisibility,
    contextReloadVersion,
    focusRequestVersion,
    newDirectionRequestVersion,
    onNewDirectionRequestHandled,
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
        onNewSkill={onNewSkill}
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
      return <GitCommitPanel dirtyCount={props.gitDirtyCount ?? 0} pending={props.gitActionPending ?? false} readOnly={!!session?.read_only} onCommit={props.onGitCommit} suggestedMessage={currentEpochSummaries.join("; ") || "Changes from MiniClaw2"} />;
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
    return (
      <AgentPanel
        sessionId={session.id}
        node={node}
        nodesById={nodesById}
        modelPresets={modelPresets}
        events={events}
        eventsLoading={eventsLoading}
        contextBundle={contextBundle}
        contextBundleLoading={contextBundleLoading}
        pendingGate={session.read_only ? null : pendingGate}
        pendingReview={session.read_only ? null : pendingReview}
        skills={skills}
        onResolveGate={onResolveGate}
        onResolveReview={onResolveReview}
        onCreateContinuationVirtual={onCreateContinuationVirtual}
        onPromoteVirtual={onPromoteVirtual}
        onUpdateVirtual={onUpdateVirtual}
        onInterruptNode={onInterruptNode}
        onRerunNode={onRerunNode}
        canInterrupt={canInterrupt && !session.read_only}
        canRerun={canRerun && !session.read_only}
        canMutate={!session.read_only}
        focusRequestVersion={focusRequestVersion}
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
    const skill =
      selection.plugId && selection.plugId.startsWith("skills.") && skills
        ? skills.find((s) => s.id === selection.plugId) ?? null
        : null;
    return (
      <ContextNodePanel
        identityKey={selection.identityKey}
        path={selection.path}
        loadedByNodeIds={loadedByNodeIds}
        nodesById={nodesById}
        sampleBundle={sample}
        onSelectConsumer={onSelectNode}
        skill={skill}
        onDeleteSkill={skill ? onDeleteSkill : undefined}
      />
    );
  }

  return <Missing />;
}

function GitCommitPanel({ dirtyCount, pending, readOnly, onCommit, suggestedMessage }: { dirtyCount: number; pending: boolean; readOnly: boolean; onCommit?: (message: string) => Promise<void> | void; suggestedMessage: string }) {
  const [message, setMessage] = useState(suggestedMessage);
  const [edited, setEdited] = useState(false);
  useEffect(() => {
    if (!edited) setMessage(suggestedMessage);
  }, [edited, suggestedMessage]);
  const disabled = readOnly || pending || dirtyCount === 0 || !message.trim();
  return (
    <div className="flex h-full flex-col bg-surface px-4 py-4">
      <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">Commit changes</div>
      <p className="mt-2 text-[12px] text-ink-muted">{dirtyCount === 0 ? "Working tree clean." : `${dirtyCount} changed ${dirtyCount === 1 ? "file" : "files"}.`}</p>
      <textarea value={message} onChange={(event) => { setEdited(true); setMessage(event.target.value); }} disabled={readOnly || pending} rows={5} className="mt-4 resize-none rounded-md border border-line bg-surface-raised px-3 py-2 text-[13px] text-ink outline-none focus:border-brand" placeholder="Commit message" />
      <button type="button" disabled={disabled} onClick={() => void onCommit?.(message.trim())} className="mt-3 inline-flex h-9 items-center justify-center rounded-md bg-brand px-3 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40">{pending ? "Committing…" : "Commit"}</button>
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
