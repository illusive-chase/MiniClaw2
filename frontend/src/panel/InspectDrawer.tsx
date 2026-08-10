import { useState } from "react";
import type { ContextBundle, ModelPreset, NodeInfo } from "../types";
import { UsageStrip } from "../components/UsageStrip";
import { modelPresetLabel } from "../modelPresets";

/**
 * The "schema escape hatch" — every word the PRD §6 wants hidden from the
 * primary surface lives behind this disclosure. Engineers debugging a run
 * can still reach the binding, planspace, plug, bundle, verdict, provider
 * session/turn, and raw snapshots in ≤2 clicks.
 */
export function InspectDrawer({
  node,
  modelPresets,
  contextBundle,
  contextBundleLoading,
  eventCount,
}: {
  node: NodeInfo;
  modelPresets: ModelPreset[];
  contextBundle: ContextBundle | null;
  contextBundleLoading: boolean;
  eventCount: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="overflow-hidden rounded-md border border-line bg-surface-sunken"
      open={open}
      onToggle={(e) => {
        if (e.currentTarget !== e.target) return;
        setOpen(e.currentTarget.open);
      }}
    >
      <summary className="flex cursor-pointer items-center justify-between gap-2 px-3 py-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted transition hover:text-ink">
        <span>Inspect ▸ raw fields</span>
        <span className="font-mono text-[10px] normal-case tracking-normal text-ink-subtle">
          {open ? "hide" : "show"}
        </span>
      </summary>
      <div className="space-y-4 border-t border-line px-3 py-3 text-xs">
        <KVTable
          rows={[
            ["kind", node.kind],
            ["state", node.state],
            ["category", node.category ?? "-"],
            ["subtype", node.subtype ?? "-"],
            ["model preset", modelPresetLabel(modelPresets, node.model_preset_id)],
            ["provider", node.provider ?? "-"],
            ["provider session", node.provider_session_id ?? "-"],
            ["provider turn", node.provider_turn_id ?? "-"],
            ["origin machine", node.origin_machine_id || "-"],
            ["context bundle", contextBundle?.bundle_id ?? node.context_bundle_id ?? "-"],
            ["project binding", contextBundle?.project_binding_id ?? "-"],
            ["active planspace", contextBundle?.active_planspace_id ?? "-"],
            ["commit before", short(node.commit_before)],
            ["commit after", short(node.commit_after)],
            ["proposed by", node.proposed_by ?? "-"],
            ["deps", node.scheduled_deps?.join(", ") || "-"],
            ["obsolete", node.obsolete_reason ?? "-"],
            ["parent", node.parent_node_id ?? "-"],
            ["events recorded", String(eventCount)],
            ["started", fmtTime(node.started_at)],
            ["finished", fmtTime(node.finished_at)],
          ]}
        />

        {node.usage && (
          <div>
            <SectionLabel>Token usage</SectionLabel>
            <UsageStrip usage={node.usage} className="text-[11px]" />
          </div>
        )}

        {node.system_context_snapshot && (
          <details className="overflow-hidden rounded-md border border-line bg-surface-raised">
            <summary className="cursor-pointer px-3 py-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted hover:text-ink">
              System context ({node.system_context_snapshot.length} chars)
            </summary>
            <pre className="whitespace-pre-wrap border-t border-line px-3 py-2 text-[11px] leading-relaxed text-ink">
              {node.system_context_snapshot}
            </pre>
          </details>
        )}

        {node.settings_snapshot && Object.keys(node.settings_snapshot).length > 0 && (
          <details className="overflow-hidden rounded-md border border-line bg-surface-raised">
            <summary className="cursor-pointer px-3 py-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted hover:text-ink">
              Settings snapshot
            </summary>
            <KVTable
              rows={Object.entries(node.settings_snapshot).map(([k, v]) => [
                k,
                typeof v === "string" ? v : JSON.stringify(v),
              ])}
              className="border-t border-line"
            />
          </details>
        )}

        <details className="overflow-hidden rounded-md border border-line bg-surface-raised">
          <summary className="cursor-pointer px-3 py-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted hover:text-ink">
            Context bundle sources
            {contextBundleLoading
              ? " (loading…)"
              : ` (${contextBundle?.sources?.length ?? 0})`}
          </summary>
          {contextBundle && contextBundle.sources.length > 0 ? (
            <div className="border-t border-line">
              {contextBundle.sources.map((src, i) => (
                <div
                  key={`${src.path}-${i}`}
                  className="grid grid-cols-[minmax(0,1fr)_56px_56px_56px] gap-x-2 px-3 py-1.5 text-[10.5px] odd:bg-surface-sunken/40"
                >
                  <div className="min-w-0">
                    <div className="truncate font-mono text-ink" title={src.path}>
                      {src.path}
                    </div>
                    <div className="text-[9.5px] text-ink-subtle">
                      {src.scope} · {src.kind}
                      {src.plug_id ? ` · ${src.plug_id}` : ""}
                    </div>
                  </div>
                  <div className="font-mono text-ink-muted">{src.injection}</div>
                  <div className="font-mono text-ink-muted">{src.chars}</div>
                  <div className="truncate font-mono text-ink-muted" title={src.sha256}>
                    {src.sha256.slice(0, 7)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="border-t border-line px-3 py-2 text-[11px] text-ink-muted">
              No bundle recorded.
            </div>
          )}
        </details>
      </div>
    </details>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
      {children}
    </div>
  );
}

function KVTable({
  rows,
  className,
}: {
  rows: Array<[string, string]>;
  className?: string;
}) {
  return (
    <dl className={"grid grid-cols-[140px_1fr] gap-x-3 gap-y-1 px-3 py-2 text-[11px] " + (className ?? "")}>
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-subtle">{k}</dt>
          <dd className="truncate font-mono text-ink" title={v}>
            {v || "-"}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function fmtTime(value?: number | null): string {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString();
}

function short(value?: string | null): string {
  if (!value) return "-";
  return value.slice(0, 12);
}
