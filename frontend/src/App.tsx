import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelProjectContext,
  createPlanspace,
  createVirtual,
  getSession,
  getNodeContextBundle,
  getNodeDiff,
  getSessionContextSpace,
  initProjectContext,
  listNodeEvents,
  listNodes,
  promoteVirtual,
  refreshProjectContext,
  updateLayoutHints,
  updatePlanspaceMode,
  updatePlanspaceView,
  updateSessionContextSpace,
  updateSessionPreferences,
  updateVirtual,
  type CreateVirtualPayload,
  type UpdateVirtualPayload,
} from "./api";
import { Canvas, type CanvasSelection } from "./canvas/Canvas";
import { setAgentNodeContext } from "./canvas/nodes/AgentNode";
import { setPhantomContext } from "./canvas/nodes/PhantomNode";
import { setPlanspaceLaneContext } from "./canvas/nodes/PlanspaceLaneNode";
import { SidePanel } from "./panel/SidePanel";
import { NewProjectModal } from "./components/NewProjectModal";
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
  SessionContextSpaceInfo,
  SessionInfo,
  PlanspaceMode,
  NodeCategory,
  ReviewBrief,
  ReviewSubtype,
} from "./types";
import { useSessionSocket } from "./ws";

type Route = "landing" | "project";
type PendingGateState = {
  request: InteractionRequest;
  nodeId: string;
};

const TERMINAL_STATES = new Set<NodeInfo["state"]>(["done", "error", "cancelled"]);

