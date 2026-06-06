import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  bootstrapSessionContextSpace,
  getNodeArtifact,
  getNodeContextBundle,
  getNodeDiff,
  getSessionContextSpace,
  listNodeEvents,
  listNodes,
  updateSessionContextSpace,
} from "./api";
import { Canvas, type CanvasSelection } from "./canvas/Canvas";
import { setPhantomContext } from "./canvas/nodes/PhantomNode";
import { SidePanel } from "./panel/SidePanel";
import { NewProjectModal } from "./components/NewProjectModal";
import { ProjectsLanding } from "./components/ProjectsLanding";
import { ThemeToggle } from "./components/ThemeToggle";
import { UsageStrip } from "./components/UsageStrip";
import { VerifyCard } from "./components/VerifyCard";
import type {
  ContextBundle,
  EventRecord,
  InteractionRequest,
  NodeDiff,
  NodeArtifact,
  NodeInfo,
  ServerEvent,
  SessionContextSpaceInfo,
  SessionInfo,
} from "./types";
import { useSessionSocket } from "./ws";

type Route = "landing" | "project";
type PendingGateState = {
  request: InteractionRequest;
  nodeId: string;
};

type OutputKind = "freeform" | "summary" | "interface" | "review_brief";

const TERMINAL_STATES = new Set<NodeInfo["state"]>(["done", "error", "cancelled"]);

