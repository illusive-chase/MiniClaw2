import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { writeClipboard } from "../clipboard";
import { googleTranslateCode } from "../languages";

/** Google Translate silently drops text past roughly this length. */
const TRANSLATE_TEXT_LIMIT = 4500;

export type TextZoomView = "markdown" | "raw";

export type TextZoomRequest = {
  /** Heading shown in the overlay, e.g. "结果摘要" or an artifact filename. */
  title: string;
  /** Secondary line under the title: node id, file path, char count context. */
  subtitle?: string;
  text: string;
  /** Which view opens first. Plain-text sources should pass "raw". */
  defaultView?: TextZoomView;
  /** Set for sources that only ever make sense as raw text (diffs, prompts). */
  rawOnly?: boolean;
};

type TextZoomContextValue = {
  open: (request: TextZoomRequest) => void;
};

const TextZoomContext = createContext<TextZoomContextValue | null>(null);

export type TextZoomProviderProps = {
  /** Project's preferred language; drives the Translate target. */
  preferredLanguage?: string | null;
  children: ReactNode;
};

/**
 * Hosts the shared "zoom this text" overlay. Long agent output, preview
 * fields, and Markdown artifacts are unreadable in the narrow side panel, so
 * any of them can hand their text here for a centered, full-size reading view.
 */
export function TextZoomProvider({
  preferredLanguage,
  children,
}: TextZoomProviderProps) {
  const [request, setRequest] = useState<TextZoomRequest | null>(null);

  const open = useCallback((next: TextZoomRequest) => {
    setRequest(next);
  }, []);

  const value = useMemo<TextZoomContextValue>(() => ({ open }), [open]);

  return (
    <TextZoomContext.Provider value={value}>
      {children}
      {request && (
        <TextZoomOverlay
          request={request}
          preferredLanguage={preferredLanguage}
          onClose={() => setRequest(null)}
        />
      )}
    </TextZoomContext.Provider>
  );
}

export function useTextZoom(): TextZoomContextValue | null {
  return useContext(TextZoomContext);
}

export type ZoomableTextProps = TextZoomRequest & {
  /** Classes for the wrapper; pass what the original container had. */
  className?: string;
  /** Nudges the button when the container has unusually tight padding. */
  buttonClassName?: string;
  children: ReactNode;
};

/**
 * Wraps an existing text container and overlays a zoom button in its top-right
 * corner. The button stays mounted for keyboard and screen-reader users but
 * only becomes visible on hover or focus so short fields stay uncluttered.
 */
export function ZoomableText({
  className,
  buttonClassName,
  children,
  ...request
}: ZoomableTextProps) {
  const zoom = useTextZoom();
  const zoomable = !!zoom && request.text.trim().length > 0;

  return (
    <div className={`group/zoom relative ${className ?? ""}`}>
      {children}
      {zoomable && (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            zoom?.open(request);
          }}
          className={
            "absolute right-1.5 top-1.5 z-10 flex h-6 w-6 items-center justify-center rounded border border-line bg-surface-raised/95 text-ink-muted opacity-0 shadow-card transition hover:border-line-strong hover:text-ink-strong focus-visible:opacity-100 group-hover/zoom:opacity-100 " +
            (buttonClassName ?? "")
          }
          title="放大显示"
          aria-label="放大显示"
        >
          <ExpandIcon />
        </button>
      )}
    </div>
  );
}