export function App() {
  const [route, setRoute] = useState<Route>("landing");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);

  const [selection, setSelection] = useState<CanvasSelection>({ kind: "none" });
  /* For data-fetching purposes we track the "currently inspected nodeId" — the
   * agent/op whose events/diff/bundle we should load. For artifact and
   * context selections, this stays pointed at the owning node so the artifact
   * panel can render the file content. */
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

  const [pendingGate, setPendingGate] = useState<PendingGateState | null>(null);
  const [pendingReview, setPendingReview] = useState<PendingGateState | null>(null);

  const [streaming, setStreaming] = useState(false);

  /* True once both initial fetches (nodes + contextspace) have settled for the
   * current session. The canvas is held off-screen until then so hidden-planspace
   * nodes never briefly flash visible during the load-order race. */
  const [initialLoadComplete, setInitialLoadComplete] = useState(false);

  /* Phantom composer:
   *   undefined → no phantom in the canvas
   *   null       → fresh-start phantom
   *   string     → phantom continuing from that node */
  const [phantomFromNodeId, setPhantomFromNodeId] = useState<string | null | undefined>(undefined);
  const [virtualComposerLaneId, setVirtualComposerLaneId] = useState<string | null>(null);

  const [newProjectModalOpen, setNewProjectModalOpen] = useState(false);

  /* ⋯ menu */
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const activeNodeIdRef = useRef<string | null>(null);
  const inspectedNodeIdRef = useRef<string | null>(null);
  const currentRouteRef = useRef<Route>("landing");
  const currentSessionIdRef = useRef<string | null>(null);
  const nodeCountRef = useRef(0);
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

  useEffect(() => {
    currentRouteRef.current = route;
  }, [route]);

  useEffect(() => {
    currentSessionIdRef.current = session?.id ?? null;
  }, [session?.id]);

  useEffect(() => {
    nodeCountRef.current = nodes.length;
  }, [nodes.length]);

  /* close the ⋯ menu on outside click */
  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    window.addEventListener("mousedown", handler);
    return () => window.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  const resetAllSessionState = useCallback(() => {
    refreshNodesSeqRef.current += 1;
    nodeCountRef.current = 0;
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
    setStreaming(false);
    setPendingGate(null);
    setPendingReview(null);
    setPhantomFromNodeId(undefined);
    setVirtualComposerLaneId(null);
    setInitialLoadComplete(false);
    activeNodeIdRef.current = null;
    inflightBundleFetchRef.current.clear();
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
    streaming ||
    sessionSettingsSaving ||
    !!sessionContextSpace?.context_refresh?.running;

  /* refresh node list */
  const refreshNodes = useCallback(async () => {
    const sessionId = session?.id;
    if (!sessionId) return;
    const seq = ++refreshNodesSeqRef.current;
    try {
      const next = await listNodes(sessionId);
      if (seq !== refreshNodesSeqRef.current) return;
      if (currentRouteRef.current !== "project" || currentSessionIdRef.current !== sessionId) {
        return;
      }
      if (nodeCountRef.current > 0 && next.length === 0) {
        console.warn("Ignoring empty node refresh for non-empty project", {
          sessionId,
          currentCount: nodeCountRef.current,
        });
        return;
      }
      nodeCountRef.current = next.length;
      setNodes(next);
      setInspectedNodeId((current) => current ?? next.at(-1)?.id ?? null);
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
      return;
    }
    setInitialLoadComplete(false);
    let cancelled = false;
    void Promise.allSettled([refreshNodes(), refreshContextSpace()]).then(() => {
      if (!cancelled) setInitialLoadComplete(true);
    });
    return () => {
      cancelled = true;
    };
  }, [session?.id, refreshNodes, refreshContextSpace]);

  useEffect(() => {
    if (!sessionContextSpace?.context_refresh?.running) return;
    const timer = window.setInterval(() => {
      void refreshContextSpace();
    }, 1200);
    return () => window.clearInterval(timer);
  }, [sessionContextSpace?.context_refresh?.running, refreshContextSpace]);

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
    async (userSeed: string, mode: PlanspaceMode) => {
      if (!session?.id || sessionSettingsSaving) return;
      setSessionContextSpaceSaving(true);
      setSessionContextSpaceError(null);
      setStreaming(true);
      try {
        const created = await createPlanspace(session.id, {
          user_seed: userSeed,
          mode,
        });
        setSelection({ kind: "agent", nodeId: created.node_id });
        setInspectedNodeId(created.node_id);
        setPhantomFromNodeId(undefined);
        await refreshContextSpace();
        await refreshNodes();
      } catch (err) {
        setStreaming(false);
        setSessionContextSpaceError(String(err));
      } finally {
        setSessionContextSpaceSaving(false);
      }
    },
    [session?.id, sessionSettingsSaving, refreshContextSpace, refreshNodes],
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
      if (!session?.id || streaming || composerLocked) return;
      setStreaming(true);
      setSessionContextSpaceError(null);
      try {
        const result = await promoteVirtual(session.id, nodeId);
        setNodes((prev) => {
          const updated = upsertNode(prev, result.node);
          nodeCountRef.current = updated.length;
          return updated;
        });
        setSelection({ kind: "agent", nodeId: result.node.id });
        setInspectedNodeId(result.node.id);
        await refreshNodes();
      } catch (err) {
        setStreaming(false);
        setSessionContextSpaceError(String(err));
      }
    },
    [session?.id, streaming, composerLocked, refreshNodes],
  );

  const updateVirtualNode = useCallback(
    async (nodeId: string, payload: UpdateVirtualPayload) => {
      if (!session?.id || streaming || composerLocked) return;
      setSessionContextSpaceError(null);
      try {
        const result = await updateVirtual(session.id, nodeId, payload);
        setNodes((prev) => {
          const updated = upsertNode(prev, result.node);
          nodeCountRef.current = updated.length;
          return updated;
        });
      } catch (err) {
        setSessionContextSpaceError(String(err));
        throw err;
      }
    },
    [session?.id, streaming, composerLocked],
  );

  const openVirtualComposer = useCallback(
    (planspaceId: string) => {
      if (virtualCreateDisabled) return;
      setVirtualComposerLaneId(planspaceId);
      setSelection({ kind: "planspace", planspaceId });
      setInspectedNodeId(null);
      setPhantomFromNodeId(undefined);
    },
    [virtualCreateDisabled],
  );

  const createVirtualNode = useCallback(
    async (
      planspaceId: string,
      payload: Omit<CreateVirtualPayload, "planspace_id">,
    ) => {
      if (!session?.id || virtualCreateDisabled) return;
      setSessionContextSpaceError(null);
      try {
        const result = await createVirtual(session.id, {
          planspace_id: planspaceId,
          ...payload,
        });
        setNodes((prev) => {
          const updated = upsertNode(prev, result.node);
          nodeCountRef.current = updated.length;
          return updated;
        });
        setSelection({ kind: "agent", nodeId: result.node.id });
        setInspectedNodeId(result.node.id);
        if (result.node.state !== "virtual") {
          activeNodeIdRef.current = result.node.id;
          setStreaming(true);
        }
        setPhantomFromNodeId(undefined);
        setVirtualComposerLaneId(null);
      } catch (err) {
        setSessionContextSpaceError(String(err));
        throw err;
      }
    },
    [session?.id, virtualCreateDisabled],
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

  /* events / diff / artifact / context-bundle fetch — keyed off inspectedNodeId */
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
          setInspectedNodeId(ownerNodeId);
          setSelection({
            kind: "agent",
            nodeId: ownerNodeId,
          });
        }
        void refreshNodes();
      } else if (ev.type === "turn_done") {
        setStreaming(false);
        void refreshNodes();
      } else if (ev.type === "error") {
        setStreaming(false);
        console.error("server error:", ev.message);
      } else if (ev.type === "node_started") {
        activeNodeIdRef.current = ev.node_id;
        eventNodeId = ev.node_id;
        const startedKind = ev.kind ?? "agent";
        if (startedKind !== "op") {
          setInspectedNodeId(ev.node_id);
          setSelection({
            kind: "agent",
            nodeId: ev.node_id,
          });
        }
        /* Dismiss any phantom — the run has materialized. */
        setPhantomFromNodeId(undefined);
        setVirtualComposerLaneId(null);
        void refreshNodes();
      } else if (ev.type === "node_updated") {
        eventNodeId = ev.node.id;
        setNodes((prev) => {
          const updated = upsertNode(prev, ev.node);
          nodeCountRef.current = updated.length;
          return updated;
        });
        if (ev.node.state !== "waiting") {
          setPendingGate((prev) => (prev?.nodeId === ev.node.id ? null : prev));
        }
        if (ev.node.state !== "awaiting_human_input") {
          setPendingReview((prev) => (prev?.nodeId === ev.node.id ? null : prev));
        }
      }
      appendSelectedEvent(eventNodeId, ev);
    },
    [appendSelectedEvent, refreshNodes],
  );

  const { status, send } = useSessionSocket(
    route === "project" ? (session?.id ?? null) : null,
    handleEvent,
  );
  const composerDisabled =
    composerLocked || streaming || sessionSettingsSaving || status !== "open";

  /* launch via the phantom */
  const launchAgentNode = useCallback(
    (text: string, resume: string | null) => {
      if (composerDisabled) return;
      setStreaming(true);
      send({
        type: "user_message",
        text,
        resume_from_node_id: resume,
      });
    },
    [composerDisabled, send],
  );

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

  /* Phantom callbacks wired into the module singleton */
  useEffect(() => {
    setPhantomContext({
      onSubmit: ({ prompt, resumeFromNodeId }) => {
        launchAgentNode(prompt, resumeFromNodeId);
      },
      onDismiss: () => setPhantomFromNodeId(undefined),
      onClearResume: () => setPhantomFromNodeId(null),
      disabled: composerDisabled,
      planspaceOptions,
      activePlanspaceId: sessionContextSpace?.active_planspace_id ?? null,
    });
  }, [
    composerDisabled,
    launchAgentNode,
    planspaceOptions,
    sessionContextSpace?.active_planspace_id,
  ]);

  const onStop = () => {
    if (!streaming || status !== "open") return;
    send({ type: "interrupt" });
  };

  const onResolveReview = useCallback(
    (payload: { id: string; judgment: string }) => {
      if (status !== "open") return;
      const interactionType =
        activePendingReview?.request.id === payload.id
          ? activePendingReview.request.interaction_type
          : "human_review_prose";
      const response =
        interactionType === "human_review_prose"
          ? { prose: payload.judgment }
          : { judgment: payload.judgment };
      send({
        type: "interaction_response",
        id: payload.id,
        allow: true,
        decision:
          interactionType === "human_review_prose"
            ? "human_review_prose"
            : "review",
        response,
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
      onCreateVirtual: openVirtualComposer,
    });
  }, [togglePlanspaceVisibility, openVirtualComposer]);

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

  /* canvas → selection. The phantom composer lives or dies by this callback:
   * every canvas-driven selection change dismisses any open composer, so
   * clicking empty pane or any other node cancels it. Canvas.onNodeClick
   * deliberately swallows clicks on the phantom itself so the composer the
   * user is editing doesn't disappear out from under them. */
  const onSelectionChange = useCallback((sel: CanvasSelection) => {
    setSelection(sel);
    if (sel.kind === "agent" || sel.kind === "op") {
      setInspectedNodeId(sel.nodeId);
    } else if (sel.kind === "none") {
      setInspectedNodeId(null);
    }
    setPhantomFromNodeId((prev) => (prev !== undefined ? undefined : prev));
  }, []);

  /* select a specific node id (used by panel "jump to" affordances) */
  const onSelectNode = useCallback(
    (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      setSelection({
        kind: node.kind === "op" ? "op" : "agent",
        nodeId,
      });
      setInspectedNodeId(nodeId);
    },
    [nodes],
  );

  const openFreshPhantom = useCallback(() => {
    if (composerDisabled) return;
    if (!sessionContextSpace?.active_planspace_id) {
      setSelection({ kind: "projectRoot" });
      setInspectedNodeId(null);
      return;
    }
    setPhantomFromNodeId(null);
  }, [composerDisabled, sessionContextSpace?.active_planspace_id]);

  const onSpawnFromAgent = useCallback(
    (nodeId: string) => {
      if (composerDisabled) return;
      setPhantomFromNodeId(nodeId);
    },
    [composerDisabled],
  );

  /* Wire per-agent canvas affordances and inline pending-response tiles into
   * the AgentNode module singleton. */
  useEffect(() => {
    setAgentNodeContext({
      onSpawnFromAgent,
      onPromoteVirtual: promoteVirtualNode,
      pendingGateForNode: (nodeId) =>
        activePendingGate?.nodeId === nodeId ? activePendingGate.request : null,
      onResolveGate,
    });
  }, [activePendingGate, onResolveGate, onSpawnFromAgent, promoteVirtualNode]);

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
          onTemplateLaunched={(s) => openProject(s)}
        />
        <NewProjectModal
          open={newProjectModalOpen}
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

        <div className="flex items-center gap-2.5">
          <UsageStrip usage={selectedNode?.usage ?? null} />

          {streaming && (
            <button
              type="button"
              onClick={onStop}
              disabled={status !== "open"}
              className="inline-flex h-8 items-center rounded-md border border-state-error/40 bg-state-error-soft px-2.5 text-xs font-medium text-state-error transition hover:border-state-error/70 disabled:opacity-40"
            >
              Stop
            </button>
          )}

          {composerLocked && (
            <span className="hidden items-center rounded-md border border-state-waiting/40 bg-state-waiting-soft px-2 py-1 text-[10.5px] text-state-waiting sm:inline-flex">
              Awaiting response on a node
            </span>
          )}

          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((v) => !v)}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-base text-ink-muted transition hover:border-line-strong hover:bg-surface-sunken hover:text-ink"
              title="More actions"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
            >
              ⋯
            </button>
            {menuOpen && (
              <div
                role="menu"
                className="absolute right-0 top-full z-30 mt-1 w-56 rounded-md border border-line bg-surface-raised p-1 shadow-modal"
              >
                <MenuItem
                  onClick={() => {
                    /* If no node selected and no phantom, open a fresh-start phantom. */
                    openFreshPhantom();
                    setMenuOpen(false);
                  }}
                  disabled={composerDisabled}
                  label="New run"
                  hint="Open composer"
                />
                <div className="my-1 border-t border-line" />
                <div className="flex items-center justify-between px-2 py-1.5 text-[11px] text-ink-muted">
                  <span>Theme</span>
                  <ThemeToggle />
                </div>
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="relative flex min-w-0 flex-1 flex-col bg-surface-sunken">
          {initialLoadComplete ? (
            <Canvas
              key={session?.id ?? "no-session"}
              nodes={nodes}
              selectedNodeId={selectedCanvasNodeId}
              activeNodeId={activeNodeIdRef.current}
              projectTitle={projectTitle}
              phantomFromNodeId={phantomFromNodeId}
              phantomDisabled={composerDisabled}
              contextBundlesByNodeId={contextBundlesByNodeId}
              knownPlanspaceIds={knownPlanspaceIds}
              hiddenPlanspaceIds={hiddenPlanspaceIds}
              activePlanspaceId={sessionContextSpace?.active_planspace_id ?? null}
              canCreateVirtual={!virtualCreateDisabled}
              initialLayoutHints={session?.layout_hints}
              initialLayoutViewport={session?.layout_viewport ?? null}
              onSelectionChange={onSelectionChange}
              onSpawnFromAgent={onSpawnFromAgent}
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
              onJump={() => {
                const target =
                  pendingBanner(activePendingGate, activePendingReview, inspectedNodeId)!.nodeId;
                onSelectNode(target);
              }}
            />
          )}

          {!streaming &&
            !composerLocked &&
            phantomFromNodeId === undefined &&
            nodes.length === 0 && (
              <button
                type="button"
                onClick={() => {
                  setSelection({ kind: "projectRoot" });
                  setInspectedNodeId(null);
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
        </main>

        <SidePanel
          selection={selection}
          nodes={nodes}
          session={session}
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
          onSpawnPhantomFromNode={onSpawnFromAgent}
          onPreferredLanguageChange={updatePreferredLanguage}
          onActivatePlanspace={activatePlanspace}
          onSelectContextBinding={selectContextBinding}
          onNewDirection={startNewDirection}
          onPromoteVirtual={promoteVirtualNode}
          onUpdateVirtual={updateVirtualNode}
          onPlanspaceModeChange={changePlanspaceMode}
          onContextInit={runContextInit}
          onContextRefresh={runContextRefresh}
          onContextCancel={runContextCancel}
          onTogglePlanspaceVisibility={togglePlanspaceVisibility}
          contextReloadVersion={contextReloadVersion}
        />
      </div>

      {virtualComposerLaneId && (
        <VirtualDraftModal
          planspaceId={virtualComposerLaneId}
          planspaceLabel={labelForPlanspace(sessionContextSpace, virtualComposerLaneId)}
          disabled={virtualCreateDisabled}
          onCancel={() => setVirtualComposerLaneId(null)}
          onCreate={(payload) => createVirtualNode(virtualComposerLaneId, payload)}
        />
      )}
    </div>
  );
}

function MenuItem({
  onClick,
  disabled,
  label,
  hint,
}: {
  onClick: () => void;
  disabled?: boolean;
  label: string;
  hint?: string;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      disabled={disabled}
      className="flex w-full items-center justify-between gap-3 rounded px-2 py-1.5 text-left text-[12px] text-ink transition hover:bg-surface-sunken hover:text-ink-strong disabled:cursor-not-allowed disabled:opacity-40"
    >
      <span>{label}</span>
      {hint && <span className="text-[10px] text-ink-subtle">{hint}</span>}
    </button>
  );
}

function PendingBanner({ label, onJump }: { label: string; onJump: () => void }) {
  return (
    <div className="absolute bottom-3 right-3 z-10 flex items-center gap-3 rounded-md border border-state-waiting/40 bg-state-waiting-soft px-3 py-1.5 text-[11px] text-state-waiting shadow-card">
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

type VirtualComposerDraft = {
  category: NodeCategory;
  subtype: Exclude<ReviewSubtype, "programmatic_review">;
  motivation: string;
  promptDraft: string;
  brief: ReviewBrief;
};

function VirtualDraftModal({
  planspaceId,
  planspaceLabel,
  disabled,
  onCancel,
  onCreate,
}: {
  planspaceId: string;
  planspaceLabel: string;
  disabled: boolean;
  onCancel: () => void;
  onCreate: (payload: Omit<CreateVirtualPayload, "planspace_id">) => Promise<void>;
}) {
  const [draft, setDraft] = useState<VirtualComposerDraft>({
    category: "regular",
    subtype: "agentic_review",
    motivation: "",
    promptDraft: "",
    brief: {
      check_what: "",
      expected: "",
      abnormal: "",
    },
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canCreate =
    draft.promptDraft.trim().length > 0 &&
    !disabled &&
    !saving &&
    (draft.category !== "review" ||
      (!!draft.brief.check_what.trim() &&
        !!draft.brief.expected.trim() &&
        !!draft.brief.abnormal.trim()));

  const submit = async () => {
    if (!canCreate) return;
    setSaving(true);
    setError(null);
    try {
      const payload: Omit<CreateVirtualPayload, "planspace_id"> = {
        category: draft.category,
        prompt_draft: draft.promptDraft.trim(),
        motivation: draft.motivation.trim() || null,
        scheduled_deps: [],
      };
      if (draft.category === "review") {
        payload.subtype = draft.subtype;
        payload.brief = {
          check_what: draft.brief.check_what.trim(),
          expected: draft.brief.expected.trim(),
          abnormal: draft.brief.abnormal.trim(),
        };
      } else {
        payload.subtype = null;
        payload.brief = null;
      }
      await onCreate(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="flex max-h-[88vh] w-[560px] max-w-[94vw] flex-col overflow-hidden rounded-xl border border-line bg-surface-raised shadow-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              New virtual
            </div>
            <h2 className="mt-1 truncate font-display text-[15px] font-semibold text-ink-strong">
              {planspaceLabel}
            </h2>
            <div className="mt-1 font-mono text-[10px] text-ink-muted">
              {planspaceId}
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-2 py-1 text-[11px] font-medium text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
          >
            Esc
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto bg-surface px-5 py-4">
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-ink-subtle">
              Type
            </div>
            <div className="inline-flex rounded-md border border-line bg-surface-sunken p-0.5">
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
          </div>

          <ModalField label="Motivation">
            <textarea
              value={draft.motivation}
              onChange={(e) =>
                setDraft((current) => ({ ...current, motivation: e.target.value }))
              }
              rows={2}
              className={modalTextareaClass}
              placeholder="Why this planned node belongs in the lane"
            />
          </ModalField>

          <ModalField label="Prompt draft">
            <textarea
              value={draft.promptDraft}
              onChange={(e) =>
                setDraft((current) => ({ ...current, promptDraft: e.target.value }))
              }
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  void submit();
                }
              }}
              rows={7}
              className={modalTextareaClass + " font-mono text-[11.5px]"}
              placeholder="What should this virtual node do when promoted?"
              autoFocus
            />
          </ModalField>

          {draft.category === "review" && (
            <div className="space-y-3 rounded-md border border-state-review/25 bg-state-review-soft/20 p-3">
              <div className="inline-flex rounded-md border border-state-review/25 bg-surface p-0.5">
                {([
                  ["agentic_review", "Agentic"],
                  ["human_interact_review", "Human"],
                ] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() =>
                      setDraft((current) => ({ ...current, subtype: value }))
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
              <ModalField label="Check">
                <textarea
                  value={draft.brief.check_what}
                  onChange={(e) =>
                    setDraft((current) => ({
                      ...current,
                      brief: { ...current.brief, check_what: e.target.value },
                    }))
                  }
                  rows={2}
                  className={modalTextareaClass}
                />
              </ModalField>
              <ModalField label="Expected">
                <textarea
                  value={draft.brief.expected}
                  onChange={(e) =>
                    setDraft((current) => ({
                      ...current,
                      brief: { ...current.brief, expected: e.target.value },
                    }))
                  }
                  rows={2}
                  className={modalTextareaClass}
                />
              </ModalField>
              <ModalField label="Abnormal">
                <textarea
                  value={draft.brief.abnormal}
                  onChange={(e) =>
                    setDraft((current) => ({
                      ...current,
                      brief: { ...current.brief, abnormal: e.target.value },
                    }))
                  }
                  rows={2}
                  className={modalTextareaClass}
                />
              </ModalField>
            </div>
          )}

          {error && (
            <div className="rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-[11.5px] text-state-error">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-line px-5 py-3">
          <div className="text-[10px] text-ink-subtle">Ctrl/⌘ + Enter</div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={saving}
              className="rounded-md border border-line bg-surface px-3 py-1.5 text-[12px] font-medium text-ink-muted transition hover:border-line-strong hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void submit()}
              disabled={!canCreate}
              className="rounded-md bg-brand px-3 py-1.5 text-[12px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
            >
              {saving ? "Creating..." : "Create virtual"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ModalField({
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

const modalTextareaClass =
  "w-full resize-y rounded-md border border-line bg-surface px-2 py-1.5 text-[12px] leading-relaxed text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none";

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
  return (
    request.interaction_type === "checkpoint_review" ||
    request.interaction_type === "human_review_prose"
  );
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

function labelForPlanspace(
  contextSpace: SessionContextSpaceInfo | null,
  planspaceId: string,
): string {
  for (const binding of contextSpace?.bindings ?? []) {
    const plug = binding.plugs.find((candidate) => candidate.id === planspaceId);
    if (plug) return plug.title || planspaceId;
  }
  return planspaceId;
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
