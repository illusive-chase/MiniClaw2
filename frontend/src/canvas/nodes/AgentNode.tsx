import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { NodeInfo, NodeState } from "../../types";
import type { AgentNodeData } from "../layout";
import { stateMeta } from "./stateMeta";

/**
 * Agent tile: rounded rectangle, ~224x130. The primary work unit.
 *
 * Color encodes `state`; shape encodes `kind`. Shows a one-line prompt preview
 * plus a streaming sweep bar when the agent is actively running. A hover-only
 * `+` handle on the right edge of a tail tile is the entry point for the
 * phantom composer.
 */
function AgentNodeImpl({ data, selected }: NodeProps<AgentNodeData>) {
  const {
    node,
    index,
    resumeParent,
    isActive,
    planspaceColor,
    crossLaneLoads,
    isLastInLane,
  } = data;
  const meta = stateMeta(node.state);
  const headline = oneLine(node.summary || node.prompt || "(empty prompt)");

  return (
    <div className="group relative w-[224px]" title={tooltipForAgent(node, isActive)}>
      <div
        className={
          "select-none overflow-hidden rounded-lg border text-left shadow-card transition " +
          (selected
            ? "border-brand ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
            : "border-line hover:border-line-strong hover:ring-2 hover:ring-line-strong/45 hover:ring-offset-2 hover:ring-offset-surface-sunken hover:shadow-raised") +
          " " +
          meta.tileBg
        }
      >
      {/* state rail */}
      <span
        className={
          "pointer-events-none absolute inset-y-0 left-0 w-[3px] " +
          (planspaceColor ? "" : meta.railBg)
        }
        style={planspaceColor ? { background: planspaceColor.accent } : undefined}
        aria-hidden="true"
      />

      {/* header row */}
      <div className="flex items-center justify-between gap-2 pl-3.5 pr-2.5 pt-2">
        <StateChip state={node.state} />
        <span className="font-mono text-[10px] text-ink-subtle">
          {index + 1}
          <span className="text-ink-subtle/70"> · run</span>
        </span>
      </div>

      {/* body — prompt preview */}
      <div className="line-clamp-3 px-3.5 pt-1.5 text-[12.5px] leading-[1.38] text-ink-strong">
        {headline}
      </div>

      {/* Cross-lane "loaded from:" chips — surfaced even when the dashed
       * loads edges are auto-hidden, so the user can see direction-crossing
       * context at a glance. */}
      {crossLaneLoads && crossLaneLoads.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1 px-3.5">
          {crossLaneLoads.map((load) => (
            <span
              key={load.planspaceId}
              className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[9.5px]"
              style={{
                borderColor: load.color.border,
                background: load.color.bg,
                color: load.color.text,
              }}
              title={`Loaded planspace state from ${load.planspaceId}`}
            >
              <span aria-hidden="true">↗</span>
              <span className="truncate">{load.label}</span>
            </span>
          ))}
        </div>
      )}

      {/* footer */}
      <div className="flex items-center justify-between gap-2 px-3.5 pb-1.5 pt-2 text-[10px] text-ink-subtle">
        <span className="font-mono">{node.id.slice(0, 8)}</span>
        {node.usage ? (
          <span className="font-mono text-ink-muted">
            ↑{compactTokens(node.usage.input_tokens)} ↓
            {compactTokens(node.usage.output_tokens)}
          </span>
        ) : (
          <span className="font-mono">{formatStartTime(node)}</span>
        )}
      </div>

      {/* state bar */}
      <span
        className={"pointer-events-none absolute bottom-0 left-0 h-[2px] w-full " + meta.barTrack}
        aria-hidden="true"
      >
        <span className={"absolute inset-y-0 " + meta.barFill} />
      </span>

      {/* awaiting_review halo */}
      {meta.ring && (
        <span
          className="pointer-events-none absolute inset-0 rounded-lg review-ring"
          aria-hidden="true"
        />
      )}

      {/* resumed-from badge */}
      {resumeParent && (
        <span
          className="pointer-events-none absolute -top-2 left-3 rounded-full border border-brand/40 bg-surface-raised px-1.5 py-0.5 font-mono text-[9px] text-brand-ink shadow-card"
          title={`Resumed from ${resumeParent.id}`}
        >
          ↻ {resumeParent.id.slice(0, 6)}
        </span>
      )}

      {/* live streaming dot */}
      {isActive && (
        <span
          className="pointer-events-none absolute right-2 top-2 inline-block h-1.5 w-1.5 rounded-full bg-state-running shadow-[0_0_0_3px_rgb(var(--state-running)/0.25)]"
          aria-hidden="true"
        />
      )}

      {/* react-flow handles */}
      <Handle
        type="target"
        position={Position.Left}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      <Handle
        type="source"
        id="produces"
        position={Position.Bottom}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      <Handle
        type="target"
        id="loads"
        position={Position.Top}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      </div>

      {/* Hover-only "+" affordance: only on tail tiles, opens a phantom composer
       * to the right. Half-overlapping the edge so cursor moves from tile to
       * button without losing hover; `hover:opacity-100` on the button itself
       * keeps it visible during that transition. */}
      {isLastInLane && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            agentNodeContext.onSpawnFromAgent(node.id);
          }}
          onMouseDown={(e) => e.stopPropagation()}
          className="nodrag absolute -right-3 top-1/2 z-10 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full border border-line-strong bg-surface-raised text-[15px] font-semibold leading-none text-ink-muted opacity-0 shadow-card transition hover:border-brand hover:bg-brand hover:text-white group-hover:opacity-100 hover:opacity-100"
          title="Start a follow-up run"
          aria-label="Start a follow-up run"
        >
          +
        </button>
      )}
    </div>
  );
}

export const AgentNode = memo(AgentNodeImpl);

/* Module-level singleton: App.tsx writes the active onSpawnFromAgent callback
 * (same pattern as PhantomNode's phantomContext) so the memoized AgentNode
 * always reads the latest handler without stale closures. */
export type AgentNodeContext = {
  onSpawnFromAgent: (nodeId: string) => void;
};

let agentNodeContext: AgentNodeContext = {
  onSpawnFromAgent: () => {},
};

export function setAgentNodeContext(ctx: AgentNodeContext): void {
  agentNodeContext = ctx;
}

function StateChip({ state }: { state: NodeState }) {
  const meta = stateMeta(state);
  return (
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
      {meta.label}
    </span>
  );
}

function oneLine(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

function compactTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`;
  return String(n);
}

function formatStartTime(node: NodeInfo): string {
  const at = node.started_at ?? node.created_at;
  return new Date(at * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function tooltipForAgent(node: NodeInfo, isActive: boolean): string {
  const prompt = node.prompt ? `"${node.prompt.slice(0, 80)}"` : "(no prompt)";
  const status = isActive ? " · streaming" : "";
  return `Agent run · ${node.state}${status}\n${prompt}\n${node.id}`;
}
