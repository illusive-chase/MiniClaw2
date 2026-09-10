import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { MarkdownViewerPage } from "./pages/MarkdownViewerPage";
import { parseMarkdownRoute } from "./markdownRoute";
import "./index.css";
// Single hljs theme: github (light). Dark mode tolerates the slight contrast
// difference against warm graphite; index.css adds minor overrides for the
// hljs tokens that most need adjustment on dark.
import "highlight.js/styles/github.css";

/* The Markdown reading tab is chosen here rather than inside `App` so that it
 * never enters App's lifecycle: mounting App opens a workspace WebSocket,
 * fetches the project list, and binds global shortcuts, none of which a
 * read-only page needs — and it can be opened with no session at all. */
const route = parseMarkdownRoute(window.location.hash);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {route ? <MarkdownViewerPage route={route} /> : <App />}
  </React.StrictMode>,
);
