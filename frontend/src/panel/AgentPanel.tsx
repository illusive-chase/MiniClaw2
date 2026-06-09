import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

import { getNodeStatusDelta } from "../api";
import type {
  Activity,
  ClientMessage,
  ContextBundle,
  ContextBundleSource,
  EventRecord,
  InteractionRequest,
  NodeStatusDelta,
  NodeInfo,
} from "../types";
import { buildTurnsFromEvents } from "../transcript";
import { ToolActivity } from "../components/ToolActivity";
import { AskUserDialog } from "../components/AskUserDialog";
import { PermissionDialog } from "../components/PermissionDialog";
import { PlanDialog } from "../components/PlanDialog";
import { canResumeNode } from "../nodeUtil";
import { InspectDrawer } from "./InspectDrawer";

type ResolveGatePayload = Omit<
  Extract<ClientMessage, { type: "interaction_response" }>,
  "type" | "id"
>;

export type AgentPanelProps = {
  sessionId: string;
  node: NodeInfo;
  events: EventRecord[];
  eventsLoading: boolean;
  contextBundle: ContextBundle | null;
  contextBundleLoading: boolean;
  pendingGate: InteractionRequest | null;
  onResolveGate?: (id: string, payload: ResolveGatePayload) => void;
  onSpawnPhantomFromNode: (nodeId: string) => void;
};

type PlanspaceUpdateSummary = {
  planspace_id?: string;
  applied?: number;
  proposed?: number;
  ignored?: number;
  reason?: string;
  source?: string;
  event_type?: string;
  staged?: boolean;
  staged_path?: string;
};

/**
 * Single progressive panel for a selected agent run.
 *
 * Three primary cards: Agent Input → Planspace Change → Activity.
 * Thinking and raw fields live behind disclosures.
 */