export function App() {
  const [route, setRoute] = useState<Route>("landing");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);

  const [selection, setSelection] = useState<CanvasSelection>({ kind: "none" });
  /* For data-fetching purposes we track the "currently inspected nodeId" — the
   * agent/gate/op whose events/diff/bundle we should load. For artifact and
   * context selections, this stays pointed at the owning node so the artifact
   * panel can render the file content. */
  const [inspectedNodeId, setInspectedNodeId] = useState<string | null>(null);

  const [selectedEvents, setSelectedEvents] = useState<EventRecord[]>([]);
  const [selectedEventsLoading, setSelectedEventsLoading] = useState(false);
  const [selectedDiff, setSelectedDiff] = useState<NodeDiff | null>(null);
  const [selectedDiffLoading, setSelectedDiffLoading] = useState(false);
  const [selectedArtifact, setSelectedArtifact] = useState<NodeArtifact | null>(null);
  const [selectedArtifactLoading, setSelectedArtifactLoading] = useState(false);
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

  const [pendingGate, setPendingGate] = useState<PendingGateState | null>(null);
  const [pendingReview, setPendingReview] = useState<PendingGateState | null>(null);

  const [streaming, setStreaming] = useState(false);

  /* Phantom composer:
   *   undefined → no phantom in the canvas
   *   null       → fresh-start phantom
   *   string     → phantom continuing from that node */
  const [phantomFromNodeId, setPhantomFromNodeId] = useState<string | null | undefined>(undefined);

  const [newProjectModalOpen, setNewProjectModalOpen] = useState(false);

  /* ⋯ menu */
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const activeNodeIdRef = useRef<string | null>(null);
  const inspectedNodeIdRef = useRef<string | null>(null);

  useEffect(() => {
    inspectedNodeIdRef.current = inspectedNodeId;
  }, [inspectedNodeId]);

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
    setNodes([]);
    setSelection({ kind: "none" });
    setInspectedNodeId(null);
    setSelectedEvents([]);
    setSelectedDiff(null);
    setSelectedArtifact(null);
    setSelectedContextBundle(null);
    setSelectedContextBundleLoading(false);
    setContextBundlesByNodeId({});
    setSessionContextSpace(null);
    setSessionContextSpaceLoading(false);
    setSessionContextSpaceSaving(false);
    setSessionContextSpaceError(null);
    setStreaming(false);
    setPendingGate(null);
    setPendingReview(null);
    setPhantomFromNodeId(undefined);
    activeNodeIdRef.current = null;
  }, []);

  const openProject = useCallback(
    (next: SessionInfo) => {
      resetAllSessionState();
      setSession(next);
      setRoute("project");
    },
    [resetAllSessionState],
  );

  const backToLanding = useCallback(() => {
    resetAllSessionState();
    setSession(null);
    setRoute("landing");
  }, [resetAllSessionState]);

  /* selected node lookup */
  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === inspectedNodeId) ?? null,
    [nodes, inspectedNodeId],
  );

  const activePendingGate = useMemo(
    () => keepPendingForState(pendingGate, nodes, "waiting"),
    [nodes, pendingGate],
  );
  const activePendingReview = useMemo(
    () => keepPendingForState(pendingReview, nodes, "awaiting_review"),
    [nodes, pendingReview],
  );
  const composerLocked = !!activePendingGate || !!activePendingReview;

  /* refresh node list */
  const refreshNodes = useCallback(async () => {
    if (!session?.id) return;
    try {
      const next = await listNodes(session.id);
      setNodes(next);
      setInspectedNodeId((current) => current ?? next.at(-1)?.id ?? null);
    } catch (err) {
      console.error("list nodes failed:", err);
    }
  }, [session?.id]);

  useEffect(() => {
    void refreshNodes();
  }, [refreshNodes]);

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

  useEffect(() => {
    void refreshContextSpace();
  }, [refreshContextSpace]);

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

  const bootstrapContext = useCallback(async () => {
    if (!session?.id) return;
    setSessionContextSpaceSaving(true);
    setSessionContextSpaceError(null);
    try {
      const next = await bootstrapSessionContextSpace(session.id, {});
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
  }, [session?.id]);

  /* events / diff / artifact / context-bundle fetch — keyed off inspectedNodeId */
  useEffect(() => {
    if (!session?.id || !inspectedNodeId) {
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
  }, [session?.id, inspectedNodeId]);

  useEffect(() => {
    if (!session?.id || !inspectedNodeId) {
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
    if (!session?.id || !inspectedNodeId) {
      setSelectedArtifact(null);
      setSelectedArtifactLoading(false);
      return;
    }
    let cancelled = false;
    setSelectedArtifactLoading(true);
    getNodeArtifact(session.id, inspectedNodeId)
      .then((artifact) => {
        if (!cancelled) setSelectedArtifact(artifact);
      })
      .catch((err) => {
        if (!cancelled) {
          console.error("get node artifact failed:", err);
          setSelectedArtifact(null);
        }
      })
      .finally(() => {
        if (!cancelled) setSelectedArtifactLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    session?.id,
    inspectedNodeId,
    selectedNode?.output_kind,
    selectedNode?.output_path,
    selectedNode?.state,
    selectedNode?.finished_at,
  ]);

  useEffect(() => {
    if (!session?.id || !inspectedNodeId) {
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
   * loaded yet, capped to avoid hammering the backend on big projects. */
  useEffect(() => {
    if (!session?.id) return;
    const missing = nodes
      .filter(
        (n) =>
          n.kind !== "op" &&
          TERMINAL_STATES.has(n.state) &&
          n.context_bundle_id &&
          contextBundlesByNodeId[n.id] === undefined,
      )
      .slice(0, 6);
    if (missing.length === 0) return;
    let cancelled = false;
    void Promise.all(
      missing.map(async (n) => {
        try {
          const bundle = await getNodeContextBundle(session.id, n.id);
          if (cancelled) return;
          setContextBundlesByNodeId((prev) =>
            prev[n.id] !== undefined ? prev : { ...prev, [n.id]: bundle },
          );
        } catch (err) {
          console.warn("prefetch bundle failed", n.id, err);
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
          if (ev.interaction_type === "checkpoint_review") {
            setPendingReview({ request: ev, nodeId: ownerNodeId });
          } else {
            setPendingGate({ request: ev, nodeId: ownerNodeId });
          }
          setInspectedNodeId(ownerNodeId);
          setSelection({
            kind: ev.interaction_type === "checkpoint_review" ? "gate" : "agent",
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
            kind: startedKind === "gate" ? "gate" : "agent",
            nodeId: ev.node_id,
          });
        }
        /* Dismiss any phantom — the run has materialized. */
        setPhantomFromNodeId(undefined);
        void refreshNodes();
      } else if (ev.type === "node_updated") {
        eventNodeId = ev.node.id;
        setNodes((prev) => upsertNode(prev, ev.node));
        if (ev.node.state !== "waiting") {
          setPendingGate((prev) => (prev?.nodeId === ev.node.id ? null : prev));
        }
        if (ev.node.state !== "awaiting_review") {
          setPendingReview((prev) => (prev?.nodeId === ev.node.id ? null : prev));
        }
      }
      appendSelectedEvent(eventNodeId, ev);
    },
    [appendSelectedEvent, refreshNodes],
  );

  const { status, send, simulateDrop } = useSessionSocket(
    route === "project" ? (session?.id ?? null) : null,
    handleEvent,
  );
  const composerDisabled = composerLocked || streaming || status !== "open";

  /* launch via the phantom */
  const launchAgentNode = useCallback(
    (text: string, resume: string | null, outputKind: OutputKind) => {
      if (composerDisabled) return;
      setStreaming(true);
      send({
        type: "user_message",
        text,
        resume_from_node_id: resume,
        output_kind: outputKind,
      });
    },
    [composerDisabled, send],
  );

  /* Phantom callbacks wired into the module singleton */
  useEffect(() => {
    setPhantomContext({
      onSubmit: ({ prompt, resumeFromNodeId, outputKind }) => {
        launchAgentNode(prompt, resumeFromNodeId, outputKind);
      },
      onDismiss: () => setPhantomFromNodeId(undefined),
      onClearResume: () => setPhantomFromNodeId(null),
      disabled: composerDisabled,
    });
  }, [composerDisabled, launchAgentNode]);

  const onStop = () => {
    if (!streaming || status !== "open") return;
    send({ type: "interrupt" });
  };

  const onResolveReview = (payload: {
    id: string;
    decision: "write-json" | "no-op";
    path?: string;
    payload?: unknown;
    notes?: string;
  }) => {
    if (status !== "open") return;
    send({
      type: "interaction_response",
      id: payload.id,
      allow: true,
      decision: payload.decision,
      response: {
        path: payload.path,
        payload: payload.payload,
        notes: payload.notes,
      },
    });
    setPendingReview(null);
    window.setTimeout(() => {
      void refreshNodes();
    }, 250);
  };

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

  /* canvas → selection */
  const onSelectionChange = useCallback((sel: CanvasSelection) => {
    setSelection(sel);
    if (sel.kind === "agent" || sel.kind === "gate" || sel.kind === "op") {
      setInspectedNodeId(sel.nodeId);
    } else if (sel.kind === "artifact") {
      setInspectedNodeId(sel.ownerNodeId);
    } else if (sel.kind === "none") {
      /* leave inspectedNodeId so the panel can show last view? — no, clear */
    }
  }, []);

  /* select a specific node id (used by panel "jump to" affordances) */
  const onSelectNode = useCallback(
    (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      setSelection({
        kind: node.kind === "gate" ? "gate" : node.kind === "op" ? "op" : "agent",
        nodeId,
      });
      setInspectedNodeId(nodeId);
    },
    [nodes],
  );

  const openFreshPhantom = useCallback(() => {
    if (composerDisabled) return;
    setPhantomFromNodeId(null);
  }, [composerDisabled]);

  const onSpawnFromAgent = useCallback(
    (nodeId: string) => {
      if (composerDisabled) return;
      setPhantomFromNodeId(nodeId);
    },
    [composerDisabled],
  );

  const onEmptyCanvasTap = useCallback(() => {
    openFreshPhantom();
  }, [openFreshPhantom]);

  const allNodesTerminal = useMemo(
    () => nodes.length > 0 && nodes.every((n) => TERMINAL_STATES.has(n.state)),
    [nodes],
  );

  const showVerifyCard =
    !!session?.scenario_name && allNodesTerminal && !streaming;

  if (route === "landing") {
    return (
      <>
        <ProjectsLanding
          onOpen={openProject}
          onCreate={() => setNewProjectModalOpen(true)}
          onScenarioLaunched={(s) => openProject(s)}
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
                {session?.scenario_name === "reconnect-replay" && (
                  <MenuItem
                    onClick={() => {
                      simulateDrop();
                      setMenuOpen(false);
                    }}
                    disabled={status !== "open"}
                    label="Simulate WS drop"
                    hint="Test reconnect-replay"
                  />
                )}
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
          <Canvas
            nodes={nodes}
            selectedNodeId={inspectedNodeId}
            activeNodeId={activeNodeIdRef.current}
            projectTitle={projectTitle}
            phantomFromNodeId={phantomFromNodeId}
            phantomDisabled={composerDisabled}
            contextBundlesByNodeId={contextBundlesByNodeId}
            onSelectionChange={onSelectionChange}
            onEmptyCanvasTap={onEmptyCanvasTap}
            onSpawnFromAgent={onSpawnFromAgent}
          />

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

          {showVerifyCard && session && session.scenario_name && (
            <div className="absolute bottom-3 left-3 z-10 max-w-md">
              <VerifyCard
                sessionId={session.id}
                scenarioName={session.scenario_name}
              />
            </div>
          )}

          {!streaming &&
            !composerLocked &&
            phantomFromNodeId === undefined &&
            nodes.length === 0 && (
              <button
                type="button"
                onClick={openFreshPhantom}
                className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 rounded-xl border-2 border-dashed border-line-strong bg-surface-raised/80 px-6 py-5 text-center text-sm text-ink-muted shadow-card transition hover:border-brand hover:bg-surface-raised hover:text-ink-strong"
              >
                <div className="font-display text-base font-semibold text-ink-strong">
                  Start the first run
                </div>
                <div className="mt-1 text-[12px] text-ink-muted">
                  Click here to open the composer.
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
          artifact={selectedArtifact}
          artifactLoading={selectedArtifactLoading}
          diff={selectedDiff}
          diffLoading={selectedDiffLoading}
          contextBundle={selectedContextBundle}
          contextBundleLoading={selectedContextBundleLoading}
          contextBundlesByNodeId={contextBundlesByNodeId}
          contextSpace={sessionContextSpace}
          contextSpaceLoading={sessionContextSpaceLoading}
          contextSpaceSaving={sessionContextSpaceSaving}
          contextSpaceError={sessionContextSpaceError}
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
            selectedNode.state === "awaiting_review" &&
            activePendingReview.nodeId === selectedNode.id
              ? activePendingReview.request
              : null
          }
          onResolveGate={onResolveGate}
          onResolveReview={onResolveReview}
          onSelectNode={onSelectNode}
          onSpawnPhantomFromNode={onSpawnFromAgent}
          onActivatePlanspace={activatePlanspace}
          onSelectContextBinding={selectContextBinding}
          onBootstrapContextSpace={bootstrapContext}
        />
      </div>
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
  if (!pending) return null;
  const owner = nodes.find((node) => node.id === pending.nodeId);
  if (owner && owner.state !== state) return null;
  return pending;
}

function pendingBanner(
  gate: PendingGateState | null,
  review: PendingGateState | null,
  selectedId: string | null,
): { nodeId: string; label: string } | null {
  const active = review ?? gate;
  if (!active || active.nodeId === selectedId) return null;
  const labelKind =
    active.request.interaction_type === "checkpoint_review" ? "review" : "response";
  return {
    nodeId: active.nodeId,
    label: `Node ${active.nodeId.slice(0, 8)} is awaiting your ${labelKind}.`,
  };
}
