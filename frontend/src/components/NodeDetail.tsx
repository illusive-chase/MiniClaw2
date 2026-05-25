import { useEffect, useMemo, useState } from "react";
import type {
  Activity,
  ClientMessage,
  EventRecord,
  InteractionRequest,
  NodeDiff,
  NodeInfo,
  ServerEvent,
} from "../types";
import { Chat, type ChatTurn } from "./Chat";
import { GateReviewPanel } from "./GateReviewPanel";
import { PermissionDialog } from "./PermissionDialog";
import { AskUserDialog } from "./AskUserDialog";
import { PlanDialog } from "./PlanDialog";

type DetailTab =
  | "summary"
  | "transcript"
  | "diff"
  | "events"
  | "settings"
  | "gate";

export type PendingGate = {
  request: InteractionRequest;
  nodeId: string;
};

type ResolveGatePayload = Omit<
  Extract<ClientMessage, { type: "interaction_response" }>,
  "type" | "id"
>;

export function NodeDetail({
  node,
  events,
  loading,
  diff,
  diffLoading,
  onResumeFromNode,
  pendingGate,
  pendingReview,
  onResolveGate,
  onResolveReview,
}: {
  node: NodeInfo | null;
  events: EventRecord[];
  loading: boolean;
  diff: NodeDiff | null;
  diffLoading: boolean;
  onResumeFromNode?: (node: NodeInfo) => void;
  pendingGate?: PendingGate | null;
  pendingReview?: PendingGate | null;
  onResolveGate?: (id: string, payload: ResolveGatePayload) => void;
  onResolveReview?: (payload: {
    id: string;
    decision: "write-json" | "no-op";
    path?: string;
    payload?: unknown;
    notes?: string;
  }) => void;
}) {
  const reviewRequest =
    pendingReview && node && pendingReview.nodeId === node.id
      ? pendingReview.request
      : null;
  const gateRequest =
    pendingGate && node && pendingGate.nodeId === node.id
      ? pendingGate.request
      : null;
  const showReview = !!node && (node.state === "awaiting_review" || !!reviewRequest);
  const showGate = showReview || !!gateRequest;
  const [tab, setTab] = useState<DetailTab>("summary");
  const turns = useMemo(() => (node ? turnsFromEvents(node, events) : []), [node, events]);

  useEffect(() => {
    if (showGate) {
      setTab("gate");
    }
  }, [showGate, node?.id]);

  useEffect(() => {
    if (!showGate && tab === "gate") {
      setTab("summary");
    }
  }, [showGate, tab]);

  return (
    <aside className="flex w-[520px] flex-none flex-col border-l border-slate-800 bg-slate-950/70">
      <div className="border-b border-slate-800 px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
              Node detail
            </div>
            <div className="mt-1 truncate font-mono text-xs text-slate-300">
              {node?.id ?? "No node selected"}
            </div>
          </div>
          {node && (
            <div className="flex flex-none items-center gap-2">
              <button
                type="button"
                onClick={() => onResumeFromNode?.(node)}
                disabled={!canResumeNode(node)}
                className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                title={
                  canResumeNode(node)
                    ? "Use this node's provider conversation as the next launch source"
                    : "Only terminal nodes with provider sessions can be resumed"
                }
              >
                Resume
              </button>
              <span className="rounded border border-slate-800 px-2 py-1 text-[11px] text-slate-400">
                {node.state}
              </span>
            </div>
          )}
        </div>
        <div className="mt-3 flex gap-1">
          {(
            [
              "summary",
              "transcript",
              "diff",
              "events",
              "settings",
              ...(showGate ? (["gate"] as DetailTab[]) : []),
            ] as DetailTab[]
          ).map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => setTab(name)}
              className={
                "rounded px-2 py-1 text-[11px] capitalize " +
                (tab === name
                  ? "bg-slate-800 text-slate-100"
                  : name === "gate"
                    ? gateTabAccentClass(reviewRequest, gateRequest)
                    : "text-slate-500 hover:bg-slate-900 hover:text-slate-300")
              }
            >
              {name === "gate" ? gateTabLabel(reviewRequest, gateRequest) : name}
            </button>
          ))}
        </div>
      </div>

      {!node ? (
        <div className="px-4 py-6 text-sm text-slate-600">
          Select a timeline node to inspect its prompt, transcript, tools, and raw events.
        </div>
      ) : tab === "summary" ? (
        <Summary
          node={node}
          eventCount={events.length}
          loading={loading}
          showReviewBanner={showReview}
        />
      ) : tab === "transcript" ? (
        <Chat turns={turns} variant="panel" />
      ) : tab === "diff" ? (
        <DiffView diff={diff} loading={diffLoading} />
      ) : tab === "settings" ? (
        <SettingsView node={node} />
      ) : tab === "gate" && node ? (
        <GatePanel
          node={node}
          reviewRequest={reviewRequest}
          gateRequest={gateRequest}
          onResolveGate={onResolveGate}
          onResolveReview={onResolveReview}
        />
      ) : (
        <RawEvents events={events} loading={loading} />
      )}
    </aside>
  );
}