export function AgentPanel({
  sessionId,
  node,
  events,
  eventsLoading,
  contextBundle,
  contextBundleLoading,
  pendingGate,
  onResolveGate,
  onSpawnPhantomFromNode,
}: AgentPanelProps) {
  const turns = useMemo(() => buildTurnsFromEvents(node, events), [node, events]);
  const planspaceUpdate = (node.settings_snapshot?.planspace_update ?? null) as
    | PlanspaceUpdateSummary
    | null;
  const headline = (node.summary || node.prompt || "(no prompt)").trim();
  const resumeParentLabel = node.parent_node_id ? node.parent_node_id.slice(0, 8) : null;

  /* Interleaved transcript items: text + tool activity in chronological order. */
  const transcriptItems = useMemo(() => flattenTranscript(turns), [turns]);
  const toolCallCount = useMemo(
    () => transcriptItems.reduce((n, item) => n + (item.kind === "tools" ? item.items.length : 0), 0),
    [transcriptItems],
  );

  const [statusDelta, setStatusDelta] = useState<NodeStatusDelta | null>(null);
  const [statusDeltaLoading, setStatusDeltaLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setStatusDeltaLoading(true);
    getNodeStatusDelta(sessionId, node.id)
      .then((next) => {
        if (!cancelled) setStatusDelta(next);
      })
      .catch((err) => {
        if (!cancelled) {
          console.warn("get node status delta failed:", err);
          setStatusDelta(null);
        }
      })
      .finally(() => {
        if (!cancelled) setStatusDeltaLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, node.id, node.finished_at, node.settings_snapshot]);

  const activityDefaultOpen = !isTerminal(node.state) || transcriptItems.length > 0;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Headline */}
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <StatePill state={node.state} />
            <h2 className="mt-1.5 line-clamp-2 font-display text-[15px] font-semibold leading-snug text-ink-strong">
              {headline}
            </h2>
            {resumeParentLabel && node.parent_node_id && (
              <div className="mt-1 text-[11px] text-ink-muted">
                ↻ continuing from{" "}
                <span className="font-mono">{resumeParentLabel}</span>
              </div>
            )}
          </div>
          {canResumeNode(node) && (
            <button
              type="button"
              onClick={() => onSpawnPhantomFromNode(node.id)}
              className="flex-none rounded-md border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-muted transition hover:border-line-strong hover:text-ink"
              title="Start a follow-up run continuing this conversation"
            >
              ↻ Follow up
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        {/* Pending gate (rendered inline at top when present) */}
        {pendingGate && (
          <section className="mb-4 rounded-md border border-state-waiting/40 bg-state-waiting-soft/40 p-3">
            <SectionHeading tone="waiting">Pending response</SectionHeading>
            <div className="mt-2">
              <PendingGateInline
                node={node}
                pending={pendingGate}
                onResolve={(payload) => onResolveGate?.(pendingGate.id, payload)}
              />
            </div>
          </section>
        )}

        {/* Agent input */}
        <section className="mb-5">
          <AgentInputCard
            node={node}
            contextBundle={contextBundle}
            loading={contextBundleLoading}
          />
        </section>

        {/* Planspace change */}
        <section className="mb-5">
          <PlanspaceChangeCard
            node={node}
            update={planspaceUpdate}
            statusDelta={statusDelta}
            loading={statusDeltaLoading}
          />
        </section>

        {/* Activity — text and tool use interleaved */}
        <section className="mb-5">
          <details
            open={activityDefaultOpen}
            className="overflow-hidden rounded-md border border-line bg-surface-sunken"
          >
            <summary className="cursor-pointer px-3 py-2">
              <SectionHeading
                right={
                  <span className="text-[10px] font-normal normal-case tracking-normal text-ink-subtle">
                    {eventsLoading
                      ? "loading..."
                      : `${toolCallCount} tool ${toolCallCount === 1 ? "call" : "calls"} · ${events.length} events`}
                  </span>
                }
              >
                Activity
              </SectionHeading>
            </summary>
            <div className="border-t border-line px-3 py-3">
              <ActivityTranscript
                items={transcriptItems}
                streaming={node.state === "running"}
              />
            </div>
          </details>
        </section>

        {/* Thinking blocks — separate section, collapsed by default */}
        <ThinkingSection turns={turns} />

        {/* Inspect drawer */}
        <section className="mb-2">
          <InspectDrawer
            node={node}
            contextBundle={contextBundle}
            contextBundleLoading={contextBundleLoading}
            eventCount={events.length}
          />
        </section>
      </div>
    </div>
  );
}

function SectionHeading({
  children,
  right,
  tone,
}: {
  children: React.ReactNode;
  right?: React.ReactNode;
  tone?: "waiting" | "review" | "error";
}) {
  const color =
    tone === "waiting"
      ? "text-state-waiting"
      : tone === "review"
        ? "text-state-review"
        : tone === "error"
          ? "text-state-error"
          : "text-ink-subtle";
  return (
    <div
      className={
        "flex items-center justify-between text-[10px] font-medium uppercase tracking-[0.14em] " +
        color
      }
    >
      <span>{children}</span>
      {right}
    </div>
  );
}

/* ─────────────────────────────────────────── */
/* Agent input card                              */
/* ─────────────────────────────────────────── */

function AgentInputCard({
  node,
  contextBundle,
  loading,
}: {
  node: NodeInfo;
  contextBundle: ContextBundle | null;
  loading: boolean;
}) {
  const systemSources = useMemo(
    () => (contextBundle?.sources ?? []).filter((s) => s.injection === "system"),
    [contextBundle],
  );
  const turnSources = useMemo(
    () => (contextBundle?.sources ?? []).filter((s) => s.injection === "turn"),
    [contextBundle],
  );
  const systemText =
    contextBundle?.system_text?.trim() || node.system_context_snapshot?.trim() || "";
  const turnText = contextBundle?.turn_text?.trim() || "";
  const userPrompt = node.prompt?.trim() || "";

  return (
    <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
      <div className="border-b border-line px-3 py-2">
        <SectionHeading
          right={
            loading ? (
              <span className="text-[10px] font-normal normal-case tracking-normal text-ink-subtle">
                loading…
              </span>
            ) : null
          }
        >
          Agent input
        </SectionHeading>
      </div>
      <div className="space-y-2 px-3 py-3">
        <PromptBlock
          label="System prompt"
          text={systemText}
          sources={systemSources}
        />
        <PromptBlock
          label="Input prompt"
          text={userPrompt}
          extras={turnText ? [{ label: "turn injection", text: turnText }] : []}
          sources={turnSources}
        />
      </div>
    </div>
  );
}

function PromptBlock({
  label,
  text,
  extras = [],
  sources = [],
}: {
  label: string;
  text: string;
  extras?: Array<{ label: string; text: string }>;
  sources?: ContextBundleSource[];
}) {
  const totalChars = text.length + extras.reduce((n, e) => n + e.text.length, 0);
  const fileSources = sources.filter((s) => s.path);
  return (
    <details className="overflow-hidden rounded border border-line bg-surface">
      <summary className="flex cursor-pointer items-center justify-between gap-2 px-3 py-1.5">
        <span className="text-[11px] font-medium text-ink">{label}</span>
        <span className="font-mono text-[10.5px] text-ink-subtle">
          {fileSources.length > 0 ? `${fileSources.length} file${fileSources.length === 1 ? "" : "s"} · ` : ""}
          {totalChars} chars
        </span>
      </summary>
      <div className="border-t border-line px-3 py-2">
        {text ? (
          <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink">
            {renderWithFootnotes(text, fileSources)}
          </pre>
        ) : (
          <div className="text-[11px] text-ink-muted">No content.</div>
        )}
        {extras.map((extra, i) => (
          <div key={i} className="mt-2 border-t border-line/60 pt-2">
            <div className="mb-1 text-[10px] uppercase tracking-[0.12em] text-ink-subtle">
              {extra.label}
            </div>
            <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink">
              {extra.text}
            </pre>
          </div>
        ))}
        {fileSources.length > 0 && (
          <ol className="mt-3 list-none space-y-0.5 border-t border-line/60 pt-2 font-mono text-[10.5px] text-ink-muted">
            {fileSources.map((src, i) => (
              <li key={`${src.path}-${i}`} className="flex gap-2">
                <span className="flex-none text-ink-subtle">[^{i + 1}]</span>
                <span className="min-w-0 truncate" title={src.path}>
                  {src.path}
                  <span className="ml-1 text-ink-subtle">
                    · {src.scope}/{src.kind}
                    {src.plug_id ? ` · ${src.plug_id}` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </details>
  );
}

function renderWithFootnotes(text: string, sources: ContextBundleSource[]): string {
  if (sources.length === 0) return text;
  /* The backend writes `# Loaded Context: <plug_id or path> (<kind>)` headers
   * above each injected source. Attach a footnote marker to the matching
   * header so readers can jump to the references list below. */
  let out = text;
  sources.forEach((src, i) => {
    const marker = `[^${i + 1}]`;
    if (out.includes(marker)) return;
    for (const candidate of [src.plug_id, src.path].filter(Boolean) as string[]) {
      const re = new RegExp(
        `^(# Loaded Context:\\s+${escapeRegExp(candidate)}[^\\n]*)$`,
        "m",
      );
      if (re.test(out)) {
        out = out.replace(re, `$1 ${marker}`);
        break;
      }
    }
  });
  return out;
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/* ─────────────────────────────────────────── */
/* Planspace change card                         */
/* ─────────────────────────────────────────── */

function PlanspaceChangeCard({
  node,
  update,
  statusDelta,
  loading,
}: {
  node: NodeInfo;
  update: PlanspaceUpdateSummary | null;
  statusDelta: NodeStatusDelta | null;
  loading: boolean;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-line bg-surface-sunken">
      <div className="border-b border-line px-3 py-2">
        <SectionHeading
          right={
            statusDelta ? (
              <span className="text-[10px] font-normal normal-case tracking-normal text-ink-subtle">
                {new Date(statusDelta.applied_at * 1000).toLocaleString()}
              </span>
            ) : null
          }
        >
          Planspace change
        </SectionHeading>
      </div>
      <div className="px-3 py-3">
        {node.error ? (
          <pre className="whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft p-3 text-xs text-state-error">
            {node.error}
          </pre>
        ) : (
          <PlanspaceChangeBody
            update={update}
            statusDelta={statusDelta}
            loading={loading}
            node={node}
          />
        )}
      </div>
    </div>
  );
}

function PlanspaceChangeBody({
  node,
  update,
  statusDelta,
  loading,
}: {
  node: NodeInfo;
  update: PlanspaceUpdateSummary | null;
  statusDelta: NodeStatusDelta | null;
  loading: boolean;
}) {
  const fields = useMemo(
    () =>
      statusDelta ? buildFieldChanges(statusDelta.before, statusDelta.after) : [],
    [statusDelta],
  );
  if (statusDelta) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2 font-mono text-[11px] text-ink-muted">
          <span>{statusDelta.planspace_id}</span>
        </div>
        {fields.length === 0 ? (
          <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
            No field-level changes detected.
          </div>
        ) : (
          <div className="space-y-1.5">
            {fields.map((f) => (
              <FieldChangeCard key={f.field} change={f} />
            ))}
          </div>
        )}
        <details className="mt-2 overflow-hidden rounded border border-line bg-surface">
          <summary className="cursor-pointer px-3 py-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted hover:text-ink">
            View raw STATUS diff
          </summary>
          <RawStatusDiff before={statusDelta.before} after={statusDelta.after} />
        </details>
      </div>
    );
  }
  if (loading && update && (update.applied || update.proposed)) {
    return (
      <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
        Loading STATUS delta…
      </div>
    );
  }
  if (node.requires_review && update?.staged) {
    return (
      <div className="rounded-md border border-state-waiting/30 bg-state-waiting-soft/30 px-3 py-2 text-[12px] text-ink">
        <div className="font-medium text-ink-strong">Staged for review</div>
        <div className="mt-0.5 text-[11.5px] text-ink-muted">
          Interim update parked in{" "}
          <span className="font-mono">{update.planspace_id ?? "—"}</span>
          {"; the gate's resolution will merge the user's judgment back into the planspace."}
        </div>
      </div>
    );
  }
  if (update && (update.applied || update.proposed)) {
    const planspaceId = update.planspace_id ?? "—";
    return (
      <div className="rounded-md border border-line bg-surface px-3 py-2 text-[12px] text-ink-strong">
        <div className="font-mono text-[11px] text-ink-muted">{planspaceId}</div>
        <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
          <span className="rounded bg-state-review-soft px-1.5 py-0.5 text-state-review">
            applied · {update.applied ?? 0}
          </span>
          <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-ink-muted">
            proposed · {update.proposed ?? 0}
          </span>
          {update.ignored !== undefined && update.ignored > 0 && (
            <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-ink-subtle">
              ignored · {update.ignored}
            </span>
          )}
        </div>
      </div>
    );
  }
  if (update?.reason) {
    return (
      <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
        No planspace update applied:{" "}
        <span className="font-mono text-ink-subtle">{update.reason}</span>
      </div>
    );
  }
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
      No planspace updates recorded for this run.
    </div>
  );
}

type FieldChange = {
  field: string;
  label: string;
  before: string;
  after: string;
  added: number;
  removed: number;
};

const FIELD_LABELS: Record<string, string> = {
  goal: "goal",
  current_state: "current state",
  open_questions: "open questions",
  decisions: "decisions",
  out_of_scope: "out of scope",
  body: "notes",
};

const FRONTMATTER_FIELDS = [
  "goal",
  "current_state",
  "open_questions",
  "decisions",
  "out_of_scope",
] as const;

function buildFieldChanges(before: string, after: string): FieldChange[] {
  const a = parseStatus(before);
  const b = parseStatus(after);
  const out: FieldChange[] = [];
  for (const field of FRONTMATTER_FIELDS) {
    const beforeText = a[field] ?? "";
    const afterText = b[field] ?? "";
    if (beforeText === afterText) continue;
    const { added, removed } = lineDiffStat(beforeText, afterText);
    out.push({
      field,
      label: FIELD_LABELS[field] ?? field,
      before: beforeText,
      after: afterText,
      added,
      removed,
    });
  }
  const beforeBody = a.body ?? "";
  const afterBody = b.body ?? "";
  if (beforeBody !== afterBody) {
    const { added, removed } = lineDiffStat(beforeBody, afterBody);
    out.push({
      field: "body",
      label: FIELD_LABELS.body,
      before: beforeBody,
      after: afterBody,
      added,
      removed,
    });
  }
  return out;
}

function FieldChangeCard({ change }: { change: FieldChange }) {
  return (
    <details className="overflow-hidden rounded border border-line bg-surface">
      <summary className="flex cursor-pointer items-center justify-between gap-2 px-3 py-2">
        <span className="text-[12px] font-medium text-ink-strong">{change.label}</span>
        <span className="font-mono text-[11px]">
          <span className="text-state-review">+{change.added}</span>
          <span className="ml-1.5 text-state-error">-{change.removed}</span>
        </span>
      </summary>
      <div className="border-t border-line">
        <div className="border-b border-line/60 px-3 py-2">
          <div className="mb-1 text-[10px] uppercase tracking-[0.12em] text-ink-subtle">
            raw
          </div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink-muted">
            {change.before || <span className="italic text-ink-subtle">empty</span>}
          </pre>
        </div>
        <div className="px-3 py-2">
          <div className="mb-1 text-[10px] uppercase tracking-[0.12em] text-ink-subtle">
            current
          </div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink">
            {change.after || <span className="italic text-ink-subtle">empty</span>}
          </pre>
        </div>
      </div>
    </details>
  );
}

function lineDiffStat(before: string, after: string): { added: number; removed: number } {
  if (before === after) return { added: 0, removed: 0 };
  const rows = makeLineDiff(before, after);
  let added = 0;
  let removed = 0;
  for (const row of rows) {
    if (row.kind === "add") added += 1;
    else if (row.kind === "remove") removed += 1;
  }
  return { added, removed };
}

type ParsedStatus = {
  goal: string;
  current_state: string;
  open_questions: string;
  decisions: string;
  out_of_scope: string;
  body: string;
};

const FRONTMATTER_RE = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/;

function parseStatus(text: string): ParsedStatus {
  const empty: ParsedStatus = {
    goal: "",
    current_state: "",
    open_questions: "",
    decisions: "",
    out_of_scope: "",
    body: "",
  };
  if (!text) return empty;
  const match = FRONTMATTER_RE.exec(text);
  if (!match) {
    return { ...empty, body: text.trim() };
  }
  const yaml = match[1];
  const body = match[2].trim();
  const parsed = parseYamlFields(yaml);
  return {
    goal: parsed.goal ?? "",
    current_state: parsed.current_state ?? "",
    open_questions: parsed.open_questions ?? "",
    decisions: parsed.decisions ?? "",
    out_of_scope: parsed.out_of_scope ?? "",
    body,
  };
}

/**
 * Slice a YAML document into top-level field text blocks, keyed by field name.
 *
 * We don't actually parse YAML semantically — we just collect each top-level
 * key's literal lines so a humanly-meaningful before/after diff can be shown
 * per field. Works for both scalar values and list values.
 */
function parseYamlFields(yaml: string): Record<string, string> {
  const lines = yaml.split("\n");
  const out: Record<string, string[]> = {};
  let current: string | null = null;
  for (const raw of lines) {
    const topKey = /^([A-Za-z_][A-Za-z0-9_]*)\s*:(.*)$/.exec(raw);
    if (topKey) {
      current = topKey[1];
      const rest = topKey[2].trim();
      out[current] = rest ? [rest] : [];
      continue;
    }
    if (current) {
      /* Continuation lines: indented or list items. Strip one level of indent
       * for readability. */
      const stripped = raw.replace(/^( {2}|\t)/, "");
      out[current].push(stripped);
    }
  }
  const result: Record<string, string> = {};
  for (const [k, v] of Object.entries(out)) {
    result[k] = v.join("\n").trim();
  }
  return result;
}

/* ─────────────────────────────────────────── */
/* Activity transcript (interleaved)            */
/* ─────────────────────────────────────────── */

type TranscriptItem =
  | { kind: "user"; id: string; text: string }
  | { kind: "text"; id: string; text: string }
  | { kind: "error"; id: string; text: string }
  | { kind: "tools"; id: string; items: Activity[] };

function flattenTranscript(turns: ReturnType<typeof buildTurnsFromEvents>): TranscriptItem[] {
  const out: TranscriptItem[] = [];
  for (const turn of turns) {
    if (turn.role === "user") {
      const trimmed = turn.text.trim();
      if (trimmed) {
        out.push({ kind: "user", id: turn.id, text: trimmed });
      }
      continue;
    }
    for (const block of turn.blocks) {
      if (block.kind === "text") {
        if (block.text.trim()) out.push({ kind: "text", id: block.id, text: block.text });
      } else if (block.kind === "activity") {
        if (block.items.length > 0) out.push({ kind: "tools", id: block.id, items: block.items });
      } else if (block.kind === "error") {
        out.push({ kind: "error", id: block.id, text: block.text });
      }
      /* thinking handled separately */
    }
  }
  return out;
}

function ActivityTranscript({
  items,
  streaming,
}: {
  items: TranscriptItem[];
  streaming: boolean;
}) {
  if (items.length === 0) {
    return (
      <div className="rounded-md border border-line bg-surface px-3 py-2 text-[11.5px] text-ink-muted">
        {streaming ? (
          <span className="inline-flex items-center gap-1 text-ink-subtle">
            <span className="stream-dot inline-block h-1.5 w-1.5 rounded-full bg-current" />
            <span
              className="stream-dot inline-block h-1.5 w-1.5 rounded-full bg-current"
              style={{ animationDelay: "0.18s" }}
            />
            <span
              className="stream-dot inline-block h-1.5 w-1.5 rounded-full bg-current"
              style={{ animationDelay: "0.36s" }}
            />
          </span>
        ) : (
          "No activity yet."
        )}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {items.map((item) => (
        <TranscriptItemView key={item.id} item={item} />
      ))}
    </div>
  );
}

function TranscriptItemView({ item }: { item: TranscriptItem }) {
  if (item.kind === "user") {
    return (
      <div className="rounded-md border border-line bg-surface px-3 py-2 text-[12px] text-ink-muted">
        <div className="mb-1 text-[10px] uppercase tracking-[0.12em] text-ink-subtle">
          user
        </div>
        <pre className="whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-ink">
          {item.text}
        </pre>
      </div>
    );
  }
  if (item.kind === "text") {
    return (
      <div className="md-prose rounded-md border border-line bg-surface-raised px-3 py-2 text-[13px] leading-relaxed text-ink-strong">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        >
          {item.text}
        </ReactMarkdown>
      </div>
    );
  }
  if (item.kind === "error") {
    return (
      <pre className="whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft p-2 text-[11.5px] text-state-error">
        {item.text}
      </pre>
    );
  }
  return <ToolActivity items={item.items} />;
}

function RawStatusDiff({ before, after }: { before: string; after: string }) {
  const rows = useMemo(() => makeLineDiff(before, after), [before, after]);
  return (
    <pre className="max-h-[42vh] overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed">
      {rows.map((row, index) => (
        <span
          key={index}
          className={
            row.kind === "add"
              ? "block bg-state-done-soft text-ink-strong"
              : row.kind === "remove"
                ? "block bg-state-error-soft text-state-error"
                : "block text-ink-muted"
          }
        >
          {row.prefix}
          {row.text || " "}
        </span>
      ))}
    </pre>
  );
}

function ThinkingSection({ turns }: { turns: ReturnType<typeof buildTurnsFromEvents> }) {
  const thinking = useMemo(() => {
    const out: string[] = [];
    for (const t of turns) {
      if (t.role !== "assistant") continue;
      for (const b of t.blocks) {
        if (b.kind === "thinking") out.push(b.text);
      }
    }
    return out;
  }, [turns]);
  if (thinking.length === 0) return null;
  return (
    <section className="mb-5">
      <details className="overflow-hidden rounded-md border border-line bg-surface-sunken">
        <summary className="cursor-pointer px-3 py-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted hover:text-ink">
          Thinking ({thinking.length} block{thinking.length === 1 ? "" : "s"})
        </summary>
        <div className="space-y-2 border-t border-line px-3 py-2">
          {thinking.map((text, i) => (
            <pre
              key={i}
              className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink-muted"
            >
              {text}
            </pre>
          ))}
        </div>
      </details>
    </section>
  );
}

function isTerminal(state: NodeInfo["state"]): boolean {
  return state === "done" || state === "error" || state === "cancelled";
}

type DiffRow = {
  kind: "same" | "add" | "remove";
  prefix: string;
  text: string;
};

function makeLineDiff(before: string, after: string): DiffRow[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const table: number[][] = Array.from({ length: a.length + 1 }, () =>
    Array(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] =
        a[i] === b[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const rows: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      rows.push({ kind: "same", prefix: "  ", text: a[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      rows.push({ kind: "remove", prefix: "- ", text: a[i] });
      i += 1;
    } else {
      rows.push({ kind: "add", prefix: "+ ", text: b[j] });
      j += 1;
    }
  }
  while (i < a.length) {
    rows.push({ kind: "remove", prefix: "- ", text: a[i] });
    i += 1;
  }
  while (j < b.length) {
    rows.push({ kind: "add", prefix: "+ ", text: b[j] });
    j += 1;
  }
  return rows;
}

function PendingGateInline({
  node,
  pending,
  onResolve,
}: {
  node: NodeInfo;
  pending: InteractionRequest;
  onResolve: (payload: ResolveGatePayload) => void;
}) {
  if (pending.interaction_type === "permission") {
    return (
      <PermissionDialog
        request={pending}
        onRespond={(args) =>
          onResolve({
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
          onResolve({
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
  if (pending.interaction_type === "plan_approval") {
    return (
      <PlanDialog
        request={pending}
        onRespond={(args) =>
          onResolve({
            allow: args.allow,
            clear_context: args.clearContext ?? false,
            permission_mode: args.permissionMode ?? null,
            message: args.message ?? "",
          })
        }
      />
    );
  }
  return (
    <div className="text-[12px] text-ink-muted">
      Pending {pending.interaction_type} on {node.id.slice(0, 8)} — open the matching review handler.
    </div>
  );
}

function toLegacyAnswers(answers: Record<string, { answers: string[] }>) {
  return Object.fromEntries(
    Object.entries(answers).map(([key, value]) => [
      key,
      value.answers.length <= 1 ? (value.answers[0] ?? "") : value.answers,
    ]),
  );
}

function StatePill({ state }: { state: NodeInfo["state"] }) {
  const map: Record<NodeInfo["state"], { bg: string; text: string; label: string }> = {
    queued: { bg: "bg-state-queued-soft", text: "text-ink-muted", label: "queued" },
    running: {
      bg: "bg-state-running-soft",
      text: "text-brand-ink dark:text-brand",
      label: "running",
    },
    waiting: {
      bg: "bg-state-waiting-soft",
      text: "text-state-waiting",
      label: "waiting",
    },
    awaiting_review: {
      bg: "bg-state-review-soft",
      text: "text-state-review",
      label: "review",
    },
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
        "inline-block rounded px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em] " +
        m.bg +
        " " +
        m.text
      }
    >
      {m.label}
    </span>
  );
}

