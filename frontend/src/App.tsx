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
  listSkills,
  deleteSkill,
  listModelPresets,
  type SkillSummary,
  type UpdateVirtualPayload,
} from "./api";
import { Canvas, type CanvasSelection } from "./canvas/Canvas";
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
import type {
  ContextBundle,
  EventRecord,
  InteractionRequest,
  NodeDiff,
  NodeInfo,
  ServerEvent,
  CanvasViewport,
  ModelPreset,
  SessionContextSpaceInfo,
  SessionInfo,
  PlanspaceMode,
} from "./types";
import { useSessionSocket } from "./ws";
import { canResumeNode } from "./nodeUtil";
import { defaultModelPresetId } from "./modelPresets";

type Route = "landing" | "project";
type PendingGateState = {
  request: InteractionRequest;
  nodeId: string;
};

const TERMINAL_STATES = new Set<NodeInfo["state"]>(["done", "error", "cancelled"]);
const INTERRUPTIBLE_STATES = new Set<NodeInfo["state"]>([
  "running",
  "waiting",
  "awaiting_human_input",
]);
const PROJECT_ACTIVE_STATES = new Set<NodeInfo["state"]>([
  "queued",
  ...INTERRUPTIBLE_STATES,
]);

export function App() {
  const [route, setRoute] = useState<Route>("landing");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [modelPresets, setModelPresets] = useState<ModelPreset[]>([]);

  const [selection, setSelection] = useState<CanvasSelection>({ kind: "none" });
  /* For data-fetching purposes we track the "currently inspected nodeId" — the
   * agent/op whose events, diff, and context bundle we should load. For context
   * selections, this stays pointed at the owning node. */
  const [inspectedNodeId, setInspectedNodeId] = useState<string | null>(null);

  const [selectedEvents, setSelectedEvents] = useState<EventRecord[]>([]);
  const [selectedEventsLoading, setSelectedEventsLoading] = useState(false);
  const [selectedDiff, setSelectedDiff] = useState<NodeDiff | null>(null);
  const [selectedDiffLoading, setSelectedDiffLoading] = useState(false);
  const [selectedContextBundle, setSelectedContextBundle] = useState<ContextBundle | null>(null);
  const [selectedContextBundleLoading, setSelectedContextBundleLoading] = useState(false);

  /* Aggregated bundles: fills in as the user explores. Keyed by node id. */
  const [contextBundlesByNodeId, setContextBundlesByNodeId] = useState<
    Record<string, ContextBundle | null>
  >({});

  /* User-wide skill shelf. Fetched on session mount and refreshed after
   * a skill-edit turn completes. See canvas layout.ts for the dimmed-tile
   * merge into the context aggregate. */
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const refreshSkills = useCallback(() => {
    listSkills()
      .then(setSkills)
      .catch(() => {
        /* non-fatal — the shelf just stays stale until the next refresh */
      });
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
    listModelPresets()
      .then((next) => {
        if (!cancelled) setModelPresets(next);
      })
      .catch((err) => {
        console.error("list model presets failed:", err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const [pendingGate, setPendingGate] = useState<PendingGateState | null>(null);
  const [pendingReview, setPendingReview] = useState<PendingGateState | null>(null);

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
  /* Programmatic-selection helper. Whenever code (not a user canvas click)
   * changes what's inspected, we must also open the details panel — otherwise
   * the freshly-inspected node's controls (gate/review form, virtual draft
   * editor) sit inside the closed floating panel and are invisible to the
   * user. */
  const selectAndOpenNode = useCallback(
    (nodeId: string, kind: "agent" | "op" = "agent") => {
      setSelection({ kind, nodeId });
      setInspectedNodeId(nodeId);
      setPanelState({ open: true, mode: "details" });
    },
    [],
  );

  const panelRef = useRef<HTMLElement | null>(null);
  const activeNodeIdRef = useRef<string | null>(null);
  const inspectedNodeIdRef = useRef<string | null>(null);
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

  useEffect(() => {
    inspectedNodeIdRef.current = inspectedNodeId;
  }, [inspectedNodeId]);

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
    setInspectedNodeId(null);
    setSelectedEvents([]);
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
    setProjectMutationPending(false);
    setPendingGate(null);
    setPendingReview(null);
    setFocusRequestVersion(0);
    setNewDirectionRequestVersion(0);
    setInitialLoadComplete(false);
    activeNodeIdRef.current = null;
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
  const selectedCanvasNodeId = useMemo(() => graphNodeIdForSelection(selection), [selection]);
  const activeNodeFromList = useMemo(
    () => nodes.find((n) => PROJECT_ACTIVE_STATES.has(n.state)) ?? null,
    [nodes],
  );
  const hasInterruptibleNode = useMemo(
    () => nodes.some((n) => INTERRUPTIBLE_STATES.has(n.state)),
    [nodes],
  );
  const projectRunnerActive = !!activeNodeFromList;
  const projectRunnerBusy = projectMutationPending || projectRunnerActive;
  const activeCanvasNodeId = activeNodeFromList?.id ?? null;

  const activePendingGate = useMemo(
    () => keepPendingForState(pendingGate, nodes, "waiting"),
    [nodes, pendingGate],
  );
  const activePendingReview = useMemo(
    () =>
      keepPendingForStates(pendingReview, nodes, ["awaiting_human_input"]),
    [nodes, pendingReview],
  );
  const composerLocked = !!activePendingGate || !!activePendingReview;
  const virtualCreateDisabled =
    composerLocked ||
    projectRunnerBusy ||
    sessionSettingsSaving ||
    !!sessionContextSpace?.context_refresh?.running;

  const handleNewSkill = useCallback(
    async (userSeed: string) => {
      if (!session?.id || virtualCreateDisabled) return;
      const active = sessionContextSpace?.active_planspace_id ?? null;
      setProjectMutationPending(true);
      setSessionContextSpaceError(null);
      try {
        const result = await createVirtual(session.id, {
          prompt_draft: userSeed,
          category: "regular",
          agent_op_kind: "skill_edit",
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
      if (wasRemovedByRefresh(activeNodeIdRef.current)) {
        activeNodeIdRef.current = null;
      }
      setPendingGate((current) =>
        current && wasRemovedByRefresh(current.nodeId) ? null : current,
      );
      setPendingReview((current) =>
        current && wasRemovedByRefresh(current.nodeId) ? null : current,
      );
      setSelection((current) =>
        (current.kind === "agent" || current.kind === "op") &&
        wasRemovedByRefresh(current.nodeId)
          ? { kind: "none" }
          : current,
      );
      setInspectedNodeId((current) => {
        if (wasRemovedByRefresh(current)) return null;
        return current ?? next.at(-1)?.id ?? null;
      });
    } catch (err) {
      console.error("list nodes failed:", err);
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
      setSkills([]);
      return;
    }
    setInitialLoadComplete(false);
    let cancelled = false;
    void Promise.allSettled([refreshNodes(), refreshContextSpace()]).then(() => {
      if (!cancelled) setInitialLoadComplete(true);
    });
    /* Skills are user-wide — fetched independently of nodes/contextspace and
     * don't gate the canvas render. Stale is acceptable; refreshSkills() is
     * called after skill-edit turns finish. */
    refreshSkills();
    return () => {
      cancelled = true;
    };
  }, [session?.id, refreshNodes, refreshContextSpace, refreshSkills]);

  useEffect(() => {
    if (!sessionContextSpace?.context_refresh?.running) return;
    const timer = window.setInterval(() => {
      void refreshContextSpace();
    }, 1200);
    return () => window.clearInterval(timer);
  }, [sessionContextSpace?.context_refresh?.running, refreshContextSpace]);

  /* Refresh the skill shelf whenever a skill-edit node reaches a terminal
   * state — creation and refinement both land through this path. */
  const terminalSkillEditCount = useMemo(() => {
    let count = 0;
    for (const n of nodes) {
      if (n.agent_op_kind === "skill_edit" && TERMINAL_STATES.has(n.state)) {
        count += 1;
      }
    }
    return count;
  }, [nodes]);
  const prevTerminalSkillEditCountRef = useRef(0);
  useEffect(() => {
    if (terminalSkillEditCount > prevTerminalSkillEditCountRef.current) {
      refreshSkills();
    }
    prevTerminalSkillEditCountRef.current = terminalSkillEditCount;
  }, [terminalSkillEditCount, refreshSkills]);

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
      if (!session?.id || projectRunnerBusy || composerLocked) return;
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
    [session?.id, projectRunnerBusy, composerLocked, refreshNodes, selectAndOpenNode],
  );

  const updateVirtualNode = useCallback(
    async (nodeId: string, payload: UpdateVirtualPayload) => {
      if (!session?.id || projectRunnerBusy || composerLocked) return;
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
    [session?.id, projectRunnerBusy, composerLocked],
  );

  /* Drag-onto-virtual attach path: Canvas hands us (virtualNodeId, skillId)
   * when a skill chip is dropped on a virtual tile. We read the target's
   * current pending_extra_skills, append, and PATCH via updateVirtualNode.
   * Fire-and-forget: errors surface through sessionContextSpaceError. */
  const handleAttachSkillToVirtual = useCallback(
    (virtualNodeId: string, skillId: string) => {
      const target = nodesRef.current.find((n) => n.id === virtualNodeId);
      if (!target || target.state !== "virtual" || target.obsolete_reason) {
        return;
      }
      const current = target.pending_extra_skills ?? [];
      if (current.includes(skillId)) return;
      void updateVirtualNode(virtualNodeId, {
        pending_extra_skills: [...current, skillId],
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
      setPendingGate((prev) => (prev?.nodeId === nodeId ? null : prev));
      setPendingReview((prev) => (prev?.nodeId === nodeId ? null : prev));
      if (inspectedNodeIdRef.current === nodeId) {
        setInspectedNodeId(null);
        setSelection({ kind: "none" });
      }
    },
    [session?.id, projectRunnerBusy, composerLocked],
  );

  const rerunFailedNode = useCallback(
    async (nodeId: string) => {
      if (!session?.id || projectRunnerBusy || composerLocked) return;
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
    [session?.id, projectRunnerBusy, composerLocked, refreshNodes, selectAndOpenNode],
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
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      try {
        const next = await updatePlanspaceView(session.id, {
          [planspaceId]: { hidden },
        });
        setSessionContextSpace(next);
        setSession((current) =>
          current && current.id === session.id
            ? {
                ...current,
                planspace_view: next.planspace_view ?? current.planspace_view,
              }
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

  /* Events, diff, and context-bundle fetch — keyed off inspectedNodeId. */
  useEffect(() => {
    if (!session?.id || !inspectedNodeId || selectedNode?.state === "virtual") {
      setSelectedEvents([]);
      setSelectedEventsLoading(false);
      return;
    }
    let cancelled = false;
    setSelectedEventsLoading(true);
    listNodeEvents(session.id, inspectedNodeId)
      .then((records) => {
        if (!cancelled) setSelectedEvents(records);
      })
      .catch((err) => {
        if (!cancelled) {
          console.error("list node events failed:", err);
          setSelectedEvents([]);
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
    setSelectedDiffLoading(true);
    getNodeDiff(session.id, inspectedNodeId)
      .then((diff) => {
        if (!cancelled) setSelectedDiff(diff);
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
    };
  }, [
    session?.id,
    inspectedNodeId,
    selectedNode?.commit_before,
    selectedNode?.commit_after,
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
  const appendSelectedEvent = useCallback((nodeId: string | null, ev: ServerEvent) => {
    const seq = ev.seq;
    if (!nodeId || inspectedNodeIdRef.current !== nodeId || typeof seq !== "number") {
      return;
    }
    setSelectedEvents((prev) => {
      if (prev.some((record) => record.seq === seq)) return prev;
      return [...prev, { seq, event: ev }].sort((a, b) => a.seq - b.seq);
    });
  }, []);

  const handleEvent = useCallback(
    (ev: ServerEvent) => {
      let eventNodeId = activeNodeIdRef.current;
      if (ev.type === "interaction_request") {
        const ownerNodeId = activeNodeIdRef.current;
        if (ownerNodeId) {
          if (isReviewInteraction(ev)) {
            setPendingReview({ request: ev, nodeId: ownerNodeId });
          } else {
            setPendingGate({ request: ev, nodeId: ownerNodeId });
          }
          /* The pending banner is suppressed once this node is the
           * inspected one, so we must also open the details panel or
           * the gate/review form ends up hidden. */
          selectAndOpenNode(ownerNodeId);
        }
        void refreshNodes();
      } else if (ev.type === "turn_done") {
        activeNodeIdRef.current = null;
        void refreshNodes();
      } else if (ev.type === "error") {
        activeNodeIdRef.current = null;
        console.error("server error:", ev.message);
      } else if (ev.type === "node_started") {
        activeNodeIdRef.current = ev.node_id;
        eventNodeId = ev.node_id;
        const startedKind = ev.kind ?? "agent";
        if (startedKind !== "op") {
          selectAndOpenNode(ev.node_id);
        }
        void refreshNodes();
      } else if (ev.type === "node_updated") {
        eventNodeId = ev.node.id;
        if (PROJECT_ACTIVE_STATES.has(ev.node.state)) {
          activeNodeIdRef.current = ev.node.id;
        } else if (activeNodeIdRef.current === ev.node.id) {
          activeNodeIdRef.current = null;
        }
        setNodes((prev) => {
          const updated = upsertNode(prev, ev.node);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        if (ev.node.state !== "waiting") {
          setPendingGate((prev) => (prev?.nodeId === ev.node.id ? null : prev));
        }
        if (ev.node.state !== "awaiting_human_input") {
          setPendingReview((prev) => (prev?.nodeId === ev.node.id ? null : prev));
        }
      } else if (ev.type === "node_removed") {
        eventNodeId = ev.id;
        setNodes((prev) => {
          const updated = prev.filter((node) => node.id !== ev.id);
          nodeCountRef.current = updated.length;
          nodesRef.current = updated;
          return updated;
        });
        setPendingGate((prev) => (prev?.nodeId === ev.id ? null : prev));
        setPendingReview((prev) => (prev?.nodeId === ev.id ? null : prev));
        if (inspectedNodeIdRef.current === ev.id) {
          setInspectedNodeId(null);
          setSelection({ kind: "none" });
        }
      }
      appendSelectedEvent(eventNodeId, ev);
    },
    [appendSelectedEvent, refreshNodes, selectAndOpenNode],
  );

  const { status, send } = useSessionSocket(
    route === "project" ? (session?.id ?? null) : null,
    handleEvent,
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
    (_nodeId: string) => {
      if (status !== "open") return;
      /* The backend runs at most one node per project at a time, so
       * interrupting "this node" is the same as interrupting the current
       * runner. The node id is accepted for call-site symmetry. */
      send({ type: "interrupt" });
    },
    [status, send],
  );

  const onResolveReview = useCallback(
    (payload: { id: string; judgment: string }) => {
      if (status !== "open") return;
      send({
        type: "interaction_response",
        id: payload.id,
        allow: true,
        response: { prose: payload.judgment },
      });
      setPendingReview(null);
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
        setInspectedNodeId(null);
      },
      onTogglePlanspaceVisibility: togglePlanspaceVisibility,
      onCreateVirtual: createUnparentedVirtual,
    });
  }, [togglePlanspaceVisibility, createUnparentedVirtual]);

  const onResolveGate = useCallback(
    (
      id: string,
      payload: Omit<
        Extract<Parameters<typeof send>[0], { type: "interaction_response" }>,
        "type" | "id"
      >,
    ) => {
      send({ type: "interaction_response", id, ...payload });
      setPendingGate((prev) => (prev && prev.request.id === id ? null : prev));
      window.setTimeout(() => {
        void refreshNodes();
      }, 250);
    },
    [send, refreshNodes],
  );

  const onSelectionChange = useCallback((sel: CanvasSelection) => {
    setSelection(sel);
    if (sel.kind === "agent" || sel.kind === "op") {
      setInspectedNodeId(sel.nodeId);
    } else if (sel.kind === "none") {
      setInspectedNodeId(null);
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
  }, []);

  const onMultiSelectionChange = useCallback((ids: string[]) => {
    setMultiSelectedNodeIds(ids);
  }, []);

  const onAgentNodeContextMenu = useCallback(
    (nodeId: string | null, x: number, y: number) => {
      if (!nodeId) return; // right-click on non-agent → no menu
      setTemplateContextMenu({ nodeId, x, y });
    },
    [],
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
      if (!session?.id) return;
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
    [refreshNodes, session?.id],
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
      setInspectedNodeId(nodeId);
      openDetails();
    },
    [nodes, openDetails],
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
      canPromoteVirtual: !projectRunnerBusy && !composerLocked,
      canInterrupt: canInterruptRunner,
      canRerun: !projectRunnerBusy && !composerLocked,
      pendingGateForNode: (nodeId) =>
        activePendingGate?.nodeId === nodeId ? activePendingGate.request : null,
      onResolveGate,
      modelPresets,
    });
  }, [
    activePendingGate,
    onResolveGate,
    promoteVirtualNode,
    createContinuationVirtual,
    createDependencyVirtual,
    updateVirtualNode,
    deleteVirtualNode,
    interruptNode,
    rerunFailedNode,
    virtualCreateDisabled,
    projectRunnerBusy,
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
      if (!session?.id) return;
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
    [session?.id],
  );

  if (route === "landing") {
    return (
      <>
        <ProjectsLanding
          onOpen={openProject}
          onCreate={() => setNewProjectModalOpen(true)}
          modelPresets={modelPresets}
          onTemplateLaunched={(s) => openProject(s)}
        />
        <NewProjectModal
          open={newProjectModalOpen}
          modelPresets={modelPresets}
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
              setInspectedNodeId(null);
              setNewDirectionRequestVersion((version) => version + 1);
              openDetails();
            }}
            disabled={sessionSettingsSaving || projectRunnerBusy || composerLocked}
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
              nodes={nodes}
              selectedNodeId={selectedCanvasNodeId}
              activeNodeId={activeCanvasNodeId}
              projectTitle={projectTitle}
              contextBundlesByNodeId={contextBundlesByNodeId}
              knownPlanspaceIds={knownPlanspaceIds}
              hiddenPlanspaceIds={hiddenPlanspaceIds}
              activePlanspaceId={sessionContextSpace?.active_planspace_id ?? null}
              canCreateVirtual={!virtualCreateDisabled}
              skills={skills}
              initialLayoutHints={session?.layout_hints}
              initialLayoutViewport={session?.layout_viewport ?? null}
              onSelectionChange={onSelectionChange}
              onMultiSelectionChange={onMultiSelectionChange}
              onAgentNodeContextMenu={onAgentNodeContextMenu}
              onTemplateDrop={onTemplateDrop}
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
          {pendingBanner(activePendingGate, activePendingReview, inspectedNodeId) && (
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

          {!projectRunnerBusy &&
            !composerLocked &&
            nodes.length === 0 && (
              <button
                type="button"
                onClick={() => {
                  setSelection({ kind: "projectRoot" });
                  setInspectedNodeId(null);
                  setNewDirectionRequestVersion((version) => version + 1);
                  openDetails();
                }}
                className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 rounded-xl border-2 border-dashed border-line-strong bg-surface-raised/80 px-6 py-5 text-center text-sm text-ink-muted shadow-card transition hover:border-brand hover:bg-surface-raised hover:text-ink-strong"
              >
                <div className="font-display text-base font-semibold text-ink-strong">
                  Start the first direction
                </div>
                <div className="mt-1 text-[12px] text-ink-muted">
                  Open project actions to create a notebook.
                </div>
              </button>
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
                nodes={nodes}
                session={session}
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
                onPreferredLanguageChange={updatePreferredLanguage}
                onActivatePlanspace={activatePlanspace}
                onSelectContextBinding={selectContextBinding}
                onNewDirection={startNewDirection}
                onStartBlankDirection={startBlankDirection}
                onNewSkill={handleNewSkill}
                onCreateContinuationVirtual={createContinuationVirtual}
                onPromoteVirtual={promoteVirtualNode}
                onUpdateVirtual={updateVirtualNode}
                onInterruptNode={interruptNode}
                onRerunNode={rerunFailedNode}
                canInterrupt={canInterruptRunner}
                canRerun={!projectRunnerBusy && !composerLocked}
                onPlanspaceModeChange={changePlanspaceMode}
                onContextInit={runContextInit}
                onContextRefresh={runContextRefresh}
                onContextCancel={runContextCancel}
                onTogglePlanspaceVisibility={togglePlanspaceVisibility}
                contextReloadVersion={contextReloadVersion}
                focusRequestVersion={focusRequestVersion}
                newDirectionRequestVersion={newDirectionRequestVersion}
                onNewDirectionRequestHandled={acknowledgeNewDirectionRequest}
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

function keepPendingForState(
  pending: PendingGateState | null,
  nodes: NodeInfo[],
  state: NodeInfo["state"],
): PendingGateState | null {
  return keepPendingForStates(pending, nodes, [state]);
}

function keepPendingForStates(
  pending: PendingGateState | null,
  nodes: NodeInfo[],
  states: NodeInfo["state"][],
): PendingGateState | null {
  if (!pending) return null;
  const owner = nodes.find((node) => node.id === pending.nodeId);
  if (owner && !states.includes(owner.state)) return null;
  return pending;
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
  if (selection.kind === "projectRoot") {
    return "root";
  }
  if (selection.kind === "planspace") {
    return `planspace:${selection.planspaceId}`;
  }
  return null;
}
