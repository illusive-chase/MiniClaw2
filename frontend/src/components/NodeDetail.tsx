import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import type {
  ClientMessage,
  EventRecord,
  InteractionRequest,
  NodeArtifact,
  NodeDiff,
  NodeInfo,
} from "../types";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { canResumeNode } from "../nodeUtil";
import { buildTurnsFromEvents } from "../transcript";
import { Chat } from "./Chat";
import { GateReviewPanel } from "./GateReviewPanel";
import { PermissionDialog } from "./PermissionDialog";
import { AskUserDialog } from "./AskUserDialog";
import { PlanDialog } from "./PlanDialog";
import { UsageStrip } from "./UsageStrip";

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
  artifact,
  artifactLoading,
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
  artifact: NodeArtifact | null;
  artifactLoading: boolean;
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
  const turns = useMemo(() => (node ? buildTurnsFromEvents(node, events) : []), [node, events]);

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
    <aside className="flex w-[520px] flex-none flex-col border-l border-line bg-surface-sunken">
      <div className="border-b border-line bg-surface px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Node detail
            </div>
            <div className="mt-1 truncate font-mono text-xs text-ink">
              {node?.id ?? "No node selected"}
            </div>
          </div>
          {node && (
            <div className="flex flex-none items-center gap-2">
              <button
                type="button"
                onClick={() => onResumeFromNode?.(node)}
                disabled={!canResumeNode(node)}
                className="rounded border border-line bg-surface px-2 py-1 text-[11px] text-ink-muted transition hover:border-line-strong hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                title={
                  canResumeNode(node)
                    ? "Use this node's provider conversation as the next launch source"
                    : "Only terminal nodes with provider sessions can be resumed"
                }
              >
                ↻ Resume
              </button>
              <StatePill state={node.state} />
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
                "rounded px-2 py-1 text-[11px] capitalize transition " +
                (tab === name
                  ? "bg-ink-strong text-surface"
                  : name === "gate"
                    ? gateTabAccentClass(reviewRequest, gateRequest)
                    : "text-ink-muted hover:bg-surface-sunken hover:text-ink")
              }
            >
              {name === "gate" ? gateTabLabel(reviewRequest, gateRequest) : name}
            </button>
          ))}
        </div>
      </div>

      {!node ? (
        <div className="px-4 py-6 text-sm text-ink-muted">
          Select a timeline node to inspect its prompt, transcript, tools, and raw events.
        </div>
      ) : tab === "summary" ? (
        <Summary
          node={node}
          eventCount={events.length}
          loading={loading}
          artifact={artifact}
          artifactLoading={artifactLoading}
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

function StatePill({ state }: { state: NodeInfo["state"] }) {
  const map: Record<
    NodeInfo["state"],
    { bg: string; text: string; label: string }
  > = {
    queued: { bg: "bg-state-queued-soft", text: "text-ink-muted", label: "queued" },
    running: { bg: "bg-state-running-soft", text: "text-brand-ink dark:text-brand", label: "running" },
    waiting: { bg: "bg-state-waiting-soft", text: "text-state-waiting", label: "waiting" },
    awaiting_review: { bg: "bg-state-review-soft", text: "text-state-review", label: "review" },
    done: { bg: "bg-state-done-soft", text: "text-ink-muted", label: "done" },
    error: { bg: "bg-state-error-soft", text: "text-state-error", label: "error" },
    cancelled: {
      bg: "bg-state-cancelled-soft",
      text: "text-ink-subtle",
      label: "cancelled",
    },
  };
  const m = map[state];
  return (
    <span
      className={
        "rounded px-2 py-1 text-[10px] font-medium uppercase tracking-[0.12em] " +
        m.bg + " " + m.text
      }
    >
      {m.label}
    </span>
  );
}

function Summary({
  node,
  eventCount,
  loading,
  artifact,
  artifactLoading,
  showReviewBanner,
}: {
  node: NodeInfo;
  eventCount: number;
  loading: boolean;
  artifact: NodeArtifact | null;
  artifactLoading: boolean;
  showReviewBanner?: boolean;
}) {
  const isOp = node.kind === "op";
  const outputKind = node.output_kind ?? "freeform";
  return (
    <div className="flex-1 overflow-y-auto bg-surface px-4 py-4 text-sm">
      <div className="space-y-4">
        {showReviewBanner && (
          <div className="rounded-md border border-state-review/30 bg-state-review-soft px-3 py-2 text-xs text-state-review">
            Awaiting reviewer response — open the Review tab.
          </div>
        )}
        {isOp ? (
          <section>
            <h3 className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Op
            </h3>
            <div className="rounded-md border border-line bg-surface-sunken p-3 text-ink-strong">
              <div className="text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
                kind
              </div>
              <div className="font-mono text-sm">{node.op_kind ?? "-"}</div>
              <div className="mt-3 text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
                result
              </div>
              <div className="text-sm">{node.summary ?? "-"}</div>
              <div className="mt-3 text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
                commit
              </div>
              <div className="font-mono text-xs text-ink">
                {(node.commit_before ?? "-").slice(0, 12)} →{" "}
                {(node.commit_after ?? "-").slice(0, 12)}
              </div>
            </div>
          </section>
        ) : outputKind !== "freeform" ? (
          <OutputArtifact
            node={node}
            artifact={artifact}
            loading={artifactLoading}
          />
        ) : (
          <section>
            <h3 className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Prompt
            </h3>
            <div className="whitespace-pre-wrap rounded-md border border-line bg-surface-sunken p-3 text-ink-strong">
              {node.prompt || "(empty prompt)"}
            </div>
          </section>
        )}

        {node.error && (
          <section>
            <h3 className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-state-error">
              Error
            </h3>
            <pre className="whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft p-3 text-xs text-state-error">
              {node.error}
            </pre>
          </section>
        )}

        {!isOp && node.system_context_snapshot && (
          <section>
            <details className="rounded-md border border-line bg-surface-sunken">
              <summary className="cursor-pointer px-3 py-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted hover:text-ink">
                System context ({node.system_context_snapshot.length} chars)
              </summary>
              <pre className="whitespace-pre-wrap border-t border-line px-3 py-2 text-xs text-ink">
                {node.system_context_snapshot}
              </pre>
            </details>
          </section>
        )}

        {!isOp && node.output_contract_snapshot && (
          <section>
            <details className="rounded-md border border-line bg-surface-sunken">
              <summary className="cursor-pointer px-3 py-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted hover:text-ink">
                Output contract ({node.output_contract_snapshot.length} chars)
              </summary>
              <div className="md-prose border-t border-line px-3 py-2 text-xs text-ink">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {node.output_contract_snapshot}
                </ReactMarkdown>
              </div>
            </details>
          </section>
        )}

        <section>
          <h3 className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            Metadata
          </h3>
          <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2 text-xs">
            <dt className="text-ink-subtle">Kind</dt>
            <dd className="text-ink">{node.kind}</dd>
            {!isOp && (
              <>
                <dt className="text-ink-subtle">Output</dt>
                <dd className="truncate font-mono text-ink" title={node.output_path ?? undefined}>
                  {outputKind}
                  {node.output_path ? ` · ${node.output_path}` : ""}
                </dd>
              </>
            )}
            <dt className="text-ink-subtle">Provider</dt>
            <dd className="text-ink">{node.provider}</dd>
            <dt className="text-ink-subtle">Parent</dt>
            <dd className="truncate font-mono text-ink">{node.parent_node_id ?? "-"}</dd>
            {!isOp && (
              <>
                <dt className="text-ink-subtle">Provider session</dt>
                <dd className="truncate font-mono text-ink">
                  {node.provider_session_id ?? node.sdk_session_id ?? "-"}
                </dd>
                <dt className="text-ink-subtle">Provider turn</dt>
                <dd className="truncate font-mono text-ink">{node.provider_turn_id ?? "-"}</dd>
              </>
            )}
            <dt className="text-ink-subtle">Usage</dt>
            <dd className="text-ink">
              <UsageStrip usage={node.usage ?? null} className="text-[11px]" />
            </dd>
            <dt className="text-ink-subtle">Events</dt>
            <dd className="text-ink">{loading ? "loading" : eventCount}</dd>
            <dt className="text-ink-subtle">Started</dt>
            <dd className="text-ink">{formatTime(node.started_at)}</dd>
            <dt className="text-ink-subtle">Finished</dt>
            <dd className="text-ink">{formatTime(node.finished_at)}</dd>
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
    return <div className="bg-surface px-4 py-6 text-sm text-ink-muted">Loading diff...</div>;
  }
  if (!diff) {
    return <div className="bg-surface px-4 py-6 text-sm text-ink-muted">No diff available.</div>;
  }
  if (diff.error) {
    return (
      <pre className="m-4 whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft p-3 text-xs text-state-error">
        {diff.error}
      </pre>
    );
  }
  if (!diff.text) {
    return <div className="bg-surface px-4 py-6 text-sm text-ink-muted">No file changes.</div>;
  }
  return (
    <div className="flex-1 overflow-y-auto bg-surface px-4 py-4">
      <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
        {diff.kind.replace("_", " ")}
      </div>
      <pre className="overflow-auto rounded-md border border-line bg-surface-sunken p-3 font-mono text-[11px] leading-relaxed">
        {diff.text.split("\n").map((line, index) => (
          <div key={index} className={diffLineClass(line)}>
            {line || " "}
          </div>
        ))}
      </pre>
    </div>
  );
}

function OutputArtifact({
  node,
  artifact,
  loading,
}: {
  node: NodeInfo;
  artifact: NodeArtifact | null;
  loading: boolean;
}) {
  if (loading) {
    return <div className="bg-surface px-4 py-6 text-sm text-ink-muted">Loading artifact...</div>;
  }
  if (!artifact) {
    return <div className="bg-surface px-4 py-6 text-sm text-ink-muted">No artifact available.</div>;
  }
  if (artifact.error) {
    return (
      <pre className="m-4 whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft p-3 text-xs text-state-error">
        {artifact.error}
      </pre>
    );
  }
  if (!artifact.exists) {
    return (
      <div className="bg-surface px-4 py-6 text-sm text-ink-muted">
        {artifact.path
          ? `Expected artifact at ${artifact.path}`
          : "No artifact path configured."}
      </div>
    );
  }
  if (node.output_kind === "interface") {
    return (
      <div className="flex-1 overflow-y-auto bg-surface px-4 py-4 text-sm">
        <section className="space-y-3">
          <div className="rounded-md border border-line bg-surface-sunken p-3">
            <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              JSON
            </div>
            <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-ink">
              {artifact.content || "{}"}
            </pre>
          </div>
          {Boolean(artifact.data) &&
            typeof artifact.data === "object" &&
            !Array.isArray(artifact.data) && (
            <div className="rounded-md border border-line bg-surface-sunken p-3 text-xs text-ink">
              <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2">
                {Object.entries(artifact.data as Record<string, unknown>).map(([key, value]) => (
                  <NodeArtifactRow key={key} label={key} value={formatArtifactValue(value)} />
                ))}
              </dl>
            </div>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto bg-surface px-4 py-4 text-sm">
      <section className="space-y-3">
        <div className="md-prose rounded-md border border-line bg-surface-sunken p-3 text-ink-strong">
          <div className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            Markdown
          </div>
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {artifact.content || ""}
          </ReactMarkdown>
        </div>
      </section>
    </div>
  );
}

function NodeArtifactRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-ink-subtle">{label}</dt>
      <dd className="whitespace-pre-wrap break-words font-mono text-ink" title={value}>
        {value}
      </dd>
    </>
  );
}

function formatArtifactValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return JSON.stringify(value, null, 2);
}

function RawEvents({
  events,
  loading,
}: {
  events: EventRecord[];
  loading: boolean;
}) {
  return (
    <div className="flex-1 overflow-y-auto bg-surface px-4 py-4">
      {loading ? (
        <div className="text-sm text-ink-muted">Loading events...</div>
      ) : events.length === 0 ? (
        <div className="text-sm text-ink-muted">No events recorded.</div>
      ) : (
        <pre className="whitespace-pre-wrap rounded-md border border-line bg-surface-sunken p-3 text-[11px] leading-relaxed text-ink-muted">
          {JSON.stringify(events, null, 2)}
        </pre>
      )}
    </div>
  );
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
    <div className="flex-1 overflow-y-auto bg-surface px-4 py-4 text-sm">
      <section>
        <h3 className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Launch settings
        </h3>
        {isSnapshotEmpty && (
          <p className="mb-3 rounded-md border border-line bg-surface-sunken p-3 text-xs text-ink-muted">
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
          <h3 className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
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
        <h3 className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
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
      <dt className="text-ink-subtle">{label}</dt>
      <dd className="truncate font-mono text-ink" title={value}>
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
      <div className="flex-1 overflow-y-auto bg-surface px-4 py-4">
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
    <div className="bg-surface px-4 py-6 text-sm text-ink-muted">
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
  if (reviewRequest)
    return "text-state-review hover:bg-surface-sunken";
  if (gateRequest?.interaction_type === "permission")
    return "text-state-waiting hover:bg-surface-sunken";
  if (gateRequest?.interaction_type === "ask_user")
    return "text-brand hover:bg-surface-sunken";
  if (gateRequest?.interaction_type === "plan_approval")
    return "text-state-review hover:bg-surface-sunken";
  return "text-state-review hover:bg-surface-sunken";
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
  if (line.startsWith("diff --git")) return "text-brand-ink dark:text-brand";
  if (line.startsWith("+++") || line.startsWith("---")) return "text-ink-muted";
  if (line.startsWith("@@")) return "text-brand";
  if (line.startsWith("+")) return "text-state-review dark:text-state-review";
  if (line.startsWith("-")) return "text-state-error";
  return "text-ink-muted";
}

function formatTime(value?: number | null): string {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString();
}
