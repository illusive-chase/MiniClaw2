import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelProjectContext,
  createBlankPlanspace,
  createPlanspace,
  createVirtual,
  dequeueNode,
  deleteTemplateInstance,
  deleteVirtual,
  getSession,
  getNodeContextBundle,
  getNodeDiff,
  getReviewedDiff,
  getSessionContextSpace,
  initProjectContext,
  listNodeEvents,
  listNodes,
  promoteVirtual,
  refreshProjectContext,
  rerunNode,
  updateLayoutHints,
  deletePlanspace,
  updatePlanspaceMode,
  updatePlanspaceView,
  updateSessionContextSpace,
  updateSessionPreferences,
  updateVirtual,
  listPrinciples,
  deletePrinciple,
  listSkills,
  deleteSkill,
  importSkill,
  getGlobalState,
  getGitState,
  gitCommit,
  gitReview,
  gitPull,
  gitPush,
  bindProjectHere,
  unbindProjectHere,
  artifactRawUrl,
  type PrincipleSummary,
  type SkillSummary,
  type UpdateVirtualPayload,
} from "./api";
import {
  Canvas,
  type CanvasCenterRequest,
  type CanvasNodePositionTarget,
  type CanvasSelection,
} from "./canvas/Canvas";
import {
  artifactNodeId,
  templateGroupNodeId,
  templateInstanceBoxNodeId,
} from "./canvas/layout";
import { resolveLaneAppendPosition } from "./canvas/lanePlacement";
import { setAgentNodeContext } from "./canvas/nodes/AgentNode";
import { setPlanspaceLaneContext } from "./canvas/nodes/PlanspaceLaneNode";
import { setTemplateGroupContext } from "./canvas/nodes/TemplateGroupNode";
import { setTemplateInstanceBoxContext } from "./canvas/nodes/TemplateInstanceBoxNode";
import { SidePanel } from "./panel/SidePanel";
import { NewProjectModal } from "./components/NewProjectModal";
import { SaveAsTemplateModal } from "./components/SaveAsTemplateModal";
import { InstantiateTemplateModal } from "./components/InstantiateTemplateModal";
import { TemplateEditor } from "./components/TemplateEditor";
import {
  LibraryDock,
  type LibraryEntrySelection,
} from "./components/LibraryDock";
import { ContextMenu, type ContextMenuItem } from "./canvas/ContextMenu";
import {
  ApiError,
  applyUserTemplate,
  listTemplateInstances,
  listUserTemplates,
} from "./api";
import {
  templateInstanceFetchScope,
  templateNeedsInstantiateDialog,
} from "./templateInstantiate";
import { ProjectsLanding } from "./components/ProjectsLanding";
import { NotificationBell } from "./components/NotificationBell";
import { ThemeToggle } from "./components/ThemeToggle";
import { UsageStrip } from "./components/UsageStrip";
import { GitWorkspaceStatus } from "./components/GitWorkspaceStatus";
import { TextZoomProvider } from "./components/TextZoom";
import type {
  ActiveNodeEntry,
  ContextBundle,
  EventRecord,
  InteractionRequest,
  NodeDiff,
  NodeInfo,
  ServerEvent,
  CanvasViewport,
  ModelPreset,
  GlobalState,
  SessionContextSpaceInfo,
  SessionInfo,
  PlanspaceMode,
  GitStatus,
  CommitDescriptor,
  TemplateSummary,
  TemplateInstanceRecord,
  Tag,
  WorkspaceEvent,
} from "./types";
import { useSessionSocket } from "./ws";
import { useActiveNodes } from "./activeNodes";
import {
  canResumeNode,
  nodeIdsByRecentActivityInLane,
  nodeIdsNeedingEventReplay,
  preferNewerNode,
  shouldAutoSelectEventNode,
  shouldOpenCreatedPlanspace,
  shouldOpenInteractionNode,
} from "./nodeUtil";
import { defaultModelPresetId } from "./modelPresets";

type Route = "landing" | "project";
type PendingGateState = {
  request: InteractionRequest;
  nodeId: string;
};
type SelectedEventsState = {
  nodeId: string | null;
  records: EventRecord[];
};
type LaneCreationNotice = {
  planspaceId: string;
  bindingId: string;
  nodeId: string;
  kind: "concierge" | "blank";
};

const TERMINAL_STATES = new Set<NodeInfo["state"]>(["done", "error", "cancelled"]);
const INTERRUPTIBLE_STATES = new Set<NodeInfo["state"]>([
  "running",
  "waiting",
  "awaiting_human_input",
]);
const LANDING_ACTIVE_STATES = new Set<NodeInfo["state"]>([
  "running",
  "waiting",
  "awaiting_human_input",
]);

function applyWorkspaceEventToSessions(
  sessions: SessionInfo[] | null,
  event: WorkspaceEvent,
): SessionInfo[] | null {
  if (sessions === null) return null;
  return sessions.map((item) => {
    if (item.id !== event.project_id) return item;
    const previous = event.previous_state ?? null;
    const current =
      event.type === "workspace_node_updated" ? event.entry.state : null;
    const activeDelta =
      Number(current !== null && LANDING_ACTIVE_STATES.has(current))
      - Number(previous !== null && LANDING_ACTIVE_STATES.has(previous));
    const queuedDelta =
      Number(current === "queued") - Number(previous === "queued");
    const activityAt =
      event.type === "workspace_node_updated"
        ? event.entry.finished_at ?? event.entry.started_at ?? null
        : null;
    return {
      ...item,
      turns: Math.max(
        0,
        item.turns
          + (event.type === "workspace_node_updated" && event.created ? 1 : 0)
          - (event.type === "workspace_node_removed" && event.deleted ? 1 : 0),
      ),
      active_count: Math.max(0, item.active_count + activeDelta),
      queued_count: Math.max(0, item.queued_count + queuedDelta),
      last_activity_at:
        activityAt === null
          ? item.last_activity_at
          : Math.max(item.last_activity_at ?? item.created_at, activityAt),
    };
  });
}

function upsertSession(
  sessions: SessionInfo[] | null,
  next: SessionInfo,
): SessionInfo[] {
  if (!sessions) return [next];
  const index = sessions.findIndex((session) => session.id === next.id);
  if (index < 0) return [...sessions, next];
  return sessions.map((session, current) => (current === index ? next : session));
}

