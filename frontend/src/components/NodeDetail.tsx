import { useMemo, useState } from "react";
import type { Activity, EventRecord, NodeDiff, NodeInfo, ServerEvent } from "../types";
import { Chat, type ChatTurn } from "./Chat";

type DetailTab = "summary" | "transcript" | "diff" | "events";

export function NodeDetail({
  node,
  events,
  loading,
  diff,
  diffLoading,
  onResumeFromNode,
}: {
  node: NodeInfo | null;
  events: EventRecord[];
  loading: boolean;
  diff: NodeDiff | null;
  diffLoading: boolean;
  onResumeFromNode?: (node: NodeInfo) => void;
}) {
  const [tab, setTab] = useState<DetailTab>("summary");
  const turns = useMemo(() => (node ? turnsFromEvents(node, events) : []), [node, events]);

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
          {(["summary", "transcript", "diff", "events"] as DetailTab[]).map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => setTab(name)}
              className={
                "rounded px-2 py-1 text-[11px] capitalize " +
                (tab === name
                  ? "bg-slate-800 text-slate-100"
                  : "text-slate-500 hover:bg-slate-900 hover:text-slate-300")
              }
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      {!node ? (
        <div className="px-4 py-6 text-sm text-slate-600">
          Select a timeline node to inspect its prompt, transcript, tools, and raw events.
        </div>
      ) : tab === "summary" ? (
        <Summary node={node} eventCount={events.length} loading={loading} />
      ) : tab === "transcript" ? (
        <Chat turns={turns} variant="panel" />
      ) : tab === "diff" ? (
        <DiffView diff={diff} loading={diffLoading} />
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
}: {
  node: NodeInfo;
  eventCount: number;
  loading: boolean;
}) {
  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 text-sm">
      <div className="space-y-4">
        <section>
          <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">
            Prompt
          </h3>
          <div className="whitespace-pre-wrap rounded-md border border-slate-800 bg-slate-900/50 p-3 text-slate-200">
            {node.prompt || "(empty prompt)"}
          </div>
        </section>

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

        {node.system_context_snapshot && (
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
            <dt className="text-slate-600">Provider session</dt>
            <dd className="truncate font-mono text-slate-300">
              {node.provider_session_id ?? node.sdk_session_id ?? "-"}
            </dd>
            <dt className="text-slate-600">Provider turn</dt>
            <dd className="truncate font-mono text-slate-300">{node.provider_turn_id ?? "-"}</dd>
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
