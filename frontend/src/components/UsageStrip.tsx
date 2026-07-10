import type { TokenUsage } from "../types";

type Props = {
  usage?: TokenUsage | null;
  className?: string;
};

export function UsageStrip({ usage, className = "" }: Props) {
  return (
    <span
      className={
        "inline-flex items-center rounded border border-line bg-surface-raised px-2 py-0.5 font-mono text-[11px] tabular-nums text-ink-muted " +
        className
      }
      title={usage ? usageTitle(usage) : "No token usage recorded for this node yet."}
    >
      {formatUsagePair(usage)}
    </span>
  );
}

function formatUsagePair(usage?: TokenUsage | null): string {
  if (!usage) return "—";
  return `${formatTotal(contextTokens(usage))}/${formatTotal(outputAndCacheWriteTokens(usage))}`;
}

function usageTitle(usage: TokenUsage): string {
  return [
    `context ${contextTokens(usage).toLocaleString()} = r ${usage.input_tokens.toLocaleString()} + cr ${usage.cache_read_tokens.toLocaleString()} + cw ${usage.cache_creation_tokens.toLocaleString()}`,
    `out+cw ${outputAndCacheWriteTokens(usage).toLocaleString()} = out ${cumulativeOutputTokens(usage).toLocaleString()} + cw ${cumulativeCacheWriteTokens(usage).toLocaleString()}`,
  ].join(" · ");
}

function contextTokens(usage: TokenUsage): number {
  return (
    usage.input_tokens +
    usage.cache_read_tokens +
    usage.cache_creation_tokens
  );
}

function outputAndCacheWriteTokens(usage: TokenUsage): number {
  return cumulativeOutputTokens(usage) + cumulativeCacheWriteTokens(usage);
}

function cumulativeOutputTokens(usage: TokenUsage): number {
  return usage.cumulative_output_tokens ?? usage.output_tokens;
}

function cumulativeCacheWriteTokens(usage: TokenUsage): number {
  return usage.cumulative_cache_creation_tokens ?? usage.cache_creation_tokens;
}

function formatTotal(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toLocaleString();
}
