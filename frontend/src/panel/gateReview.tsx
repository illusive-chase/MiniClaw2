import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

import type { InteractionRequest, NodeInfo } from "../types";

export type GateReviewSubmit = (payload: {
  id: string;
  judgment: string;
}) => void;

export type GateReviewFormProps = {
  node: NodeInfo;
  pending: InteractionRequest | null;
  onSubmit: GateReviewSubmit;
  variant: "panel" | "inline";
};

/** Shared review form for human-interact review agents. */
export function GateReviewForm({
  node,
  pending,
  onSubmit,
  variant,
}: GateReviewFormProps) {
  const [judgment, setJudgment] = useState("");

  useEffect(() => {
    setJudgment("");
  }, [pending?.id]);

  const guidance = useMemo(() => {
    const fromRequest =
      textValue(pending?.tool_input?.review_guidance) ||
      textValue(pending?.tool_input?.contract) ||
      humanReviewGuidance(pending);
    return fromRequest || reviewBriefGuidance(node);
  }, [node, pending]);

  const lastError =
    pending && typeof pending.tool_input?.last_error === "string"
      ? (pending.tool_input.last_error as string)
      : null;

  const canSubmit = !!pending && judgment.trim().length > 0;

  const handleSubmit = () => {
    if (!pending || !judgment.trim()) return;
    onSubmit({ id: pending.id, judgment: judgment.trim() });
  };

  if (variant === "inline") {
    return (
      <div className="flex h-full w-full flex-col gap-1.5 overflow-hidden text-[11px]">
        <div className="md-prose nodrag flex-1 overflow-y-auto rounded border border-line bg-surface-raised/95 px-2 py-1.5 text-[11px] leading-snug text-ink-strong">
          {guidance ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
            >
              {guidance}
            </ReactMarkdown>
          ) : (
            <span className="text-ink-muted">No review handoff was written.</span>
          )}
        </div>

        {pending ? (
          <>
            {lastError && (
              <div className="rounded border border-state-error/30 bg-state-error-soft px-1.5 py-1 text-[10px] text-state-error">
                Previous attempt failed: {lastError}
              </div>
            )}
            <textarea
              value={judgment}
              onChange={(e) => setJudgment(e.target.value)}
              rows={3}
              placeholder={
                pending.interaction_type === "human_review_prose"
                  ? "Write your review notes."
                  : "What did you decide?"
              }
              className="nodrag w-full resize-none rounded border border-line bg-surface-sunken px-2 py-1 text-[11px] leading-snug text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
            />
            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleSubmit}
                disabled={!canSubmit}
                className="rounded bg-brand px-2 py-0.5 text-[10px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
              >
                Send
              </button>
            </div>
          </>
        ) : (
          <div className="rounded border border-line bg-surface-sunken px-2 py-1 text-[10px] text-ink-muted">
            No pending response.
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section>
        <SectionHeading>Review handoff</SectionHeading>
        {guidance ? (
          <div className="md-prose mt-2 rounded-md border border-line bg-surface-raised px-4 py-3 text-[13px] leading-relaxed text-ink-strong shadow-card">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
            >
              {guidance}
            </ReactMarkdown>
          </div>
        ) : (
          <div className="mt-2 rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
            No review handoff was written.
          </div>
        )}
      </section>

      {pending ? (
        <section className="space-y-3">
          <SectionHeading>
            {pending.interaction_type === "human_review_prose"
              ? "Your review"
              : "Your judgment"}
          </SectionHeading>
          {lastError && (
            <div className="rounded-md border border-state-error/30 bg-state-error-soft p-2 text-xs text-state-error">
              Previous attempt failed: {lastError}
            </div>
          )}
          <textarea
            value={judgment}
            onChange={(e) => setJudgment(e.target.value)}
            rows={9}
            placeholder={
              pending.interaction_type === "human_review_prose"
                ? "Write the evidence, concerns, and recommendation for the review agent."
                : "Write what you decided, what looks wrong, or what the next agent should do."
            }
            className="w-full resize-none rounded-md border border-line bg-surface-sunken px-3 py-2 text-[13px] leading-relaxed text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
          />
          <div className="flex justify-end">
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
            >
              {pending.interaction_type === "human_review_prose"
                ? "Send review"
                : "Send to next agent"}
            </button>
          </div>
        </section>
      ) : (
        <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
          No pending response.
        </div>
      )}
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
      {children}
    </div>
  );
}

function textValue(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function humanReviewGuidance(pending: InteractionRequest | null): string {
  if (!pending || pending.interaction_type !== "human_review_prose") return "";
  const brief = pending.tool_input?.brief;
  const lines = ["Write a human review for this node."];
  if (isRecord(brief)) {
    const checkWhat = textValue(brief.check_what);
    const expected = textValue(brief.expected);
    const abnormal = textValue(brief.abnormal);
    if (checkWhat) lines.push(`Check: ${checkWhat}`);
    if (expected) lines.push(`Expected: ${expected}`);
    if (abnormal) lines.push(`Flag as abnormal: ${abnormal}`);
  }
  const path = textValue(pending.tool_input?.human_review_path);
  if (path) lines.push(`Your notes will be saved to ${path}.`);
  return lines.join("\n\n");
}

function reviewBriefGuidance(node: NodeInfo): string {
  if (!node.brief) return "";
  const lines = ["Review brief"];
  if (node.brief.check_what) lines.push(`Check: ${node.brief.check_what}`);
  if (node.brief.expected) lines.push(`Expected: ${node.brief.expected}`);
  if (node.brief.abnormal) lines.push(`Flag as abnormal: ${node.brief.abnormal}`);
  return lines.join("\n\n");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
