import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toNodeInfo } from "./nodeProjection";
import { useContextBundleSources } from "./useContextBundleSources";
import { useNodeContextBundle } from "./useNodeContextBundle";
import {
  cancelProjectContext,
  createBlankPlanspace,
  createVirtual,
  dequeueNode,
  deleteTemplateInstance,
  deleteVirtual,
  getSession,
  getNodeDiff,
  getReviewedDiff,
  getSessionContextSpace,
  initProjectContext,
  listNodeEvents,
  listNodes,
  promoteVirtual,
  refreshProjectContext,
  rerunNode,
  updateNodeLayout,
  updateGitLayout,
  updateLaneLayout,
  updateContextLayout,
  deletePlanspace,
  updatePlanspaceMode,
  updatePlanspaceView,
  updateSessionPreferences,
  updateVirtual,
  listPrinciples,
  deletePrinciple,
  listSkills,
  deleteSkill,
  importSkill,
  getGlobalState,
  getMigrationStatus,
  getGitState,
  gitCommit,
  gitReview,
  gitPull,
  gitPush,
  bindProjectHere,
  unbindProjectHere,
  revealProjectRoot,
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
  nodeLaneResolver,
  resolveNodePlanspaceId,
  templateGroupNodeId,
  templateInstanceBoxNodeId,
} from "./canvas/layout";
import { resolveLaneAppendPosition } from "./canvas/lanePlacement";
import { captureGitChangesPosition, type CommitPositionTarget } from "./canvas/gitPositions";
import {
  readFocusedLane,
  resolveFocusedLane,
  writeFocusedLane,
} from "./focusedLane";
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
/* The canvas and the template loader must agree on what a placeholder is, so the
 * argument chips reuse the editor's scanner rather than re-deriving the rule. */
