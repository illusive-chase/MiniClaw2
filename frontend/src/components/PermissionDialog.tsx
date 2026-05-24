import { useState } from "react";
import type { InteractionRequest } from "../types";

type Props = {
  request: InteractionRequest;
  onRespond: (args: {
    allow: boolean;
    message?: string;
    decision?: string;
    scope?: string;
    interrupt?: boolean;
  }) => void;
};

export function PermissionDialog({ request, onRespond }: Props) {
  const [reason, setReason] = useState("");
  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
      <div className="mb-2 text-sm font-medium text-amber-300">
        Allow tool: <span className="font-mono">{request.tool_name}</span>?
      </div>
      <pre className="mb-3 max-h-48 overflow-auto rounded bg-slate-950/60 p-2 text-xs text-slate-300">
        {JSON.stringify(request.tool_input, null, 2)}
      </pre>
      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Optional denial reason"
        className="mb-2 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
      />
      <div className="flex gap-2">
        <button
          onClick={() => onRespond({ allow: true })}
          className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium hover:bg-emerald-500"
        >
          Allow
        </button>
        <button
          onClick={() => onRespond({ allow: true, decision: "acceptForSession", scope: "session" })}
          className="rounded bg-emerald-700 px-3 py-1 text-xs font-medium hover:bg-emerald-600"
        >
          Allow for session
        </button>
        <button
          onClick={() => onRespond({ allow: false, message: reason })}
          className="rounded bg-rose-600 px-3 py-1 text-xs font-medium hover:bg-rose-500"
        >
          Deny
        </button>
        <button
          onClick={() =>
            onRespond({
              allow: false,
              decision: "cancel",
              interrupt: true,
              message: reason || "Cancelled by user",
            })
          }
          className="rounded border border-rose-500/50 px-3 py-1 text-xs font-medium text-rose-300 hover:bg-rose-500/10"
        >
          Cancel turn
        </button>
      </div>
    </div>
  );
}
