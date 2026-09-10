/* The standalone Markdown reading page.
 *
 * Reached only through a hash route, and mounted instead of `App` rather than
 * inside it: `App` opens a workspace WebSocket, fetches the project list, and
 * installs global shortcuts on mount, none of which a read-only view needs —
 * and it may be opened with no session at all.
 *
 * The column is capped at 78ch, which is the one deliberate difference from
 * the embedded views. Those are boxed into a 380px panel; this one would
 * otherwise run 200+ characters per line on a wide display, which is the
 * problem it exists to solve. Everything that makes it *look* the same —
 * `.md-prose`, the theme tokens, the highlight theme, the plugin set — is
 * shared through `MarkdownView` and `index.css`.
 */

import { useEffect, useState } from "react";

import { getNodeArtifact, readMarkdownFile } from "../api";
import { writeClipboard } from "../clipboard";
import { FontSizeControl } from "../components/FontSizeControl";
import { MarkdownView } from "../components/MarkdownView";
import { defaultFontIndex, fontPxAt } from "../markdownFont";
import type { MarkdownRoute } from "../markdownRoute";
import { readStashedMarkdown } from "../mdHandoff";
import { applyStoredTheme } from "../theme";
import type { MarkdownLinkBase } from "../types";

type Loaded = {
  title: string;
  subtitle: string;
  text: string;
  linkBase: MarkdownLinkBase | null;
  /** Session local links resolve against. Stash routes carry it in the
   *  payload; URL routes have it in the route itself. */
  sessionId: string | null;
  truncated?: boolean;
};

const PAGE_DEFAULT_INDEX = defaultFontIndex("page");

export function MarkdownViewerPage({ route }: { route: MarkdownRoute }) {
  const [doc, setDoc] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fontIndex, setFontIndex] = useState(PAGE_DEFAULT_INDEX);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    applyStoredTheme();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setDoc(null);
    setError(null);

    const load = async (): Promise<Loaded> => {
      if (route.src === "artifact") {
        const file = await getNodeArtifact(route.sessionId, route.nodeId, route.name);
        return {
          title: route.name,
          subtitle: `node ${route.nodeId.slice(0, 8)}`,
          text: file.text ?? "",
          truncated: file.truncated,
          sessionId: route.sessionId,
          /* Relative links in an artifact are written by the agent that
           * produced it, so they resolve against its output directory. */
          linkBase: {
            kind: "artifact",
            path: `.miniclaw2/outputs/${route.nodeId}`,
          },
        };
      }
      if (route.src === "project-file") {
        const file = await readMarkdownFile(route.sessionId, route.path);
        return {
          title: route.path.split("/").pop() ?? route.path,
          subtitle: file.path,
          text: file.text,
          truncated: file.truncated,
          sessionId: route.sessionId,
          linkBase: { kind: "project-file", path: file.path },
        };
      }
      const stashed = readStashedMarkdown(route.key);
      if (!stashed) {
        throw new Error(
          "这段内容来自一个已经关闭或过期的视图，请回到主界面重新打开。",
        );
      }
      return {
        title: stashed.title,
        subtitle: stashed.subtitle ?? "",
        text: stashed.text,
        linkBase: stashed.linkBase ?? null,
        sessionId: stashed.sessionId ?? null,
      };
    };

    void load()
      .then((loaded) => {
        if (cancelled) return;
        setDoc(loaded);
        document.title = `${loaded.title} · MiniClaw2`;
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "加载失败");
      });

    return () => {
      cancelled = true;
    };
  }, [route]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <div className="min-h-screen bg-surface text-ink">
      <header className="sticky top-0 z-10 border-b border-line bg-surface-raised/95 backdrop-blur">
        <div className="mx-auto flex max-w-[78ch] items-center justify-between gap-4 px-5 py-3">
          <div className="min-w-0">
            <div className="truncate font-display text-sm font-semibold text-ink-strong">
              {doc?.title ?? (error ? "无法打开" : "加载中…")}
            </div>
            <div className="mt-0.5 truncate font-mono text-[10px] text-ink-subtle">
              {doc ? `${doc.subtitle ? `${doc.subtitle} · ` : ""}${doc.text.length} chars` : ""}
            </div>
          </div>
          <div className="flex flex-none items-center gap-2">
            <FontSizeControl
              index={fontIndex}
              onChange={setFontIndex}
              defaultIndex={PAGE_DEFAULT_INDEX}
            />
            <button
              type="button"
              disabled={!doc}
              onClick={() => {
                if (!doc) return;
                void writeClipboard(doc.text).then(() => setCopied(true));
              }}
              className="inline-flex h-7 items-center rounded-md border border-line bg-surface px-2.5 text-[11px] font-medium text-ink-muted transition hover:border-line-strong hover:text-ink-strong disabled:opacity-40"
              title="复制全文"
            >
              <span aria-live="polite">{copied ? "已复制" : "Copy"}</span>
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[78ch] px-5 py-6">
        {error ? (
          <div className="rounded-md border border-state-error/30 bg-state-error-soft px-4 py-3 text-[12.5px] text-state-error">
            {error}
          </div>
        ) : !doc ? (
          <div className="rounded-md border border-line bg-surface-sunken px-4 py-3 text-[12.5px] text-ink-muted">
            正在加载…
          </div>
        ) : (
          <>
            {doc.truncated && (
              <div className="mb-3 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] text-state-waiting">
                文件过大，仅显示开头部分。
              </div>
            )}
            <MarkdownView
              text={doc.text}
              density="page"
              fontPx={fontPxAt(fontIndex)}
              sessionId={doc.sessionId}
              linkBase={doc.linkBase}
              className="leading-relaxed text-ink-strong"
            />
          </>
        )}
      </main>
    </div>
  );
}
