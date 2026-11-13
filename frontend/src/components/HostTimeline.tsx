import type { HostEntry } from "../types";
import { HostGlyph } from "./HostGlyph";

const WINDOW_DAYS = 7;
const DAY = 86400;

/**
 * One row per host across the last week; each dot is one sync that host
 * reported through its beacon. Hosts without a beacon show no dots — the
 * timeline only draws what a host said about itself.
 */
export function HostTimeline({ hosts, now }: { hosts: HostEntry[]; now: number }) {
  const start = now - WINDOW_DAYS * DAY;
  const rows = hosts.filter((entry) => !entry.stale || entry.is_self);
  if (!rows.some((entry) => entry.history.length > 0)) return null;
  const position = (at: number) => `${Math.min(100, Math.max(0, ((at - start) / (now - start)) * 100))}%`;
  const ticks = Array.from({ length: WINDOW_DAYS + 1 }, (_, index) => start + index * DAY);
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2">
      <div className="mb-1.5 text-[10.5px] font-medium text-ink-muted">近 {WINDOW_DAYS} 天的同步</div>
      <div className="space-y-1.5">
        {rows.map((entry) => (
          <div key={entry.machine_id} className="flex items-center gap-2">
            <div className="flex w-28 min-w-0 flex-none items-center gap-1">
              <HostGlyph icon={entry.icon} color={entry.color} size={11} />
              <span className="truncate text-[10.5px] text-ink">{entry.label}</span>
            </div>
            <div className="relative h-3 flex-1 rounded-full bg-surface-sunken">
              {entry.history
                .filter((item) => item.synced_at !== null && item.synced_at >= start)
                .map((item) => (
                  <span
                    key={item.sha}
                    className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand"
                    style={{ left: position(item.synced_at!) }}
                    title={`${new Date(item.synced_at! * 1000).toLocaleString()} · 整合到 ${item.integrated?.slice(0, 8) ?? "?"} · 本次贡献 ${item.contributed} 个提交`}
                  />
                ))}
            </div>
          </div>
        ))}
      </div>
      <div className="relative ml-[7.5rem] mt-1 h-3">
        {ticks.filter((_, index) => index % 2 === 1).map((at) => (
          <span key={at} className="absolute -translate-x-1/2 text-[9px] text-ink-subtle" style={{ left: position(at) }}>
            {new Date(at * 1000).toLocaleDateString(undefined, { month: "2-digit", day: "2-digit" })}
          </span>
        ))}
      </div>
    </div>
  );
}