function canResumeNode(node: NodeInfo): boolean {
  return (
    Boolean(node.provider_session_id || node.sdk_session_id) &&
    (node.state === "done" || node.state === "error" || node.state === "cancelled")
  );
}

function Summary({
  node,
  eventCount,
  loading,
  showReviewBanner,
}: {
  node: NodeInfo;
  eventCount: number;
  loading: boolean;
  showReviewBanner?: boolean;
}) {
  const isOp = node.kind === "op";
  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 text-sm">
      <div className="space-y-4">
        {showReviewBanner && (
          <div className="rounded-md border border-emerald-900 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-200">
            Awaiting reviewer response — open the Review tab.
          </div>
        )}
        {isOp ? (
          <section>
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">
              Op
            </h3>
            <div className="rounded-md border border-slate-800 bg-slate-900/50 p-3 text-slate-200">
              <div className="text-xs uppercase tracking-wider text-slate-500">
                kind
              </div>
              <div className="font-mono text-sm">{node.op_kind ?? "-"}</div>
              <div className="mt-3 text-xs uppercase tracking-wider text-slate-500">
                result
              </div>
              <div className="text-sm">{node.summary ?? "-"}</div>
              <div className="mt-3 text-xs uppercase tracking-wider text-slate-500">
                commit
              </div>
              <div className="font-mono text-xs text-slate-300">
                {(node.commit_before ?? "-").slice(0, 12)} →{" "}
                {(node.commit_after ?? "-").slice(0, 12)}
              </div>
            </div>
          </section>
        ) : (
          <section>
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">
              Prompt
            </h3>
            <div className="whitespace-pre-wrap rounded-md border border-slate-800 bg-slate-900/50 p-3 text-slate-200">
              {node.prompt || "(empty prompt)"}
            </div>
          </section>
        )}

        {node.error && (
          <section>
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-rose-400">
              Error
            </h3>
            <pre className="whitespace-pre-wrap rounded-md border border-rose-950 bg-rose-950/30 p-3 text-xs text-rose-200">
              {node.error}
            </pre>
          </section>
        )}

        {!isOp && node.system_context_snapshot && (
          <section>
            <details className="rounded-md border border-slate-800 bg-slate-900/50">
              <summary className="cursor-pointer px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-slate-500 hover:text-slate-300">
                System context ({node.system_context_snapshot.length} chars)
              </summary>
              <pre className="whitespace-pre-wrap border-t border-slate-800 px-3 py-2 text-xs text-slate-300">
                {node.system_context_snapshot}
              </pre>
            </details>
          </section>
        )}

        <section>
          <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">
            Metadata
          </h3>
          <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2 text-xs">
            <dt className="text-slate-600">Kind</dt>
            <dd className="text-slate-300">{node.kind}</dd>
            <dt className="text-slate-600">Provider</dt>
            <dd className="text-slate-300">{node.provider}</dd>
            <dt className="text-slate-600">Parent</dt>
            <dd className="truncate font-mono text-slate-300">{node.parent_node_id ?? "-"}</dd>
            {!isOp && (
              <>
                <dt className="text-slate-600">Provider session</dt>
                <dd className="truncate font-mono text-slate-300">
                  {node.provider_session_id ?? node.sdk_session_id ?? "-"}
                </dd>
                <dt className="text-slate-600">Provider turn</dt>
                <dd className="truncate font-mono text-slate-300">{node.provider_turn_id ?? "-"}</dd>
              </>
            )}
            <dt className="text-slate-600">Events</dt>
            <dd className="text-slate-300">{loading ? "loading" : eventCount}</dd>
            <dt className="text-slate-600">Started</dt>
            <dd className="text-slate-300">{formatTime(node.started_at)}</dd>
            <dt className="text-slate-600">Finished</dt>
            <dd className="text-slate-300">{formatTime(node.finished_at)}</dd>
          </dl>
        </section>
      </div>
    </div>
  );
}

