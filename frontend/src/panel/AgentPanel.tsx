import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

import type {
  ClientMessage,
  ContextBundle,
  EventRecord,
  InteractionRequest,
  NodeArtifact,
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
  node: NodeInfo;
  events: EventRecord[];
  eventsLoading: boolean;
  artifact: NodeArtifact | null;
  artifactLoading: boolean;
  contextBundle: ContextBundle | null;
  contextBundleLoading: boolean;
  pendingGate: InteractionRequest | null;
  onResolveGate?: (id: string, payload: ResolveGatePayload) => void;
  onSpawnPhantomFromNode: (nodeId: string) => void;
};

/**
 * Single progressive panel for a selected agent run.
 *
 * Sections: headline → Result → Activity → Pending → Inspect▸
 * No tabs. Selection drives polymorphism.
 */
export function AgentPanel({
  node,
  events,
  eventsLoading,
  artifact,
  artifactLoading,
  contextBundle,
  contextBundleLoading,
  pendingGate,
  onResolveGate,
  onSpawnPhantomFromNode,
}: AgentPanelProps) {
  const turns = useMemo(() => buildTurnsFromEvents(node, events), [node, events]);
  const outputKind = node.output_kind ?? "freeform";
  const showArtifact = outputKind !== "freeform" && (artifact || artifactLoading);
  const headline = (node.summary || node.prompt || "(no prompt)").trim();
  const resumeParentLabel = node.parent_node_id ? node.parent_node_id.slice(0, 8) : null;

  /* Last assistant text block — feeds the "Result" section when no artifact. */
  const lastText = useMemo(() => {
    for (let i = turns.length - 1; i >= 0; i--) {
      const t = turns[i];
      if (t.role !== "assistant") continue;
      const direct = t.text.trim();
      if (direct) return direct;
      const textBlock = [...t.blocks].reverse().find((b) => b.kind === "text");
      if (textBlock && textBlock.kind === "text") return textBlock.text.trim();
    }
    return "";
  }, [turns]);

  /* Tool activity — flatten across all assistant turns, collapsed by default. */
  const activityItems = useMemo(() => {
    const out: import("../types").Activity[] = [];
    for (const t of turns) {
      if (t.role !== "assistant") continue;
      for (const block of t.blocks) {
        if (block.kind === "activity") out.push(...block.items);
      }
    }
    return out;
  }, [turns]);

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

        {/* Result */}
        <section className="mb-5">
          <SectionHeading>Result</SectionHeading>
          {node.error ? (
            <pre className="mt-2 whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft p-3 text-xs text-state-error">
              {node.error}
            </pre>
          ) : showArtifact ? (
            <ArtifactPreview
              node={node}
              artifact={artifact}
              loading={artifactLoading}
            />
          ) : (
            <AssistantResult text={lastText} streaming={node.state === "running"} />
          )}
        </section>

        {/* Activity */}
        <section className="mb-5">
          <SectionHeading
            right={
              <span className="text-[10px] font-normal normal-case tracking-normal text-ink-subtle">
                {eventsLoading
                  ? "loading…"
                  : `${activityItems.length} tool ${activityItems.length === 1 ? "call" : "calls"} · ${events.length} events`}
              </span>
            }
          >
            Activity
          </SectionHeading>
          {activityItems.length === 0 ? (
            <div className="mt-2 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px] text-ink-muted">
              No tool calls yet.
            </div>
          ) : (
            <div className="mt-2">
              <ToolActivity items={activityItems} />
            </div>
          )}
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

function AssistantResult({ text, streaming }: { text: string; streaming?: boolean }) {
  if (!text) {
    return (
      <div className="mt-2 rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12.5px] text-ink-muted">
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
          <span>No assistant output yet.</span>
        )}
      </div>
    );
  }
  return (
    <div className="md-prose mt-2 rounded-md border border-line bg-surface-raised px-4 py-3 text-[13px] leading-relaxed text-ink-strong shadow-card">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

function ArtifactPreview({
  node,
  artifact,
  loading,
}: {
  node: NodeInfo;
  artifact: NodeArtifact | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="mt-2 rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12.5px] text-ink-muted">
        Loading artifact…
      </div>
    );
  }
  if (!artifact) {
    return (
      <div className="mt-2 rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12.5px] text-ink-muted">
        No artifact yet.
      </div>
    );
  }
  if (artifact.error) {
    return (
      <pre className="mt-2 whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft px-3 py-3 text-xs text-state-error">
        {artifact.error}
      </pre>
    );
  }
  if (!artifact.exists) {
    return (
      <div className="mt-2 rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12.5px] text-ink-muted">
        {artifact.path
          ? `Expected file at ${artifact.path} — not written yet.`
          : "No artifact path configured."}
      </div>
    );
  }
  if (node.output_kind === "interface") {
    return (
      <div className="mt-2 overflow-hidden rounded-md border border-line bg-surface-sunken">
        <div className="flex items-center justify-between border-b border-line px-3 py-1.5 text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
          <span className="font-mono normal-case tracking-normal text-ink-muted">
            {artifact.path ?? "result.json"}
          </span>
          <span>JSON</span>
        </div>
        <pre className="whitespace-pre-wrap px-3 py-2 font-mono text-[11px] leading-relaxed text-ink">
          {artifact.content || "{}"}
        </pre>
      </div>
    );
  }
  return (
    <div className="md-prose mt-2 overflow-hidden rounded-md border border-line bg-surface-raised shadow-card">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5 text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
        <span className="font-mono normal-case tracking-normal text-ink-muted">
          {artifact.path ?? "result.md"}
        </span>
        <span>Markdown</span>
      </div>
      <div className="px-4 py-3">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        >
          {artifact.content || ""}
        </ReactMarkdown>
      </div>
    </div>
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
