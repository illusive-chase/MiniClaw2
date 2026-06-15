import type { ClientMessage, InteractionRequest, NodeInfo } from "../types";
import { AskUserDialog } from "./AskUserDialog";
import { PermissionDialog } from "./PermissionDialog";
import { PlanDialog } from "./PlanDialog";

export type ResolveGatePayload = Omit<
  Extract<ClientMessage, { type: "interaction_response" }>,
  "type" | "id"
>;

type Props = {
  node: NodeInfo;
  pending: InteractionRequest;
  onResolve: (payload: ResolveGatePayload) => void;
  compact?: boolean;
};

export function PendingGateInline({
  node,
  pending,
  onResolve,
  compact = false,
}: Props) {
  const variant = compact ? "compact" : "panel";
  if (pending.interaction_type === "permission") {
    return (
      <PermissionDialog
        request={pending}
        variant={variant}
        onRespond={(args) =>
          onResolve({
            allow: args.allow,
            decision: args.decision ?? null,
            scope: args.scope ?? null,
            interrupt: args.interrupt ?? false,
            message: args.message ?? "",
          })
        }
      />
    );
  }
  if (pending.interaction_type === "ask_user") {
    return (
      <AskUserDialog
        request={pending}
        variant={variant}
        onRespond={(answers) =>
          onResolve({
            allow: true,
            updated_input: {
              ...pending.tool_input,
              answers: toLegacyAnswers(answers),
            },
            response: { answers },
          })
        }
      />
    );
  }
  if (pending.interaction_type === "plan_approval") {
    return (
      <PlanDialog
        request={pending}
        variant={variant}
        onRespond={(args) =>
          onResolve({
            allow: args.allow,
            clear_context: args.clearContext ?? false,
            permission_mode: args.permissionMode ?? null,
            message: args.message ?? "",
          })
        }
      />
    );
  }
  return (
    <div className="text-[12px] text-ink-muted">
      Pending {pending.interaction_type} on {node.id.slice(0, 8)}.
    </div>
  );
}

function toLegacyAnswers(answers: Record<string, { answers: string[] }>) {
  return Object.fromEntries(
    Object.entries(answers).map(([key, value]) => [
      key,
      value.answers.length <= 1 ? (value.answers[0] ?? "") : value.answers,
    ]),
  );
}
