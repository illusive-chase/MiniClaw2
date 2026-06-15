import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { GateNodeData } from "../layout";
import { GateReviewForm } from "../../panel/gateReview";
import type { InteractionRequest } from "../../types";
import { stateMeta } from "./stateMeta";

/**
 * Gate tile: hexagon, ~180x110 collapsed. Passive review checkpoint that
 * inspects an upstream handoff and captures free-form human judgment.
 *
 * When selected and awaiting review/input, the hex expands inline to host the
 * review handoff text and a free-form textarea — the design doc calls
 * this the primary review surface; the side panel is a wider mirror.
 *
 * The hex shape comes from a CSS clip-path so it nests inside React Flow's
 * rectangular bounding box for drag/select math.
 */
function GateNodeImpl({ data, selected }: NodeProps<GateNodeData>) {
  const { node, isActive, planspaceColor } = data;
  const meta = stateMeta(node.state);
  const body =
    (node.summary || node.prompt || "review checkpoint").replace(/\s+/g, " ").trim();
  const ctx = gateInlineContext;
  const expanded =
    selected &&
    (node.state === "awaiting_review" || node.state === "awaiting_human_input") &&
    !!ctx.pending;

  const width = expanded ? 340 : 200;
  const height = expanded ? 280 : 116;

  return (
    <div
      title={`Review gate · ${node.state}\n${body.slice(0, 80)}\n${node.id}`}
      className="relative select-none transition-[width,height] duration-150 ease-out"
      style={{ width, height }}
    >
      {/* hex outline */}
      <div
        className={
          "absolute inset-0 transition " +
          (selected
            ? "ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
            : "hover:ring-2 hover:ring-line-strong/45 hover:ring-offset-2 hover:ring-offset-surface-sunken")
        }
        style={{ clipPath: HEX_CLIP }}
      >
        <div
          className={
            "h-full w-full border " +
            (selected ? "border-brand" : "border-line") +
            " " +
            meta.tileBg
          }
        />
      </div>

      {planspaceColor && (
        <span
          className="pointer-events-none absolute bottom-2 left-4 top-2 z-10 w-[3px]"
          style={{ background: planspaceColor.accent, clipPath: HEX_CLIP }}
          aria-hidden="true"
        />
      )}

      {/* inner content overlays the hex (clipped) */}
      <div
        className="relative z-10 flex h-full w-full flex-col px-6 py-2.5"
        style={{ clipPath: HEX_CLIP }}
      >
        <div className="flex items-center justify-between gap-2">
          <span
            className={
              "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] " +
              meta.chipBg +
              " " +
              meta.chipText
            }
          >
            <span className="inline-flex h-2 w-2 items-center justify-center">
              <meta.Icon />
            </span>
            gate · {meta.label}
          </span>
          {isActive && (
            <span
              className="inline-block h-1.5 w-1.5 rounded-full bg-state-running shadow-[0_0_0_3px_rgb(var(--state-running)/0.25)]"
              aria-hidden="true"
            />
          )}
        </div>

        {expanded ? (
          <div className="mt-2 flex flex-1 min-h-0 flex-col">
            <GateReviewForm
              node={node}
              pending={ctx.pending}
              onSubmit={ctx.onSubmit}
              variant="inline"
            />
          </div>
        ) : (
          <>
            <div className="line-clamp-2 pt-1.5 text-[12px] leading-[1.35] text-ink-strong">
              {body}
            </div>
            <div className="mt-auto flex items-center justify-between text-[10px] text-ink-subtle">
              <span className="font-mono">{node.id.slice(0, 8)}</span>
              {node.review_outcome && (
                <span
                  className={
                    "font-mono " +
                    (node.review_outcome === "approved"
                      ? "text-state-review"
                      : "text-state-error")
                  }
                >
                  {node.review_outcome}
                </span>
              )}
            </div>
          </>
        )}
      </div>

      {/* halo for awaiting review */}
      {meta.ring && (
        <span
          className="pointer-events-none absolute inset-0 review-ring"
          style={{ clipPath: HEX_CLIP }}
          aria-hidden="true"
        />
      )}

      {/* handles — placed at the hex's pointy left/right tips */}
      <Handle
        type="target"
        id="reviews"
        position={Position.Left}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
        style={{ left: -6, top: "50%" }}
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
        style={{ right: -6, top: "50%" }}
      />
      <Handle
        type="source"
        id="produces"
        position={Position.Bottom}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
    </div>
  );
}

export const GateNode = memo(GateNodeImpl);

const HEX_CLIP =
  "polygon(12% 0%, 88% 0%, 100% 50%, 88% 100%, 12% 100%, 0% 50%)";

/* Module-level singleton: App.tsx wires the current pending review +
 * submit handler so the memoized hex can render without prop drilling
 * through React Flow's `data`. Same pattern as PhantomNode's
 * phantomContext. */
export type GateInlineContext = {
  pending: InteractionRequest | null;
  onSubmit: (payload: { id: string; judgment: string }) => void;
};

let gateInlineContext: GateInlineContext = {
  pending: null,
  onSubmit: () => {},
};

export function setGateInlineContext(ctx: GateInlineContext): void {
  gateInlineContext = ctx;
}
