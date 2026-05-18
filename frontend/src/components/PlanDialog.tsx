import type { InteractionRequest } from "../types";

type Props = {
  request: InteractionRequest;
  onRespond: (args: {
    allow: boolean;
    clearContext?: boolean;
    permissionMode?: string;
    message?: string;
  }) => void;
};

export function PlanDialog({ request, onRespond }: Props) {
  const plan = (request.tool_input.plan as string) || "(no plan provided)";
  return (
    <div className="rounded-lg border border-violet-500/40 bg-violet-500/5 p-4 space-y-3">
      <div className="text-sm font-medium text-violet-300">Plan approval</div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-slate-950/60 p-3 text-xs text-slate-200">
        {plan}
      </pre>
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() =>
            onRespond({ allow: true, clearContext: true, permissionMode: "acceptEdits" })
          }
          className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium hover:bg-emerald-500"
        >
          Approve &amp; execute
        </button>
        <button
          onClick={() => onRespond({ allow: false, message: "Plan rejected" })}
          className="rounded bg-rose-600 px-3 py-1 text-xs font-medium hover:bg-rose-500"
        >
          Reject
        </button>
      </div>
    </div>
  );
}
