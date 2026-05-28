import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getNodeDiff, listNodeEvents, listNodes } from "./api";
import { canResumeNode } from "./nodeUtil";
import { Chat } from "./components/Chat";
import { ProjectTimeline } from "./components/ProjectTimeline";
import { NodeDetail, type PendingGate } from "./components/NodeDetail";
import { GateLaunchModal } from "./components/GateLaunchModal";
import { NodeLaunchModal } from "./components/NodeLaunchModal";
import { NewProjectModal } from "./components/NewProjectModal";
import { ProjectsLanding } from "./components/ProjectsLanding";
import { TestsPanel } from "./components/TestsPanel";
import { ThemeToggle } from "./components/ThemeToggle";
import { UsageStrip } from "./components/UsageStrip";
import { VerifyCard } from "./components/VerifyCard";
import type {
  EventRecord,
  NodeDiff,
  NodeInfo,
  ServerEvent,
  SessionInfo,
} from "./types";
import {
  appendServerEvent,
  createAssistantTurn,
  createUserTurn,
  type ChatTurn,
} from "./transcript";
import { useSessionSocket } from "./ws";

type View = "chat" | "tests";
type Route = "landing" | "project";

const TERMINAL_STATES = new Set(["done", "error", "cancelled"]);

export function App() {
  const [route, setRoute] = useState<Route>("landing");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [view, setView] = useState<View>("chat");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEvents, setSelectedEvents] = useState<EventRecord[]>([]);
  const [selectedEventsLoading, setSelectedEventsLoading] = useState(false);
  const [selectedDiff, setSelectedDiff] = useState<NodeDiff | null>(null);
  const [selectedDiffLoading, setSelectedDiffLoading] = useState(false);
  const [pendingGate, setPendingGate] = useState<PendingGate | null>(null);
  const [pendingReview, setPendingReview] = useState<PendingGate | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [gateModalOpen, setGateModalOpen] = useState(false);
  const [nodeModalOpen, setNodeModalOpen] = useState(false);
  const [nodeModalResumeId, setNodeModalResumeId] = useState<string | null>(null);
  const [newProjectModalOpen, setNewProjectModalOpen] = useState(false);
  const turnIdRef = useRef(0);
  const activeNodeIdRef = useRef<string | null>(null);
  const selectedNodeIdRef = useRef<string | null>(null);

  const resetAllSessionState = useCallback(() => {
    setNodes([]);
    setSelectedNodeId(null);
    setSelectedEvents([]);
    setSelectedDiff(null);
    setTurns([]);
    setStreaming(false);
    setPendingGate(null);
    setPendingReview(null);
    activeNodeIdRef.current = null;
  }, []);

  const openProject = useCallback(
    (next: SessionInfo) => {
      resetAllSessionState();
      setSession(next);
      setView("chat");
      setRoute("project");
    },
    [resetAllSessionState],
  );

  const backToLanding = useCallback(() => {
    resetAllSessionState();
    setSession(null);
    setRoute("landing");
  }, [resetAllSessionState]);

  const onScenarioLaunched = useCallback(
    (next: SessionInfo) => {
      openProject(next);
    },
    [openProject],
  );

  useEffect(() => {
    selectedNodeIdRef.current = selectedNodeId;
  }, [selectedNodeId]);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );

  const refreshNodes = useCallback(async () => {
    if (!session?.id) return;
    try {
      const next = await listNodes(session.id);
      setNodes(next);
      setSelectedNodeId((current) => current ?? next.at(-1)?.id ?? null);
    } catch (err) {
      console.error("list nodes failed:", err);
    }
  }, [session?.id]);

  useEffect(() => {
    void refreshNodes();
  }, [refreshNodes]);

  useEffect(() => {
    if (!session?.id || !selectedNodeId) {
      setSelectedEvents([]);
      setSelectedEventsLoading(false);
      return;
    }
    let cancelled = false;
    setSelectedEventsLoading(true);
    listNodeEvents(session.id, selectedNodeId)
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
  }, [session?.id, selectedNodeId]);

  useEffect(() => {
    if (!session?.id || !selectedNodeId) {
      setSelectedDiff(null);
      setSelectedDiffLoading(false);
      return;
    }
    let cancelled = false;
    setSelectedDiffLoading(true);
    getNodeDiff(session.id, selectedNodeId)
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
    selectedNodeId,
    selectedNode?.commit_before,
    selectedNode?.commit_after,
    selectedNode?.state,
  ]);

  const appendSelectedEvent = useCallback((nodeId: string | null, ev: ServerEvent) => {
    const seq = ev.seq;
    if (!nodeId || selectedNodeIdRef.current !== nodeId || typeof seq !== "number") {
      return;
    }
    setSelectedEvents((prev) => {
      if (prev.some((record) => record.seq === seq)) return prev;
      return [...prev, { seq, event: ev }].sort((a, b) => a.seq - b.seq);
    });
  }, []);

  const handleEvent = useCallback((ev: ServerEvent) => {
    let eventNodeId = activeNodeIdRef.current;
    setTurns((prev) => appendServerEvent(prev, ev));
    if (ev.type === "interaction_request") {
      const ownerNodeId = activeNodeIdRef.current;
      if (ownerNodeId) {
        if (ev.interaction_type === "checkpoint_review") {
          setPendingReview({ request: ev, nodeId: ownerNodeId });
        } else {
          setPendingGate({ request: ev, nodeId: ownerNodeId });
        }
        setSelectedNodeId(ownerNodeId);
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
        setSelectedNodeId(ev.node_id);
      }
      void refreshNodes();
    } else if (ev.type === "node_updated") {
      eventNodeId = ev.node.id;
      setNodes((prev) => upsertNode(prev, ev.node));
    }
    appendSelectedEvent(eventNodeId, ev);
  }, [appendSelectedEvent, refreshNodes]);

  const { status, send } = useSessionSocket(
    route === "project" ? (session?.id ?? null) : null,
    handleEvent,
  );

  const launchAgentNode = useCallback(
    (text: string, resume: string | null) => {
      if (streaming || status !== "open") return;
      const userId = `u${++turnIdRef.current}`;
      const aId = `a${++turnIdRef.current}`;
      setTurns((prev) => [
        ...prev,
        createUserTurn(userId, text),
        createAssistantTurn(aId, true),
      ]);
      setStreaming(true);
      send({ type: "user_message", text, resume_from_node_id: resume });
    },
    [streaming, status, send],
  );

  const onLaunchNode = useCallback(
    (prompt: string, resume: string | null) => {
      setNodeModalOpen(false);
      setNodeModalResumeId(null);
      launchAgentNode(prompt, resume);
    },
    [launchAgentNode],
  );

  const onStop = () => {
    if (!streaming || status !== "open") return;
    send({ type: "interrupt" });
  };

  const onLaunchGate = (prompt: string, contract: string) => {
    if (streaming || status !== "open") return;
    setGateModalOpen(false);
    const userId = `u${++turnIdRef.current}`;
    const aId = `a${++turnIdRef.current}`;
    setTurns((prev) => [
      ...prev,
      createUserTurn(userId, `[gate] ${prompt}`),
      createAssistantTurn(aId, true),
    ]);
    setStreaming(true);
    send({ type: "start_gate_node", prompt, contract });
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

  const pendingBanner = useMemo(() => {
    const active = pendingReview ?? pendingGate;
    if (!active || active.nodeId === selectedNodeId) return null;
    const labelKind =
      active.request.interaction_type === "checkpoint_review"
        ? "review"
        : "response";
    return {
      nodeId: active.nodeId,
      label: `Node ${active.nodeId.slice(0, 8)} is awaiting your ${labelKind}.`,
    };
  }, [pendingGate, pendingReview, selectedNodeId]);

  const openNodeModalForResume = useCallback((node: NodeInfo) => {
    if (!canResumeNode(node)) return;
    setNodeModalResumeId(node.id);
    setSelectedNodeId(node.id);
    setNodeModalOpen(true);
  }, []);

  const resumeOptions = useMemo(
    () => nodes.filter((n) => canResumeNode(n)),
    [nodes],
  );

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
      <header className="flex items-center justify-between gap-4 border-b border-line bg-surface-raised px-6 py-3">
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
            <div className="truncate font-display text-[15px] font-semibold tracking-tight text-ink-strong">
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

        <div className="flex items-center gap-3">
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

          <button
            type="button"
            onClick={() => {
              setNodeModalResumeId(null);
              setNodeModalOpen(true);
            }}
            disabled={
              streaming ||
              status !== "open" ||
              !!pendingGate ||
              !!pendingReview
            }
            className="inline-flex h-8 items-center rounded-md bg-brand px-3 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <span className="mr-1 text-sm leading-none">+</span> Node
          </button>

          <button
            type="button"
            onClick={() => setGateModalOpen(true)}
            disabled={streaming || status !== "open"}
            className="inline-flex h-8 items-center rounded-md border border-state-review/40 bg-state-review-soft px-2.5 text-xs font-medium text-state-review transition hover:border-state-review/70 disabled:opacity-40"
          >
            <span className="mr-1 text-sm leading-none">+</span> Gate
          </button>

          <div className="inline-flex h-8 overflow-hidden rounded-md border border-line text-xs">
            <button
              type="button"
              onClick={() => setView("chat")}
              className={
                view === "chat"
                  ? "bg-surface-sunken px-3 font-medium text-ink-strong"
                  : "bg-surface-raised px-3 text-ink-muted hover:bg-surface-sunken hover:text-ink"
              }
            >
              Chat
            </button>
            <button
              type="button"
              onClick={() => setView("tests")}
              className={
                view === "tests"
                  ? "bg-surface-sunken px-3 font-medium text-ink-strong"
                  : "bg-surface-raised px-3 text-ink-muted hover:bg-surface-sunken hover:text-ink"
              }
            >
              Tests
            </button>
          </div>

          <ThemeToggle />
        </div>
      </header>

      <GateLaunchModal
        open={gateModalOpen}
        onCancel={() => setGateModalOpen(false)}
        onLaunch={onLaunchGate}
      />

      <NodeLaunchModal
        open={nodeModalOpen}
        onCancel={() => {
          setNodeModalOpen(false);
          setNodeModalResumeId(null);
        }}
        onLaunch={onLaunchNode}
        resumeOptions={resumeOptions}
        presetResumeFromNodeId={nodeModalResumeId}
      />

      {view === "tests" ? (
        <div className="flex min-h-0 flex-1">
          <TestsPanel onLaunched={(s) => onScenarioLaunched(s)} />
        </div>
      ) : (
        <>
      <ProjectTimeline
        nodes={nodes}
        selectedNodeId={selectedNodeId}
        onSelect={setSelectedNodeId}
      />

      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          <Chat turns={turns} />

          {pendingBanner && (
            <div className="flex items-center justify-between gap-3 border-t border-state-waiting/30 bg-state-waiting-soft px-6 py-2 text-[11px] text-state-waiting">
              <span>{pendingBanner.label}</span>
              <button
                type="button"
                onClick={() => setSelectedNodeId(pendingBanner.nodeId)}
                className="rounded border border-state-waiting/40 bg-surface-raised px-2 py-0.5 text-state-waiting transition hover:border-state-waiting/70"
              >
                Open in side panel
              </button>
            </div>
          )}

          {showVerifyCard && session && session.scenario_name && (
            <VerifyCard
              sessionId={session.id}
              scenarioName={session.scenario_name}
            />
          )}
        </main>

        <NodeDetail
          node={selectedNode}
          events={selectedEvents}
          loading={selectedEventsLoading}
          diff={selectedDiff}
          diffLoading={selectedDiffLoading}
          onResumeFromNode={openNodeModalForResume}
          pendingGate={
            pendingGate && selectedNode && pendingGate.nodeId === selectedNode.id
              ? pendingGate
              : null
          }
          pendingReview={
            pendingReview && selectedNode && selectedNode.state === "awaiting_review"
              ? pendingReview
              : null
          }
          onResolveGate={onResolveGate}
          onResolveReview={onResolveReview}
        />
      </div>
        </>
      )}
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