/** Prefer the backend's `detail` over the generic wrapper message. */
function apiErrorText(err: unknown): string {
  if (err instanceof ApiError) return err.detail ?? err.message;
  return err instanceof Error ? err.message : String(err);
}
export function App() {
  const [route, setRoute] = useState<Route>("landing");
  const [landingSessions, setLandingSessions] = useState<SessionInfo[] | null>(null);
  const handleWorkspaceEvent = useCallback((event: WorkspaceEvent) => {
    setLandingSessions((current) => applyWorkspaceEventToSessions(current, event));
  }, []);
  const activeNodesFeed = useActiveNodes(true, handleWorkspaceEvent);
  const [landingTags, setLandingTags] = useState<Tag[]>([]);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [modelPresets, setModelPresets] = useState<ModelPreset[]>([]);
  const [globalState, setGlobalState] = useState<GlobalState | null>(null);
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null);
  const [gitCommits, setGitCommits] = useState<CommitDescriptor[]>([]);
  const [gitAction, setGitAction] = useState<"commit" | "review" | "pull" | "push" | null>(null);
  const [gitError, setGitError] = useState<string | null>(null);
  const pendingUiCommitNodeIdsRef = useRef<Set<string>>(new Set());
  const [uiCommitPositionTargets, setUiCommitPositionTargets] = useState<string[]>([]);

  const [selection, setSelection] = useState<CanvasSelection>({ kind: "none" });
  const selectionRef = useRef<CanvasSelection>(selection);
  selectionRef.current = selection;
  const [activityFocusRequestVersion, setActivityFocusRequestVersion] =
    useState(0);
  /* For data-fetching purposes we track the "currently inspected nodeId" — the
   * agent/op whose events, diff, and context bundle we should load. For context
   * selections, this stays pointed at the owning node. */
  const [inspectedNodeId, setInspectedNodeId] = useState<string | null>(null);
  const inspectedNodeIdRef = useRef<string | null>(null);

  const [selectedEventsState, setSelectedEventsState] = useState<SelectedEventsState>({
    nodeId: null,
    records: [],
  });
  const [selectedEventsLoading, setSelectedEventsLoading] = useState(false);
  const pendingSelectedEventsRef = useRef<{
    nodeId: string;
    records: EventRecord[];
  } | null>(null);
  const selectedEventsFlushTimerRef = useRef<number | null>(null);
  const selectedEvents =
    selectedEventsState.nodeId === inspectedNodeId
      ? selectedEventsState.records
      : [];
  const [selectedDiff, setSelectedDiff] = useState<NodeDiff | null>(null);
  const [selectedDiffLoading, setSelectedDiffLoading] = useState(false);
  const [selectedContextBundle, setSelectedContextBundle] = useState<ContextBundle | null>(null);
  const [selectedContextBundleLoading, setSelectedContextBundleLoading] = useState(false);

  /* Aggregated bundles: fills in as the user explores. Keyed by node id. */
  const [contextBundlesByNodeId, setContextBundlesByNodeId] = useState<
    Record<string, ContextBundle | null>
  >({});

  /* User-wide library entries. The canvas uses these only to resolve bound
   * entries; the complete collection lives in LibraryDock. */
  const [principles, setPrinciples] = useState<PrincipleSummary[]>([]);
  const refreshPrinciples = useCallback(() => {
    return listPrinciples()
      .then(setPrinciples)
      .catch(() => {
        /* non-fatal — the library just stays stale until the next refresh */
      });
  }, []);
  const handleDeletePrinciple = useCallback(
    async (slug: string) => {
      try {
        await deletePrinciple(slug);
      } finally {
        refreshPrinciples();
      }
    },
    [refreshPrinciples],
  );
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const refreshSkills = useCallback(() => {
    return listSkills().then(setSkills).catch(() => {});
  }, []);
  const handleDeleteSkill = useCallback(
    async (slug: string) => {
      try {
        await deleteSkill(slug);
      } finally {
        refreshSkills();
      }
    },
    [refreshSkills],
  );
  const handleImportSkill = useCallback(
    async (source: string) => {
      await importSkill(source);
      refreshSkills();
    },
    [refreshSkills],
  );
  const [sessionContextSpace, setSessionContextSpace] = useState<SessionContextSpaceInfo | null>(
    null,
  );
  const [sessionContextSpaceLoading, setSessionContextSpaceLoading] = useState(false);
  const [sessionContextSpaceSaving, setSessionContextSpaceSaving] = useState(false);
  const [sessionContextSpaceError, setSessionContextSpaceError] = useState<string | null>(null);
  const [contextReloadVersion, setContextReloadVersion] = useState(0);
  const prevContextRefreshRunningRef = useRef(false);
  const [sessionSettingsSaving, setSessionSettingsSaving] = useState(false);
  const [sessionSettingsError, setSessionSettingsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getGlobalState()
      .then((next) => {
        if (!cancelled) {
          setGlobalState(next);
          setModelPresets(next.model_presets);
        }
      })
      .catch((err) => {
        console.error("get global state failed:", err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const [pendingGates, setPendingGates] = useState<Record<string, PendingGateState>>({});
  const [pendingReviews, setPendingReviews] = useState<Record<string, PendingGateState>>({});

  const [projectMutationPending, setProjectMutationPending] = useState(false);
  const [laneCreationNotice, setLaneCreationNotice] =
    useState<LaneCreationNotice | null>(null);
  const [nodePositionTarget, setNodePositionTarget] =
    useState<CanvasNodePositionTarget | null>(null);

  /* True once both initial fetches (nodes + contextspace) have settled for the
   * current session. The canvas is held off-screen until then so hidden-planspace
   * nodes never briefly flash visible during the load-order race. */
  const [initialLoadComplete, setInitialLoadComplete] = useState(false);
  const [nodesHydratedSessionId, setNodesHydratedSessionId] = useState<string | null>(null);

  const [focusRequestVersion, setFocusRequestVersion] = useState(0);
  const [newDirectionRequestVersion, setNewDirectionRequestVersion] = useState(0);

  /* Cross-project jump (NotificationBell). Selecting a node in another project
   * cannot happen in one step: openProject() calls resetAllSessionState(),
   * which clears selection, and the node list for the target project has not
   * arrived yet. So the target is parked here and applied once that project's
   * initial load completes. */
  const [pendingJump, setPendingJump] = useState<
    { sessionId: string; nodeId: string } | null
  >(null);
  const [centerOnNodeRequest, setCenterOnNodeRequest] =
    useState<CanvasCenterRequest | null>(null);
  /* Set when a jump lands on a node whose lane is hidden. The side panel opens
   * normally (it reads `nodes`, not the graph), but the canvas has nothing to
   * center on, so the user needs to be told why and offered the unhide. */
  const [hiddenLaneNotice, setHiddenLaneNotice] = useState<
    { planspaceId: string; nodeId: string } | null
  >(null);

  const [newProjectModalOpen, setNewProjectModalOpen] = useState(false);

  /* Template-library state: multi-selection on the canvas + right-click menu +
   * "save as template" modal + a bump to force the dock to refetch after a
   * save or delete. Kept flat in App.tsx because both the canvas surface and
   * the dock need to observe the same underlying state. */
  const [multiSelectedNodeIds, setMultiSelectedNodeIds] = useState<string[]>([]);
  const [templateContextMenu, setTemplateContextMenu] = useState<
    { x: number; y: number; nodeId: string | null } | null
  >(null);
  const [saveTemplateOpen, setSaveTemplateOpen] = useState(false);
  const [saveTemplateNodeIds, setSaveTemplateNodeIds] = useState<string[]>([]);
  /* Slug currently open in the template editor. Templates are session-
   * independent library state, so this is not tied to the active project. */
  const [editingTemplateSlug, setEditingTemplateSlug] = useState<string | null>(null);
  /* Set when a dropped template declares arguments or input ports; the anchor
   * travels with it so the dialog can prefill the first port. */
  const [instantiateTarget, setInstantiateTarget] = useState<{
    template: TemplateSummary;
    anchorNodeId: string | null;
  } | null>(null);
  /* Stamped instance records for the active planspace — the group header's
   * template name and argument values. Nodes only carry the instance id. */
  const [templateInstances, setTemplateInstances] = useState<TemplateInstanceRecord[]>([]);
  /* Which instances render as a single collapsed box. Purely a view concern:
   * persisted locally per session, never sent to the backend, and with no
   * effect on scheduling or on the stored graph. */
  const [collapsedTemplateInstancesBySession, setCollapsedTemplateInstancesBySession] =
    useState<Record<string, string[]>>(() => readCollapsedTemplateInstances());
  const [libraryRefreshToken, setLibraryRefreshToken] = useState(0);
  const [librarySurfaceToken, setLibrarySurfaceToken] = useState(0);
  const [librarySurfaceBaselineIds, setLibrarySurfaceBaselineIds] = useState<string[]>([]);
  const prevTerminalLibraryEditCountRef = useRef(0);
  const terminalLibraryBaselineSessionIdRef = useRef<string | null>(null);

  /* Single floating side panel: `panelOpen` controls the slide-in animation
   * and `panelMode` decides whether details or the library renders.
   * Node clicks set mode='details' (but never force-open per UX spec);
   * the Library top-bar button toggles open+library; empty-canvas click
   * or the panel's close button closes. */
  const [panelState, setPanelState] = useState<{
    open: boolean;
    mode: "details" | "library";
  }>(() => readPanelState());
  useEffect(() => {
    try {
      window.localStorage.setItem("miniclaw.panelState", JSON.stringify(panelState));
    } catch {
      /* localStorage unavailable — state stays session-scoped */
    }
  }, [panelState]);
  useEffect(() => {
    try {
      window.localStorage.setItem(
        "miniclaw.collapsedTemplateInstances",
        JSON.stringify(collapsedTemplateInstancesBySession),
      );
    } catch {
      /* localStorage unavailable — state stays session-scoped */
    }
  }, [collapsedTemplateInstancesBySession]);
  const closePanel = useCallback(() => {
    setPanelState((prev) => (prev.open ? { ...prev, open: false } : prev));
  }, []);
  const openDetails = useCallback(() => {
    setPanelState({ open: true, mode: "details" });
  }, []);
  const toggleLibrary = useCallback(() => {
    setPanelState((prev) =>
      prev.open && prev.mode === "library"
        ? { ...prev, open: false }
        : { open: true, mode: "library" },
    );
  }, []);
  const inspectNode = useCallback((nodeId: string | null) => {
    inspectedNodeIdRef.current = nodeId;
    setInspectedNodeId(nodeId);
    setSelectedEventsState((current) =>
      current.nodeId === nodeId ? current : { nodeId, records: [] },
    );
    const pending = pendingSelectedEventsRef.current;
    if (pending && pending.nodeId !== nodeId) {
      pendingSelectedEventsRef.current = null;
      if (selectedEventsFlushTimerRef.current !== null) {
        window.clearTimeout(selectedEventsFlushTimerRef.current);
        selectedEventsFlushTimerRef.current = null;
      }
    }
  }, []);
  const refreshLibraryEntries = useCallback(
    () => Promise.all([refreshPrinciples(), refreshSkills()]),
    [refreshPrinciples, refreshSkills],
  );
  const openLibraryEntry = useCallback(
    (entry: LibraryEntrySelection) => {
      setSelection({
        kind: "context",
        identityKey: entry.identityKey,
        path: entry.path,
        scope: "contextspace",
        sourceKind: entry.sourceKind,
        plugId: entry.plugId,
      });
      inspectNode(null);
      openDetails();
    },
    [inspectNode, openDetails],
  );
  /* Programmatic-selection helper. Whenever code (not a user canvas click)
   * changes what's inspected, we must also open the details panel — otherwise
   * the freshly-inspected node's controls (gate/review form, virtual draft
   * editor) sit inside the closed floating panel and are invisible to the
   * user. */
  const selectAndOpenNode = useCallback(
    (nodeId: string, kind: "agent" | "op" = "agent") => {
      const nextSelection: CanvasSelection = { kind, nodeId };
      selectionRef.current = nextSelection;
      setSelection(nextSelection);
      inspectNode(nodeId);
      setPanelState({ open: true, mode: "details" });
    },
    [inspectNode],
  );

  const selectEventNodeIfIdle = useCallback(
    (nodeId: string) => {
      if (!shouldAutoSelectEventNode(selectionRef.current)) return;
      selectAndOpenNode(nodeId);
    },
    [selectAndOpenNode],
  );

  const openInteractionNodeIfAppropriate = useCallback(
    (nodeId: string) => {
      if (!shouldOpenInteractionNode(selectionRef.current, nodeId)) return;
      selectAndOpenNode(nodeId);
    },
    [selectAndOpenNode],
  );

  const panelRef = useRef<HTMLElement | null>(null);
  const currentRouteRef = useRef<Route>("landing");
  const currentSessionIdRef = useRef<string | null>(null);
  const nodeCountRef = useRef(0);
  const nodesRef = useRef<NodeInfo[]>([]);
  const refreshNodesSeqRef = useRef(0);
  const lastLayoutSaveRef = useRef<Promise<SessionInfo> | null>(null);
  const layoutSaveChainRef = useRef<Promise<void>>(Promise.resolve());
  const openProjectRequestRef = useRef(0);
  /* Node ids whose bundle prefetch is currently in flight. Each fetch
   * resolution updates contextBundlesByNodeId, which retriggers the prefetch
   * effect; without this guard the still-in-flight nodes would be refetched
   * on every resolution. */
  const inflightBundleFetchRef = useRef<Set<string>>(new Set());

  /* Keyboard focus must not enter the panel while it's translated offscreen —
   * pointer-events-none only blocks the mouse, and aria-hidden without inert
   * still lets Tab move into the subtree. React 18's typings don't expose
   * `inert`, so toggle it via ref. */
  useEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    if (panelState.open) {
      el.removeAttribute("inert");
    } else {
      el.setAttribute("inert", "");
    }
  }, [panelState.open]);

  useEffect(() => {
    currentRouteRef.current = route;
  }, [route]);

  useEffect(() => {
    currentSessionIdRef.current = session?.id ?? null;
  }, [session?.id]);

  useEffect(() => {
    nodeCountRef.current = nodes.length;
    nodesRef.current = nodes;
  }, [nodes]);

  const resetAllSessionState = useCallback(() => {
    refreshNodesSeqRef.current += 1;
    nodeCountRef.current = 0;
    nodesRef.current = [];
    setNodes([]);
    setNodesHydratedSessionId(null);
    prevTerminalLibraryEditCountRef.current = 0;
    terminalLibraryBaselineSessionIdRef.current = null;
    setSelection({ kind: "none" });
    inspectedNodeIdRef.current = null;
    setInspectedNodeId(null);
    setSelectedEventsState({ nodeId: null, records: [] });
    setSelectedEventsLoading(false);
    pendingSelectedEventsRef.current = null;
    if (selectedEventsFlushTimerRef.current !== null) {
      window.clearTimeout(selectedEventsFlushTimerRef.current);
      selectedEventsFlushTimerRef.current = null;
    }
    setSelectedDiff(null);
    setSelectedContextBundle(null);
    setSelectedContextBundleLoading(false);
    setContextBundlesByNodeId({});
    setSessionContextSpace(null);
    setSessionContextSpaceLoading(false);
    setSessionContextSpaceSaving(false);
    setSessionContextSpaceError(null);
    setSessionSettingsSaving(false);
    setSessionSettingsError(null);
    setGitStatus(null);
    setGitCommits([]);
    setGitAction(null);
    setGitError(null);
    pendingUiCommitNodeIdsRef.current.clear();
    setUiCommitPositionTargets([]);
    setProjectMutationPending(false);
    setLaneCreationNotice(null);
    setNodePositionTarget(null);
    setPendingGates({});
    setPendingReviews({});
    setFocusRequestVersion(0);
    setActivityFocusRequestVersion(0);
    setNewDirectionRequestVersion(0);
    setCenterOnNodeRequest(null);
    setHiddenLaneNotice(null);
    setInitialLoadComplete(false);
    inflightBundleFetchRef.current.clear();
  }, []);

  const acknowledgeNewDirectionRequest = useCallback(() => {
    setNewDirectionRequestVersion(0);
  }, []);

  const waitForLayoutSaves = useCallback(async () => {
    for (;;) {
      const save = lastLayoutSaveRef.current;
      if (!save) return;
      try {
        await save;
      } catch {
        /* Opening should still proceed if a best-effort layout save failed. */
      }
      if (lastLayoutSaveRef.current === save) return;
    }
  }, []);

  const openProject = useCallback(
    async (next: SessionInfo) => {
      const requestId = ++openProjectRequestRef.current;
      await waitForLayoutSaves();
      if (requestId !== openProjectRequestRef.current) return;
      let fresh = next;
      try {
        const fetched = await getSession(next.id);
        if (requestId !== openProjectRequestRef.current) return;
        fresh = fetched;
      } catch (err) {
        if (requestId !== openProjectRequestRef.current) return;
        console.warn("get session failed:", err);
      }
      resetAllSessionState();
      currentSessionIdRef.current = fresh.id;
      currentRouteRef.current = "project";
      setSession(fresh);
      setRoute("project");
    },
    [resetAllSessionState, waitForLayoutSaves],
  );

  /* Assigned next to the hiddenPlanspaceIds memo far below. Declared here
   * because revealJumpTarget reads it and must not be re-created whenever
   * lane visibility changes. */
  const hiddenPlanspaceIdsRef = useRef<string[]>([]);

  const centerOnNode = useCallback((nodeId: string) => {
    setCenterOnNodeRequest((current) => ({
      nodeId,
      version: (current?.version ?? 0) + 1,
    }));
  }, []);

  /* Reveal a node the user jumped to. A hidden lane draws none of its nodes,
   * so centering would silently do nothing; surface the reason and let the
   * user unhide instead. Hiding is an explicit choice, so it is never undone
   * automatically — same reasoning as never stealing the active lane. */
  const revealJumpTarget = useCallback(
    (nodeId: string) => {
      selectAndOpenNode(nodeId);
      const node = nodesRef.current.find((item) => item.id === nodeId);
      const planspaceId = node?.planspace_id ?? null;
      if (planspaceId && hiddenPlanspaceIdsRef.current.includes(planspaceId)) {
        setHiddenLaneNotice({ planspaceId, nodeId });
        return;
      }
      setHiddenLaneNotice(null);
      centerOnNode(nodeId);
    },
    [centerOnNode, selectAndOpenNode],
  );

  /* Jump to a node from the cross-project bar.
   *
   * Same project: select directly. Calling openProject() here would reset all
   * session state and remount the canvas for no reason.
   *
   * Other project: switch first, then let the pendingJump effect finish the
   * selection once nodes have loaded — selecting now would target a node id
   * that is not in `nodes` yet.
   */
  const jumpToActiveNode = useCallback(
    (entry: Pick<ActiveNodeEntry, "project_id" | "node_id">) => {
      if (entry.project_id === currentSessionIdRef.current) {
        revealJumpTarget(entry.node_id);
        return;
      }
      /* Claim the open sequence at click time rather than after the lookup
       * below. Otherwise two quick jumps — or a jump plus a manual open —
       * race, and whichever getSession happens to be slowest finishes last
       * and overrides the project the user actually asked for. */
      const requestId = ++openProjectRequestRef.current;
      setPendingJump({ sessionId: entry.project_id, nodeId: entry.node_id });
      void (async () => {
        try {
          const target = await getSession(entry.project_id);
          if (requestId !== openProjectRequestRef.current) return;
          await openProject(target);
        } catch (err) {
          console.warn("jump to project failed:", err);
          /* A newer navigation owns pendingJump now; clearing it would
           * cancel that one instead of this one. */
          if (requestId === openProjectRequestRef.current) setPendingJump(null);
        }
      })();
    },
    [openProject, revealJumpTarget],
  );

  useEffect(() => {
    if (!pendingJump) return;
    if (!initialLoadComplete) return;
    if (session?.id !== pendingJump.sessionId) {
      /* A different project finished loading than the one we were jumping to
       * — the user opened something else mid-flight. Abandon the jump rather
       * than letting it fire whenever that project is next opened. */
      if (session?.id) setPendingJump(null);
      return;
    }
    /* The node may have been deleted between the poll and the jump. Leaving
     * the panel closed is better than selecting an id that is not there. */
    if (nodes.some((node) => node.id === pendingJump.nodeId)) {
      revealJumpTarget(pendingJump.nodeId);
    }
    setPendingJump(null);
  }, [
    pendingJump,
    initialLoadComplete,
    session?.id,
    nodes,
    revealJumpTarget,
  ]);

  const backToLanding = useCallback(() => {
    openProjectRequestRef.current += 1;
    currentRouteRef.current = "landing";
    currentSessionIdRef.current = null;
    resetAllSessionState();
    /* Abandon any jump in flight. Left set, it would fire the next time the
     * user opened that project on their own and select a node they never
     * asked for. */
    setPendingJump(null);
    setSession(null);
    setRoute("landing");
  }, [resetAllSessionState]);

  /* selected node lookup */
  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === inspectedNodeId) ?? null,
    [nodes, inspectedNodeId],
  );
  const isNodeNative = useCallback(
    (node: NodeInfo) => node.owner_host_id === session?.local_machine_id,
    [session?.local_machine_id],
  );
  const sessionWithRuntimeCounts = useMemo(
    () =>
      session
        ? {
            ...session,
            active_count: nodes.filter(
              (node) => isNodeNative(node) && INTERRUPTIBLE_STATES.has(node.state),
            ).length,
            queued_count: nodes.filter(
              (node) => isNodeNative(node) && node.state === "queued",
            ).length,
          }
        : null,
    [isNodeNative, nodes, session],
  );
  const selectedCanvasNodeId = useMemo(() => graphNodeIdForSelection(selection), [selection]);
  const activeNodesFromList = useMemo(
    () => nodes.filter((n) => isNodeNative(n) && INTERRUPTIBLE_STATES.has(n.state)),
    [isNodeNative, nodes],
  );
  const hasInterruptibleNode = useMemo(
    () => nodes.some((n) => isNodeNative(n) && INTERRUPTIBLE_STATES.has(n.state)),
    [isNodeNative, nodes],
  );
  const readOnly = session?.read_only ?? false;
  const activeCanvasNodeIds = useMemo(
    () => activeNodesFromList.map((node) => node.id),
    [activeNodesFromList],
  );
  const socketReplayNodeIds = useMemo(
    () => nodeIdsNeedingEventReplay(nodes),
    [nodes],
  );

  const validPendingGates = useMemo(
    () => keepPendingForStates(pendingGates, nodes, ["waiting"]),
    [nodes, pendingGates],
  );
  const pendingGateNodeIds = useMemo(
    () => (readOnly ? [] : Object.keys(validPendingGates)),
    [readOnly, validPendingGates],
  );
  const validPendingReviews = useMemo(
    () => keepPendingForStates(pendingReviews, nodes, ["awaiting_human_input"]),
    [nodes, pendingReviews],
  );
  const activePendingGate =
    (inspectedNodeId && validPendingGates[inspectedNodeId]) ||
    Object.values(validPendingGates)[0] ||
    null;
  const activePendingReview =
    (inspectedNodeId && validPendingReviews[inspectedNodeId]) ||
    Object.values(validPendingReviews)[0] ||
    null;
  const composerLocked = !readOnly && (!!activePendingGate || !!activePendingReview);
  const virtualCreateDisabled =
    readOnly ||
    projectMutationPending ||
    sessionSettingsSaving ||
    !!sessionContextSpace?.context_refresh?.running;

  /* Refresh the node list for initial loads and explicit mutations.
   *
   * Between the listNodes API request
   * and its response, ev-driven setNodes calls can add or mutate nodes
   * locally. A blanket ``setNodes(next)`` would clobber those in-flight
   * updates — most visibly, when the backend snapshot is taken between a
   * node completing and the follow-up scheduler activity, ``next`` can
   * be missing whatever the WS just added, and the canvas appears to
   * "clean up" nodes.
   *
   * Fix: treat ``next`` as authoritative for nodes that existed when the
   * request started, so missed ``node_removed`` events are reconciled. Preserve
   * nodes that appeared locally while the request was in flight, because the
   * backend snapshot can legitimately predate those WS/POST additions. */
  const refreshNodes = useCallback(async () => {
    const sessionId = session?.id;
    if (!sessionId) return;
    const seq = ++refreshNodesSeqRef.current;
    const refreshStartedNodeIds = new Set(nodesRef.current.map((node) => node.id));
    try {
      const next = await listNodes(sessionId);
      if (seq !== refreshNodesSeqRef.current) return;
      if (currentRouteRef.current !== "project" || currentSessionIdRef.current !== sessionId) {
        return;
      }
      const nextById = new Map(next.map((node) => [node.id, node]));
      const wasRemovedByRefresh = (nodeId: string | null | undefined) =>
        !!nodeId && refreshStartedNodeIds.has(nodeId) && !nextById.has(nodeId);
      setNodes((current) => {
        if (current.length === 0) {
          nodeCountRef.current = next.length;
          nodesRef.current = next;
          return next;
        }
        const currentById = new Map(current.map((n) => [n.id, n]));
        const merged: NodeInfo[] = [];
        for (const c of current) {
          /* Reconcile drift without letting an older HTTP snapshot replace a
           * newer WebSocket update received while this request was in flight. */
          const refreshed = nextById.get(c.id);
          if (refreshed) {
            merged.push(preferNewerNode(c, refreshed));
          } else if (!refreshStartedNodeIds.has(c.id)) {
            merged.push(c);
          }
        }
        for (const n of next) {
          if (!currentById.has(n.id)) merged.push(n);
        }
        merged.sort((a, b) => a.created_at - b.created_at);
        nodeCountRef.current = merged.length;
        nodesRef.current = merged;
        return merged;
      });
      setNodesHydratedSessionId(sessionId);
      setPendingGates((current) => removePendingNodes(current, wasRemovedByRefresh));
      setPendingReviews((current) => removePendingNodes(current, wasRemovedByRefresh));
      setSelection((current) =>
        (current.kind === "agent" || current.kind === "op") &&
        wasRemovedByRefresh(current.nodeId)
          ? { kind: "none" }
          : current,
      );
      const currentInspectedNodeId = inspectedNodeIdRef.current;
      const idleFallbackNodeId = shouldAutoSelectEventNode(selectionRef.current)
        ? next.at(-1)?.id ?? null
        : null;
      inspectNode(
        wasRemovedByRefresh(currentInspectedNodeId)
          ? null
          : currentInspectedNodeId ?? idleFallbackNodeId,
      );
    } catch (err) {
      console.error("list nodes failed:", err);
    }
  }, [inspectNode, session?.id]);

  const refreshGit = useCallback(async () => {
    if (!session?.id) return;
    try {
      const state = await getGitState(session.id);
      setGitStatus(state.status);
      setGitCommits(state.commits);
      setGitError(null);
    } catch (err) {
      console.warn("get git state failed:", err);
    }
  }, [session?.id]);

  /* Instance records are stored per planspace, so every lane that owns a
   * stamped node is fetched. Missing records degrade to a generic group label
   * rather than hiding the group, so a failure here is warn-only. */
  const templateInstanceFetchScopeState = useMemo(
    () => templateInstanceFetchScope(nodes),
    [nodes],
  );
  const laneIdsWithTemplateInstances = templateInstanceFetchScopeState.laneIds;
  const templateInstanceFetchKey = templateInstanceFetchScopeState.key;

  useEffect(() => {
    const sessionId = session?.id;
    if (!sessionId || laneIdsWithTemplateInstances.length === 0) {
      setTemplateInstances([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      const collected: TemplateInstanceRecord[] = [];
      for (const laneId of laneIdsWithTemplateInstances) {
        try {
          collected.push(...(await listTemplateInstances(sessionId, laneId)));
        } catch (err) {
          console.warn("list template instances failed:", err);
        }
      }
      if (!cancelled) setTemplateInstances(collected);
    })();
    return () => {
      cancelled = true;
    };
    /* The stable key includes instance ids as well as lane ids: a second stamp
     * in an existing lane must refresh records, while a state-only node update
     * must not. `laneIdsWithTemplateInstances` is rebuilt with the key. */
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id, templateInstanceFetchKey]);

  const collapsedTemplateInstanceIds = useMemo(
    () =>
      session?.id ? collapsedTemplateInstancesBySession[session.id] ?? [] : [],
    [collapsedTemplateInstancesBySession, session?.id],
  );

  const toggleTemplateInstanceCollapsed = useCallback(
    (instanceId: string, collapsed: boolean) => {
      const sessionId = session?.id;
      if (!sessionId) return;
      setCollapsedTemplateInstancesBySession((current) => {
        const existing = current[sessionId] ?? [];
        if (collapsed === existing.includes(instanceId)) return current;
        const next = collapsed
          ? [...existing, instanceId]
          : existing.filter((id) => id !== instanceId);
        return { ...current, [sessionId]: next };
      });
    },
    [session?.id],
  );

  useEffect(() => {
    let committed = false;
    for (const nodeId of [...pendingUiCommitNodeIdsRef.current]) {
      const node = nodes.find((candidate) => candidate.id === nodeId);
      if (!node || !TERMINAL_STATES.has(node.state)) continue;
      pendingUiCommitNodeIdsRef.current.delete(nodeId);
      if (
        node.state === "done" &&
        node.commit_after &&
        node.commit_after !== node.commit_before
      ) {
        committed = true;
        setUiCommitPositionTargets((current) =>
          current.includes(node.commit_after!)
            ? current
            : [...current, node.commit_after!],
        );
      }
    }
    if (committed) void refreshGit();
  }, [nodes, refreshGit]);

  const consumeUiCommitPositionTarget = useCallback((sha: string) => {
    setUiCommitPositionTargets((current) =>
      current[0] === sha
        ? current.slice(1)
        : current.filter((candidate) => candidate !== sha),
    );
  }, []);

  /* context space */
  /* `quiet` is for reconciliation rather than a user-initiated read: it skips
   * the loading spinner and, on failure, keeps the last-known snapshot instead
   * of clearing it. The reconnect path needs this — it fires exactly when the
   * network is unreliable, and dropping the snapshot there would replace a
   * stale `context_refresh` with no contextspace at all. */
  const refreshContextSpace = useCallback(
    async (opts?: { quiet?: boolean }) => {
      if (!session?.id) return;
      const quiet = opts?.quiet === true;
      if (!quiet) {
        setSessionContextSpaceLoading(true);
        setSessionContextSpaceError(null);
      }
      try {
        const next = await getSessionContextSpace(session.id);
        setSessionContextSpace(next);
        setSession((current) =>
          current && current.id === session.id
            ? { ...current, project_context_binding_id: next.project_context_binding_id ?? null }
            : current,
        );
      } catch (err) {
        if (!quiet) {
          setSessionContextSpaceError(String(err));
          setSessionContextSpace(null);
        }
      } finally {
        if (!quiet) setSessionContextSpaceLoading(false);
      }
    },
    [session?.id],
  );

  const reconcileContextSpace = useCallback(() => {
    void refreshContextSpace({ quiet: true });
  }, [refreshContextSpace]);

  /* Coordinated bootstrap: fetch nodes and contextspace in parallel and hold
   * the canvas off-screen until BOTH resolve. Otherwise the faster of the two
   * wins and the canvas renders with a partial picture — when listNodes lands
   * before getSessionContextSpace, nodes from hidden planspaces flash visible
   * for one frame and then disappear as the hidden-planspace filter kicks in. */
  useEffect(() => {
    if (!session?.id) {
      setInitialLoadComplete(false);
      setPrinciples([]);
      setSkills([]);
      return;
    }
    setInitialLoadComplete(false);
    let cancelled = false;
    void Promise.allSettled([refreshNodes(), refreshContextSpace(), refreshGit()]).then(() => {
      if (!cancelled) setInitialLoadComplete(true);
    });
    /* Principles are user-wide — fetched independently of nodes/contextspace and
     * don't gate the canvas render. Stale is acceptable; refreshPrinciples() is
     * called after principle-edit turns finish. */
    refreshPrinciples();
    refreshSkills();
    return () => {
      cancelled = true;
    };
  }, [
    session?.id,
    refreshNodes,
    refreshContextSpace,
    refreshGit,
    refreshPrinciples,
    refreshSkills,
  ]);

  useEffect(() => {
    if (!session?.id) return;
    const onFocus = () => void refreshGit();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshGit, session?.id]);

  /* Historical principle-edit nodes refresh only principles. Librarian nodes may
   * write either entry type, so their terminal transition refreshes both shelves. */
  const terminalPrincipleEditCount = useMemo(() => {
    let count = 0;
    for (const n of nodes) {
      if (n.agent_op_kind === "principle_edit" && TERMINAL_STATES.has(n.state)) {
        count += 1;
      }
    }
    return count;
  }, [nodes]);
  const prevTerminalPrincipleEditCountRef = useRef(0);
  useEffect(() => {
    if (terminalPrincipleEditCount > prevTerminalPrincipleEditCountRef.current) {
      refreshPrinciples();
    }
    prevTerminalPrincipleEditCountRef.current = terminalPrincipleEditCount;
  }, [terminalPrincipleEditCount, refreshPrinciples]);

  const terminalLibraryEditCount = useMemo(() => {
    let count = 0;
    for (const n of nodes) {
      if (n.agent_op_kind === "library_edit" && TERMINAL_STATES.has(n.state)) {
        count += 1;
      }
    }
    return count;
  }, [nodes]);
  useEffect(() => {
    const sessionId = session?.id ?? null;
    if (!sessionId || nodesHydratedSessionId !== sessionId) return;
    if (terminalLibraryBaselineSessionIdRef.current !== sessionId) {
      terminalLibraryBaselineSessionIdRef.current = sessionId;
      prevTerminalLibraryEditCountRef.current = terminalLibraryEditCount;
      return;
    }
    if (terminalLibraryEditCount > prevTerminalLibraryEditCountRef.current) {
      setLibrarySurfaceBaselineIds([
        ...principles.map((item) => item.id),
        ...skills.map((item) => item.id),
      ]);
      refreshPrinciples();
      refreshSkills();
      setLibraryRefreshToken((token) => token + 1);
      setLibrarySurfaceToken((token) => token + 1);
      setPanelState({ open: true, mode: "library" });
    }
    prevTerminalLibraryEditCountRef.current = terminalLibraryEditCount;
  }, [
    session?.id,
    nodesHydratedSessionId,
    terminalLibraryEditCount,
    refreshPrinciples,
    refreshSkills,
    principles,
    skills,
  ]);

  /* Bump the reload version each time the context task finishes, so the
     CONTEXT.md viewer re-reads from disk. */
  useEffect(() => {
    const running = !!sessionContextSpace?.context_refresh?.running;
    if (prevContextRefreshRunningRef.current && !running) {
      setContextReloadVersion((v) => v + 1);
    }
    prevContextRefreshRunningRef.current = running;
  }, [sessionContextSpace?.context_refresh?.running]);

  const activatePlanspace = useCallback(
    async (binding_id: string, planspace_id: string) => {
      if (!session?.id) return;
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      try {
        const next = await updateSessionContextSpace(session.id, {
          project_context_binding_id: binding_id,
          active_planspace_id: planspace_id,
        });
        setSessionContextSpace(next);
        setSession((current) =>
          current && current.id === session.id
            ? { ...current, project_context_binding_id: next.project_context_binding_id ?? null }
            : current,
        );
      } catch (err) {
        setSessionContextSpaceError(String(err));
      } finally {
        setSessionContextSpaceSaving(false);
      }
    },
    [session?.id],
  );

  const selectContextBinding = useCallback(
    async (binding_id: string) => {
      if (!session?.id) return;
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      try {
        const next = await updateSessionContextSpace(session.id, {
          project_context_binding_id: binding_id,
        });
        setSessionContextSpace(next);
        setSession((current) =>
          current && current.id === session.id
            ? { ...current, project_context_binding_id: next.project_context_binding_id ?? null }
            : current,
        );
      } catch (err) {
        setSessionContextSpaceError(String(err));
      } finally {
        setSessionContextSpaceSaving(false);
      }
    },
    [session?.id],
  );

  const startNewDirection = useCallback(
    async (userSeed: string, mode: PlanspaceMode, modelPresetId: string) => {
      if (!session?.id || projectMutationPending) return;
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      setProjectMutationPending(true);
      try {
        const created = await createPlanspace(session.id, {
          seed: userSeed,
          mode,
          model_preset_id: modelPresetId,
        });
        if (shouldOpenCreatedPlanspace(created.activated)) {
          selectAndOpenNode(created.node_id);
        } else {
          setLaneCreationNotice({
            planspaceId: created.planspace_id,
            bindingId: created.binding_id,
            nodeId: created.node_id,
            kind: "concierge",
          });
        }
        await refreshContextSpace();
        await refreshNodes();
      } catch (err) {
        setSessionContextSpaceError(String(err));
      } finally {
        setProjectMutationPending(false);
        setSessionContextSpaceSaving(false);
      }
    },
    [
      session?.id,
      projectMutationPending,
      refreshContextSpace,
      refreshNodes,
      selectAndOpenNode,
    ],
  );

  const startBlankDirection = useCallback(
    async (userSeed: string, mode: PlanspaceMode, modelPresetId: string) => {
      if (!session?.id || projectMutationPending) return;
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      setProjectMutationPending(true);
      try {
        const created = await createBlankPlanspace(session.id, {
          seed: userSeed,
          mode,
          model_preset_id: modelPresetId,
        });
        if (shouldOpenCreatedPlanspace(created.activated)) {
          selectAndOpenNode(created.node_id);
          setFocusRequestVersion((version) => version + 1);
        } else {
          setLaneCreationNotice({
            planspaceId: created.planspace_id,
            bindingId: created.binding_id,
            nodeId: created.node_id,
            kind: "blank",
          });
        }
        await refreshContextSpace();
        await refreshNodes();
      } catch (err) {
        setSessionContextSpaceError(String(err));
      } finally {
        setProjectMutationPending(false);
        setSessionContextSpaceSaving(false);
      }
    },
    [
      session?.id,
      projectMutationPending,
      refreshContextSpace,
      refreshNodes,
      selectAndOpenNode,
    ],
  );

  const changePlanspaceMode = useCallback(
    async (planspaceId: string, mode: PlanspaceMode) => {
      if (!session?.id) return;
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      try {
        const next = await updatePlanspaceMode(session.id, planspaceId, mode);
        setSessionContextSpace(next);
        await refreshNodes();
      } catch (err) {
        setSessionContextSpaceError(String(err));
      } finally {
        setSessionContextSpaceSaving(false);
      }
    },
    [refreshNodes, session?.id],
  );

  /* Throws instead of swallowing so the caller can render the busy-node
   * conflict inline; the lane and all of its nodes are gone server-side, so
   * both the contextspace and the node list must be refetched. */
  const deletePlanspaceLane = useCallback(
    async (planspaceId: string) => {
      if (!session?.id) throw new Error("No active project.");
      if (projectMutationPending) {
        throw new Error("Project is busy.");
      }
      setProjectMutationPending(true);
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      try {
        const next = await deletePlanspace(session.id, planspaceId);
        setSessionContextSpace(next);
        setSelection((current) =>
          current.kind === "planspace" && current.planspaceId === planspaceId
            ? { kind: "none" }
            : current,
        );
        await refreshNodes();
      } finally {
        setProjectMutationPending(false);
        setSessionContextSpaceSaving(false);
      }
    },
    [projectMutationPending, refreshNodes, session?.id],
  );

  const promoteVirtualNode = useCallback(
    async (nodeId: string) => {
      if (!session?.id || projectMutationPending) return;
      setProjectMutationPending(true);
      setSessionContextSpaceError(null);
      try {
        const result = await promoteVirtual(session.id, nodeId);
        setNodes((prev) => {
          const updated = upsertNode(prev, result.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        selectAndOpenNode(result.node.id);
        await refreshNodes();
      } catch (err) {
        await refreshNodes().catch(() => {});
        setSessionContextSpaceError(String(err));
      } finally {
        setProjectMutationPending(false);
      }
    },
    [session?.id, projectMutationPending, refreshNodes, selectAndOpenNode],
  );

  const dequeueQueuedNode = useCallback(
    async (nodeId: string) => {
      if (!session?.id || projectMutationPending) return;
      setProjectMutationPending(true);
      setSessionContextSpaceError(null);
      try {
        const result = await dequeueNode(session.id, nodeId);
        setNodes((prev) => {
          const updated = upsertNode(prev, result.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        selectAndOpenNode(result.node.id);
        await refreshNodes();
      } catch (err) {
        await refreshNodes().catch(() => {});
        setSessionContextSpaceError(String(err));
      } finally {
        setProjectMutationPending(false);
      }
    },
    [session?.id, projectMutationPending, refreshNodes, selectAndOpenNode],
  );

  const updateVirtualNode = useCallback(
    async (nodeId: string, payload: UpdateVirtualPayload): Promise<NodeInfo | undefined> => {
      if (!session?.id) return undefined;
      setSessionContextSpaceError(null);
      try {
        const result = await updateVirtual(session.id, nodeId, payload);
        setNodes((prev) => {
          const updated = upsertNode(prev, result.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        return result.node;
      } catch (err) {
        setSessionContextSpaceError(String(err));
        throw err;
      }
    },
    [session?.id],
  );

  /* Drag-onto-virtual attach path: Canvas hands us (virtualNodeId, principleId)
   * when a principle chip is dropped on a virtual tile. We read the target's
   * current pending_extra_principles, append, and PATCH via updateVirtualNode.
   * Fire-and-forget: errors surface through sessionContextSpaceError. */
  const handleAttachPrincipleToVirtual = useCallback(
    (virtualNodeId: string, principleId: string) => {
      const target = nodesRef.current.find((n) => n.id === virtualNodeId);
      if (!target || target.state !== "virtual" || target.obsolete_reason) {
        return;
      }
      const current = target.pending_extra_principles ?? [];
      if (current.includes(principleId)) return;
      void updateVirtualNode(virtualNodeId, {
        pending_extra_principles: [...current, principleId],
      });
    },
    [updateVirtualNode],
  );

  const handleAttachSkillToVirtual = useCallback(
    (virtualNodeId: string, skillId: string) => {
      const target = nodesRef.current.find((n) => n.id === virtualNodeId);
      if (!target || target.state !== "virtual" || target.obsolete_reason) return;
      const current = target.pending_extra_skills ?? [];
      if (current.some((selection) => selection.id === skillId)) return;
      void updateVirtualNode(virtualNodeId, {
        pending_extra_skills: [...current, { id: skillId, suggest: false }],
      });
    },
    [updateVirtualNode],
  );

  /* The library preview modal's attach button targets whatever virtual node is
   * selected, under the same eligibility rules the canvas drop path enforces
   * (`Canvas.tsx:974-993`): a virtual, non-obsolete, mutable agent node. Null
   * disables the button rather than hiding it, so the reason stays visible. */
  const libraryAttachTarget = useMemo(() => {
    if (readOnly) return null;
    const nodeId = selection.kind === "agent" ? selection.nodeId : null;
    if (!nodeId) return null;
    const node = nodes.find((item) => item.id === nodeId);
    if (!node || node.state !== "virtual" || node.obsolete_reason) return null;
    if (!isNodeNative(node)) return null;
    /* A motivation can run to a full paragraph, so the label is the first line
     * trimmed to something that fits a one-line hint. */
    const raw = (node.summary || node.prompt_draft || node.prompt || "").trim();
    const firstLine = raw.split("\n", 1)[0].trim();
    const label = firstLine.length > 0
      ? (firstLine.length > 36 ? `${firstLine.slice(0, 36)}…` : firstLine)
      : nodeId.slice(0, 8);
    return { nodeId, label };
  }, [isNodeNative, nodes, readOnly, selection]);

  const handleAttachLibraryEntryToSelection = useCallback(
    (entryId: string) => {
      if (!libraryAttachTarget) return;
      if (entryId.startsWith("skills.")) {
        handleAttachSkillToVirtual(libraryAttachTarget.nodeId, entryId);
      } else {
        handleAttachPrincipleToVirtual(libraryAttachTarget.nodeId, entryId);
      }
    },
    [handleAttachPrincipleToVirtual, handleAttachSkillToVirtual, libraryAttachTarget],
  );

  const createVirtualNode = useCallback(
    async (payload: {
      planspace_id: string;
      scheduled_deps?: string[];
      model_preset_id?: string | null;
      resume_from_node_id?: string | null;
      position?: { x: number; y: number };
    }) => {
      if (!session?.id || virtualCreateDisabled) return;
      setProjectMutationPending(true);
      setSessionContextSpaceError(null);
      try {
        const result = await createVirtual(session.id, {
          prompt_draft: "",
          category: "regular",
          motivation: "",
          scheduled_deps: payload.scheduled_deps ?? [],
          model_preset_id: payload.resume_from_node_id
            ? undefined
            : defaultModelPresetId(
                modelPresets,
                payload.model_preset_id ?? session.model_preset_id,
              ),
          planspace_id: payload.planspace_id,
          resume_from_node_id: payload.resume_from_node_id ?? null,
        });
        if (payload.position) {
          setNodePositionTarget({
            nodeId: result.node.id,
            position: payload.position,
          });
        }
        setNodes((prev) => {
          const updated = upsertNode(prev, result.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        selectAndOpenNode(result.node.id);
        setFocusRequestVersion((version) => version + 1);
      } catch (err) {
        setSessionContextSpaceError(String(err));
        throw err;
      } finally {
        setProjectMutationPending(false);
      }
    },
    [
      session?.id,
      session?.model_preset_id,
      modelPresets,
      virtualCreateDisabled,
      selectAndOpenNode,
    ],
  );

  const createUnparentedVirtual = useCallback(
    (planspaceId: string, position?: { x: number; y: number }) => {
      // Prefer the planspace's own model preset (from its earliest node) over
      // the project-level default.
      const laneNodes = nodes.filter((n) => n.planspace_id === planspaceId);
      const laneAnchor = laneNodes.reduce<NodeInfo | null>((acc, n) => {
        if (acc === null) return n;
        return n.created_at < acc.created_at ? n : acc;
      }, null);
      const modelPresetId = laneAnchor?.model_preset_id ?? session?.model_preset_id ?? null;
      /* Without an explicit position (the lane header "+" button, as opposed to
       * a double-click that names a spot), append under the lane's existing
       * work instead of letting the default cursor put the tile on the top row
       * far off to the right. */
      const resolvedPosition =
        position ??
        resolveLaneAppendPosition(
          planspaceId,
          nodeIdsByRecentActivityInLane(nodes, planspaceId),
        ) ??
        undefined;
      void createVirtualNode({
        planspace_id: planspaceId,
        model_preset_id: modelPresetId,
        position: resolvedPosition,
      });
    },
    [createVirtualNode, nodes, session?.model_preset_id],
  );

  const createDependencyVirtual = useCallback(
    (parentNodeId: string) => {
      const parent = nodes.find((node) => node.id === parentNodeId);
      const planspaceId = parent?.planspace_id ?? sessionContextSpace?.active_planspace_id;
      if (!parent || !planspaceId) return;
      void createVirtualNode({
        planspace_id: planspaceId,
        scheduled_deps: [parent.id],
        model_preset_id: parent.model_preset_id ?? session?.model_preset_id ?? null,
      });
    },
    [createVirtualNode, nodes, session?.model_preset_id, sessionContextSpace?.active_planspace_id],
  );

  /* Attaching downstream of a whole instance (§4.3). The instance declares no
   * explicit outputs: its sinks — members with no downstream inside it — are
   * the outputs, so the new node depends on all of them. In the expanded view
   * the per-node "↘" affordance still creates a single-parent dependency, so
   * connecting to one internal node remains possible; the black box is the
   * default reading, not a wall. */
  const createDownstreamOfTemplateInstance = useCallback(
    (sinkNodeIds: string[]) => {
      const sinks = sinkNodeIds
        .map((nodeId) => nodes.find((node) => node.id === nodeId))
        .filter((node): node is NodeInfo => !!node);
      if (sinks.length === 0) return;
      const planspaceId =
        sinks[0].planspace_id ?? sessionContextSpace?.active_planspace_id;
      if (!planspaceId) return;
      void createVirtualNode({
        planspace_id: planspaceId,
        scheduled_deps: sinks.map((node) => node.id),
        model_preset_id:
          sinks[0].model_preset_id ?? session?.model_preset_id ?? null,
      });
    },
    [
      createVirtualNode,
      nodes,
      session?.model_preset_id,
      sessionContextSpace?.active_planspace_id,
    ],
  );

  const createContinuationVirtual = useCallback(
    (parentNodeId: string) => {
      const parent = nodes.find((node) => node.id === parentNodeId);
      const planspaceId = parent?.planspace_id ?? sessionContextSpace?.active_planspace_id;
      if (!parent || !planspaceId || !canResumeNode(parent)) return;
      void createVirtualNode({
        planspace_id: planspaceId,
        model_preset_id: parent.model_preset_id ?? null,
        resume_from_node_id: parent.id,
      });
    },
    [createVirtualNode, nodes, sessionContextSpace?.active_planspace_id],
  );

  const deleteVirtualNode = useCallback(
    async (nodeId: string) => {
      if (!session?.id) throw new Error("No active project.");
      if (virtualCreateDisabled) {
        throw new Error("Virtual deletion is temporarily unavailable.");
      }
      setSessionContextSpaceError(null);
      await deleteVirtual(session.id, nodeId);
      setNodes((prev) => {
        const updated = prev.filter((node) => node.id !== nodeId);
        nodeCountRef.current = updated.length;
        nodesRef.current = updated;
        return updated;
      });
      setPendingGates((prev) => withoutPendingNode(prev, nodeId));
      setPendingReviews((prev) => withoutPendingNode(prev, nodeId));
      if (inspectedNodeIdRef.current === nodeId) {
        inspectNode(null);
        setSelection({ kind: "none" });
      }
    },
    [session?.id, virtualCreateDisabled, inspectNode],
  );

  const canDeleteVirtualTemplateInstance = useCallback(
    (instanceId: string) => {
      if (virtualCreateDisabled) return false;
      const members = nodesRef.current.filter(
        (node) => node.template_instance_id === instanceId,
      );
      return (
        members.length > 0 &&
        members.every(
          (node) => node.state === "virtual" && isNodeNative(node),
        )
      );
    },
    [isNodeNative, virtualCreateDisabled],
  );

  const deleteVirtualTemplateInstance = useCallback(
    async (instanceId: string) => {
      const sessionId = session?.id;
      if (!sessionId) return;
      const members = nodesRef.current.filter(
        (node) => node.template_instance_id === instanceId,
      );
      const planspaceIds = new Set(
        members.map((node) => node.planspace_id).filter((id): id is string => !!id),
      );
      if (
        members.length === 0 ||
        planspaceIds.size !== 1 ||
        !members.every((node) => node.state === "virtual" && isNodeNative(node))
      ) {
        window.alert("只有成员全部仍为 virtual 的模板实例才能整组删除。");
        return;
      }
      if (!window.confirm(`删除这组 ${members.length} 个 virtual 模板节点？`)) {
        return;
      }

      try {
        const result = await deleteTemplateInstance(
          sessionId,
          [...planspaceIds][0],
          instanceId,
        );
        const removed = new Set(result.removed_node_ids);
        setNodes((current) => {
          const updated = current.filter((node) => !removed.has(node.id));
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        for (const nodeId of removed) {
          setPendingGates((current) => withoutPendingNode(current, nodeId));
          setPendingReviews((current) => withoutPendingNode(current, nodeId));
        }
        setTemplateInstances((current) =>
          current.filter((record) => record.instance_id !== instanceId),
        );
        setCollapsedTemplateInstancesBySession((current) => ({
          ...current,
          [sessionId]: (current[sessionId] ?? []).filter((id) => id !== instanceId),
        }));
        if (
          selectionRef.current.kind === "templateInstance" &&
          selectionRef.current.instanceId === instanceId
        ) {
          setSelection({ kind: "none" });
          inspectNode(null);
        } else if (
          inspectedNodeIdRef.current &&
          removed.has(inspectedNodeIdRef.current)
        ) {
          setSelection({ kind: "none" });
          inspectNode(null);
        }
      } catch (err) {
        window.alert(`无法删除这组模板节点：${apiErrorText(err)}`);
      }
    },
    [inspectNode, isNodeNative, session?.id],
  );

  const rerunFailedNode = useCallback(
    async (nodeId: string) => {
      if (!session?.id || projectMutationPending) return;
      setProjectMutationPending(true);
      setSessionContextSpaceError(null);
      try {
        const result = await rerunNode(session.id, nodeId);
        setNodes((prev) => {
          const updated = upsertNode(prev, result.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        selectAndOpenNode(result.node.id);
        setFocusRequestVersion((version) => version + 1);
        await refreshNodes();
      } catch (err) {
        setSessionContextSpaceError(String(err));
      } finally {
        setProjectMutationPending(false);
      }
    },
    [session?.id, projectMutationPending, refreshNodes, selectAndOpenNode],
  );

  const runContextInit = useCallback(async () => {
    if (!session?.id) return;
    setSessionContextSpaceSaving(true);
    setSessionContextSpaceError(null);
    try {
      const next = await initProjectContext(session.id);
      setSessionContextSpace(next);
    } catch (err) {
      setSessionContextSpaceError(String(err));
    } finally {
      setSessionContextSpaceSaving(false);
    }
  }, [session?.id]);

  const runContextRefresh = useCallback(async () => {
    if (!session?.id) return;
    setSessionContextSpaceSaving(true);
    setSessionContextSpaceError(null);
    try {
      const next = await refreshProjectContext(session.id);
      setSessionContextSpace(next);
    } catch (err) {
      setSessionContextSpaceError(String(err));
    } finally {
      setSessionContextSpaceSaving(false);
    }
  }, [session?.id]);

  const runContextCancel = useCallback(async () => {
    if (!session?.id) return;
    try {
      const next = await cancelProjectContext(session.id);
      setSessionContextSpace(next);
    } catch (err) {
      setSessionContextSpaceError(String(err));
    }
  }, [session?.id]);

  const togglePlanspaceVisibility = useCallback(
    async (planspaceId: string, hidden: boolean) => {
      if (!session?.id) return;
      if (readOnly) {
        setSessionContextSpace((current) => current ? {
          ...current,
          planspace_view: {
            ...(current.planspace_view ?? {}),
            [planspaceId]: { hidden },
          },
        } : current);
        return;
      }
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      try {
        const next = await updatePlanspaceView(session.id, {
          [planspaceId]: { hidden },
        });
        setSessionContextSpace(next);
      } catch (err) {
        setSessionContextSpaceError(String(err));
      } finally {
        setSessionContextSpaceSaving(false);
      }
    },
    [readOnly, session?.id],
  );

  const updatePreferredLanguage = useCallback(
    async (preferredLanguage: string | null) => {
      if (!session?.id) return;
      setSessionSettingsSaving(true);
      setSessionSettingsError(null);
      try {
        const next = await updateSessionPreferences(session.id, {
          preferred_language: preferredLanguage,
        });
        setSession((current) =>
          current && current.id === session.id ? { ...current, ...next } : current,
        );
      } catch (err) {
        setSessionSettingsError(String(err));
      } finally {
        setSessionSettingsSaving(false);
      }
    },
    [session?.id],
  );

  const updateConcurrency = useCallback(
    async (concurrency: number) => {
      if (!session?.id) return;
      setSessionSettingsSaving(true);
      setSessionSettingsError(null);
      try {
        const next = await updateSessionPreferences(session.id, { concurrency });
        setSession((current) =>
          current && current.id === session.id ? { ...current, ...next } : current,
        );
        await refreshNodes();
      } catch (err) {
        setSessionSettingsError(String(err));
      } finally {
        setSessionSettingsSaving(false);
      }
    },
    [refreshNodes, session?.id],
  );

  /* Events, diff, and context-bundle fetch — keyed off inspectedNodeId. */
  useEffect(() => {
    if (!session?.id || !inspectedNodeId || selectedNode?.state === "virtual") {
      setSelectedEventsState({ nodeId: inspectedNodeId, records: [] });
      setSelectedEventsLoading(false);
      return;
    }
    const requestedNodeId = inspectedNodeId;
    let cancelled = false;
    setSelectedEventsLoading(true);
    listNodeEvents(session.id, requestedNodeId)
      .then((records) => {
        if (cancelled) return;
        setSelectedEventsState((current) =>
          current.nodeId === requestedNodeId
            ? {
                nodeId: requestedNodeId,
                records: mergeEventRecords(current.records, records),
              }
            : current,
        );
      })
      .catch((err) => {
        if (!cancelled) {
          console.error("list node events failed:", err);
        }
      })
      .finally(() => {
        if (!cancelled) setSelectedEventsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [session?.id, inspectedNodeId, selectedNode?.state]);

  useEffect(() => {
    if (!session?.id || !inspectedNodeId || selectedNode?.state === "virtual") {
      setSelectedDiff(null);
      setSelectedDiffLoading(false);
      return;
    }
    let cancelled = false;
    let retryTimer: number | null = null;
    let resolveRetry: (() => void) | null = null;
    setSelectedDiffLoading(true);
    const loadDiff =
      selectedNode?.subtype === "code_review" ? getReviewedDiff : getNodeDiff;
    const loadWithRunningRetry = async () => {
      try {
        return await loadDiff(session.id, inspectedNodeId);
      } catch (err) {
        const shouldRetry =
          selectedNode?.subtype === "code_review" &&
          selectedNode.state === "running" &&
          String(err).includes(": 404");
        if (!shouldRetry) throw err;
        await new Promise<void>((resolve) => {
          resolveRetry = resolve;
          retryTimer = window.setTimeout(() => {
            retryTimer = null;
            resolveRetry = null;
            resolve();
          }, 500);
        });
        if (cancelled) return null;
        return loadDiff(session.id, inspectedNodeId);
      }
    };
    loadWithRunningRetry()
      .then((diff) => {
        if (!cancelled && diff) setSelectedDiff(diff);
      })
      .catch((err) => {
        if (!cancelled) {
          console.error("get node diff failed:", err);
          setSelectedDiff(null);
        }
      })
      .finally(() => {
        if (!cancelled) setSelectedDiffLoading(false);
      });
    return () => {
      cancelled = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      resolveRetry?.();
    };
  }, [
    session?.id,
    inspectedNodeId,
    selectedNode?.commit_before,
    selectedNode?.commit_after,
    selectedNode?.subtype,
    selectedNode?.state,
  ]);

  useEffect(() => {
    if (!session?.id || !inspectedNodeId || selectedNode?.state === "virtual") {
      setSelectedContextBundle(null);
      setSelectedContextBundleLoading(false);
      return;
    }
    let cancelled = false;
    setSelectedContextBundleLoading(true);
    getNodeContextBundle(session.id, inspectedNodeId)
      .then((bundle) => {
        if (cancelled) return;
        setSelectedContextBundle(bundle);
        /* aggregate so the context lane has data even after the user navigates away */
        if (bundle) {
          setContextBundlesByNodeId((prev) => ({
            ...prev,
            [inspectedNodeId]: bundle,
          }));
        }
      })
      .catch((err) => {
        if (!cancelled) {
          console.error("get node context bundle failed:", err);
          setSelectedContextBundle(null);
        }
      })
      .finally(() => {
        if (!cancelled) setSelectedContextBundleLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    session?.id,
    inspectedNodeId,
    selectedNode?.context_bundle_id,
    selectedNode?.context_bundle_path,
    selectedNode?.state,
    selectedNode?.finished_at,
  ]);

  /* When the node list refreshes, prefetch bundles for terminal nodes we haven't
   * loaded yet, capped to avoid hammering the backend on big projects. The
   * `inflightBundleFetchRef` guard prevents the self-modifying dep loop where
   * each successful fetch updates `contextBundlesByNodeId`, retriggers this
   * effect, and refetches the still-in-flight nodes. */
  useEffect(() => {
    if (!session?.id) return;
    const sessionId = session.id;
    const missing = nodes
      .filter(
        (n) =>
          n.kind !== "op" &&
          TERMINAL_STATES.has(n.state) &&
          n.context_bundle_id &&
          contextBundlesByNodeId[n.id] === undefined &&
          !inflightBundleFetchRef.current.has(n.id),
      )
      .slice(0, 6);
    if (missing.length === 0) return;
    let cancelled = false;
    for (const n of missing) inflightBundleFetchRef.current.add(n.id);
    void Promise.all(
      missing.map(async (n) => {
        try {
          const bundle = await getNodeContextBundle(sessionId, n.id);
          if (cancelled) return;
          setContextBundlesByNodeId((prev) =>
            prev[n.id] !== undefined ? prev : { ...prev, [n.id]: bundle },
          );
        } catch (err) {
          console.warn("prefetch bundle failed", n.id, err);
        } finally {
          inflightBundleFetchRef.current.delete(n.id);
        }
      }),
    );
    return () => {
      cancelled = true;
    };
  }, [session?.id, nodes, contextBundlesByNodeId]);

  /* WS event handling */
  const flushSelectedEvents = useCallback(() => {
    selectedEventsFlushTimerRef.current = null;
    const pending = pendingSelectedEventsRef.current;
    pendingSelectedEventsRef.current = null;
    if (!pending || inspectedNodeIdRef.current !== pending.nodeId) return;
    setSelectedEventsState((current) =>
      current.nodeId === pending.nodeId
        ? {
            nodeId: current.nodeId,
            records: mergeEventRecords(current.records, pending.records),
          }
        : current,
    );
  }, []);

  const appendSelectedEvent = useCallback((nodeId: string | null, ev: ServerEvent) => {
    const seq = ev.seq;
    if (
      !nodeId ||
      inspectedNodeIdRef.current !== nodeId ||
      typeof seq !== "number" ||
      seq <= 0
    ) {
      return;
    }
    const record = { seq, event: ev };
    const pending = pendingSelectedEventsRef.current;
    if (pending?.nodeId === nodeId) {
      pending.records.push(record);
    } else {
      pendingSelectedEventsRef.current = { nodeId, records: [record] };
    }
    if (selectedEventsFlushTimerRef.current === null) {
      selectedEventsFlushTimerRef.current = window.setTimeout(flushSelectedEvents, 32);
    }
  }, [flushSelectedEvents]);

  useEffect(
    () => () => {
      if (selectedEventsFlushTimerRef.current !== null) {
        window.clearTimeout(selectedEventsFlushTimerRef.current);
      }
    },
    [],
  );

  const handleEvent = useCallback(
    (ev: ServerEvent) => {
      let eventNodeId = "node_id" in ev ? ev.node_id : null;
      if (ev.type === "interaction_request") {
        const pending = { request: ev, nodeId: ev.node_id };
        if (isReviewInteraction(ev)) {
          setPendingReviews((current) => ({ ...current, [ev.node_id]: pending }));
        } else {
          setPendingGates((current) => ({ ...current, [ev.node_id]: pending }));
        }
        openInteractionNodeIfAppropriate(ev.node_id);
      } else if (ev.type === "turn_done") {
        setPendingGates((current) => withoutPendingNode(current, ev.node_id));
        setPendingReviews((current) => withoutPendingNode(current, ev.node_id));
        setNodes((prev) => {
          const updated = upsertNode(prev, ev.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
      } else if (ev.type === "error") {
        console.error("server error:", ev.message);
      } else if (ev.type === "node_started") {
        eventNodeId = ev.node_id;
        const startedKind = ev.kind ?? "agent";
        if (startedKind !== "op") {
          selectEventNodeIfIdle(ev.node_id);
        }
        setNodes((prev) => {
          const updated = upsertNode(prev, ev.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
      } else if (ev.type === "node_updated") {
        eventNodeId = ev.node.id;
        setNodes((prev) => {
          const updated = upsertNode(prev, ev.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        if (ev.node.state !== "waiting") {
          setPendingGates((prev) => withoutPendingNode(prev, ev.node.id));
        }
        if (ev.node.state !== "awaiting_human_input") {
          setPendingReviews((prev) => withoutPendingNode(prev, ev.node.id));
        }
      } else if (ev.type === "node_removed") {
        eventNodeId = ev.id;
        setNodes((prev) => {
          const updated = prev.filter((node) => node.id !== ev.id);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        setPendingGates((prev) => withoutPendingNode(prev, ev.id));
        setPendingReviews((prev) => withoutPendingNode(prev, ev.id));
        if (inspectedNodeIdRef.current === ev.id) {
          inspectNode(null);
          setSelection({ kind: "none" });
        }
      } else if (ev.type === "git_status") {
        setGitStatus((current) => ({
          is_repo: ev.is_repo,
          head: ev.head,
          branch: ev.branch,
          detached: ev.detached,
          upstream: ev.upstream,
          ahead: ev.ahead,
          behind: ev.behind,
          dirty_count: ev.dirty_count,
          files: ev.files ?? current?.files ?? [],
        }));
        void refreshGit();
      } else if (ev.type === "context_refresh_updated") {
        setSessionContextSpace((current) =>
          current
            ? { ...current, context_refresh: ev.context_refresh }
            : current,
        );
        if (!ev.context_refresh.running) void refreshContextSpace();
      }
      appendSelectedEvent(eventNodeId, ev);
    },
    [
      appendSelectedEvent,
      inspectNode,
      openInteractionNodeIfAppropriate,
      refreshContextSpace,
      refreshGit,
      selectEventNodeIfIdle,
    ],
  );

  const { status, send } = useSessionSocket(
    route === "project" ? (session?.id ?? null) : null,
    handleEvent,
    socketReplayNodeIds,
    /* `context_refresh_updated` is ephemeral (seq 0, never persisted, never
     * replayed) and is the only live channel that clears `running`. A drop
     * between the start and finish of a refresh would otherwise leave
     * `virtualCreateDisabled` stuck true until a full page reload, so a
     * reconnect re-reads the authoritative server-side status. */
    reconcileContextSpace,
  );
  const canInterruptRunner = status === "open" && hasInterruptibleNode;
  /* Planspace ids available for cross-lane loads, sourced from the
   * contextspace describe call. Excludes the active planspace (it loads
   * by default via the binding). */
  const planspaceOptions = useMemo(() => {
    const out: { id: string; label: string }[] = [];
    const seen = new Set<string>();
    for (const binding of sessionContextSpace?.bindings ?? []) {
      for (const plug of binding.plugs) {
        if (plug.kind !== "planspace" || seen.has(plug.id)) continue;
        seen.add(plug.id);
        out.push({ id: plug.id, label: plug.title || plug.id });
      }
    }
    return out;
  }, [sessionContextSpace]);

  const knownPlanspaceIds = useMemo(
    () => planspaceOptions.map((opt) => opt.id),
    [planspaceOptions],
  );

  const activatablePlanspaceIds = useMemo(() => {
    if (readOnly) return [];
    const resolvedBindingId = sessionContextSpace?.resolved_binding_id;
    const binding = sessionContextSpace?.bindings.find(
      (candidate) => candidate.id === resolvedBindingId,
    );
    return (binding?.plugs ?? [])
      .filter((plug) => plug.kind === "planspace")
      .map((plug) => plug.id);
  }, [readOnly, sessionContextSpace]);

  const manualPromotionPlanspaceId = useMemo(() => {
    const activeId = sessionContextSpace?.active_planspace_id ?? null;
    if (!activeId) return null;
    for (const binding of sessionContextSpace?.bindings ?? []) {
      const activePlug = binding.plugs.find(
        (plug) => plug.kind === "planspace" && plug.id === activeId,
      );
      if (activePlug) return activePlug.mode === "auto" ? null : activeId;
    }
    return activeId;
  }, [sessionContextSpace]);

  /* Dequeue is decided by the queued node's own lane mode (matching the
   * backend), not by which lane is active, so track auto lanes as a set.
   * Lanes without a planspace plug default to manual, like the backend. */
  const autoPlanspaceIds = useMemo(() => {
    const out = new Set<string>();
    for (const binding of sessionContextSpace?.bindings ?? []) {
      for (const plug of binding.plugs) {
        if (plug.kind === "planspace" && plug.mode === "auto") out.add(plug.id);
      }
    }
    return out;
  }, [sessionContextSpace]);

  useEffect(() => {
    if (
      laneCreationNotice &&
      sessionContextSpace?.active_planspace_id === laneCreationNotice.planspaceId
    ) {
      setLaneCreationNotice(null);
    }
  }, [laneCreationNotice, sessionContextSpace?.active_planspace_id]);

  const isManualPlanspace = useCallback(
    (planspaceId: string | null | undefined): boolean =>
      Boolean(planspaceId) && !autoPlanspaceIds.has(planspaceId ?? ""),
    [autoPlanspaceIds],
  );

  const hiddenPlanspaceIds = useMemo(() => {
    const hidden = new Set<string>();
    for (const [id, pref] of Object.entries(sessionContextSpace?.planspace_view ?? {})) {
      if (pref?.hidden) hidden.add(id);
    }
    for (const binding of sessionContextSpace?.bindings ?? []) {
      for (const plug of binding.plugs) {
        if (plug.kind === "planspace" && plug.hidden) hidden.add(plug.id);
      }
    }
    return Array.from(hidden);
  }, [sessionContextSpace]);
  hiddenPlanspaceIdsRef.current = hiddenPlanspaceIds;

  const interruptNode = useCallback(
    (nodeId: string) => {
      if (status !== "open") return;
      send({ type: "interrupt", node_id: nodeId });
    },
    [status, send],
  );

  const onResolveReview = useCallback(
    (payload: { id: string; judgment: string }) => {
      if (status !== "open") return;
      send({
        type: "interaction_response",
        id: payload.id,
        node_id: activePendingReview?.nodeId ?? null,
        allow: true,
        response: { prose: payload.judgment },
      });
      if (activePendingReview) {
        setPendingReviews((current) =>
          withoutPendingNode(current, activePendingReview.nodeId),
        );
      }
      window.setTimeout(() => {
        void refreshNodes();
      }, 250);
    },
    [activePendingReview, status, send, refreshNodes],
  );

  /* Wire the planspace lane header click → side-panel selection. */
  useEffect(() => {
    setPlanspaceLaneContext({
      onSelectPlanspace: (planspaceId) => {
        setSelection({ kind: "planspace", planspaceId });
        inspectNode(null);
      },
      onTogglePlanspaceVisibility: togglePlanspaceVisibility,
      onCreateVirtual: createUnparentedVirtual,
      onActivatePlanspace: (planspaceId) => {
        const bindingId = sessionContextSpace?.resolved_binding_id;
        if (bindingId) void activatePlanspace(bindingId, planspaceId);
      },
    });
  }, [
    activatePlanspace,
    createUnparentedVirtual,
    inspectNode,
    sessionContextSpace?.resolved_binding_id,
    togglePlanspaceVisibility,
  ]);

  /* Wire both instance views' collapse toggles. Collapsing is view-only, so it
   * touches no node state and issues no request. */
  useEffect(() => {
    setTemplateGroupContext({
      onToggleCollapsed: toggleTemplateInstanceCollapsed,
      canDelete: canDeleteVirtualTemplateInstance,
      onDelete: deleteVirtualTemplateInstance,
    });
    setTemplateInstanceBoxContext({
      onToggleCollapsed: toggleTemplateInstanceCollapsed,
      onCreateDownstream: createDownstreamOfTemplateInstance,
      canDelete: canDeleteVirtualTemplateInstance,
      onDelete: deleteVirtualTemplateInstance,
    });
  }, [
    canDeleteVirtualTemplateInstance,
    createDownstreamOfTemplateInstance,
    deleteVirtualTemplateInstance,
    toggleTemplateInstanceCollapsed,
  ]);

  const onResolveGate = useCallback(
    (
      id: string,
      payload: Omit<
        Extract<Parameters<typeof send>[0], { type: "interaction_response" }>,
        "type" | "id"
      >,
    ) => {
      const owner = Object.values(validPendingGates).find(
        (pending) => pending.request.id === id,
      );
      send({
        type: "interaction_response",
        id,
        node_id: owner?.nodeId ?? null,
        ...payload,
      });
      if (owner) {
        setPendingGates((current) => withoutPendingNode(current, owner.nodeId));
      }
      window.setTimeout(() => {
        void refreshNodes();
      }, 250);
    },
    [send, refreshNodes, validPendingGates],
  );

  const onSelectionChange = useCallback((sel: CanvasSelection) => {
    setSelection(sel);
    if (sel.kind === "agent" || sel.kind === "op" || sel.kind === "artifact") {
      inspectNode(sel.nodeId);
    } else if (sel.kind === "commit") {
      inspectNode(null);
    } else if (sel.kind === "none") {
      inspectNode(null);
    }
    /* Node clicks open the panel in details mode (overriding the library
     * if that was showing). Empty-canvas click closes it. */
    if (sel.kind === "none") {
      setPanelState((prev) => (prev.open ? { ...prev, open: false } : prev));
    } else {
      setPanelState((prev) =>
        prev.open && prev.mode === "details" ? prev : { open: true, mode: "details" },
      );
    }
    if (
      sel.kind === "agent" &&
      nodesRef.current.some(
        (node) => node.id === sel.nodeId && node.state === "running",
      )
    ) {
      setActivityFocusRequestVersion((version) => version + 1);
    }
  }, [inspectNode]);

  const onMultiSelectionChange = useCallback((ids: string[]) => {
    setMultiSelectedNodeIds(ids);
  }, []);

  const onAgentNodeContextMenu = useCallback(
    (nodeId: string | null, x: number, y: number) => {
      if (!nodeId || readOnly) return; // right-click on non-agent → no menu
      setTemplateContextMenu({ nodeId, x, y });
    },
    [readOnly],
  );

  const openSaveTemplateModal = useCallback(
    (nodeIds: string[]) => {
      if (nodeIds.length === 0) return;
      setSaveTemplateNodeIds(nodeIds);
      setSaveTemplateOpen(true);
    },
    [],
  );

  /* A dropped template either stamps straight away (no arguments, no input
   * ports — unchanged pre-schema-v2 behaviour) or opens the instantiation
   * dialog. The summary is fetched on drop rather than read from the dock's
   * cache so a template edited since the last library refresh still gets its
   * current argument list. */
  const onTemplateDrop = useCallback(
    async (slug: string, anchorNodeId: string | null, anchorSinkNodeIds?: string[]) => {
      if (!session?.id || readOnly) return;
      /* Dropping on a collapsed instance anchors to its output. `apply` takes a
       * single anchor, so the first sink is used; full multi-sink expansion is
       * available on the virtual-creation path, where the frontend owns the
       * whole dependency array (§4.3). */
      const resolvedAnchorNodeId = anchorNodeId ?? anchorSinkNodeIds?.[0] ?? null;
      try {
        const templates = await listUserTemplates();
        const template = templates.find((item) => item.slug === slug);
        if (!template) throw new Error(`template not found: ${slug}`);
        if (templateNeedsInstantiateDialog(template)) {
          setInstantiateTarget({ template, anchorNodeId: resolvedAnchorNodeId });
          return;
        }
        const applied = await applyUserTemplate(session.id, slug, {
          anchor_node_id: resolvedAnchorNodeId,
          arguments: {},
          input_bindings: {},
        });
        /* Collapsed is the default view for a fresh instance (§6.2). */
        toggleTemplateInstanceCollapsed(applied.instance_id, true);
        // Manual-lane stamps do not emit node_updated events.
        await refreshNodes();
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("applyUserTemplate failed", err);
        window.alert(`Could not apply template: ${apiErrorText(err)}`);
      }
    },
    [readOnly, refreshNodes, session?.id, toggleTemplateInstanceCollapsed],
  );

  /* select a specific node id (used by panel "jump to" affordances and the
   * pending-node banner). Unlike a bare canvas click, these are explicit
   * user asks to *inspect* the node, so we also open the panel. */
  const onSelectNode = useCallback(
    (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      setSelection({
        kind: node.kind === "op" ? "op" : "agent",
        nodeId,
      });
      inspectNode(nodeId);
      openDetails();
    },
    [inspectNode, nodes, openDetails],
  );

  const onSelectArtifact = useCallback(
    (nodeId: string, name: string, ext: "md" | "json" | "html") => {
      if (!nodes.some((node) => node.id === nodeId)) return;
      if (ext === "html" && session?.id) {
        window.open(artifactRawUrl(session.id, nodeId, name), "_blank", "noopener");
      }
      setSelection({ kind: "artifact", nodeId, name, ext });
      inspectNode(nodeId);
      openDetails();
    },
    [inspectNode, nodes, openDetails, session?.id],
  );

  /* Wire per-agent canvas affordances and inline pending-response tiles into
   * the AgentNode module singleton. */
  useEffect(() => {
    setAgentNodeContext({
      onPromoteVirtual: promoteVirtualNode,
      onDequeueNode: dequeueQueuedNode,
      onCreateContinuationVirtual: createContinuationVirtual,
      onCreateDependencyVirtual: createDependencyVirtual,
      onMarkVirtualObsolete: async (nodeId) => {
        await updateVirtualNode(nodeId, { obsolete_reason: "Obsoleted by user" });
      },
      onDeleteVirtual: deleteVirtualNode,
      onInterruptNode: interruptNode,
      onRerunNode: rerunFailedNode,
      canCreateVirtual: !virtualCreateDisabled,
      canMutateNode: (nodeId) => {
        const node = nodes.find((item) => item.id === nodeId);
        return !!node && isNodeNative(node);
      },
      canPromoteVirtual: !projectMutationPending && !readOnly,
      canDequeue: !projectMutationPending && !readOnly,
      manualPromotionPlanspaceId,
      isManualPlanspace,
      canInterrupt: canInterruptRunner && !readOnly,
      canRerun: !projectMutationPending && !readOnly,
      pendingGateForNode: (nodeId) => {
        if (readOnly) return null;
        const node = nodes.find((item) => item.id === nodeId);
        return node && isNodeNative(node)
          ? validPendingGates[nodeId]?.request ?? null
          : null;
      },
      onResolveGate,
      modelPresets,
    });
  }, [
    validPendingGates,
    onResolveGate,
    promoteVirtualNode,
    dequeueQueuedNode,
    createContinuationVirtual,
    createDependencyVirtual,
    updateVirtualNode,
    deleteVirtualNode,
    interruptNode,
    rerunFailedNode,
    virtualCreateDisabled,
    projectMutationPending,
    manualPromotionPlanspaceId,
    isManualPlanspace,
    readOnly,
    canInterruptRunner,
    composerLocked,
    modelPresets,
    nodes,
    isNodeNative,
  ]);

  /* Canvas layout changes -> serialized backend PATCHes. Best-effort: log on
   * failure but don't surface; the client-side ref keeps working either way. */
  const onLayoutHintsChange = useCallback(
    (
      updates: Record<string, { x: number; y: number }>,
      layoutViewport?: CanvasViewport | null,
      remove: string[] = [],
    ) => {
      if (!session?.id || readOnly) return;
      if (Object.keys(updates).length === 0 && remove.length === 0 && !layoutViewport) return;
      const sessionId = session.id;
      const updatesSnapshot = Object.fromEntries(
        Object.entries(updates).map(([id, pos]) => [id, { x: pos.x, y: pos.y }]),
      );
      const viewportSnapshot = layoutViewport ? { ...layoutViewport } : layoutViewport;
      const removeSnapshot = [...remove];
      const save = layoutSaveChainRef.current
        .catch(() => undefined)
        .then(() => updateLayoutHints(sessionId, updatesSnapshot, removeSnapshot, viewportSnapshot))
        .then((next) => {
          setSession((current) =>
            current && current.id === next.id ? { ...current, ...next } : current,
          );
          return next;
        });
      lastLayoutSaveRef.current = save;
      layoutSaveChainRef.current = save.then(
        () => undefined,
        () => undefined,
      );
      save.catch((err) => {
        console.warn("update layout hints failed:", err);
      }).finally(() => {
        if (lastLayoutSaveRef.current === save) {
          lastLayoutSaveRef.current = null;
        }
      });
    },
    [readOnly, session?.id],
  );

  if (route === "landing") {
    return (
      <div className="flex h-screen flex-col bg-surface text-ink">
        <div className="min-h-0 flex-1">
          <ProjectsLanding
          onOpen={openProject}
          onCreate={() => setNewProjectModalOpen(true)}
          sessions={landingSessions}
          setSessions={setLandingSessions}
          tags={landingTags}
          setTags={setLandingTags}
          modelPresets={modelPresets}
          globalState={globalState}
          onGlobalStateChanged={(next) => {
            setGlobalState(next);
            setModelPresets(next.model_presets);
          }}
          onTemplateLaunched={(s) => {
            setLandingSessions((current) => upsertSession(current, s));
            openProject(s);
          }}
          notificationBell={(
            <NotificationBell
              enabled
              feed={activeNodesFeed}
              currentSessionId={null}
              onJump={jumpToActiveNode}
            />
          )}
          />
        </div>
        <NewProjectModal
          open={newProjectModalOpen}
          modelPresets={modelPresets}
          defaults={globalState?.defaults ?? null}
          onCancel={() => setNewProjectModalOpen(false)}
          onCreated={(next) => {
            setNewProjectModalOpen(false);
            setLandingSessions((current) => upsertSession(current, next));
            openProject(next);
          }}
        />
      </div>
    );
  }

  const projectTitle =
    session?.name?.trim() ||
    (session ? `Project ${session.id.slice(0, 8)}` : "Project");
  const pendingControlsNodeId =
    panelState.open &&
    panelState.mode === "details" &&
    (selection.kind === "agent" || selection.kind === "op")
      ? selection.nodeId
      : null;
  const pendingNotice = pendingBanner(
    activePendingGate,
    activePendingReview,
    pendingControlsNodeId,
  );

  const wsTone =
    status === "open"
      ? "text-state-running"
      : status === "connecting"
        ? "text-state-waiting"
        : "text-state-error";

  const gitQuiescent = !nodes.some((node) => node.state === "running" || node.state === "queued");
  const pullInFlight = nodes.some(
    (node) =>
      node.kind === "op" &&
      node.op_kind === "pull" &&
      (node.state === "running" || node.state === "queued"),
  );
  const reviewInFlight = nodes.some(
    (node) =>
      node.subtype === "code_review" &&
      (node.state === "running" || node.state === "queued"),
  );
  const runGitAction = async (action: "commit" | "review" | "pull" | "push", message = "") => {
    if (
      !session?.id ||
      readOnly ||
      !gitStatus?.is_repo ||
      gitAction ||
      (action === "review" && reviewInFlight)
    ) return;
    setGitAction(action);
    setGitError(null);
    try {
      if (action === "commit") {
        const result = await gitCommit(session.id, message);
        pendingUiCommitNodeIdsRef.current.add(result.node.id);
      } else if (action === "review") {
        const result = await gitReview(session.id);
        /* The review node is created server-side into the active planspace, so
         * it arrives with no position of its own and would otherwise take the
         * lane's default top-row slot. Place it under the lane's current work,
         * the same way the lane "+" button does. Passing the node id makes this
         * a no-op when the tile is already on the canvas — `spawn_code_review`
         * returns an in-flight review rather than creating one, and a WebSocket
         * refresh can render a genuinely new node before this point. Either way
         * a tile the user can already see must not jump. */
        const laneId = result.node.planspace_id;
        if (laneId) {
          const position = resolveLaneAppendPosition(
            laneId,
            nodeIdsByRecentActivityInLane(nodesRef.current, laneId),
            result.node.id,
          );
          if (position) {
            setNodePositionTarget({ nodeId: result.node.id, position });
          }
        }
        selectAndOpenNode(result.node.id);
      } else if (action === "pull") {
        await gitPull(session.id);
      } else {
        await gitPush(session.id);
      }
    } catch (err) {
      setGitError(err instanceof Error ? err.message : String(err));
    } finally {
      if (action === "commit") {
        // The terminal-node effect queues the ghost-position target before it
        // refreshes Git. Refreshing here can render the commit first and race
        // that transfer signal.
        await refreshNodes();
      } else {
        await Promise.all([refreshGit(), refreshNodes()]);
      }
      setGitAction(null);
    }
  };
  const commitGitMessage = (message: string) => runGitAction("commit", message);
  const reviewGitChanges = () => runGitAction("review");

  return (
    <TextZoomProvider preferredLanguage={session?.preferred_language ?? null}>
    <div className="flex h-screen flex-col bg-surface text-ink">
      <header className="flex items-center justify-between gap-4 border-b border-line bg-surface-raised px-6 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={backToLanding}
            className="inline-flex h-8 items-center gap-1 rounded-md border border-line bg-surface px-2.5 text-[11px] text-ink-muted transition hover:border-line-strong hover:bg-surface-sunken hover:text-ink"
            title="Back to projects"
          >
            <span aria-hidden="true">←</span>
            <span className="hidden sm:inline">Projects</span>
          </button>
          <div className="min-w-0">
            <div className="truncate font-display text-[14px] font-semibold tracking-tight text-ink-strong">
              {projectTitle}
            </div>
            <div className="flex items-center gap-2 font-mono text-[10px] text-ink-subtle">
              <span className="truncate">{session?.id ?? "—"}</span>
              <span className="text-line-strong">·</span>
              <span className={"inline-flex items-center gap-1 " + wsTone}>
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" />
                ws {status}
              </span>
              <GitWorkspaceStatus
                status={gitStatus}
                action={gitAction}
                canCommit={!readOnly && !!gitStatus?.is_repo && !gitAction && !!gitStatus.dirty_count}
                canPull={!readOnly && !!gitStatus?.is_repo && !gitAction && gitQuiescent}
                canPush={!readOnly && !!gitStatus?.is_repo && !gitAction && !pullInFlight}
                onRefresh={refreshGit}
                onCommit={() => {
                  setSelection({ kind: "commit", sha: null });
                  inspectNode(null);
                  openDetails();
                }}
                onPull={() => void runGitAction("pull")}
                onPush={() => void runGitAction("push")}
              />
              {gitError && <span className="max-w-[18rem] truncate text-state-error" title={gitError}>{gitError}</span>}
              {session?.read_only && (
                <span className="rounded border border-state-waiting/40 bg-state-waiting-soft px-1.5 py-0.5 font-sans text-state-waiting">
                  只读 · 此设备尚未配置项目路径
                </span>
              )}
              {session?.can_bind_here && (
                <button
                  type="button"
                  disabled={projectMutationPending}
                  onClick={() => {
                    const rootPath = window.prompt(
                      "请输入此设备上的项目目录绝对路径",
                    );
                    if (!rootPath?.trim()) return;
                    setProjectMutationPending(true);
                    void bindProjectHere(session.id, rootPath.trim())
                      .then(setSession)
                      .catch(async (error: unknown) => {
                        const message = error instanceof Error ? error.message : String(error);
                        if (!message.includes("无法校验") || !window.confirm(message)) {
                          if (!message.includes("无法校验")) window.alert(message);
                          return;
                        }
                        try {
                          setSession(await bindProjectHere(session.id, rootPath.trim(), {
                            unverifiedAcknowledged: true,
                          }));
                        } catch (retryError) {
                          window.alert(retryError instanceof Error ? retryError.message : String(retryError));
                        }
                      })
                      .finally(() => setProjectMutationPending(false));
                  }}
                  className="rounded border border-brand/50 bg-brand-soft px-1.5 py-0.5 font-sans text-brand-ink transition hover:border-brand disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {projectMutationPending ? "正在配置..." : "配置路径"}
                </button>
              )}
              {session?.bound_here && session.hosts.length > 0 && (
                <span
                  className="max-w-[16rem] truncate font-sans text-ink-muted"
                  title={session.hosts.map((host) => host.label || host.mid).join("、")}
                >
                  设备 {session.hosts.map((host) => host.label || host.mid).join("、")}
                </span>
              )}
              {session?.bound_here && !session.temporary && (
                <button
                  type="button"
                  disabled={projectMutationPending}
                  onClick={() => {
                    if (!window.confirm("解除此设备的项目路径绑定？项目记录与历史仍会保留，可稍后重新绑定。")) return;
                    setProjectMutationPending(true);
                    void unbindProjectHere(session.id, session.local_machine_id)
                      .then(setSession)
                      .catch((error: unknown) => window.alert(error instanceof Error ? error.message : String(error)))
                      .finally(() => setProjectMutationPending(false));
                  }}
                  className="rounded border border-line bg-surface px-1.5 py-0.5 font-sans text-ink-muted transition hover:border-line-strong hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
                >
                  解除绑定
                </button>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <UsageStrip usage={selectedNode?.usage ?? null} />

          {composerLocked && (
            <span className="hidden items-center rounded-md border border-state-waiting/40 bg-state-waiting-soft px-2 py-1 text-[10.5px] text-state-waiting sm:inline-flex">
              Awaiting response on a node
            </span>
          )}

          <NotificationBell
            enabled
            feed={activeNodesFeed}
            currentSessionId={session?.id ?? null}
            onJump={jumpToActiveNode}
          />

          <button
            type="button"
            onClick={() => {
              setSelection({ kind: "projectRoot" });
              inspectNode(null);
              if (!readOnly) {
                setNewDirectionRequestVersion((version) => version + 1);
              }
              openDetails();
            }}
            disabled={sessionSettingsSaving || projectMutationPending}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-ink-muted transition hover:border-line-strong hover:bg-surface-sunken hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
            title="Project and new direction"
            aria-label="Open project panel and new direction composer"
          >
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M3 12 12 4l9 8" />
              <path d="M5 10v9a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-9" />
              <path d="M10 20v-6h4v6" />
            </svg>
          </button>

          <button
            type="button"
            onClick={toggleLibrary}
            aria-pressed={panelState.open && panelState.mode === "library"}
            className={
              "inline-flex h-8 items-center rounded-md border px-2.5 text-xs font-medium transition " +
              (panelState.open && panelState.mode === "library"
                ? "border-brand bg-brand/10 text-ink-strong"
                : "border-line bg-surface text-ink-muted hover:border-line-strong hover:bg-surface-sunken hover:text-ink")
            }
            title="Toggle library"
          >
            Library
          </button>

          <ThemeToggle />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden bg-surface-sunken">
          {initialLoadComplete ? (
            <Canvas
              key={session?.id ?? "no-session"}
              sessionId={session?.id ?? ""}
              nodes={nodes}
              selectedNodeId={selectedCanvasNodeId}
              activeNodeIds={activeCanvasNodeIds}
              pendingGateNodeIds={pendingGateNodeIds}
              contextBundlesByNodeId={contextBundlesByNodeId}
              knownPlanspaceIds={knownPlanspaceIds}
              activatablePlanspaceIds={activatablePlanspaceIds}
              hiddenPlanspaceIds={hiddenPlanspaceIds}
              activePlanspaceId={sessionContextSpace?.active_planspace_id ?? null}
              autoPlanspaceIds={Array.from(autoPlanspaceIds)}
              canCreateVirtual={!virtualCreateDisabled}
              templateInstances={templateInstances}
              collapsedTemplateInstanceIds={collapsedTemplateInstanceIds}
              nodePositionTarget={nodePositionTarget}
              centerOnNodeRequest={centerOnNodeRequest}
              onNodePositionTargetApplied={(nodeId) => {
                setNodePositionTarget((current) =>
                  current?.nodeId === nodeId ? null : current,
                );
              }}
              onCreateVirtualAt={createUnparentedVirtual}
              principles={principles}
              skills={skills}
              gitCommits={gitCommits}
              gitHead={gitStatus?.head ?? null}
              gitHosts={session?.hosts ?? []}
              gitDirtyCount={gitStatus?.dirty_count ?? 0}
              commitPositionTarget={uiCommitPositionTargets[0] ?? null}
              onCommitPositionTransferHandled={consumeUiCommitPositionTarget}
              initialLayoutHints={session?.layout_hints}
              initialLayoutViewport={session?.layout_viewport ?? null}
              onSelectionChange={onSelectionChange}
              onMultiSelectionChange={onMultiSelectionChange}
              onAgentNodeContextMenu={onAgentNodeContextMenu}
              onTemplateDrop={onTemplateDrop}
              onAttachPrincipleToVirtual={handleAttachPrincipleToVirtual}
              onAttachSkillToVirtual={handleAttachSkillToVirtual}
              onLayoutHintsChange={onLayoutHintsChange}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-[11px] uppercase tracking-[0.18em] text-ink-subtle">
              Loading canvas…
            </div>
          )}

          {/* Hide the banner only while the matching response controls are
              actually visible in the details panel. */}
          {!readOnly && pendingNotice && (
            <PendingBanner
              label={pendingNotice.label}
              panelOpen={panelState.open}
              onJump={() => {
                onSelectNode(pendingNotice.nodeId);
              }}
            />
          )}

          {!readOnly && !pendingNotice && !hiddenLaneNotice && laneCreationNotice && (
            <CanvasNotice
              label={
                laneCreationNotice.kind === "concierge"
                  ? "新方向已创建并排队，将在当前节点跑完后自动开始"
                  : "新方向已创建，尚未激活"
              }
              panelOpen={panelState.open}
              actions={[
                {
                  label: "跳转",
                  onClick: () => {
                    selectAndOpenNode(laneCreationNotice.nodeId);
                    setLaneCreationNotice(null);
                  },
                },
                ...(laneCreationNotice.kind === "blank"
                  ? [
                      {
                        label: "激活",
                        onClick: () => {
                          void activatePlanspace(
                            laneCreationNotice.bindingId,
                            laneCreationNotice.planspaceId,
                          );
                          setLaneCreationNotice(null);
                        },
                      },
                    ]
                  : []),
              ]}
            />
          )}

          {!pendingNotice && hiddenLaneNotice && (
            <CanvasNotice
              label="该节点所在方向已隐藏，画布上不显示"
              panelOpen={panelState.open}
              actions={[
                {
                  label: "显示此方向",
                  onClick: () => {
                    const { planspaceId, nodeId } = hiddenLaneNotice;
                    setHiddenLaneNotice(null);
                    /* Center only once the lane is actually visible. Issued
                     * eagerly, CenterOnNode retries for ~600ms against a
                     * still-filtered-out node and then gives up for good. */
                    void togglePlanspaceVisibility(planspaceId, false).then(() =>
                      centerOnNode(nodeId),
                    );
                  },
                },
                { label: "关闭", onClick: () => setHiddenLaneNotice(null) },
              ]}
            />
          )}

          {/* Floating side panel — slides in from the right, swapping between
              the node inspector and the project library. */}
          <aside
            ref={panelRef}
            aria-hidden={!panelState.open}
            className={
              "absolute inset-y-0 right-0 z-20 flex w-[380px] flex-col border-l border-line bg-surface-sunken shadow-modal transition-transform duration-200 ease-out will-change-transform " +
              (panelState.open ? "translate-x-0" : "pointer-events-none translate-x-full")
            }
          >
            {panelState.mode === "library" ? (
              <LibraryDock
                refreshToken={libraryRefreshToken}
                surfaceNewToken={librarySurfaceToken}
                surfaceBaselineIds={librarySurfaceBaselineIds}
                modelPresets={modelPresets}
                principles={principles}
                skills={skills}
                nodes={nodes}
                contextBundlesByNodeId={contextBundlesByNodeId}
                onRefreshEntries={refreshLibraryEntries}
                onDeletePrinciple={handleDeletePrinciple}
                onDeleteSkill={handleDeleteSkill}
                onOpenFull={openLibraryEntry}
                onEditTemplate={setEditingTemplateSlug}
                onApplyTemplate={
                  readOnly ? undefined : (slug) => void onTemplateDrop(slug, null)
                }
                onAttachToVirtual={
                  libraryAttachTarget ? handleAttachLibraryEntryToSelection : undefined
                }
                attachTargetLabel={libraryAttachTarget?.label ?? null}
                onClose={closePanel}
              />
            ) : (
              <SidePanel
                onClose={closePanel}
                selection={selection}
                gitCommits={gitCommits}
                gitHead={gitStatus?.head ?? null}
                gitDirtyCount={gitStatus?.dirty_count ?? 0}
                gitActionPending={gitAction === "commit"}
                gitReviewPending={gitAction === "review" || reviewInFlight}
                onGitCommit={commitGitMessage}
                onGitReview={reviewGitChanges}
                nodes={nodes}
                session={sessionWithRuntimeCounts}
                modelPresets={modelPresets}
                templateInstances={templateInstances}
                onCreateDownstreamOfTemplateInstance={
                  virtualCreateDisabled ? undefined : createDownstreamOfTemplateInstance
                }
                onDeleteTemplateInstance={
                  selection.kind === "templateInstance" &&
                  canDeleteVirtualTemplateInstance(selection.instanceId)
                    ? deleteVirtualTemplateInstance
                    : undefined
                }
                events={selectedEvents}
                eventsLoading={selectedEventsLoading}
                diff={selectedDiff}
                diffLoading={selectedDiffLoading}
                contextBundle={selectedContextBundle}
                contextBundleLoading={selectedContextBundleLoading}
                contextBundlesByNodeId={contextBundlesByNodeId}
                contextSpace={sessionContextSpace}
                contextSpaceLoading={sessionContextSpaceLoading}
                contextSpaceSaving={sessionContextSpaceSaving}
                contextSpaceError={sessionContextSpaceError}
                settingsSaving={sessionSettingsSaving}
                settingsError={sessionSettingsError}
                pendingGate={
                  activePendingGate &&
                  selectedNode &&
                  selectedNode.state === "waiting" &&
                  activePendingGate.nodeId === selectedNode.id
                    ? activePendingGate.request
                    : null
                }
                pendingReview={
                  activePendingReview &&
                  selectedNode &&
                  selectedNode.state === "awaiting_human_input" &&
                  activePendingReview.nodeId === selectedNode.id
                    ? activePendingReview.request
                    : null
                }
                onResolveGate={onResolveGate}
                onResolveReview={onResolveReview}
                onSelectNode={onSelectNode}
                onSelectArtifact={onSelectArtifact}
                onSessionChange={(updated) => {
                  setSession((current) =>
                    current?.id === updated.id ? updated : current,
                  );
                }}
                onPreferredLanguageChange={updatePreferredLanguage}
                onConcurrencyChange={updateConcurrency}
                onActivatePlanspace={activatePlanspace}
                onSelectContextBinding={selectContextBinding}
                onNewDirection={startNewDirection}
                onStartBlankDirection={startBlankDirection}
                onImportSkill={handleImportSkill}
                onCreateContinuationVirtual={createContinuationVirtual}
                onPromoteVirtual={promoteVirtualNode}
                onDequeueNode={dequeueQueuedNode}
                onUpdateVirtual={updateVirtualNode}
                onInterruptNode={interruptNode}
                onRerunNode={rerunFailedNode}
                canInterrupt={
                  canInterruptRunner &&
                  !readOnly &&
                  !!selectedNode &&
                  isNodeNative(selectedNode)
                }
                canRerun={
                  !projectMutationPending &&
                  !readOnly &&
                  !!selectedNode &&
                  isNodeNative(selectedNode)
                }
                manualPromotionPlanspaceId={manualPromotionPlanspaceId}
                isManualPlanspace={isManualPlanspace}
                onPlanspaceModeChange={changePlanspaceMode}
                onContextInit={runContextInit}
                onContextRefresh={runContextRefresh}
                onContextCancel={runContextCancel}
                onTogglePlanspaceVisibility={togglePlanspaceVisibility}
                onDeletePlanspace={deletePlanspaceLane}
                contextReloadVersion={contextReloadVersion}
                focusRequestVersion={focusRequestVersion}
                activityFocusRequestVersion={activityFocusRequestVersion}
                newDirectionRequestVersion={newDirectionRequestVersion}
                onNewDirectionRequestHandled={acknowledgeNewDirectionRequest}
                principles={principles}
                onDeletePrinciple={handleDeletePrinciple}
                skills={skills}
                onDeleteSkill={handleDeleteSkill}
              />
            )}
          </aside>
        </main>
      </div>
    </div>
    {templateContextMenu && (
      <ContextMenu
        x={templateContextMenu.x}
        y={templateContextMenu.y}
        onClose={() => setTemplateContextMenu(null)}
        items={buildTemplateContextMenuItems({
          rightClickedNodeId: templateContextMenu.nodeId,
          multiSelectedNodeIds,
          onSaveAsTemplate: openSaveTemplateModal,
        })}
      />
    )}
    <SaveAsTemplateModal
      open={saveTemplateOpen}
      sessionId={session?.id ?? null}
      nodeIds={saveTemplateNodeIds}
      onCancel={() => setSaveTemplateOpen(false)}
      onSaved={() => {
        setSaveTemplateOpen(false);
        setLibraryRefreshToken((v) => v + 1);
      }}
      onSavedAndEdit={(slug) => {
        setSaveTemplateOpen(false);
        setLibraryRefreshToken((v) => v + 1);
        setEditingTemplateSlug(slug);
      }}
    />
    <TemplateEditor
      slug={editingTemplateSlug}
      onClose={() => setEditingTemplateSlug(null)}
      onSaved={() => setLibraryRefreshToken((v) => v + 1)}
    />
    <InstantiateTemplateModal
      open={instantiateTarget !== null}
      sessionId={session?.id ?? null}
      template={instantiateTarget?.template ?? null}
      nodes={nodes}
      activePlanspaceId={sessionContextSpace?.active_planspace_id ?? null}
      anchorNodeId={instantiateTarget?.anchorNodeId ?? null}
      onCancel={() => setInstantiateTarget(null)}
      onApplied={(result) => {
        setInstantiateTarget(null);
        /* Collapsed is the default view for a fresh instance (§6.2): the
         * template reads as one operator until the user opens it. */
        toggleTemplateInstanceCollapsed(result.instanceId, true);
        // Manual-lane stamps do not emit node_updated events.
        void refreshNodes();
      }}
    />
    </TextZoomProvider>
  );
}

function buildTemplateContextMenuItems(args: {
  rightClickedNodeId: string | null;
  multiSelectedNodeIds: string[];
  onSaveAsTemplate: (ids: string[]) => void;
}): ContextMenuItem[] {
  const { rightClickedNodeId, multiSelectedNodeIds, onSaveAsTemplate } = args;
  const idsForSave =
    multiSelectedNodeIds.length > 1 &&
    rightClickedNodeId &&
    multiSelectedNodeIds.includes(rightClickedNodeId)
      ? multiSelectedNodeIds
      : rightClickedNodeId
        ? [rightClickedNodeId]
        : multiSelectedNodeIds;
  return [
    {
      label:
        idsForSave.length === 1
          ? "Save as template…"
          : `Save ${idsForSave.length} nodes as template…`,
      disabled: idsForSave.length === 0,
      onClick: () => onSaveAsTemplate(idsForSave),
    },
  ];
}

function PendingBanner({
  label,
  onJump,
  panelOpen,
}: {
  label: string;
  onJump: () => void;
  panelOpen: boolean;
}) {
  return (
    <CanvasNotice
      label={label}
      panelOpen={panelOpen}
      actions={[{ label: "Jump", onClick: onJump }]}
    />
  );
}

function CanvasNotice({
  label,
  actions,
  panelOpen,
}: {
  label: string;
  actions: Array<{ label: string; onClick: () => void }>;
  panelOpen: boolean;
}) {
  /* When the floating side panel is open it sits at z-20 in the bottom-right
   * corner (w-380px). Slide the banner past the panel's left edge so the
   * Jump affordance stays clickable instead of being hidden underneath. */
  const positionClass = panelOpen ? "right-[calc(380px+0.75rem)]" : "right-3";
  return (
    <div
      className={
        "absolute bottom-3 z-10 flex items-center gap-3 rounded-md border border-state-waiting/40 bg-state-waiting-soft px-3 py-1.5 text-[11px] text-state-waiting shadow-card transition-[right] duration-200 ease-out " +
        positionClass
      }
    >
      <span>{label}</span>
      <span className="flex flex-none items-center gap-1.5">
        {actions.map((action) => (
          <button
            key={action.label}
            type="button"
            onClick={action.onClick}
            className="rounded border border-state-waiting/40 bg-surface-raised px-2 py-0.5 text-state-waiting transition hover:border-state-waiting/70"
          >
            {action.label}
          </button>
        ))}
      </span>
    </div>
  );
}

function upsertNode(prev: NodeInfo[], node: NodeInfo): NodeInfo[] {
  const index = prev.findIndex((item) => item.id === node.id);
  if (index < 0) {
    return [...prev, node].sort((a, b) => a.created_at - b.created_at);
  }
  return prev.map((item, i) =>
    i === index ? preferNewerNode(item, node) : item,
  );
}

function mergeEventRecords(
  current: EventRecord[],
  incoming: EventRecord[],
): EventRecord[] {
  if (incoming.length === 0) return current;

  let previousSeq = current.at(-1)?.seq ?? -1;
  let appendOnly = true;
  for (const record of incoming) {
    if (record.seq <= previousSeq) {
      appendOnly = false;
      break;
    }
    previousSeq = record.seq;
  }
  if (appendOnly) return [...current, ...incoming];

  const bySeq = new Map(current.map((record) => [record.seq, record]));
  let changed = false;
  for (const record of incoming) {
    if (bySeq.has(record.seq)) continue;
    bySeq.set(record.seq, record);
    changed = true;
  }
  if (!changed) return current;
  return Array.from(bySeq.values()).sort((left, right) => left.seq - right.seq);
}

function keepPendingForStates(
  pending: Record<string, PendingGateState>,
  nodes: NodeInfo[],
  states: NodeInfo["state"][],
): Record<string, PendingGateState> {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  return Object.fromEntries(
    Object.entries(pending).filter(([nodeId]) => {
      const owner = byId.get(nodeId);
      return !owner || states.includes(owner.state);
    }),
  );
}

function withoutPendingNode(
  pending: Record<string, PendingGateState>,
  nodeId: string,
): Record<string, PendingGateState> {
  if (!(nodeId in pending)) return pending;
  const next = { ...pending };
  delete next[nodeId];
  return next;
}

function removePendingNodes(
  pending: Record<string, PendingGateState>,
  shouldRemove: (nodeId: string) => boolean,
): Record<string, PendingGateState> {
  return Object.fromEntries(
    Object.entries(pending).filter(([nodeId]) => !shouldRemove(nodeId)),
  );
}

function isReviewInteraction(request: InteractionRequest): boolean {
  return request.interaction_type === "human_review_prose";
}

function pendingBanner(
  gate: PendingGateState | null,
  review: PendingGateState | null,
  visibleControlsNodeId: string | null,
): { nodeId: string; label: string } | null {
  const active = review ?? gate;
  if (!active || active.nodeId === visibleControlsNodeId) return null;
  const labelKind = isReviewInteraction(active.request) ? "review" : "response";
  return {
    nodeId: active.nodeId,
    label: `Node ${active.nodeId.slice(0, 8)} is awaiting your ${labelKind}.`,
  };
}

function readPanelState(): { open: boolean; mode: "details" | "library" } {
  try {
    const raw = window.localStorage.getItem("miniclaw.panelState");
    if (raw) {
      const parsed = JSON.parse(raw) as { open?: unknown; mode?: unknown };
      const mode = parsed.mode === "library" || parsed.mode === "templates"
        ? "library"
        : "details";
      return { open: parsed.open === true, mode };
    }
  } catch {
    /* fall through */
  }
  return { open: false, mode: "details" };
}

function graphNodeIdForSelection(selection: CanvasSelection): string | null {
  if (selection.kind === "agent" || selection.kind === "op") {
    return selection.nodeId;
  }
  if (selection.kind === "context") {
    return `ctx:${selection.identityKey}`;
  }
  if (selection.kind === "artifact") {
    return artifactNodeId(selection.nodeId, selection.name);
  }
  if (selection.kind === "projectRoot") {
    return null;
  }
  if (selection.kind === "planspace") {
    return `planspace:${selection.planspaceId}`;
  }
  if (selection.kind === "templateInstance") {
    return selection.collapsed
      ? templateInstanceBoxNodeId(selection.instanceId)
      : templateGroupNodeId(selection.instanceId);
  }
  if (selection.kind === "commit") {
    return selection.sha ? `commit:${selection.sha}` : "commit:ghost";
  }
  return null;
}

/** Collapsed instance ids per session. Instance ids are only unique within a
 * project, so the map is keyed by session id — a bare id set would collapse an
 * unrelated instance after switching projects. */
function readCollapsedTemplateInstances(): Record<string, string[]> {
  try {
    const raw = window.localStorage.getItem("miniclaw.collapsedTemplateInstances");
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const out: Record<string, string[]> = {};
        for (const [sessionId, ids] of Object.entries(parsed)) {
          if (!Array.isArray(ids)) continue;
          out[sessionId] = ids.filter((id): id is string => typeof id === "string");
        }
        return out;
      }
    }
  } catch {
    /* fall through */
  }
  return {};
}