function DiffView({
  diff,
  loading,
}: {
  diff: NodeDiff | null;
  loading: boolean;
}) {
  if (loading) {
    return <div className="px-4 py-6 text-sm text-slate-600">Loading diff...</div>;
  }
  if (!diff) {
    return <div className="px-4 py-6 text-sm text-slate-600">No diff available.</div>;
  }
  if (diff.error) {
    return (
      <pre className="m-4 whitespace-pre-wrap rounded-md border border-rose-950 bg-rose-950/30 p-3 text-xs text-rose-200">
        {diff.error}
      </pre>
    );
  }
  if (!diff.text) {
    return <div className="px-4 py-6 text-sm text-slate-600">No file changes.</div>;
  }
  return (
    <div className="flex-1 overflow-y-auto px-4 py-4">
      <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">
        {diff.kind.replace("_", " ")}
      </div>
      <pre className="overflow-auto rounded-md border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] leading-relaxed">
        {diff.text.split("\n").map((line, index) => (
          <div key={index} className={diffLineClass(line)}>
            {line || " "}
          </div>
        ))}
      </pre>
    </div>
  );
}

function RawEvents({
  events,
  loading,
}: {
  events: EventRecord[];
  loading: boolean;
}) {
  return (
    <div className="flex-1 overflow-y-auto px-4 py-4">
      {loading ? (
        <div className="text-sm text-slate-600">Loading events...</div>
      ) : events.length === 0 ? (
        <div className="text-sm text-slate-600">No events recorded.</div>
      ) : (
        <pre className="whitespace-pre-wrap rounded-md border border-slate-800 bg-slate-950 p-3 text-[11px] leading-relaxed text-slate-400">
          {JSON.stringify(events, null, 2)}
        </pre>
      )}
    </div>
  );
}

function turnsFromEvents(node: NodeInfo, records: EventRecord[]): ChatTurn[] {
  const assistant: ChatTurn = {
    id: `${node.id}-assistant`,
    role: "assistant",
    text: "",
    activities: [],
    streaming: node.state === "running" || node.state === "waiting",
  };
  for (const record of records) {
    applyEventToTurn(assistant, record.event);
  }
  return [
    {
      id: `${node.id}-user`,
      role: "user",
      text: node.prompt,
      activities: [],
    },
    assistant,
  ];
}

function applyEventToTurn(turn: ChatTurn, event: ServerEvent) {
  if (event.type === "text_delta") {
    turn.text += event.text;
  } else if (event.type === "thinking") {
    turn.thinking = (turn.thinking ?? "") + (event.text.endsWith("\n") ? event.text : event.text + "\n");
  } else if (event.type === "activity") {
    turn.activities = mergeActivity(turn.activities, event);
  } else if (event.type === "turn_done") {
    turn.streaming = false;
  } else if (event.type === "error") {
    turn.text += `${turn.text ? "\n\n" : ""}Error: ${event.message}`;
  }
}

function mergeActivity(items: Activity[], activity: Activity): Activity[] {
  const index = items.findIndex((item) => item.id === activity.id);
  if (index < 0) return [...items, activity];
  return items.map((item, i) => (i === index ? activity : item));
}

function SettingsView({ node }: { node: NodeInfo }) {
  const snapshot = node.settings_snapshot ?? {};
  const entries = Object.entries(snapshot);
  const known = new Map(entries);
  const knownRows: Array<[string, string]> = [
    ["Provider", String(known.get("provider") ?? node.provider)],
    ["Model", String(known.get("model") ?? "(default)")],
    ["Model provider", String(known.get("model_provider") ?? "(default)")],
    ["CWD", String(known.get("cwd") ?? "(unknown)")],
    [
      "Auto-commit",
      known.get("auto_commit") === undefined ? "off" : known.get("auto_commit") ? "on" : "off",
    ],
  ];
  const extras = entries.filter(
    ([key]) => !["provider", "model", "model_provider", "cwd", "auto_commit"].includes(key),
  );
  const isSnapshotEmpty = entries.length === 0;

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 text-sm">
      <section>
        <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">
          Launch settings
        </h3>
        {isSnapshotEmpty && (
          <p className="mb-3 rounded-md border border-slate-800 bg-slate-900/30 p-3 text-xs text-slate-500">
            No snapshot recorded for this node (predates the settings-snapshot
            field). Showing live node fields where possible.
          </p>
        )}
        <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-2 text-xs">
          {knownRows.map(([label, value]) => (
            <KeyValueRow key={label} label={label} value={value} />
          ))}
        </dl>
      </section>

      {extras.length > 0 && (
        <section className="mt-5">
          <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">
            Other snapshotted settings
          </h3>
          <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-2 text-xs">
            {extras.map(([key, value]) => (
              <KeyValueRow
                key={key}
                label={key}
                value={typeof value === "string" ? value : JSON.stringify(value)}
              />
            ))}
          </dl>
        </section>
      )}

      <section className="mt-5">
        <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">
          Context
        </h3>
        <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-2 text-xs">
          <KeyValueRow
            label="System context"
            value={
              node.system_context_snapshot
                ? `${node.system_context_snapshot.length} chars`
                : "none"
            }
          />
          <KeyValueRow
            label="Context sources"
            value={
              node.context_sources.length === 0
                ? "none"
                : node.context_sources.join(", ")
            }
          />
        </dl>
      </section>
    </div>
  );
}

function KeyValueRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-slate-600">{label}</dt>
      <dd className="truncate font-mono text-slate-300" title={value}>
        {value}
      </dd>
    </>
  );
}

function GatePanel({
  node,
  reviewRequest,
  gateRequest,
  onResolveGate,
  onResolveReview,
}: {
  node: NodeInfo;
  reviewRequest: InteractionRequest | null;
  gateRequest: InteractionRequest | null;
  onResolveGate?: (id: string, payload: ResolveGatePayload) => void;
  onResolveReview?: (payload: {
    id: string;
    decision: "write-json" | "no-op";
    path?: string;
    payload?: unknown;
    notes?: string;
  }) => void;
}) {
  if (reviewRequest || node.state === "awaiting_review") {
    return (
      <GateReviewPanel
        node={node}
        pending={reviewRequest}
        onSubmit={(payload) => onResolveReview?.(payload)}
      />
    );
  }
  if (gateRequest) {
    const resolve = (payload: ResolveGatePayload) =>
      onResolveGate?.(gateRequest.id, payload);
    return (
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {gateRequest.interaction_type === "permission" ? (
          <PermissionDialog
            request={gateRequest}
            onRespond={(args) =>
              resolve({
                allow: args.allow,
                decision: args.decision ?? null,
                scope: args.scope ?? null,
                interrupt: args.interrupt ?? false,
                message: args.message ?? "",
              })
            }
          />
        ) : gateRequest.interaction_type === "ask_user" ? (
          <AskUserDialog
            request={gateRequest}
            onRespond={(answers) =>
              resolve({
                allow: true,
                updated_input: {
                  ...gateRequest.tool_input,
                  answers: toLegacyAnswers(answers),
                },
                response: { answers },
              })
            }
          />
        ) : (
          <PlanDialog
            request={gateRequest}
            onRespond={(args) =>
              resolve({
                allow: args.allow,
                clear_context: args.clearContext ?? false,
                permission_mode: args.permissionMode ?? null,
                message: args.message ?? "",
              })
            }
          />
        )}
      </div>
    );
  }
  return (
    <div className="px-4 py-6 text-sm text-slate-600">
      No pending gate for this node.
    </div>
  );
}

function gateTabLabel(
  reviewRequest: InteractionRequest | null,
  gateRequest: InteractionRequest | null,
): string {
  if (reviewRequest) return "review";
  if (gateRequest) {
    if (gateRequest.interaction_type === "permission") return "permission";
    if (gateRequest.interaction_type === "ask_user") return "ask";
    if (gateRequest.interaction_type === "plan_approval") return "plan";
  }
  return "gate";
}

function gateTabAccentClass(
  reviewRequest: InteractionRequest | null,
  gateRequest: InteractionRequest | null,
): string {
  if (reviewRequest) return "text-emerald-400 hover:bg-slate-900";
  if (gateRequest?.interaction_type === "permission")
    return "text-amber-300 hover:bg-slate-900";
  if (gateRequest?.interaction_type === "ask_user")
    return "text-sky-300 hover:bg-slate-900";
  if (gateRequest?.interaction_type === "plan_approval")
    return "text-violet-300 hover:bg-slate-900";
  return "text-emerald-400 hover:bg-slate-900";
}

function toLegacyAnswers(answers: Record<string, { answers: string[] }>) {
  return Object.fromEntries(
    Object.entries(answers).map(([key, value]) => [
      key,
      value.answers.length <= 1 ? (value.answers[0] ?? "") : value.answers,
    ]),
  );
}

function diffLineClass(line: string): string {
  if (line.startsWith("diff --git")) return "text-cyan-300";
  if (line.startsWith("+++") || line.startsWith("---")) return "text-slate-400";
  if (line.startsWith("@@")) return "text-cyan-400";
  if (line.startsWith("+")) return "text-emerald-400";
  if (line.startsWith("-")) return "text-rose-400";
  return "text-slate-400";
}

function formatTime(value?: number | null): string {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString();
}
