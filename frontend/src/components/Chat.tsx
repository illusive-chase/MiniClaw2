import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Activity } from "../types";
import { ToolActivity } from "./ToolActivity";

export type ChatTurn = {
  id: string;
  role: "user" | "assistant";
  text: string;
  thinking?: string;
  activities: Activity[];
  streaming?: boolean;
};

export function Chat({
  turns,
  variant = "main",
}: {
  turns: ChatTurn[];
  variant?: "main" | "panel";
}) {
  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  return (
    <div
      className={
        variant === "panel"
          ? "flex-1 overflow-y-auto px-4 py-3 space-y-4 bg-surface"
          : "flex-1 overflow-y-auto bg-surface px-6 py-5 space-y-5"
      }
    >
      {turns.map((t) => (
        <div key={t.id} className="space-y-1.5">
          <div className="flex items-center gap-2">
            <span
              className={
                "inline-block h-1 w-1 rounded-full " +
                (t.role === "user" ? "bg-brand" : "bg-state-done")
              }
            />
            <span className="text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
              {t.role === "user" ? "you" : "assistant"}
            </span>
          </div>

          {t.role === "assistant" && t.thinking && (
            <details className="rounded-md border border-line bg-surface-sunken px-3 py-1.5 text-xs text-ink-muted">
              <summary className="cursor-pointer select-none text-ink-muted">
                thinking ({t.thinking.length} chars)
              </summary>
              <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px] text-ink-muted">
                {t.thinking}
              </pre>
            </details>
          )}

          {t.role === "assistant" && <ToolActivity items={t.activities} />}

          {t.role === "user" ? (
            <div className="whitespace-pre-wrap rounded-md border-l-2 border-brand bg-brand-soft px-4 py-3 text-[13px] leading-relaxed text-ink-strong">
              {t.text}
            </div>
          ) : (
            <div className="rounded-md border border-line bg-surface-raised px-4 py-3 text-[13px] leading-relaxed text-ink-strong shadow-card">
              {t.text ? (
                <div className="md-prose">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
                  >
                    {t.text}
                  </ReactMarkdown>
                </div>
              ) : (
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
              )}
            </div>
          )}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}
