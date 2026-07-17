import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelProjectContext,
  createBlankPlanspace,
  createPlanspace,
  createVirtual,
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
  artifactRawUrl,
  type PrincipleSummary,
  type SkillSummary,
  type UpdateVirtualPayload,
} from "./api";
import { Canvas, type CanvasSelection } from "./canvas/Canvas";
import { artifactNodeId } from "./canvas/layout";
import { setAgentNodeContext } from "./canvas/nodes/AgentNode";
import { setPlanspaceLaneContext } from "./canvas/nodes/PlanspaceLaneNode";
import { SidePanel } from "./panel/SidePanel";
import { NewProjectModal } from "./components/NewProjectModal";
import { SaveAsTemplateModal } from "./components/SaveAsTemplateModal";
import { TemplateLibraryDock } from "./components/TemplateLibraryDock";
import { ContextMenu, type ContextMenuItem } from "./canvas/ContextMenu";
import { applyUserTemplate } from "./api";
import { ProjectsLanding } from "./components/ProjectsLanding";
import { ThemeToggle } from "./components/ThemeToggle";
import { UsageStrip } from "./components/UsageStrip";
import { GitWorkspaceStatus } from "./components/GitWorkspaceStatus";
import type {
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
} from "./types";
import { useSessionSocket } from "./ws";
import { canResumeNode } from "./nodeUtil";
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

