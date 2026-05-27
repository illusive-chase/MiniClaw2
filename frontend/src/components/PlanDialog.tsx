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
    <div className="space-y-3 rounded-lg border border-state-review/30 bg-state-review-soft p-4">
      <div className="text-sm font-medium text-state-review">Plan approval</div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded border border-line bg-surface-raised p-3 text-xs text-ink-strong">
        {plan}
      </pre>
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() =>
            onRespond({ allow: true, permissionMode: "acceptEdits" })
          }
          className="rounded-md bg-state-review px-3 py-1 text-xs font-medium text-white transition hover:brightness-[0.92]"
        >
          Approve &amp; execute
        </button>
        <button
          onClick={() => onRespond({ allow: false, message: "Plan rejected" })}
          className="rounded-md bg-state-error px-3 py-1 text-xs font-medium text-white transition hover:brightness-[0.92]"
        >
          Reject
        </button>
      </div>
    </div>
  );
}
