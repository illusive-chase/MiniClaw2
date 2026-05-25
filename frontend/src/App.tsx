import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createSession, getNodeDiff, listNodeEvents, listNodes } from "./api";
import { Chat, type ChatTurn } from "./components/Chat";
import { PermissionDialog } from "./components/PermissionDialog";
import { AskUserDialog } from "./components/AskUserDialog";
import { PlanDialog } from "./components/PlanDialog";
import { ProjectTimeline } from "./components/ProjectTimeline";
import { NodeDetail } from "./components/NodeDetail";
import type {
  Activity,
  EventRecord,
  InteractionRequest,
  NodeDiff,
  NodeInfo,
  ServerEvent,
  SessionInfo,
  Usage,
} from "./types";
import { useSessionSocket } from "./ws";

const sessionCreateInFlight = new Map<string, Promise<SessionInfo>>();

function createSessionOnce(provider: "claude" | "codex"): Promise<SessionInfo> {
  const cached = sessionCreateInFlight.get(provider);
  if (cached) return cached;
  const request = createSession({ provider }).finally(() => {
    sessionCreateInFlight.delete(provider);
  });
  sessionCreateInFlight.set(provider, request);
  return request;
}

export function App() {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [provider, setProvider] = useState<"claude" | "codex">("claude");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEvents, setSelectedEvents] = useState<EventRecord[]>([]);
  const [selectedEventsLoading, setSelectedEventsLoading] = useState(false);
  const [selectedDiff, setSelectedDiff] = useState<NodeDiff | null>(null);
  const [selectedDiffLoading, setSelectedDiffLoading] = useState(false);
  const [pending, setPending] = useState<InteractionRequest | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [input, setInput] = useState("");
  const turnIdRef = useRef(0);
  const activeNodeIdRef = useRef<string | null>(null);
  const selectedNodeIdRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSession(null);
    setNodes([]);
    setSelectedNodeId(null);
    setSelectedEvents([]);
    setSelectedDiff(null);
    activeNodeIdRef.current = null;
    createSessionOnce(provider)
      .then((next) => {
        if (!cancelled) setSession(next);
      })
      .catch(console.error);
    return () => {
      cancelled = true;
    };
  }, [provider]);

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
    if (ev.type === "text_delta") {
      setTurns((prev) => appendAssistantText(prev, ev.text));
    } else if (ev.type === "thinking") {
      setTurns((prev) => appendAssistantThinking(prev, ev.text));
    } else if (ev.type === "activity") {
      setTurns((prev) => mergeActivity(prev, ev));
    } else if (ev.type === "interaction_request") {
      setPending(ev);
      void refreshNodes();
    } else if (ev.type === "usage") {
      setUsage(ev);
    } else if (ev.type === "turn_done") {
      setStreaming(false);
      setTurns((prev) =>
        prev.map((t, i) => (i === prev.length - 1 ? { ...t, streaming: false } : t)),
      );
      void refreshNodes();
    } else if (ev.type === "error") {
      console.error("server error:", ev.message);
    } else if (ev.type === "node_started") {
      activeNodeIdRef.current = ev.node_id;
      eventNodeId = ev.node_id;
      setSelectedNodeId(ev.node_id);
      void refreshNodes();
    } else if (ev.type === "node_updated") {
      eventNodeId = ev.node.id;
      setNodes((prev) => upsertNode(prev, ev.node));
    }
    appendSelectedEvent(eventNodeId, ev);
  }, [appendSelectedEvent, refreshNodes]);

  const { status, send } = useSessionSocket(session?.id ?? null, handleEvent);

  const onSend = () => {
    const text = input.trim();
    if (!text || streaming || status !== "open") return;
    const userId = `u${++turnIdRef.current}`;
    const aId = `a${++turnIdRef.current}`;
    setTurns((prev) => [
      ...prev,
      { id: userId, role: "user", text, activities: [] },
      { id: aId, role: "assistant", text: "", activities: [], streaming: true },
    ]);
    setInput("");
    setStreaming(true);
    send({ type: "user_message", text });
  };

  const onStop = () => {
    if (!streaming || status !== "open") return;
    send({ type: "interrupt" });
  };

  const resolvePending = (
    payload: Omit<
      Extract<Parameters<typeof send>[0], { type: "interaction_response" }>,
      "type" | "id"
    >,
  ) => {
    if (!pending) return;
    send({ type: "interaction_response", id: pending.id, ...payload });
    setPending(null);
    window.setTimeout(() => {
      void refreshNodes();
    }, 250);
  };

  const interactionUI = useMemo(() => {
    if (!pending) return null;
    if (pending.interaction_type === "permission") {
      return (
        <PermissionDialog
          request={pending}
          onRespond={(args) =>
            resolvePending({
              allow: args.allow,
              decision: args.decision ?? null,
              scope: args.scope ?? null,
              interrupt: args.interrupt ?? false,
              message: args.message ?? "",
            })
          }
        />
      );
    }
    if (pending.interaction_type === "ask_user") {
      return (
        <AskUserDialog
          request={pending}
          onRespond={(answers) =>
            resolvePending({
              allow: true,
              updated_input: {
                ...pending.tool_input,
                answers: toLegacyAnswers(answers),
              },
              response: { answers },
            })
          }
        />
      );
    }
    return (
      <PlanDialog
        request={pending}
        onRespond={(args) =>
          resolvePending({
            allow: args.allow,
            clear_context: args.clearContext ?? false,
            permission_mode: args.permissionMode ?? null,
            message: args.message ?? "",
          })
        }
      />
    );
  }, [pending, refreshNodes]);

  return (
    <div className="flex h-screen flex-col bg-slate-950 text-slate-100">
      <header className="flex items-center justify-between border-b border-slate-800 px-6 py-3">
        <div>
          <div className="text-sm font-semibold">MiniClaw2</div>
          <div className="text-[11px] text-slate-500">
            session {session?.id ?? "—"} · ws {status}
          </div>
        </div>
        <div className="flex items-center gap-4">
          {usage && (
            <div className="text-[11px] text-slate-500 font-mono">
              in {usage.input_tokens} · out {usage.output_tokens} · cache r{" "}
              {usage.cache_read_tokens} / w {usage.cache_creation_tokens}
            </div>
          )}
          <select
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value as "claude" | "codex");
              setTurns([]);
              setUsage(null);
              setStreaming(false);
              setPending(null);
            }}
            disabled={streaming}
            className="rounded border border-slate-800 bg-slate-900 px-2 py-1 text-xs text-slate-300"
          >
            <option value="claude">Claude</option>
            <option value="codex">Codex</option>
          </select>
        </div>
      </header>

      <ProjectTimeline
        nodes={nodes}
        selectedNodeId={selectedNodeId}
        onSelect={setSelectedNodeId}
      />

      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          <Chat turns={turns} />

          {interactionUI && <div className="px-6 pb-3">{interactionUI}</div>}

          <div className="border-t border-slate-800 px-6 py-3">
            <div className="flex gap-2">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    onSend();
                  }
                }}
                rows={2}
                placeholder={status === "open" ? "Message agent..." : "Connecting..."}
                disabled={status !== "open" || streaming}
                className="flex-1 resize-none rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-sm focus:border-slate-600 focus:outline-none disabled:opacity-50"
              />
              {streaming ? (
                <button
                  onClick={onStop}
                  disabled={status !== "open"}
                  className="rounded-lg bg-rose-600 px-4 text-sm font-medium text-white hover:bg-rose-500 disabled:opacity-40"
                >
                  Stop
                </button>
              ) : (
                <button
                  onClick={onSend}
                  disabled={status !== "open" || !input.trim()}
                  className="rounded-lg bg-slate-100 px-4 text-sm font-medium text-slate-900 hover:bg-white disabled:opacity-40"
                >
                  Send
                </button>
              )}
            </div>
          </div>
        </main>

        <NodeDetail
          node={selectedNode}
          events={selectedEvents}
          loading={selectedEventsLoading}
          diff={selectedDiff}
          diffLoading={selectedDiffLoading}
        />
      </div>
    </div>
  );
}

