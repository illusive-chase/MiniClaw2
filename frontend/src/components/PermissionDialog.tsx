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
    <div className="rounded-lg border border-state-waiting/30 bg-state-waiting-soft p-4">
      <div className="mb-2 text-sm font-medium text-state-waiting">
        Allow tool: <span className="font-mono">{request.tool_name}</span>?
      </div>
      <pre className="mb-3 max-h-48 overflow-auto rounded border border-line bg-surface-raised p-2 text-xs text-ink">
        {JSON.stringify(request.tool_input, null, 2)}
      </pre>
      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Optional denial reason"
        className="mb-2 w-full rounded-md border border-line bg-surface-raised px-2 py-1 text-xs text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
      />
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => onRespond({ allow: true })}
          className="rounded-md bg-state-review px-3 py-1 text-xs font-medium text-white transition hover:brightness-[0.92]"
        >
          Allow
        </button>
        <button
          onClick={() => onRespond({ allow: true, decision: "acceptForSession", scope: "session" })}
          className="rounded-md border border-state-review/40 bg-state-review-soft px-3 py-1 text-xs font-medium text-state-review transition hover:border-state-review/70"
        >
          Allow for session
        </button>
        <button
          onClick={() => onRespond({ allow: false, message: reason })}
          className="rounded-md bg-state-error px-3 py-1 text-xs font-medium text-white transition hover:brightness-[0.92]"
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
          className="rounded-md border border-state-error/40 px-3 py-1 text-xs font-medium text-state-error transition hover:bg-state-error-soft"
        >
          Cancel turn
        </button>
      </div>
    </div>
  );
}