const TERMINAL_STATES = new Set<NodeInfo["state"]>(["done", "error", "cancelled"]);
const INTERRUPTIBLE_STATES = new Set<NodeInfo["state"]>([
  "running",
  "waiting",
  "awaiting_human_input",
]);
export function App() {
  const [route, setRoute] = useState<Route>("landing");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [modelPresets, setModelPresets] = useState<ModelPreset[]>([]);
  const [globalState, setGlobalState] = useState<GlobalState | null>(null);
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null);
  const [gitCommits, setGitCommits] = useState<CommitDescriptor[]>([]);
  const [gitAction, setGitAction] = useState<"commit" | "review" | "pull" | "push" | null>(null);
  const [gitError, setGitError] = useState<string | null>(null);

  const [selection, setSelection] = useState<CanvasSelection>({ kind: "none" });
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

  /* User-wide principle shelf. Fetched on session mount and refreshed after
   * a principle-edit turn completes. See canvas layout.ts for the dimmed-tile
   * merge into the context aggregate. */
  const [principles, setPrinciples] = useState<PrincipleSummary[]>([]);
  const refreshPrinciples = useCallback(() => {
    listPrinciples()
      .then(setPrinciples)
      .catch(() => {
        /* non-fatal — the shelf just stays stale until the next refresh */
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
    listSkills().then(setSkills).catch(() => {});
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

  /* True once both initial fetches (nodes + contextspace) have settled for the
   * current session. The canvas is held off-screen until then so hidden-planspace
   * nodes never briefly flash visible during the load-order race. */
  const [initialLoadComplete, setInitialLoadComplete] = useState(false);

  const [focusRequestVersion, setFocusRequestVersion] = useState(0);
  const [newDirectionRequestVersion, setNewDirectionRequestVersion] = useState(0);

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
  const [libraryRefreshToken, setLibraryRefreshToken] = useState(0);

  /* Single floating side panel: `panelOpen` controls the slide-in animation
   * and `panelMode` decides whether details or the template library renders.
   * Node clicks set mode='details' (but never force-open per UX spec);
   * the templates top-bar button toggles open+templates; empty-canvas click
   * or the panel's close button closes. */
  const [panelState, setPanelState] = useState<{
    open: boolean;
    mode: "details" | "templates";
  }>(() => readPanelState());
  useEffect(() => {
    try {
      window.localStorage.setItem("miniclaw.panelState", JSON.stringify(panelState));
    } catch {
      /* localStorage unavailable — state stays session-scoped */
    }
  }, [panelState]);
  const closePanel = useCallback(() => {
    setPanelState((prev) => (prev.open ? { ...prev, open: false } : prev));
  }, []);
  const openDetails = useCallback(() => {
    setPanelState({ open: true, mode: "details" });
  }, []);
  const toggleTemplates = useCallback(() => {
    setPanelState((prev) =>
      prev.open && prev.mode === "templates"
        ? { ...prev, open: false }
        : { open: true, mode: "templates" },
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
  /* Programmatic-selection helper. Whenever code (not a user canvas click)
   * changes what's inspected, we must also open the details panel — otherwise
   * the freshly-inspected node's controls (gate/review form, virtual draft
   * editor) sit inside the closed floating panel and are invisible to the
   * user. */
  const selectAndOpenNode = useCallback(
    (nodeId: string, kind: "agent" | "op" = "agent") => {
      setSelection({ kind, nodeId });
      inspectNode(nodeId);
      setPanelState({ open: true, mode: "details" });
    },
    [inspectNode],
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
    setProjectMutationPending(false);
    setPendingGates({});
    setPendingReviews({});
    setFocusRequestVersion(0);
    setNewDirectionRequestVersion(0);
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

  const backToLanding = useCallback(() => {
    openProjectRequestRef.current += 1;
    currentRouteRef.current = "landing";
    currentSessionIdRef.current = null;
    resetAllSessionState();
    setSession(null);
    setRoute("landing");
  }, [resetAllSessionState]);

  /* selected node lookup */
  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === inspectedNodeId) ?? null,
    [nodes, inspectedNodeId],
  );
  const sessionWithRuntimeCounts = useMemo(
    () =>
      session
        ? {
            ...session,
            active_count: nodes.filter((node) => INTERRUPTIBLE_STATES.has(node.state)).length,
            queued_count: nodes.filter((node) => node.state === "queued").length,
          }
        : null,
    [nodes, session],
  );
  const selectedCanvasNodeId = useMemo(() => graphNodeIdForSelection(selection), [selection]);
  const activeNodesFromList = useMemo(
    () => nodes.filter((n) => INTERRUPTIBLE_STATES.has(n.state)),
    [nodes],
  );
  const hasInterruptibleNode = useMemo(
    () => nodes.some((n) => INTERRUPTIBLE_STATES.has(n.state)),
    [nodes],
  );
  const projectRunnerActive = activeNodesFromList.length > 0;
  const projectRunnerBusy = projectMutationPending || projectRunnerActive;
  const readOnly = session?.read_only ?? false;
  const activeCanvasNodeIds = useMemo(
    () => activeNodesFromList.map((node) => node.id),
    [activeNodesFromList],
  );

  const validPendingGates = useMemo(
    () => keepPendingForStates(pendingGates, nodes, ["waiting"]),
    [nodes, pendingGates],
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

  const handleNewLibraryEntry = useCallback(
    async (userSeed: string) => {
      if (!session?.id || virtualCreateDisabled) return;
      const active = sessionContextSpace?.active_planspace_id ?? null;
      setProjectMutationPending(true);
      setSessionContextSpaceError(null);
      try {
        const result = await createVirtual(session.id, {
          prompt_draft: userSeed,
          category: "regular",
          agent_op_kind: "library_edit",
          model_preset_id: defaultModelPresetId(modelPresets, session.model_preset_id),
          planspace_id: active,
        });
        setNodes((prev) => {
          const updated = upsertNode(prev, result.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        selectAndOpenNode(result.node.id);
        setFocusRequestVersion((v) => v + 1);
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
      sessionContextSpace?.active_planspace_id,
      virtualCreateDisabled,
      selectAndOpenNode,
    ],
  );

  /* refresh node list.
   *
   * refreshNodes is called both on initial load and as a hedge from
   * handleEvent (turn_done / node_started / interaction_request). The
   * hedge races with the WebSocket: between the listNodes API request
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
          /* Prefer the backend version when it exists (state drift correction);
           * otherwise only keep local nodes added after this refresh began. */
          const refreshed = nextById.get(c.id);
          if (refreshed) {
            merged.push(refreshed);
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
      setPendingGates((current) => removePendingNodes(current, wasRemovedByRefresh));
      setPendingReviews((current) => removePendingNodes(current, wasRemovedByRefresh));
      setSelection((current) =>
        (current.kind === "agent" || current.kind === "op") &&
        wasRemovedByRefresh(current.nodeId)
          ? { kind: "none" }
          : current,
      );
      const currentInspectedNodeId = inspectedNodeIdRef.current;
      inspectNode(
        wasRemovedByRefresh(currentInspectedNodeId)
          ? null
          : currentInspectedNodeId ?? next.at(-1)?.id ?? null,
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

  /* context space */
  const refreshContextSpace = useCallback(async () => {
    if (!session?.id) return;
    setSessionContextSpaceLoading(true);
    setSessionContextSpaceError(null);
    try {
      const next = await getSessionContextSpace(session.id);
      setSessionContextSpace(next);
      setSession((current) =>
        current && current.id === session.id
          ? { ...current, project_context_binding_id: next.project_context_binding_id ?? null }
          : current,
      );
    } catch (err) {
      setSessionContextSpaceError(String(err));
      setSessionContextSpace(null);
    } finally {
      setSessionContextSpaceLoading(false);
    }
  }, [session?.id]);

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

  useEffect(() => {
    if (!sessionContextSpace?.context_refresh?.running) return;
    const timer = window.setInterval(() => {
      void refreshContextSpace();
    }, 1200);
    return () => window.clearInterval(timer);
  }, [sessionContextSpace?.context_refresh?.running, refreshContextSpace]);

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
  const prevTerminalLibraryEditCountRef = useRef(0);
  useEffect(() => {
    if (terminalLibraryEditCount > prevTerminalLibraryEditCountRef.current) {
      refreshPrinciples();
      refreshSkills();
    }
    prevTerminalLibraryEditCountRef.current = terminalLibraryEditCount;
  }, [terminalLibraryEditCount, refreshPrinciples, refreshSkills]);

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
      if (!session?.id || sessionSettingsSaving || projectRunnerBusy) return;
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      setProjectMutationPending(true);
      try {
        const created = await createPlanspace(session.id, {
          seed: userSeed,
          mode,
          model_preset_id: modelPresetId,
        });
        selectAndOpenNode(created.node_id);
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
      sessionSettingsSaving,
      projectRunnerBusy,
      refreshContextSpace,
      refreshNodes,
      selectAndOpenNode,
    ],
  );

  const startBlankDirection = useCallback(
    async (userSeed: string, mode: PlanspaceMode, modelPresetId: string) => {
      if (!session?.id || sessionSettingsSaving || projectRunnerBusy) return;
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      try {
        const created = await createBlankPlanspace(session.id, {
          seed: userSeed,
          mode,
          model_preset_id: modelPresetId,
        });
        selectAndOpenNode(created.node_id);
        setFocusRequestVersion((version) => version + 1);
        await refreshContextSpace();
        await refreshNodes();
      } catch (err) {
        setSessionContextSpaceError(String(err));
      } finally {
        setSessionContextSpaceSaving(false);
      }
    },
    [
      session?.id,
      sessionSettingsSaving,
      projectRunnerBusy,
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
      } catch (err) {
        setSessionContextSpaceError(String(err));
      } finally {
        setSessionContextSpaceSaving(false);
      }
    },
    [session?.id],
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
        setSessionContextSpaceError(String(err));
      } finally {
        setProjectMutationPending(false);
      }
    },
    [session?.id, projectMutationPending, refreshNodes, selectAndOpenNode],
  );

  const updateVirtualNode = useCallback(
    async (nodeId: string, payload: UpdateVirtualPayload) => {
      if (!session?.id) return;
      setSessionContextSpaceError(null);
      try {
        const result = await updateVirtual(session.id, nodeId, payload);
        setNodes((prev) => {
          const updated = upsertNode(prev, result.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
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

  const createVirtualNode = useCallback(
    async (payload: {
      planspace_id: string;
      scheduled_deps?: string[];
      model_preset_id?: string | null;
      resume_from_node_id?: string | null;
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
    (planspaceId: string) => {
      // Prefer the planspace's own model preset (from its earliest node) over
      // the project-level default.
      const laneNodes = nodes.filter((n) => n.planspace_id === planspaceId);
      const laneAnchor = laneNodes.reduce<NodeInfo | null>((acc, n) => {
        if (acc === null) return n;
        return n.created_at < acc.created_at ? n : acc;
      }, null);
      const modelPresetId = laneAnchor?.model_preset_id ?? session?.model_preset_id ?? null;
      void createVirtualNode({
        planspace_id: planspaceId,
        model_preset_id: modelPresetId,
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
      if (!session?.id || projectRunnerBusy || composerLocked) return;
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
    [session?.id, projectRunnerBusy, composerLocked, inspectNode],
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
        selectAndOpenNode(ev.node_id);
        void refreshNodes();
      } else if (ev.type === "turn_done") {
        setPendingGates((current) => withoutPendingNode(current, ev.node_id));
        setPendingReviews((current) => withoutPendingNode(current, ev.node_id));
        void refreshNodes();
      } else if (ev.type === "error") {
        console.error("server error:", ev.message);
      } else if (ev.type === "node_started") {
        eventNodeId = ev.node_id;
        const startedKind = ev.kind ?? "agent";
        if (startedKind !== "op") {
          selectAndOpenNode(ev.node_id);
        }
        void refreshNodes();
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
      }
      appendSelectedEvent(eventNodeId, ev);
    },
    [appendSelectedEvent, inspectNode, refreshGit, refreshNodes, selectAndOpenNode],
  );

  const { status, send } = useSessionSocket(
    route === "project" ? (session?.id ?? null) : null,
    handleEvent,
    activeCanvasNodeIds,
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
    });
  }, [togglePlanspaceVisibility, createUnparentedVirtual, inspectNode]);

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
    /* Node clicks open the panel in details mode (overriding templates
     * if that was showing). Empty-canvas click closes it. */
    if (sel.kind === "none") {
      setPanelState((prev) => (prev.open ? { ...prev, open: false } : prev));
    } else {
      setPanelState((prev) =>
        prev.open && prev.mode === "details" ? prev : { open: true, mode: "details" },
      );
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

  const onTemplateDrop = useCallback(
    async (slug: string, anchorNodeId: string | null) => {
      if (!session?.id || readOnly) return;
      try {
        await applyUserTemplate(session.id, slug, anchorNodeId);
        // Manual-lane stamps do not emit node_updated events.
        await refreshNodes();
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("applyUserTemplate failed", err);
        window.alert(
          `Could not apply template: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
    [readOnly, refreshNodes, session?.id],
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
      onCreateContinuationVirtual: createContinuationVirtual,
      onCreateDependencyVirtual: createDependencyVirtual,
      onMarkVirtualObsolete: (nodeId) =>
        updateVirtualNode(nodeId, { obsolete_reason: "Obsoleted by user" }),
      onDeleteVirtual: deleteVirtualNode,
      onInterruptNode: interruptNode,
      onRerunNode: rerunFailedNode,
      canCreateVirtual: !virtualCreateDisabled,
      canPromoteVirtual: !projectMutationPending && !readOnly,
      canInterrupt: canInterruptRunner && !readOnly,
      canRerun: !projectMutationPending && !readOnly,
      pendingGateForNode: (nodeId) =>
        readOnly ? null : validPendingGates[nodeId]?.request ?? null,
      onResolveGate,
      modelPresets,
    });
  }, [
    validPendingGates,
    onResolveGate,
    promoteVirtualNode,
    createContinuationVirtual,
    createDependencyVirtual,
    updateVirtualNode,
    deleteVirtualNode,
    interruptNode,
    rerunFailedNode,
    virtualCreateDisabled,
    projectMutationPending,
    readOnly,
    canInterruptRunner,
    composerLocked,
    modelPresets,
  ]);

  /* Canvas layout changes -> serialized backend PATCHes. Best-effort: log on
   * failure but don't surface; the client-side ref keeps working either way. */
  const onLayoutHintsChange = useCallback(
    (
      updates: Record<string, { x: number; y: number }>,
      layoutViewport?: CanvasViewport | null,
    ) => {
      if (!session?.id || readOnly) return;
      if (Object.keys(updates).length === 0 && !layoutViewport) return;
      const sessionId = session.id;
      const updatesSnapshot = Object.fromEntries(
        Object.entries(updates).map(([id, pos]) => [id, { x: pos.x, y: pos.y }]),
      );
      const viewportSnapshot = layoutViewport ? { ...layoutViewport } : layoutViewport;
      const save = layoutSaveChainRef.current
        .catch(() => undefined)
        .then(() => updateLayoutHints(sessionId, updatesSnapshot, [], viewportSnapshot))
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
      <>
        <ProjectsLanding
          onOpen={openProject}
          onCreate={() => setNewProjectModalOpen(true)}
          modelPresets={modelPresets}
          globalState={globalState}
          onGlobalStateChanged={(next) => {
            setGlobalState(next);
            setModelPresets(next.model_presets);
          }}
          onTemplateLaunched={(s) => openProject(s)}
        />
        <NewProjectModal
          open={newProjectModalOpen}
          modelPresets={modelPresets}
          defaults={globalState?.defaults ?? null}
          onCancel={() => setNewProjectModalOpen(false)}
          onCreated={(next) => {
            setNewProjectModalOpen(false);
            openProject(next);
          }}
        />
      </>
    );
  }

  const projectTitle =
    session?.name?.trim() ||
    (session ? `Project ${session.id.slice(0, 8)}` : "Project");

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
        await gitCommit(session.id, message);
      } else if (action === "review") {
        const result = await gitReview(session.id);
        selectAndOpenNode(result.node.id);
      } else if (action === "pull") {
        await gitPull(session.id);
      } else {
        await gitPush(session.id);
      }
    } catch (err) {
      setGitError(err instanceof Error ? err.message : String(err));
    } finally {
      await Promise.all([refreshGit(), refreshNodes()]);
      setGitAction(null);
    }
  };
  const commitGitMessage = (message: string) => runGitAction("commit", message);
  const reviewGitChanges = () => runGitAction("review");

  return (
    <>
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
                  read-only · native to {session.native_machine_label} · as of {session.last_sync_at ? new Date(session.last_sync_at * 1000).toLocaleString() : "never synced"}
                </span>
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

          <button
            type="button"
            onClick={() => {
              setSelection({ kind: "projectRoot" });
              inspectNode(null);
              setNewDirectionRequestVersion((version) => version + 1);
              openDetails();
            }}
            disabled={sessionSettingsSaving || projectMutationPending || readOnly}
            className="inline-flex h-8 items-center rounded-md border border-line bg-surface px-2.5 text-xs font-medium text-ink-muted transition hover:border-line-strong hover:bg-surface-sunken hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
            title="Open new direction composer"
          >
            + New direction
          </button>

          <button
            type="button"
            onClick={toggleTemplates}
            aria-pressed={panelState.open && panelState.mode === "templates"}
            className={
              "inline-flex h-8 items-center rounded-md border px-2.5 text-xs font-medium transition " +
              (panelState.open && panelState.mode === "templates"
                ? "border-brand bg-brand/10 text-ink-strong"
                : "border-line bg-surface text-ink-muted hover:border-line-strong hover:bg-surface-sunken hover:text-ink")
            }
            title="Toggle template library"
          >
            Templates
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
              projectTitle={projectTitle}
              contextBundlesByNodeId={contextBundlesByNodeId}
              knownPlanspaceIds={knownPlanspaceIds}
              hiddenPlanspaceIds={hiddenPlanspaceIds}
              activePlanspaceId={sessionContextSpace?.active_planspace_id ?? null}
              canCreateVirtual={!virtualCreateDisabled}
              principles={principles}
              skills={skills}
              gitCommits={gitCommits}
              gitHead={gitStatus?.head ?? null}
              gitDirtyCount={gitStatus?.dirty_count ?? 0}
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

          {/* Cross-node pending banner — only show if the pending node isn't the
              one the user is currently inspecting. */}
          {!readOnly && pendingBanner(activePendingGate, activePendingReview, inspectedNodeId) && (
            <PendingBanner
              label={
                pendingBanner(activePendingGate, activePendingReview, inspectedNodeId)!.label
              }
              panelOpen={panelState.open}
              onJump={() => {
                const target =
                  pendingBanner(activePendingGate, activePendingReview, inspectedNodeId)!.nodeId;
                onSelectNode(target);
              }}
            />
          )}

          {/* Floating side panel — slides in from the right, swaps between
              details (node inspector) and the template library. */}
          <aside
            ref={panelRef}
            aria-hidden={!panelState.open}
            className={
              "absolute inset-y-0 right-0 z-20 flex w-[380px] flex-col border-l border-line bg-surface-sunken shadow-modal transition-transform duration-200 ease-out will-change-transform " +
              (panelState.open ? "translate-x-0" : "pointer-events-none translate-x-full")
            }
          >
            {panelState.mode === "templates" ? (
              <TemplateLibraryDock
                refreshToken={libraryRefreshToken}
                modelPresets={modelPresets}
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
                onPreferredLanguageChange={updatePreferredLanguage}
                onConcurrencyChange={updateConcurrency}
                onActivatePlanspace={activatePlanspace}
                onSelectContextBinding={selectContextBinding}
                onNewDirection={startNewDirection}
                onStartBlankDirection={startBlankDirection}
                onNewLibraryEntry={handleNewLibraryEntry}
                onImportSkill={handleImportSkill}
                onCreateContinuationVirtual={createContinuationVirtual}
                onPromoteVirtual={promoteVirtualNode}
                onUpdateVirtual={updateVirtualNode}
                onInterruptNode={interruptNode}
                onRerunNode={rerunFailedNode}
                canInterrupt={canInterruptRunner && !readOnly}
                canRerun={!projectMutationPending && !readOnly}
                onPlanspaceModeChange={changePlanspaceMode}
                onContextInit={runContextInit}
                onContextRefresh={runContextRefresh}
                onContextCancel={runContextCancel}
                onTogglePlanspaceVisibility={togglePlanspaceVisibility}
                contextReloadVersion={contextReloadVersion}
                focusRequestVersion={focusRequestVersion}
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
    />
    </>
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
      <button
        type="button"
        onClick={onJump}
        className="rounded border border-state-waiting/40 bg-surface-raised px-2 py-0.5 text-state-waiting transition hover:border-state-waiting/70"
      >
        Jump
      </button>
    </div>
  );
}

function upsertNode(prev: NodeInfo[], node: NodeInfo): NodeInfo[] {
  const index = prev.findIndex((item) => item.id === node.id);
  if (index < 0) {
    return [...prev, node].sort((a, b) => a.created_at - b.created_at);
  }
  return prev.map((item, i) => (i === index ? node : item));
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
  selectedId: string | null,
): { nodeId: string; label: string } | null {
  const active = review ?? gate;
  if (!active || active.nodeId === selectedId) return null;
  const labelKind = isReviewInteraction(active.request) ? "review" : "response";
  return {
    nodeId: active.nodeId,
    label: `Node ${active.nodeId.slice(0, 8)} is awaiting your ${labelKind}.`,
  };
}

function readPanelState(): { open: boolean; mode: "details" | "templates" } {
  try {
    const raw = window.localStorage.getItem("miniclaw.panelState");
    if (raw) {
      const parsed = JSON.parse(raw) as { open?: unknown; mode?: unknown };
      const mode = parsed.mode === "templates" ? "templates" : "details";
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
    return "root";
  }
  if (selection.kind === "planspace") {
    return `planspace:${selection.planspaceId}`;
  }
  if (selection.kind === "commit") {
    return selection.sha ? `commit:${selection.sha}` : "commit:ghost";
  }
  return null;
}
