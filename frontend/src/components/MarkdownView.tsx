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

import { followMarkdownLink } from "../markdownLink";
import type { MarkdownLinkNote } from "../markdownLink";
import {
  classifyHref,
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

export function MarkdownView({
  text,
  density = "panel",
  fontPx,
  sessionId,
  linkBase,
  className,
}: MarkdownViewProps) {
  const [note, setNote] = useState<MarkdownLinkNote | null>(null);
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
    setNote(null);
    setNote(await followMarkdownLink(sessionId, href, linkBase));
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
          {note.href && (
            <a
              href={note.href}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-2 underline"
            >
              在新标签页阅读
            </a>
          )}
        </div>
      )}
    </div>
  );
}
