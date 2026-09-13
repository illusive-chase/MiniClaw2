import { resolveMarkdownLink, revealPath } from "./api";
import { markdownRouteUrl } from "./markdownRoute";
import type { MarkdownLinkBase } from "./types";

export type MarkdownLinkNote = {
  kind: "error" | "info";
  text: string;
  href?: string;
};

export async function followMarkdownLink(
  sessionId: string,
  href: string,
  linkBase?: MarkdownLinkBase | null,
): Promise<MarkdownLinkNote | null> {
  try {
    const verdict = await resolveMarkdownLink(sessionId, href, linkBase);
    if (verdict.verdict === "markdown" && verdict.relative_path) {
      const url = new URL(
        markdownRouteUrl({
          src: "project-file",
          sessionId,
          path: verdict.relative_path,
        }),
        window.location.href,
      ).href;
      const fallback: MarkdownLinkNote = {
        kind: "info",
        text: "未能自动打开阅读页，请点击链接阅读。",
        href: url,
      };
      try {
        const reader = window.open(url, "_blank");
        if (!reader) return fallback;
        reader.opener = null;
        return null;
      } catch {
        return fallback;
      }
    }
    if (verdict.verdict === "reveal" && verdict.path) {
      await revealPath(sessionId, verdict.path);
      return { kind: "info", text: `已在文件管理器中显示：${verdict.path}` };
    }
    return {
      kind: "error",
      text: verdict.reason ?? `找不到该路径：${href}`,
    };
  } catch (err) {
    return {
      kind: "error",
      text: err instanceof Error ? err.message : `无法打开链接：${href}`,
    };
  }
}
