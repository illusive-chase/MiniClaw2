import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { searchNodes } from "../api";
import {
  highlightSegments,
  searchFieldLabel,
  splitSearchTerms,
} from "../nodeSearch";
import type { NodeSearchMatch, NodeSearchResponse, NodeSearchResult } from "../types";

/* Typing settles before the server is asked: a cold scan of a large project
 * reads every event log once, and each keystroke would otherwise queue one. */
const QUERY_DEBOUNCE_MS = 250;

type Props = {
  open: boolean;
  sessionId: string | null;
  /** Bumped by the shortcut so a second press refocuses the open panel. */
  focusRequestVersion: number;
  /** Lets an open panel re-run its query when the graph changes. */
  refreshKey: string;
  selectedNodeId: string | null;
  onOpenResult: (
    result: NodeSearchResult,
    target: "preview" | "text",
    textIndex: number,
    terms: string[],
  ) => void;
  onClose: () => void;
};

/**
 * Project-wide keyword search over node previews and plain-text responses.
 *
 * Lives on the canvas's left edge rather than in the right-hand panel so the
 * result list stays put while each hit opens in the inspector beside it.
 */
export function NodeSearchPanel({
  open,
  sessionId,
  focusRequestVersion,
  refreshKey,
  selectedNodeId,
  onOpenResult,
  onClose,
}: Props) {
  const panelRef = useRef<HTMLElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<NodeSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* A new project starts from an empty search: the old terms say nothing
   * about this project's nodes. */
  useEffect(() => {
    setQuery("");
    setResponse(null);
    setError(null);
  }, [sessionId]);

  /* Same reason as the right-hand panel: `aria-hidden` alone still lets Tab
   * walk into an offscreen subtree, and React 18 does not type `inert`. */
  useEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    if (open) el.removeAttribute("inert");
    else el.setAttribute("inert", "");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open, focusRequestVersion]);

  const terms = splitSearchTerms(query);
  const termsKey = terms.join("\u0000");

  useEffect(() => {
    if (!open || !sessionId) return;
    if (termsKey === "") {
      setResponse(null);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      searchNodes(sessionId, query, controller.signal)
        .then((next) => {
          setResponse(next);
          setError(null);
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, QUERY_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // `query` is read through `termsKey`: only a change in the terms the
    // server would match on is worth another request.
  }, [open, sessionId, termsKey, refreshKey]);

  const results = response?.results ?? [];
  const highlightTerms = response?.terms ?? terms;

  return (
    <aside
      ref={panelRef}
      aria-hidden={!open}
      className={
        "absolute inset-y-0 left-0 z-30 flex w-[340px] flex-col border-r border-line bg-surface-sunken shadow-modal transition-transform duration-200 ease-out will-change-transform " +
        (open ? "translate-x-0" : "pointer-events-none -translate-x-full")
      }
      aria-label="搜索节点"
    >
      <div className="border-b border-line bg-surface-raised px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-muted">
            搜索
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-6 w-6 items-center justify-center rounded text-ink-subtle transition hover:bg-surface-sunken hover:text-ink"
            title="关闭搜索 (Esc)"
            aria-label="关闭搜索"
          >
            <X size={14} strokeWidth={1.8} aria-hidden="true" />
          </button>
        </div>
        <label className="mt-2 flex items-center gap-2 rounded-md border border-line bg-surface px-2 py-1.5 focus-within:border-brand/60">
          <Search size={14} strokeWidth={1.8} className="flex-none text-ink-subtle" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                if (query) setQuery("");
                else onClose();
              }
            }}
            placeholder="preview 或回复中的关键词"
            className="min-w-0 flex-1 bg-transparent text-[12.5px] text-ink-strong placeholder:text-ink-subtle focus:outline-none"
            aria-label="搜索关键词"
            spellCheck={false}
          />
          {query && (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                inputRef.current?.focus();
              }}
              className="flex-none text-ink-subtle hover:text-ink"
              aria-label="清空"
            >
              <X size={12} strokeWidth={1.8} aria-hidden="true" />
            </button>
          )}
        </label>
        <div className="mt-1.5 flex min-h-[16px] items-center justify-between text-[10.5px] text-ink-subtle">
          <span>
            {terms.length > 1 ? "以空格或标点分词，节点需包含全部关键词" : "不区分大小写"}
          </span>
          <span aria-live="polite">
            {loading
              ? "搜索中…"
              : response && termsKey
                ? response.truncated
                  ? `显示 ${results.length} / ${response.total} 个节点`
                  : `${response.total} 个节点`
                : ""}
          </span>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {error ? (
          <p className="rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-[11.5px] text-state-error">
            {error}
          </p>
        ) : !termsKey ? (
          <p className="px-2 py-3 text-[11.5px] leading-relaxed text-ink-muted">
            在本项目所有可见方向中查找 preview（运行原因、结果摘要、后续影响，virtual 节点为 prompt 草稿）或 agent 文字回复包含关键词的节点。
          </p>
        ) : response && results.length === 0 && !loading ? (
          <p className="px-2 py-3 text-[11.5px] text-ink-muted">没有匹配的节点。</p>
        ) : (
          <ul className="space-y-1.5">
            {results.map((result) => (
              <li key={result.node_id}>
                <SearchResultCard
                  result={result}
                  terms={highlightTerms}
                  selected={result.node_id === selectedNodeId}
                  onOpen={(target, textIndex) =>
                    onOpenResult(result, target, textIndex, highlightTerms)
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

function SearchResultCard({
  result,
  terms,
  selected,
  onOpen,
}: {
  result: NodeSearchResult;
  terms: string[];
  selected: boolean;
  onOpen: (target: "preview" | "text", textIndex: number) => void;
}) {
  const firstTextOnly = result.matches.every((match) => match.field === "text");
  const hiddenTextHits =
    result.text_match_count -
    result.matches.filter((match) => match.field === "text").length;
  let textIndex = 0;
  return (
    <div
      className={
        "overflow-hidden rounded-md border bg-surface-raised transition " +
        (selected ? "border-brand/60" : "border-line hover:border-line-strong")
      }
    >
      <button
        type="button"
        onClick={() => onOpen(firstTextOnly ? "text" : "preview", 0)}
        className="block w-full px-2.5 pb-1 pt-2 text-left"
        title="打开节点"
      >
        <div className="flex items-center gap-1.5 text-[10px] text-ink-subtle">
          <StateDot state={result.state} />
          <span className="font-mono">{result.node_id.slice(0, 8)}</span>
          <span className="uppercase tracking-[0.08em]">{result.state}</span>
          {result.planspace_title && (
            <span className="min-w-0 truncate" title={result.planspace_id ?? undefined}>
              · {result.planspace_title}
            </span>
          )}
        </div>
        <div className="mt-0.5 line-clamp-2 break-words text-[12px] font-medium leading-snug text-ink-strong">
          <Highlighted text={result.label || "(无标题)"} terms={terms} />
        </div>
      </button>
      <div className="space-y-0.5 px-1.5 pb-1.5">
        {result.matches.map((match, index) => {
          const ownIndex = match.field === "text" ? textIndex++ : 0;
          return (
            <MatchRow
              key={`${match.field}:${index}`}
              match={match}
              terms={terms}
              onClick={() =>
                onOpen(match.field === "text" ? "text" : "preview", ownIndex)
              }
            />
          );
        })}
        {hiddenTextHits > 0 && (
          <div className="px-1 text-[10.5px] text-ink-subtle">
            另有 {hiddenTextHits} 处回复命中
          </div>
        )}
      </div>
    </div>
  );
}

function MatchRow({
  match,
  terms,
  onClick,
}: {
  match: NodeSearchMatch;
  terms: string[];
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-start gap-1.5 rounded px-1 py-1 text-left transition hover:bg-surface-sunken"
      title={match.field === "text" ? "定位到这段回复" : "定位到 Preview"}
    >
      <span
        className={
          "mt-px flex-none rounded px-1 py-px text-[9.5px] font-medium " +
          (match.field === "text"
            ? "bg-surface-sunken text-ink-muted"
            : "bg-brand-soft text-brand-ink dark:text-brand")
        }
      >
        {searchFieldLabel(match.field)}
      </span>
      <span className="line-clamp-3 min-w-0 break-words text-[11px] leading-[1.45] text-ink-muted">
        <Highlighted text={match.snippet} terms={terms} />
      </span>
    </button>
  );
}

function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  return (
    <>
      {highlightSegments(text, terms).map((segment, index) =>
        segment.hit ? (
          <mark key={index} className="search-hit">
            {segment.text}
          </mark>
        ) : (
          <span key={index}>{segment.text}</span>
        ),
      )}
    </>
  );
}

function StateDot({ state }: { state: NodeSearchResult["state"] }) {
  const tone: Record<NodeSearchResult["state"], string> = {
    virtual: "bg-line-strong",
    queued: "bg-state-queued",
    running: "bg-state-running",
    waiting: "bg-state-waiting",
    awaiting_human_input: "bg-state-review",
    done: "bg-state-done",
    error: "bg-state-error",
    cancelled: "bg-state-cancelled",
  };
  return <span className={`inline-block h-1.5 w-1.5 flex-none rounded-full ${tone[state]}`} />;
}