function appendAssistantText(prev: ChatTurn[], text: string): ChatTurn[] {
  if (prev.length === 0) return prev;
  const last = prev[prev.length - 1];
  if (last.role !== "assistant") return prev;
  const updated = { ...last, text: last.text + text };
  return [...prev.slice(0, -1), updated];
}

function appendAssistantThinking(prev: ChatTurn[], text: string): ChatTurn[] {
  if (prev.length === 0) return prev;
  const last = prev[prev.length - 1];
  if (last.role !== "assistant") return prev;
  const updated = {
    ...last,
    thinking: (last.thinking ?? "") + (text.endsWith("\n") ? text : text + "\n"),
  };
  return [...prev.slice(0, -1), updated];
}

function mergeActivity(prev: ChatTurn[], a: Activity): ChatTurn[] {
  if (prev.length === 0) return prev;
  const last = prev[prev.length - 1];
  if (last.role !== "assistant") return prev;
  const i = last.activities.findIndex((x) => x.id === a.id);
  const next = i >= 0
    ? last.activities.map((x, idx) => (idx === i ? a : x))
    : [...last.activities, a];
  return [...prev.slice(0, -1), { ...last, activities: next }];
}

function upsertNode(prev: NodeInfo[], node: NodeInfo): NodeInfo[] {
  const index = prev.findIndex((item) => item.id === node.id);
  if (index < 0) {
    return [...prev, node].sort((a, b) => a.created_at - b.created_at);
  }
  return prev.map((item, i) => (i === index ? node : item));
}

function toLegacyAnswers(answers: Record<string, { answers: string[] }>) {
  return Object.fromEntries(
    Object.entries(answers).map(([key, value]) => [
      key,
      value.answers.length <= 1 ? (value.answers[0] ?? "") : value.answers,
    ]),
  );
}
