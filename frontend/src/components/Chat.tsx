import { useEffect, useRef } from "react";
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

export function Chat({ turns }: { turns: ChatTurn[] }) {
  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
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
          <div
            className={
              "whitespace-pre-wrap rounded-lg px-4 py-3 text-sm leading-relaxed " +
              (t.role === "user"
                ? "bg-slate-800/60 text-slate-100"
                : "bg-slate-900/50 text-slate-200")
            }
          >
            {t.text || (t.streaming ? "…" : "")}
          </div>
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}
