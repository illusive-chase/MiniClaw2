import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./index.css";
// Single hljs theme: github (light). Dark mode tolerates the slight contrast
// difference against warm graphite; index.css adds minor overrides for the
// hljs tokens that most need adjustment on dark.
import "highlight.js/styles/github.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
