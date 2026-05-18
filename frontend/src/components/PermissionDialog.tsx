import { useState } from "react";
import type { InteractionRequest } from "../types";

type Props = {
  request: InteractionRequest;
  onRespond: (allow: boolean, message?: string) => void;
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
          onClick={() => onRespond(true)}
          className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium hover:bg-emerald-500"
        >
          Allow
        </button>
        <button
          onClick={() => onRespond(false, reason)}
          className="rounded bg-rose-600 px-3 py-1 text-xs font-medium hover:bg-rose-500"
        >
          Deny
        </button>
      </div>
    </div>
  );
}
