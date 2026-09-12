/* Where a Markdown link should be handled: here, or by the backend.
 *
 * The frontend makes exactly one decision — is this href the browser's
 * business? Everything else (what a relative path is relative to, whether the
 * target exists, whether it is inside the project) is only knowable on the
 * machine the backend runs on, so those hrefs are sent to `/files/resolve`
 * and the verdict is obeyed.
 */

import { defaultUrlTransform } from "react-markdown";

export type LinkKind = "external" | "anchor" | "local";

/** Schemes the browser already handles correctly on its own. */
const BROWSER_SCHEMES = /^(https?|mailto|tel|data|blob):/i;
const SOURCE_LOCATION_SUFFIX = /:[1-9]\d*(?::[1-9]\d*)?(?:[?#].*)?$/;

export function classifyHref(href: string): LinkKind {
  const trimmed = href.trim();
  if (!trimmed) return "local";
  if (trimmed.startsWith("#")) return "anchor";
  if (BROWSER_SCHEMES.test(trimmed)) return "external";
  /* `//host/path` is a scheme-relative URL, which the browser resolves against
   * the current origin's scheme. Unix would read it as an absolute filesystem
   * path instead, so the backend must never see it. */
  if (trimmed.startsWith("//")) return "external";
  /* `file://` is deliberately NOT external: the browser refuses to navigate
   * to it from an http origin, so it has to go through the backend like any
   * other local path. */
  return "local";
}

/* `react-markdown` blanks the href of any scheme outside its safe list, and
 * `file:` is outside it. That default is right for a page that lets the
 * browser follow links, but here every non-browser href is intercepted and
 * handed to the backend instead, so a blanked href only loses the target
 * before the click handler can see it. Nothing is navigated to directly, and
 * `javascript:` and friends stay blanked, so admitting `file:` costs nothing
 * and is the only way the backend's `file://` support is reachable at all. */
export function markdownUrlTransform(url: string): string {
  if (/^file:/i.test(url.trim())) return url;
  const safe = defaultUrlTransform(url);
  if (safe || !SOURCE_LOCATION_SUFFIX.test(url)) return safe;
  /* A root-level filename such as `README.md:12` looks like an unknown scheme
   * to the default sanitizer. Encode its colons so the browser keeps it inert
   * while the click handler passes it to the backend, which decodes local
   * hrefs before resolving them. */
  return url.replace(/:/g, "%3A");
}

/* ── the reading page's hash route ──────────────────────────────────────
 *
 * A hash, not a path. In production the backend serves `frontend/dist`
 * through `StaticFiles(html=True)` mounted at `/`, whose html=True means
 * "serve index.html for a *directory*" — not SPA fallback. `/md?...` finds
 * neither `dist/md` nor `dist/md/index.html` and 404s. Vite's dev server does
 * fall back to index.html, so a path route works in dev and white-screens in
 * production. A hash never reaches the server at all.
 */

export type MarkdownRoute =
  | { src: "artifact"; sessionId: string; nodeId: string; name: string }
  | { src: "project-file"; sessionId: string; path: string }
  | { src: "stash"; key: string };

export function parseMarkdownRoute(hash: string): MarkdownRoute | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!raw.startsWith("/md")) return null;
  const query = raw.slice(raw.indexOf("?") + 1);
  if (!raw.includes("?")) return null;
  const params = new URLSearchParams(query);
  const src = params.get("src");
  const session = params.get("session") ?? "";

  if (src === "artifact") {
    const nodeId = params.get("node") ?? "";
    const name = params.get("name") ?? "";
    if (!session || !nodeId || !name) return null;
    return { src, sessionId: session, nodeId, name };
  }
  if (src === "project-file") {
    const path = params.get("path") ?? "";
    if (!session || !path) return null;
    return { src, sessionId: session, path };
  }
  if (src === "stash") {
    const key = params.get("key") ?? "";
    if (!key) return null;
    return { src, key };
  }
  return null;
}

export function markdownRouteUrl(route: MarkdownRoute): string {
  const params = new URLSearchParams();
  params.set("src", route.src);
  if (route.src === "artifact") {
    params.set("session", route.sessionId);
    params.set("node", route.nodeId);
    params.set("name", route.name);
  } else if (route.src === "project-file") {
    params.set("session", route.sessionId);
    params.set("path", route.path);
  } else {
    params.set("key", route.key);
  }
  return `${window.location.pathname}#/md?${params.toString()}`;
}
