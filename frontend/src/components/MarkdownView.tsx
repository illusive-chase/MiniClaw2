/* The one place Markdown is rendered.
 *
 * Six call sites used to each spell out their own `ReactMarkdown` with three
 * different plugin combinations, and none overrode link behavior, so an
 * `[app.py](backend/app.py)` link navigated the whole SPA to a dead path.
 * Collapsing them here makes consistency structural rather than a matter of
 * remembering to keep six copies in step — the plugin set, the link handling,
 * and the font scaling all live in this component and cannot be overridden by
 * a caller.
 */

import { useState } from "react";
import type { AnchorHTMLAttributes, MouseEvent } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { resolveMarkdownLink, revealPath } from "../api";
import {
  classifyHref,
  markdownRouteUrl,
  markdownUrlTransform,
} from "../markdownRoute";
import type { MarkdownDensity } from "../markdownFont";
import { defaultFontIndex, fontPxAt } from "../markdownFont";
import type { MarkdownLinkBase } from "../types";

export type MarkdownViewProps = {
  text: string;
  /** Which surface this is; sets the default font size when `fontPx` is unset. */
  density?: MarkdownDensity;
  /** Overrides the density default (the font control drives this). */
  fontPx?: number;
  /** Enables in-app link handling. Without a session, links fall back to
   *  their default browser behavior — safe, if less useful. */
  sessionId?: string | null;
  /** What a relative href in this text resolves against. */
  linkBase?: MarkdownLinkBase | null;
  className?: string;
};

const EMPTY = "_Empty text._";

/** A transient note when a link could not be followed. */
type LinkNote = { kind: "error" | "info"; text: string };

export function MarkdownView({
  text,
  density = "panel",
  fontPx,
  sessionId,
  linkBase,
  className,
}: MarkdownViewProps) {
  const [note, setNote] = useState<LinkNote | null>(null);
  const size = fontPx ?? fontPxAt(defaultFontIndex(density));

  const onLink = async (
    event: MouseEvent<HTMLAnchorElement>,
    href: string,
  ) => {
    const kind = classifyHref(href);
    if (kind === "external") return; // let the browser open it
    if (kind === "anchor") return; // in-document scroll; leave to the browser
    event.preventDefault();
    if (!sessionId) {
      setNote({ kind: "error", text: "无法解析链接：当前视图没有绑定会话。" });
      return;
    }
    /* Claim the tab now, while the click is still the user's. Only the
     * backend knows whether this href is a readable Markdown file, and Safari
     * in particular drops the click's activation across that await — a
     * `window.open` afterwards is then blocked. So open a blank tab
     * synchronously and either point it at the file or close it again. It
     * keeps an opener, which `noopener` would deny us; the target is this
     * same app, so there is nothing to protect it from. */
    const pending = window.open("", "_blank");
    const settle = (url: string | null) => {
      /* `markdownRouteUrl` is relative to the current page; the blank tab has
       * no URL of its own to resolve it against. */
      const absolute = url ? new URL(url, window.location.href).href : null;
      if (!pending) {
        /* Popup blocked outright. Try once more anyway: a browser that
         * refuses the blank tab may still allow a real URL. */
        if (absolute) window.open(absolute, "_blank", "noopener");
        return;
      }
      if (absolute) pending.location.replace(absolute);
      else pending.close();
    };
    try {
      const verdict = await resolveMarkdownLink(sessionId, href, linkBase);
      if (verdict.verdict === "markdown" && verdict.relative_path) {
        settle(
          markdownRouteUrl({
            src: "project-file",
            sessionId,
            path: verdict.relative_path,
          }),
        );
      } else if (verdict.verdict === "reveal" && verdict.path) {
        settle(null);
        await revealPath(sessionId, verdict.path);
        setNote({ kind: "info", text: `已在文件管理器中显示：${verdict.path}` });
      } else {
        settle(null);
        setNote({
          kind: "error",
          text: verdict.reason ?? `找不到该路径：${href}`,
        });
      }
    } catch (err) {
      settle(null);
      setNote({
        kind: "error",
        text: err instanceof Error ? err.message : `无法打开链接：${href}`,
      });
    }
  };

  return (
    <div className={className}>
      <div className="md-prose" style={{ fontSize: `${size}px` }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
          urlTransform={markdownUrlTransform}
          components={{
            a: ({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => (
              <a
                {...props}
                href={href}
                onClick={(event) => {
                  if (href) void onLink(event, href);
                }}
              >
                {children}
              </a>
            ),
          }}
        >
          {text || EMPTY}
        </ReactMarkdown>
      </div>
      {note && (
        <div
          role="status"
          className={
            "mt-2 rounded border px-2.5 py-1.5 text-[11px] " +
            (note.kind === "error"
              ? "border-state-error/30 bg-state-error-soft text-state-error"
              : "border-line bg-surface-sunken text-ink-muted")
          }
        >
          {note.text}
        </div>
      )}
    </div>
  );
}
