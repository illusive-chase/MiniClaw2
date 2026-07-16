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
} from "../types";
import type { SkillSummary, UpdateVirtualPayload } from "../api";
import { AgentPanel } from "./AgentPanel";
import { ContextNodePanel } from "./ContextNodePanel";
import { OpPanel } from "./OpPanel";
import { PlanspaceFilePanel } from "./PlanspaceFilePanel";
import { ProjectPanel } from "./ProjectPanel";

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
      />
    );
  }

  if (selection.kind === "op") {
    const node = nodesById.get(selection.nodeId);
    if (!node) return <Missing />;
    return <OpPanel node={node} diff={diff} diffLoading={diffLoading} />;
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