function TextZoomOverlay({
  request,
  preferredLanguage,
  onClose,
}: {
  request: TextZoomRequest;
  preferredLanguage?: string | null;
  onClose: () => void;
}) {
  const rawOnly = request.rawOnly === true;
  const [view, setView] = useState<TextZoomView>(
    rawOnly ? "raw" : (request.defaultView ?? "markdown"),
  );
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const [translateNote, setTranslateNote] = useState<string | null>(null);

  useEffect(() => {
    setView(rawOnly ? "raw" : (request.defaultView ?? "markdown"));
    setCopyState("idle");
    setTranslateNote(null);
  }, [request, rawOnly]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [onClose]);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  useEffect(() => {
    if (copyState === "idle") return;
    const timeout = window.setTimeout(() => setCopyState("idle"), 2000);
    return () => window.clearTimeout(timeout);
  }, [copyState]);

  const copy = async () => {
    try {
      await writeClipboard(request.text);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
  };

  const translate = () => {
    const target = googleTranslateCode(preferredLanguage);
    const truncated = request.text.length > TRANSLATE_TEXT_LIMIT;
    const excerpt = truncated
      ? request.text.slice(0, TRANSLATE_TEXT_LIMIT)
      : request.text;
    /* Open before copying: an await here would detach the click from the
     * popup and trip the browser's popup blocker. */
    window.open(
      `https://translate.google.com/?sl=auto&tl=${encodeURIComponent(target)}&op=translate&text=${encodeURIComponent(excerpt)}`,
      "_blank",
      "noopener",
    );
    void writeClipboard(request.text)
      .then(() => {
        setTranslateNote(
          truncated
            ? `全文已复制到剪贴板；翻译页链接只带了前 ${TRANSLATE_TEXT_LIMIT} 字符，可在页面里粘贴全文。`
            : "全文已复制到剪贴板，并已打开翻译页。",
        );
      })
      .catch(() => {
        setTranslateNote(
          truncated
            ? `翻译页链接只带了前 ${TRANSLATE_TEXT_LIMIT} 字符，剩余部分需要手动复制（本次自动复制失败）。`
            : "已打开翻译页（本次自动复制到剪贴板失败）。",
        );
      });
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-surface-scrim/60 p-6 backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="flex max-h-[88vh] w-[min(1040px,94vw)] flex-col overflow-hidden rounded-xl border border-line bg-surface-raised shadow-modal"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={request.title}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="truncate font-display text-sm font-semibold text-ink-strong">
              {request.title}
            </div>
            <div className="mt-0.5 truncate font-mono text-[10px] text-ink-subtle">
              {request.subtitle ? `${request.subtitle} · ` : ""}
              {request.text.length} chars
            </div>
          </div>
          <div className="flex flex-none items-center gap-2">
            {!rawOnly && (
              <div className="inline-flex rounded-md border border-line bg-surface-sunken p-0.5">
                {(
                  [
                    ["markdown", "Markdown"],
                    ["raw", "Raw"],
                  ] as Array<[TextZoomView, string]>
                ).map(([option, label]) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setView(option)}
                    className={
                      "rounded px-2.5 py-1 text-[11px] font-medium transition " +
                      (view === option
                        ? "bg-surface-raised text-ink-strong shadow-card"
                        : "text-ink-muted hover:text-ink")
                    }
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
            <button
              type="button"
              onClick={() => void copy()}
              className={
                "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-medium transition " +
                (copyState === "error"
                  ? "border-state-error/40 bg-state-error-soft text-state-error"
                  : copyState === "copied"
                    ? "border-state-done/40 bg-state-done-soft text-state-done"
                    : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink-strong")
              }
              title="复制全文"
            >
              <span aria-live="polite">
                {copyState === "copied"
                  ? "已复制"
                  : copyState === "error"
                    ? "复制失败"
                    : "Copy"}
              </span>
            </button>
            <button
              type="button"
              onClick={translate}
              className="inline-flex h-7 items-center rounded-md border border-line bg-surface px-2.5 text-[11px] font-medium text-ink-muted transition hover:border-line-strong hover:text-ink-strong"
              title="用 Google 翻译打开"
            >
              Translate
            </button>
            <button
              type="button"
              onClick={onClose}
              autoFocus
              className="rounded px-2 py-1 text-[11px] text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
              title="关闭"
            >
              Esc
            </button>
          </div>
        </div>

        {translateNote && (
          <div className="border-b border-line bg-surface-sunken px-5 py-2 text-[11px] text-ink-muted">
            {translateNote}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto bg-surface px-6 py-5">
          {view === "markdown" ? (
            <div className="md-prose text-[14px] leading-relaxed text-ink-strong">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
              >
                {request.text || "_Empty text._"}
              </ReactMarkdown>
            </div>
          ) : (
            <pre className="whitespace-pre-wrap break-words font-mono text-[12.5px] leading-relaxed text-ink">
              {request.text}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

function ExpandIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9.5 2.5h4v4M13.5 2.5 9 7" />
      <path d="M6.5 13.5h-4v-4M2.5 13.5 7 9" />
    </svg>
  );
}
