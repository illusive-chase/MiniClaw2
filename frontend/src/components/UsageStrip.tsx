import { useState } from "react";
import type { Usage } from "../types";

type Props = {
  usage: Usage | null;
};

const SEGMENTS = [
  { key: "input", label: "in", color: "rgb(var(--brand))", opacity: 1 },
  { key: "output", label: "out", color: "rgb(var(--brand))", opacity: 0.5 },
  { key: "cache_read", label: "cr", color: "rgb(var(--state-done))", opacity: 0.55 },
  { key: "cache_creation", label: "cw", color: "rgb(var(--state-waiting))", opacity: 0.85 },
] as const;

function valueOf(usage: Usage | null, key: (typeof SEGMENTS)[number]["key"]): number {
  if (!usage) return 0;
  switch (key) {
    case "input":
      return usage.input_tokens;
    case "output":
      return usage.output_tokens;
    case "cache_read":
      return usage.cache_read_tokens;
    case "cache_creation":
      return usage.cache_creation_tokens;
  }
}

function formatTotal(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toLocaleString();
}

function formatSeg(n: number): string {
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toString();
}

export function UsageStrip({ usage }: Props) {
  const [hovered, setHovered] = useState(false);

  const total = usage
    ? usage.input_tokens +
      usage.output_tokens +
      usage.cache_read_tokens +
      usage.cache_creation_tokens
    : 0;

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="flex items-center gap-2 text-[11px]"
      title={
        usage
          ? `input ${usage.input_tokens.toLocaleString()} · output ${usage.output_tokens.toLocaleString()} · cache-read ${usage.cache_read_tokens.toLocaleString()} · cache-write ${usage.cache_creation_tokens.toLocaleString()}`
          : "Token usage will appear once the first agent turn reports it."
      }
    >
      <span className="w-[58px] text-right font-mono tabular-nums text-ink-muted">
        {usage ? formatTotal(total) : "—"}
      </span>

      {/* the bar */}
      <div className="relative h-2 w-[180px] overflow-hidden rounded-full border border-line bg-surface-sunken">
        {usage && total > 0 && (
          <div className="flex h-full w-full">
            {SEGMENTS.map((seg) => {
              const v = valueOf(usage, seg.key);
              const pct = (v / total) * 100;
              if (pct <= 0) return null;
              return (
                <div
                  key={seg.key}
                  style={{
                    width: `${pct}%`,
                    background: seg.color,
                    opacity: seg.opacity,
                  }}
                />
              );
            })}
          </div>
        )}
      </div>

      {/* hover legend — width reserved so total layout doesn't jitter */}
      <div
        className={
          "flex w-[170px] items-center gap-2 font-mono text-[10px] text-ink-muted transition-opacity duration-150 " +
          (hovered && usage ? "opacity-100" : "opacity-0")
        }
        aria-hidden={!hovered}
      >
        {usage && (
          <>
            {SEGMENTS.map((seg) => (
              <span key={seg.key} className="inline-flex items-center gap-1">
                <span
                  className="inline-block h-1.5 w-1.5 rounded-sm"
                  style={{ background: seg.color, opacity: seg.opacity }}
                />
                {seg.label}{" "}
                <span className="text-ink-subtle">
                  {formatSeg(valueOf(usage, seg.key))}
                </span>
              </span>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
