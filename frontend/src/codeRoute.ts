/* The code-reading page's hash route: `#/code?session=<sid>`.
 *
 * A hash for the same reason as the Markdown page (see `markdownRoute.ts`):
 * production serves `dist/` without SPA fallback, so a path route would 404.
 */

export type CodeRoute = { sessionId: string };

export function parseCodeRoute(hash: string): CodeRoute | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  if (raw !== "/code" && !raw.startsWith("/code?")) return null;
  const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
  const session = new URLSearchParams(query).get("session") ?? "";
  return session ? { sessionId: session } : null;
}

export function codeRouteUrl(sessionId: string): string {
  const params = new URLSearchParams({ session: sessionId });
  return `${window.location.pathname}#/code?${params.toString()}`;
}

/** One reading tab per project: a second click focuses the existing one. */
export function codeWindowName(sessionId: string): string {
  return `miniclaw2-code-${sessionId}`;
}
