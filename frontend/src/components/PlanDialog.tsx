import type { InteractionRequest } from "../types";

type Props = {
  request: InteractionRequest;
  onRespond: (args: {
    allow: boolean;
    clearContext?: boolean;
    permissionMode?: string;
    message?: string;
  }) => void;
  variant?: "panel" | "compact";
};

export function PlanDialog({ request, onRespond, variant = "panel" }: Props) {
  const plan = (request.tool_input.plan as string) || "(no plan provided)";
  const compact = variant === "compact";
  return (
    <div
      className={
        "rounded-lg border border-state-review/30 bg-state-review-soft " +
        (compact ? "space-y-2 p-2" : "space-y-3 p-4")
      }
    >
      <div className={(compact ? "text-[11px]" : "text-sm") + " font-medium text-state-review"}>Plan approval</div>
      <pre className={(compact ? "max-h-28 p-2 text-[11px]" : "max-h-64 p-3 text-xs") + " overflow-auto whitespace-pre-wrap rounded border border-line bg-surface-raised text-ink-strong"}>
        {plan}
      </pre>
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() =>
            onRespond({ allow: true, permissionMode: "acceptEdits" })
          }
          className={(compact ? "px-2 py-0.5 text-[11px]" : "px-3 py-1 text-xs") + " rounded-md bg-state-review font-medium text-white transition hover:brightness-[0.92]"}
        >
          Approve &amp; execute
        </button>
        <button
          onClick={() => onRespond({ allow: false, message: "Plan rejected" })}
          className={(compact ? "px-2 py-0.5 text-[11px]" : "px-3 py-1 text-xs") + " rounded-md bg-state-error font-medium text-white transition hover:brightness-[0.92]"}
        >
          Reject
        </button>
      </div>
    </div>
  );
}
