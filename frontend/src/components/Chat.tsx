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
          ? "flex-1 overflow-y-auto px-4 py-3 space-y-3"
          : "flex-1 overflow-y-auto px-6 py-4 space-y-4"
      }
    >
      {turns.map((t) => (
        <div key={t.id} className="space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">
            {t.role}
          </div>
          {t.role === "assistant" && t.thinking && (
            <details className="rounded-lg border border-slate-800/60 bg-slate-900/30 px-3 py-2 text-xs text-slate-400">
              <summary className="cursor-pointer select-none text-slate-500">
                thinking ({t.thinking.length} chars)
              </summary>
              <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px] text-slate-500">
                {t.thinking}
              </pre>
            </details>
          )}
          {t.role === "assistant" && <ToolActivity items={t.activities} />}
          {t.role === "user" ? (
            <div className="whitespace-pre-wrap rounded-lg bg-slate-800/60 px-4 py-3 text-sm leading-relaxed text-slate-100">
              {t.text}
            </div>
          ) : (
            <div className="rounded-lg bg-slate-900/50 px-4 py-3 text-sm leading-relaxed text-slate-200">
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
                <span className="text-slate-500">{t.streaming ? "…" : ""}</span>
              )}
            </div>
          )}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}
