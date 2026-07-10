import type { ClientMessage, InteractionRequest, NodeInfo } from "../types";
import { AskUserDialog } from "./AskUserDialog";
import { PermissionDialog } from "./PermissionDialog";

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
            scope: args.scope ?? null,
            interrupt: args.interrupt ?? false,
            message: args.message ?? "",
            updated_input: args.updatedInput ?? null,
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
            response: { answers },
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
