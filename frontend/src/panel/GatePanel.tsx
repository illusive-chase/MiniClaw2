import type { InteractionRequest, NodeInfo } from "../types";
import { GateReviewForm } from "./gateReview";

export type GatePanelProps = {
  node: NodeInfo;
  pending: InteractionRequest | null;
  onSubmit: (payload: { id: string; judgment: string }) => void;
};

/**
 * Side-panel view for a passive review checkpoint.
 *
 * The gate hexagon itself is the primary submit surface; this panel
 * mirrors the same form for users who prefer the wider chrome or who
 * landed here from the keyboard / banner shortcut.
 */
export function GatePanel({ node, pending, onSubmit }: GatePanelProps) {
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Review gate
        </div>
        <h2 className="mt-1 line-clamp-2 font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {node.summary || "Review checkpoint"}
        </h2>
        {node.review_outcome && (
          <div
            className={
              "mt-1 text-[11px] " +
              (node.review_outcome === "approved"
                ? "text-state-review"
                : "text-state-error")
            }
          >
            Already resolved · {node.review_outcome}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        <GateReviewForm
          node={node}
          pending={pending}
          onSubmit={onSubmit}
          variant="panel"
        />
      </div>
    </div>
  );
}