import { scanPlaceholders } from "./templateEditor";
import { ProjectsLanding } from "./components/ProjectsLanding";
import { StorageMaintenance } from "./components/StorageMaintenance";
import { storageGuidance, type StorageFailure } from "./storageMaintenance";
import { NotificationBell } from "./components/NotificationBell";
import { NoticeBannerRail } from "./components/NoticeBannerRail";
import { RunStatusButton } from "./components/RunStatusButton";
import { ThemeToggle } from "./components/ThemeToggle";
import { UsageStrip } from "./components/UsageStrip";
import { GitWorkspaceStatus } from "./components/GitWorkspaceStatus";
import { TextZoomProvider } from "./components/TextZoom";
import type {
  ActiveNodeEntry,
  ArtifactExtension,
  EventRecord,
  InteractionRequest,
  NodeDiff,
  NodeInfo,
  ServerEvent,
  NodePosition,
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
import { EMBEDDED_SESSION_PREFIX } from "./types";
import { useSessionSocket } from "./ws";
import { useActiveNodes, useReadKeys } from "./activeNodes";
import { useNotices } from "./notices";
import {
  canResumeNode,
  extraPrinciplesAvailable,
  lanesByRecentActivity,
  nodeBelongsToHost,
  nodeClassification,
  nodeIdsByRecentActivityInLane,
  nodeIdsNeedingEventReplay,
  preferNewerNode,
  scheduledDepsAvailable,
  shouldAutoSelectEventNode,
  shouldOpenInteractionNode,
} from "./nodeUtil";
import { defaultModelPresetId } from "./modelPresets";
import {
  canConnectDependency,
  ScheduledDepsUpdateQueue,
} from "./canvas/dependencyWiring";

/** Top-level surfaces. `template` is the template editor, which used to be a
 * `fixed inset-0` overlay stacked on top of `project`.
 *
 * It is a sibling of `project` rather than a replacement for it: the project
 * session stays loaded underneath, exactly as it did while the overlay was up.
 * That is what {@link isSessionRoute} preserves — anything gated on "a session
 * is open" must treat `template` like `project`, or opening the editor would
 * silently drop the socket and every in-flight node refresh. */
type Route = "landing" | "project" | "template";

/** True on any route that has a live project session behind it. */
function isSessionRoute(route: Route): boolean {
  return route !== "landing";
}
type PendingGateState = {
  request: InteractionRequest;
  nodeId: string;
};
type SelectedEventsState = {
  nodeId: string | null;
  records: EventRecord[];
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

/** A non-`ready` migration status, carrying its `state` to the maintenance page.
    The status endpoint answers 200 with a state, so this is not an `ApiError`. */
class StorageBlocked extends Error {
  readonly state: string;

  constructor(state: string, detail: string) {
    super(detail);
    this.name = "StorageBlocked";
    this.state = state;
  }
}

export function App() {
  const [route, setRoute] = useState<Route>("landing");
  const [landingSessions, setLandingSessions] = useState<SessionInfo[] | null>(null);

  /* `jumpToActiveNode` is defined much further down, and a system
   * notification's click handler fires long after render — so it is reached
   * through a ref rather than hoisting the whole jump machinery up here.
   *
   * `dismissBanner` is reached the same way for a different reason: it needs
   * `markRead`, which comes from the read-keys controller, which needs the feed,
   * which needs the push callback that `useNotices` returns. That is a genuine
   * cycle rather than mere ordering, so one side of it goes through a ref. */
  const jumpToActiveNodeRef = useRef<
    ((entry: Pick<ActiveNodeEntry, "project_id" | "node_id">) => void) | null
  >(null);
  const dismissBannerRef = useRef<
    ((notice: { id: string; readKey: string }) => void) | null
  >(null);
  const {
    notices,
    push: pushNotice,
    dismiss: dismissNotice,
    expire: expireNotice,
    clearAll: clearAllNotices,
    /* Clicking a system notification is at least as deliberate as clicking the
     * in-page banner, so it settles the same way: the banner goes and its read
     * key is marked. Without this the user answered a notification and came
     * back to the banner and unread badge still asking about it. */
  } = useNotices((notice) => {
    dismissBannerRef.current?.(notice);
    jumpToActiveNodeRef.current?.(notice.entry);
  });

  /* Banners are derived here, from *pushed* transitions only. The snapshot
   * path inside `useActiveNodes` never reaches this callback, which is what
   * keeps a reconnect or a tab refresh from redelivering a screenful of
   * banners for work the user already saw. */
  const handleWorkspaceEvent = useCallback(
    (event: WorkspaceEvent) => {
      setLandingSessions((current) => applyWorkspaceEventToSessions(current, event));
      pushNotice(event);
    },
    [pushNotice],
  );
  const activeNodesFeed = useActiveNodes(true, handleWorkspaceEvent);
  /* One owner for read state: the bell's history rows and the banner rail
   * acknowledge the same keys. */
  const readKeysController = useReadKeys(activeNodesFeed);
  const { markRead } = readKeysController;

  /* The two header panels are both full-width, so only one can be open. Held
   * here rather than inside each component, which would let them overlap. */
  const [openPanel, setOpenPanel] = useState<"run" | "notifications" | null>(null);
  const toggleRunPanel = useCallback(() => {
    setOpenPanel((current) => (current === "run" ? null : "run"));
  }, []);
  const toggleNotificationsPanel = useCallback(() => {
    setOpenPanel((current) =>
      current === "notifications" ? null : "notifications",
    );
  }, []);
  /* Close only if still the one asking: a click that opens the other panel
   * already reassigned this state, and clobbering it would close both. */
  const closeRunPanel = useCallback(() => {
    setOpenPanel((current) => (current === "run" ? null : current));
  }, []);
  const closeNotificationsPanel = useCallback(() => {
    setOpenPanel((current) => (current === "notifications" ? null : current));
  }, []);

  const dismissBanner = useCallback(
    (notice: { id: string; readKey: string }) => {
      /* Closing or clicking through a banner is an acknowledgement, so it
       * settles the matching history row too. Expiry does not: a banner that
       * timed out was never necessarily seen. */
      markRead([notice.readKey]);
      dismissNotice(notice.id);
    },
    [dismissNotice, markRead],
  );
  dismissBannerRef.current = dismissBanner;

  /* Clearing the rail is a stronger acknowledgement than closing one banner,
   * so it settles every read key it removes. Leaving them unread would have
   * the bell still claiming N unread for banners the user just swept away. */
  const clearAllBanners = useCallback(() => {
    const cleared = clearAllNotices();
    markRead(cleared.map((notice) => notice.readKey));
  }, [clearAllNotices, markRead]);

  const [landingTags, setLandingTags] = useState<Tag[]>([]);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [modelPresets, setModelPresets] = useState<ModelPreset[]>([]);
  const [globalState, setGlobalState] = useState<GlobalState | null>(null);
  const [storageError, setStorageError] = useState<StorageFailure | null>(null);
  const [storageLoading, setStorageLoading] = useState(true);
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null);
  const [gitCommits, setGitCommits] = useState<CommitDescriptor[]>([]);
  const [gitAction, setGitAction] = useState<"commit" | "review" | "pull" | "push" | null>(null);
  const [gitError, setGitError] = useState<string | null>(null);
  const pendingUiCommitNodeIdsRef = useRef<Map<string, NodePosition | null>>(new Map());
  const [uiCommitPositionTargets, setUiCommitPositionTargets] = useState<CommitPositionTarget[]>([]);

  const [selection, setSelection] = useState<CanvasSelection>({ kind: "none" });
  const selectionRef = useRef<CanvasSelection>(selection);
  selectionRef.current = selection;

  /* The lane the user is currently looking at. Purely a view concern: it
   * decides where the `+` sits, which lane the canvas accents, and where an
   * unanchored create lands, and nothing else — the backend never sees it.
   * Execution reads each node's own `planspace_id`, so focus is free to
   * follow every click without touching what can run.
   *
   * Resolved from storage by the effect below rather than in the initializer,
   * because the lane list arrives with the contextspace, one render later. */
  const [focusedPlanspaceId, setFocusedPlanspaceId] = useState<string | null>(
    null,
  );
  /* Focus is remembered per project, so switching projects must re-resolve
   * rather than carry the previous project's lane over. */
  const focusResolvedForProjectRef = useRef<string | null>(null);

  /* Single writer for the focused lane: keeps in-memory state and the
   * persisted record from drifting, and makes "focus never lands on a hidden
   * lane" one rule in one place rather than a precondition repeated at every
   * call site. A hidden lane draws no nodes, so focusing it would put the `+`
   * and the double-click target somewhere the user cannot see.
   *
   * Ignoring a null id is deliberate: a node with no resolvable lane (one
   * predating lanes entirely) should leave focus where it is, not clear it.
   *
   * Reads `hiddenPlanspaceIdsRef` and `currentSessionIdRef`, both declared
   * below — legal because the body runs after render, and keeping the
   * dependency list empty is what lets every caller depend on this without
   * being re-created whenever lane visibility changes. */
  const focusPlanspace = useCallback(
    (planspaceId: string | null | undefined) => {
      if (!planspaceId) return;
      if (hiddenPlanspaceIdsRef.current.includes(planspaceId)) return;
      setFocusedPlanspaceId((current) =>
        current === planspaceId ? current : planspaceId,
      );
      writeFocusedLane(currentSessionIdRef.current, planspaceId);
    },
    [],
  );
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
    getMigrationStatus()
      .then((migration) => {
        if (migration.state !== "ready") {
          throw new StorageBlocked(migration.state, migration.detail);
        }
        return getGlobalState();
      })
      .then((next) => {
        if (!cancelled) {
          setGlobalState(next);
          setModelPresets(next.model_presets);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setStorageError({
          state: err instanceof StorageBlocked ? err.state
            : err instanceof ApiError ? err.state
            : null,
          detail: apiErrorText(err),
        });
      })
      .finally(() => {
        if (!cancelled) setStorageLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const [pendingGates, setPendingGates] = useState<Record<string, PendingGateState>>({});
  const [pendingReviews, setPendingReviews] = useState<Record<string, PendingGateState>>({});

  const [projectMutationPending, setProjectMutationPending] = useState(false);
  const [revealPending, setRevealPending] = useState(false);
  const [nodePositionTarget, setNodePositionTarget] =
    useState<CanvasNodePositionTarget | null>(null);

  /* True once both initial fetches (nodes + contextspace) have settled for the
   * current session. The canvas is held off-screen until then so hidden-planspace
   * nodes never briefly flash visible during the load-order race. */
  const [initialLoadComplete, setInitialLoadComplete] = useState(false);
  const [nodesHydratedSessionId, setNodesHydratedSessionId] = useState<string | null>(null);
  const contextBundlesByNodeId = useContextBundleSources(
    session && nodesHydratedSessionId === session.id ? session.id : null,
    nodes,
  );

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
    /* Captured at drop time, not read from focus at submit time: the user can
     * move focus while the dialog is open, and the template must still land
     * where they dropped it. */
    planspaceId: string;
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
      /* Focus follows a programmatic jump for the same reason it follows a
       * click: arriving at a node from a notice, a search result or the
       * cross-project bar puts the user in that node's lane, and the create
       * affordances should already be there when they look. */
      const node = nodesRef.current.find((item) => item.id === nodeId);
      if (node) focusPlanspace(resolveNodePlanspaceId(node, nodesRef.current));
    },
    [focusPlanspace, inspectNode],
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
    setNodePositionTarget(null);
    setPendingGates({});
    setPendingReviews({});
    setFocusRequestVersion(0);
    setActivityFocusRequestVersion(0);
    setNewDirectionRequestVersion(0);
    setCenterOnNodeRequest(null);
    setHiddenLaneNotice(null);
    setInitialLoadComplete(false);
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

  /* Opening and closing the template editor is a route change, not just a state
   * flip. The ref is written before setRoute for the same reason openProject
   * does it: async callbacks already in flight read the ref, not the state. */
  const openTemplateEditor = useCallback((slug: string) => {
    setEditingTemplateSlug(slug);
    currentRouteRef.current = "template";
    setRoute("template");
  }, []);

  const closeTemplateEditor = useCallback(() => {
    setEditingTemplateSlug(null);
    /* Back to the project underneath: the editor is stacked on it, never a
     * replacement for it. Landing is the fallback when no session is open. */
    const next: Route = currentSessionIdRef.current ? "project" : "landing";
    currentRouteRef.current = next;
    setRoute(next);
  }, []);

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
  jumpToActiveNodeRef.current = jumpToActiveNode;

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
    /* The editor slug is session-independent library state, so
     * resetAllSessionState leaves it alone — but landing has no editor route to
     * return to, and a stale slug would reopen it on the next project. */
    setEditingTemplateSlug(null);
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
  const { bundle: selectedContextBundle, loading: selectedContextBundleLoading } =
    useNodeContextBundle(session?.id, selectedNode);
  const isNodeNative = useCallback(
    (node: NodeInfo) => nodeBelongsToHost(node, session?.local_machine_id),
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
  const canvasNodesById = useMemo(
    () => new Map(nodes.map((node) => [node.id, node])),
    [nodes],
  );
  const canMutateCanvasNode = useCallback(
    (nodeId: string) => {
      const node = nodesRef.current.find((item) => item.id === nodeId);
      return !readOnly && !!node && isNodeNative(node);
    },
    [isNodeNative, readOnly],
  );
  const canAcceptCanvasDependency = useCallback(
    (sourceNodeId: string, targetNodeId: string) => {
      if (!canMutateCanvasNode(targetNodeId)) return false;
      return canConnectDependency(
        { sourceId: sourceNodeId, targetId: targetNodeId },
        canvasNodesById,
      );
    },
    [canMutateCanvasNode, canvasNodesById],
  );
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
      if (
        !isSessionRoute(currentRouteRef.current) ||
        currentSessionIdRef.current !== sessionId
      ) {
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
    if (session.capabilities?.git_review === false) {
      setGitStatus(null);
      setGitCommits([]);
      setGitError(null);
      return;
    }
    try {
      const state = await getGitState(session.id);
      setGitStatus(state.status);
      setGitCommits(state.commits);
      setGitError(null);
    } catch (err) {
      console.warn("get git state failed:", err);
    }
  }, [session?.id, session?.capabilities?.git_review]);

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

  /* An embedded template session, identified by the ports its one lane
   * declares. Everything below degrades to empty for an ordinary project. */
  const templatePorts = useMemo(
    () => sessionContextSpace?.template_ports ?? [],
    [sessionContextSpace?.template_ports],
  );
  /* The lane those ports belong to. The backend reads them off one lane's
   * manifest and reports that lane by id, so the canvas draws them exactly
   * where they were declared. Deriving it here instead — by counting lanes, or
   * by picking the focused one — would let a port and the node that declares it
   * land in different lanes, and a consumer edge would cross lanes. */
  const templatePortLaneId = useMemo(
    () =>
      templatePorts.length > 0
        ? sessionContextSpace?.template_port_lane_id ?? null
        : null,
    [sessionContextSpace?.template_port_lane_id, templatePorts.length],
  );
  /* The backend marks an embedded editing session with an `embedded:` prefix;
   * a bundled template test run carries a bare template name. A port-less
   * template is still an editing session, so the marker — not the port list —
   * is what decides whether this project shows template affordances. */
  const isEmbeddedTemplateSession =
    session?.template_id?.startsWith(EMBEDDED_SESSION_PREFIX) ?? false;

  /* Argument chips come from scanning the prompt each node actually holds, not
   * from the template's declared argument list: an embedded session keeps its
   * `{{placeholder}}` text unrendered, and a placeholder typed into the session
   * is a new argument the moment it appears. Reuses the editor's scanner so the
   * canvas and the template loader agree on what counts as a placeholder. */
  const templateArgumentsByNodeId = useMemo(() => {
    if (!isEmbeddedTemplateSession) return {};
    const out: Record<string, string[]> = {};
    for (const node of nodes) {
      if (node.kind !== "agent") continue;
      const argumentNames = node.prompt_argument_names ??
        scanPlaceholders(node.prompt_draft || node.prompt || "").argumentNames;
      if (argumentNames.length > 0) out[node.id] = argumentNames;
    }
    return out;
  }, [nodes, isEmbeddedTemplateSession]);

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
    for (const [nodeId, position] of pendingUiCommitNodeIdsRef.current) {
      const node = nodes.find((candidate) => candidate.id === nodeId);
      if (!node || !TERMINAL_STATES.has(node.state)) continue;
      pendingUiCommitNodeIdsRef.current.delete(nodeId);
      if (
        node.state === "done" &&
        node.commit_after &&
        node.commit_after !== node.commit_before
      ) {
        committed = true;
        if (position) {
          setUiCommitPositionTargets((current) => [
            ...current,
            { sha: node.commit_after!, position },
          ]);
        }
      }
    }
    if (committed) void refreshGit();
  }, [nodes, refreshGit]);

  const consumeUiCommitPositionTarget = useCallback((sha: string) => {
    setUiCommitPositionTargets((current) => current.filter((target) => target.sha !== sha));
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
        /* Refresh-then-focus, in this order, for two separate reasons.
         *
         * The contextspace must land first because the focus-resolution
         * effect only accepts lanes present in `knownPlanspaceIds`: focusing
         * a lane the contextspace has not reported yet makes the current
         * focus look unusable, and the effect re-resolves straight back to
         * the old lane — and then never reconsiders, because that lane is
         * perfectly valid.
         *
         * The nodes must land before `selectAndOpenNode`, which derives the
         * lane by looking the node up in `nodesRef`; called on a node the
         * list has not seen, it selects without moving focus.
         *
         * Creating a direction always opens it now: creation no longer
         * depends on whether the project is idle, so there is no "queued,
         * not yet activated" outcome for the user to be notified about. */
        await refreshContextSpace();
        await refreshNodes();
        focusPlanspace(created.planspace_id);
        selectAndOpenNode(created.node_id);
        setFocusRequestVersion((version) => version + 1);
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
      focusPlanspace,
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

  /* Dependency PATCHes replace the whole array. Keep one promise chain per
   * target so rapid edits derive from the preceding response instead of all
   * racing from the same stale snapshot. Different targets remain parallel. */
  const dependencyUpdateQueueRef = useRef(new ScheduledDepsUpdateQueue());
  const enqueueDependencyUpdate = useCallback(
    (
      targetNodeId: string,
      rewrite: (current: string[]) => string[] | null,
    ) => {
      void dependencyUpdateQueueRef.current.enqueue(targetNodeId, rewrite, {
        getTarget: () =>
          nodesRef.current.find((node) => node.id === targetNodeId),
        canMutate: (target) =>
          !readOnly &&
          isNodeNative(target) &&
          target.state === "virtual" &&
          !target.obsolete_reason &&
          scheduledDepsAvailable(nodeClassification(target)),
        write: (scheduledDeps) =>
          updateVirtualNode(targetNodeId, { scheduled_deps: scheduledDeps }),
      });
    },
    [isNodeNative, readOnly, updateVirtualNode],
  );

  /* Canvas wiring path: the user drew an edge from `sourceNodeId` to
   * `targetNodeId`, meaning the target depends on the source. Only the target's
   * scheduled_deps is sent, so a prompt the user is editing in the inspector
   * survives — the panel reconciles this write field by field.
   *
   * The canvas has already applied the same rules the backend enforces; these
   * checks are the last read of live state before the write. */
  const handleConnectDependency = useCallback(
    (targetNodeId: string, sourceNodeId: string) => {
      enqueueDependencyUpdate(targetNodeId, (current) => {
        if (current.includes(sourceNodeId)) return null;
        return [...current, sourceNodeId];
      });
    },
    [enqueueDependencyUpdate],
  );

  /* Withdrawing one dependency. `resume_from_node_id` is deliberately untouched:
   * at runtime the two are independent relations drawn as separate edges, and a
   * resume edge that outlives its dependency edge is correct. */
  const handleDisconnectDependency = useCallback(
    (targetNodeId: string, sourceNodeId: string) => {
      enqueueDependencyUpdate(targetNodeId, (current) => {
        if (!current.includes(sourceNodeId)) return null;
        return current.filter((id) => id !== sourceNodeId);
      });
    },
    [enqueueDependencyUpdate],
  );

  /* Drag-onto-virtual attach path: Canvas hands us (virtualNodeId, principleId)
   * when a principle chip is dropped on a virtual tile. We read the target's
   * current pending_extra_principles, append, and PATCH via updateVirtualNode.
   * Fire-and-forget: errors surface through sessionContextSpaceError. */
  const handleAttachPrincipleToVirtual = useCallback(
    (virtualNodeId: string, principleId: string) => {
      const target = nodesRef.current.find((n) => n.id === virtualNodeId);
      if (
        !target ||
        target.state !== "virtual" ||
        target.obsolete_reason ||
        !extraPrinciplesAvailable(nodeClassification(target))
      ) {
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
    return {
      nodeId,
      label,
      acceptsPrinciples: extraPrinciplesAvailable(nodeClassification(node)),
    };
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
          qa_mode: false,
          artifact_mode: "default",
          artifact_spec: "",
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
      const planspaceId = parent?.planspace_id ?? focusedPlanspaceId;
      if (!parent || !planspaceId) return;
      void createVirtualNode({
        planspace_id: planspaceId,
        scheduled_deps: [parent.id],
        model_preset_id: parent.model_preset_id ?? session?.model_preset_id ?? null,
      });
    },
    [createVirtualNode, focusedPlanspaceId, nodes, session?.model_preset_id],
  );

  /* Canvas wiring path, empty-canvas release: a new draft virtual that waits for
   * `sourceNodeId`, placed where the wire was let go. Same write as the "↘"
   * button's plain click, with an explicit position instead of the lane's
   * default append slot. */
  const createDependencyVirtualAt = useCallback(
    (sourceNodeId: string, position: { x: number; y: number }) => {
      const parent = nodesRef.current.find((node) => node.id === sourceNodeId);
      const planspaceId = parent?.planspace_id ?? focusedPlanspaceId;
      if (!parent || !planspaceId) return;
      void createVirtualNode({
        planspace_id: planspaceId,
        scheduled_deps: [parent.id],
        model_preset_id: parent.model_preset_id ?? session?.model_preset_id ?? null,
        position,
      });
    },
    [
      createVirtualNode,
      focusedPlanspaceId,
      session?.model_preset_id,
    ],
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
      const planspaceId = sinks[0].planspace_id ?? focusedPlanspaceId;
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
      focusedPlanspaceId,
      nodes,
      session?.model_preset_id,
    ],
  );

  const createContinuationVirtual = useCallback(
    (parentNodeId: string) => {
      const parent = nodes.find((node) => node.id === parentNodeId);
      const planspaceId = parent?.planspace_id ?? focusedPlanspaceId;
      if (!parent || !planspaceId || !canResumeNode(parent)) return;
      void createVirtualNode({
        planspace_id: planspaceId,
        model_preset_id: parent.model_preset_id ?? null,
        resume_from_node_id: parent.id,
      });
    },
    [createVirtualNode, focusedPlanspaceId, nodes],
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
    if (
      !session?.id || !inspectedNodeId || selectedNode?.state === "virtual" ||
      (session.capabilities?.git_review === false && selectedNode?.subtype !== "code_review")
    ) {
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
    session?.capabilities?.git_review,
    inspectedNodeId,
    selectedNode?.commit_before,
    selectedNode?.commit_after,
    selectedNode?.subtype,
    selectedNode?.state,
  ]);

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
    isSessionRoute(route) ? (session?.id ?? null) : null,
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

  /* Dequeue and Promote are both decided by the node's own lane mode
   * (matching the backend), not by which lane is active, so track auto lanes
   * as a set. Lanes without a planspace plug default to manual, like the
   * backend. */
  const autoPlanspaceIds = useMemo(() => {
    const out = new Set<string>();
    for (const binding of sessionContextSpace?.bindings ?? []) {
      for (const plug of binding.plugs) {
        if (plug.kind === "planspace" && plug.mode === "auto") out.add(plug.id);
      }
    }
    return out;
  }, [sessionContextSpace]);

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

  /* Resolve the focused lane once per project, and re-resolve whenever the
   * current focus stops being a valid target — the lane was deleted, or the
   * user hid it. Both cases would otherwise leave focus pointing at a lane
   * that draws nothing, with no `+` anywhere on the canvas.
   *
   * This does not run on every lane-list change, only when focus is actually
   * unusable. Re-resolving eagerly would fight the user: `resolveFocusedLane`
   * prefers the stored lane, so a fresh resolution after every contextspace
   * refresh would keep yanking focus back from wherever they just clicked. */
  useEffect(() => {
    const projectId = session?.id ?? null;
    if (!projectId) {
      focusResolvedForProjectRef.current = null;
      setFocusedPlanspaceId(null);
      return;
    }
    const hidden = new Set(hiddenPlanspaceIds);
    const visible = knownPlanspaceIds.filter((id) => id && !hidden.has(id));
    const sameProject = focusResolvedForProjectRef.current === projectId;
    const stillUsable =
      focusedPlanspaceId !== null && visible.includes(focusedPlanspaceId);
    if (sameProject && stillUsable) return;
    /* Nothing to focus yet: an empty contextspace during the initial load
     * would otherwise burn this project's one resolution on a null answer,
     * and the real lanes arriving a moment later would never be considered. */
    if (visible.length === 0) return;
    /* Nodes and the contextspace are fetched in parallel, so the lane list can
     * arrive first. Resolving now would rank recency against an empty node
     * list, fall through to "first visible lane", and persist that guess —
     * and the guard above then prevents the correct answer from ever being
     * computed, because the wrong lane is perfectly usable. Waiting for
     * hydration costs one render and makes the recency step real.
     *
     * `initialLoadComplete` is the escape hatch: if `listNodes` failed
     * outright, hydration never happens, and holding out for it forever would
     * leave the canvas with no focused lane and therefore no `+` at all.
     * Once the initial fetches have all settled, resolve with whatever we
     * have rather than not at all.
     *
     * Only the initial resolution waits. A later re-resolution (the focused
     * lane was deleted or hidden) already has nodes, and gating it on this
     * would be a no-op. */
    if (nodesHydratedSessionId !== projectId && !initialLoadComplete) return;

    const resolved = resolveFocusedLane({
      stored: readFocusedLane(projectId),
      visible,
      recentlyActive: lanesByRecentActivity(
        nodesRef.current,
        visible,
        /* Same attribution the canvas draws with, so a lane whose only nodes
         * predate `planspace_id` still counts as used. */
        nodeLaneResolver(nodesRef.current),
      ),
    });
    focusResolvedForProjectRef.current = projectId;
    setFocusedPlanspaceId(resolved);
    /* Persist the resolution so the next open of this project is a storage
     * hit rather than a re-derivation — which matters once Phase 3 removes
     * the `active` step and recency becomes the only inference left. */
    if (resolved) writeFocusedLane(projectId, resolved);
  }, [
    focusedPlanspaceId,
    hiddenPlanspaceIds,
    initialLoadComplete,
    knownPlanspaceIds,
    nodesHydratedSessionId,
    session?.id,
  ]);

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
    },
    [activePendingReview, status, send],
  );

  /* Wire the planspace lane header click → side-panel selection. */
  useEffect(() => {
    setPlanspaceLaneContext({
      onSelectPlanspace: (planspaceId) => {
        setSelection({ kind: "planspace", planspaceId });
        inspectNode(null);
        /* Clicking a lane header is the most explicit focus request there
         * is — the user named the lane itself, not a node inside it. */
        focusPlanspace(planspaceId);
      },
      onTogglePlanspaceVisibility: togglePlanspaceVisibility,
      onCreateVirtual: createUnparentedVirtual,
    });
  }, [
    createUnparentedVirtual,
    focusPlanspace,
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
    },
    [send, validPendingGates],
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
    /* Focus follows the click: selecting a node in lane B means the user is
     * working in B, so B is where the `+`, the double-click create and the
     * vertical jumps belong.
     *
     * A template instance carries no lane of its own — it is a frame drawn
     * around member nodes — so its lane comes from one of those members. A
     * stamped instance never straddles lanes, so the first resolvable member
     * answers for all of them; sinks are consulted first because a collapsed
     * box is selected by its outputs.
     *
     * Empty canvas (`kind: "none"`) deliberately does NOT clear focus. A
     * click on the background is how the user dismisses the panel, and
     * dropping focus there would leave the canvas with no create target
     * until they clicked a node again. A node with no resolvable lane leaves
     * focus alone too — `focusPlanspace` ignores a null id. */
    if (sel.kind === "agent" || sel.kind === "op" || sel.kind === "artifact") {
      const node = nodesRef.current.find((item) => item.id === sel.nodeId);
      if (node) {
        focusPlanspace(resolveNodePlanspaceId(node, nodesRef.current));
      }
    } else if (sel.kind === "templateInstance") {
      const laneOf = nodeLaneResolver(nodesRef.current);
      for (const nodeId of [...sel.sinkNodeIds, ...sel.memberNodeIds]) {
        const member = nodesRef.current.find((item) => item.id === nodeId);
        const laneId = member ? laneOf(member) : null;
        if (laneId) {
          focusPlanspace(laneId);
          break;
        }
      }
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
  }, [focusPlanspace, inspectNode]);

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
      /* Prefer the lane of whatever was dropped on: dropping onto a node in
       * lane B means lane B, even while focus sits on A. Only an unanchored
       * drop onto empty canvas falls back to the focused lane.
       *
       * Resolved through `resolveNodePlanspaceId` (not a bare
       * `node.planspace_id` read) so the answer matches the lane the canvas
       * drew the anchor in — legacy nodes get their lane from a settings
       * snapshot or a parent, and disagreeing here would stamp the template
       * into a lane the user cannot see the anchor in. */
      const anchorNode = resolvedAnchorNodeId
        ? nodesRef.current.find((item) => item.id === resolvedAnchorNodeId)
        : undefined;
      const anchorLane = anchorNode
        ? resolveNodePlanspaceId(anchorNode, nodesRef.current)
        : null;
      const targetPlanspaceId = anchorLane ?? focusedPlanspaceId;
      if (!targetPlanspaceId) {
        window.alert("请先选择一个方向，再应用模板。");
        return;
      }
      try {
        const templates = await listUserTemplates();
        const template = templates.find((item) => item.slug === slug);
        if (!template) throw new Error(`template not found: ${slug}`);
        if (templateNeedsInstantiateDialog(template)) {
          setInstantiateTarget({
            template,
            anchorNodeId: resolvedAnchorNodeId,
            planspaceId: targetPlanspaceId,
          });
          return;
        }
        const applied = await applyUserTemplate(session.id, slug, {
          planspace_id: targetPlanspaceId,
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
    [
      focusedPlanspaceId,
      readOnly,
      refreshNodes,
      session?.id,
      toggleTemplateInstanceCollapsed,
    ],
  );

  /* select a specific node id (used by panel "jump to" affordances and the
   * pending-node banner). Unlike a bare canvas click, these are explicit
   * user asks to *inspect* the node, so we also open the panel.
   *
   * Focus follows for the same reason it follows `selectAndOpenNode`: jumping
   * to a commit epoch member or a pending node in another lane puts the user
   * in that lane, and leaving focus behind would send their next unanchored
   * create back to the lane they just left. */
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
      focusPlanspace(resolveNodePlanspaceId(node, nodes));
    },
    [focusPlanspace, inspectNode, nodes, openDetails],
  );

  const onSelectArtifact = useCallback(
    (nodeId: string, name: string, ext: ArtifactExtension) => {
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
      canMutateNode: canMutateCanvasNode,
      canAcceptDependency: canAcceptCanvasDependency,
      canPromoteVirtual: !projectMutationPending && !readOnly,
      canDequeue: !projectMutationPending && !readOnly,
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
    isManualPlanspace,
    readOnly,
    canInterruptRunner,
    composerLocked,
    modelPresets,
    nodes,
    isNodeNative,
    canMutateCanvasNode,
    canAcceptCanvasDependency,
  ]);

  const canvasPositions = useMemo(() => ({
    ...session?.node_positions,
    ...session?.git_positions,
    ...session?.lane_positions,
    ...session?.context_positions,
  }), [session?.node_positions, session?.git_positions, session?.lane_positions, session?.context_positions]);

  const onNodePositionsChange = useCallback(
    (
      updates: Record<string, NodePosition>,
      remove: string[] = [],
    ) => {
      if (!session?.id || readOnly) return;
      if (Object.keys(updates).length === 0 && remove.length === 0) return;
      const sessionId = session.id;
      const updatesSnapshot = Object.fromEntries(
        Object.entries(updates).map(([id, pos]) => [id, { ...pos }]),
      );
      const removeSnapshot = [...remove];
      const save = layoutSaveChainRef.current
        .catch(() => undefined)
        .then(async () => {
          const writers = [
            { matches: (id: string) => !id.startsWith("commit:") && !id.startsWith("planspace:") && !id.startsWith("ctx:"), write: updateNodeLayout },
            { matches: (id: string) => id.startsWith("commit:"), write: updateGitLayout },
            { matches: (id: string) => id.startsWith("planspace:"), write: updateLaneLayout },
            { matches: (id: string) => id.startsWith("ctx:"), write: updateContextLayout },
          ];
          let next: SessionInfo | null = null;
          for (const { matches, write } of writers) {
            const positions = Object.fromEntries(Object.entries(updatesSnapshot).filter(([id]) => matches(id)));
            const removals = removeSnapshot.filter(matches);
            if (Object.keys(positions).length || removals.length) {
              next = await write(sessionId, positions, removals);
            }
          }
          if (!next) throw new Error("没有可保存的位置");
          return next;
        })
        .then((next) => {
          if (lastLayoutSaveRef.current === save) {
            setSession((current) =>
              current && current.id === next.id ? { ...current, ...next } : current,
            );
          }
          return next;
        });
      lastLayoutSaveRef.current = save;
      layoutSaveChainRef.current = save.then(
        () => undefined,
        () => undefined,
      );
      save.catch((err) => {
        console.warn("保存节点位置失败：", err);
        window.alert(`节点位置未保存，请重试拖动：${apiErrorText(err)}`);
      }).finally(() => {
        if (lastLayoutSaveRef.current === save) {
          lastLayoutSaveRef.current = null;
        }
      });
    },
    [readOnly, session?.id],
  );

  /* The template editor is its own route rather than an overlay on `project`.
   * Placed as an early return ahead of both other branches, and deliberately
   * before `landing`: the project session it was opened from stays loaded (the
   * socket and node refreshes are gated on `isSessionRoute`), so returning goes
   * straight back to the graph. A missing slug degrades to the normal routes
   * rather than rendering an empty shell. */
  if (route === "template" && editingTemplateSlug) {
    return (
      <TemplateEditor
        slug={editingTemplateSlug}
        onClose={closeTemplateEditor}
        onSaved={() => setLibraryRefreshToken((v) => v + 1)}
      />
    );
  }

  if (storageLoading || storageError) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-surface p-8 text-ink">
        <section className="max-w-2xl space-y-4" role="status">
          <h1 className="text-xl font-semibold">
            {storageError ? storageGuidance(storageError.state).title : "正在检查存储格式…"}
          </h1>
          {storageError && <StorageMaintenance error={storageError} />}
        </section>
      </main>
    );
  }

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
          headerStatus={(
            <>
              <RunStatusButton
                enabled
                entries={activeNodesFeed.entries}
                currentSessionId={null}
                open={openPanel === "run"}
                onToggle={toggleRunPanel}
                onClose={closeRunPanel}
                onJump={jumpToActiveNode}
              />
              <NotificationBell
                enabled
                feed={activeNodesFeed}
                currentSessionId={null}
                readKeysController={readKeysController}
                open={openPanel === "notifications"}
                onToggle={toggleNotificationsPanel}
                onClose={closeNotificationsPanel}
                onJump={jumpToActiveNode}
              />
            </>
          )}
          noticeRail={(
            <NoticeBannerRail
              notices={notices}
              onJump={jumpToActiveNode}
              onDismiss={dismissBanner}
              onExpire={(notice) => expireNotice(notice.id)}
              onClearAll={clearAllBanners}
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
        const position = captureGitChangesPosition();
        const result = await gitCommit(session.id, message);
        pendingUiCommitNodeIdsRef.current.set(result.node.id, position);
      } else if (action === "review") {
        /* The review is filed in the lane the user is looking at. With no
         * lane focused it stays unlaned rather than landing in a direction
         * the user never chose. */
        const result = await gitReview(session.id, focusedPlanspaceId);
        /* The review node arrives with no position of its own and would
         * otherwise take the lane's default top-row slot. Place it under the
         * lane's current work, the same way the lane "+" button does. Passing
         * the node id makes this a no-op when the tile is already on the
         * canvas — `spawn_code_review` returns an in-flight review rather
         * than creating one, and a WebSocket refresh can render a genuinely
         * new node before this point. Either way a tile the user can already
         * see must not jump. */
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
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-ink-muted transition hover:border-line-strong hover:bg-surface-sunken hover:text-ink"
            title="返回项目列表"
            aria-label="返回项目列表"
          >
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M3.5 10.8 12 4l8.5 6.8" />
              <path d="M5.8 9.5v9.9a1.5 1.5 0 0 0 1.5 1.5h9.4a1.5 1.5 0 0 0 1.5-1.5V9.5" />
              <path d="M9.9 20.9v-4.8a1.3 1.3 0 0 1 1.3-1.3h1.6a1.3 1.3 0 0 1 1.3 1.3v4.8" />
            </svg>
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
              {session?.capabilities?.git_review !== false && <GitWorkspaceStatus
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
              />}
              {gitError && <span className="max-w-[18rem] truncate text-state-error" title={gitError}>{gitError}</span>}
              {session?.read_only && (
                <span className="rounded border border-state-waiting/40 bg-state-waiting-soft px-1.5 py-0.5 font-sans text-state-waiting">
                  {session.bound_here
                    ? "只读 · 当前项目存储在本机不可写"
                    : "只读 · 此设备尚未配置项目路径"}
                </span>
              )}
              {session?.capabilities?.workspace !== false && session?.can_bind_here && (
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

          <RunStatusButton
            enabled
            entries={activeNodesFeed.entries}
            currentSessionId={session?.id ?? null}
            open={openPanel === "run"}
            onToggle={toggleRunPanel}
            onClose={closeRunPanel}
            onJump={jumpToActiveNode}
          />

          <NotificationBell
            enabled
            feed={activeNodesFeed}
            currentSessionId={session?.id ?? null}
            readKeysController={readKeysController}
            open={openPanel === "notifications"}
            onToggle={toggleNotificationsPanel}
            onClose={closeNotificationsPanel}
            onJump={jumpToActiveNode}
          />

          {session?.capabilities?.workspace !== false && <button
            type="button"
            onClick={() => {
              if (!session?.id) return;
              setRevealPending(true);
              void revealProjectRoot(session.id)
                .catch((error: unknown) => window.alert(apiErrorText(error)))
                .finally(() => setRevealPending(false));
            }}
            disabled={!session?.bound_here || revealPending}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-ink-muted transition hover:border-line-strong hover:bg-surface-sunken hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
            title={
              session?.bound_here
                ? `在文件管理器中打开 ${session.root_path || "项目目录"}`
                : "此设备尚未配置项目路径"
            }
            aria-label="在文件管理器中打开项目目录"
          >
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="m6 14 1.45-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.55 6a2 2 0 0 1-1.94 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2" />
            </svg>
          </button>}

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
            title="项目详情与新方向"
            aria-label="打开项目详情面板与新方向输入框"
          >
            {/* An info mark, not a house: this button opens the project detail
              * panel, and a home glyph promised navigation it never performed. */}
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 11v5" />
              <path d="M12 7.75h.01" />
            </svg>
          </button>

          <button
            type="button"
            onClick={toggleLibrary}
            aria-pressed={panelState.open && panelState.mode === "library"}
            className={
              "inline-flex h-8 w-8 items-center justify-center rounded-md border transition " +
              (panelState.open && panelState.mode === "library"
                ? "border-line-strong bg-surface-sunken text-ink"
                : "border-line bg-surface text-ink-muted hover:border-line-strong hover:bg-surface-sunken hover:text-ink")
            }
            title="Library"
            aria-label="展开 / 收起 library 面板"
          >
            {/* An open book. The shelf of three upright volumes carried four
              * closed shapes and roughly 1.7x the ink of its neighbours, which
              * read as a solid block at 17px; two curved leaves and a spine
              * match the bell and folder beside it. */}
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 7.4C10.3 6.1 7.7 5.4 4.2 5.5V17.9C7.7 17.8 10.3 18.5 12 19.8" />
              <path d="M12 7.4C13.7 6.1 16.3 5.4 19.8 5.5V17.9C16.3 17.8 13.7 18.5 12 19.8" />
              <path d="M12 7.4V19.8" />
            </svg>
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
              hiddenPlanspaceIds={hiddenPlanspaceIds}
              focusedPlanspaceId={focusedPlanspaceId}
              templatePortLaneId={templatePortLaneId}
              autoPlanspaceIds={Array.from(autoPlanspaceIds)}
              canCreateVirtual={!virtualCreateDisabled}
              templateInstances={templateInstances}
              collapsedTemplateInstanceIds={collapsedTemplateInstanceIds}
              templatePorts={templatePorts}
              templateArgumentsByNodeId={templateArgumentsByNodeId}
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
              initialNodePositions={canvasPositions}
              onSelectionChange={onSelectionChange}
              onMultiSelectionChange={onMultiSelectionChange}
              onAgentNodeContextMenu={onAgentNodeContextMenu}
              onTemplateDrop={onTemplateDrop}
              onAttachPrincipleToVirtual={handleAttachPrincipleToVirtual}
              onAttachSkillToVirtual={handleAttachSkillToVirtual}
              canMutateNode={canMutateCanvasNode}
              canMutateGitLayout={!readOnly && session?.capabilities?.git_review !== false}
              canMutateLaneLayout={!readOnly}
              canMutateContextLayout={!readOnly}
              onConnectDependency={handleConnectDependency}
              onCreateDependencyVirtualAt={createDependencyVirtualAt}
              onDisconnectDependency={handleDisconnectDependency}
              onNodePositionsChange={onNodePositionsChange}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-[11px] uppercase tracking-[0.18em] text-ink-subtle">
              Loading canvas…
            </div>
          )}

          {/* Sibling of the canvas, not a child: wheel events over a banner
              never reach React Flow's zoom handler, which is what lets the
              rail scroll internally. Top-left, clear of the bottom-right
              notices below. */}
          <NoticeBannerRail
            notices={notices}
            onJump={jumpToActiveNode}
            onDismiss={dismissBanner}
            onExpire={(notice) => expireNotice(notice.id)}
            onClearAll={clearAllBanners}
          />

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
                onEditTemplate={openTemplateEditor}
                onApplyTemplate={
                  readOnly ? undefined : (slug) => void onTemplateDrop(slug, null)
                }
                onAttachToVirtual={
                  libraryAttachTarget ? handleAttachLibraryEntryToSelection : undefined
                }
                attachTargetLabel={libraryAttachTarget?.label ?? null}
                principleAttachmentEnabled={
                  libraryAttachTarget?.acceptsPrinciples ?? true
                }
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
                onSessionChange={setSession}
                onPreferredLanguageChange={updatePreferredLanguage}
                onConcurrencyChange={updateConcurrency}
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
        openTemplateEditor(slug);
      }}
    />
    <InstantiateTemplateModal
      open={instantiateTarget !== null}
      sessionId={session?.id ?? null}
      template={instantiateTarget?.template ?? null}
      nodes={nodes}
      planspaceId={instantiateTarget?.planspaceId ?? null}
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

function upsertNode(prev: NodeInfo[], incoming: NodeInfo): NodeInfo[] {
  const node = toNodeInfo(incoming);
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
