import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { AuthGate } from "./AuthGate";
import { MarkdownViewerPage } from "./pages/MarkdownViewerPage";
import { DiffViewerPage } from "./pages/DiffViewerPage";
import { parseMarkdownRoute } from "./markdownRoute";
import { parseCodeRoute } from "./codeRoute";
import "./index.css";
// Single hljs theme: github (light). Dark mode tolerates the slight contrast
// difference against warm graphite; index.css adds minor overrides for the
// hljs tokens that most need adjustment on dark.
import "highlight.js/styles/github.css";
// KaTeX's stylesheet and fonts for math in MarkdownView. Imported here rather
// than in the component so the esbuild-bundled node tests that reach
// MarkdownView never have to load CSS or font files.
import "katex/dist/katex.min.css";

/* The Markdown reading tab is chosen here rather than inside `App` so that it
 * never enters App's lifecycle: mounting App opens a workspace WebSocket,
 * fetches the project list, and binds global shortcuts, none of which a
 * read-only page needs — and it can be opened with no session at all. */
const codeRoute = parseCodeRoute(window.location.hash);
const route = codeRoute ? null : parseMarkdownRoute(window.location.hash);
/* Lazy so lowlight and the reader stay out of the canvas bundle. */
const CodeReaderPage = lazy(() => import("./pages/CodeReaderPage"));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthGate>
      {codeRoute
        ? (
          <Suspense fallback={<div className="min-h-screen bg-surface p-6 text-sm text-ink-muted">加载中...</div>}>
            <CodeReaderPage route={codeRoute} />
          </Suspense>
        )
        : route?.src === "diff"
          ? <DiffViewerPage route={route} />
          : route
            ? <MarkdownViewerPage route={route} />
            : <App />}
    </AuthGate>
  </React.StrictMode>,
);
