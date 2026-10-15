/* Colouring data for the code-reading renderer.
 *
 * Both sources end up as the same thing: per line, a sorted list of
 * non-overlapping spans `{start, end, cls, ref?}` with UTF-16 offsets into
 * the line (JS string indices). Lexical colour comes from lowlight; the
 * Python analysis replaces it wholesale once its hash matches the text.
 */

import type { CodeAnalysis } from "./types";

export type Span = { start: number; end: number; cls: string; ref?: number };
export type LineSpans = Span[][];

/** Token type ids, shared with `backend/miniclaw2/code_analysis.py`. */
export const TOKEN_CLASSES: Record<number, string> = {
  0: "tok-keyword",
  1: "tok-string",
  2: "tok-number",
  3: "tok-comment",
  4: "tok-operator",
  5: "tok-decorator",
  6: "tok-module",
  7: "tok-class",
  8: "tok-function",
  9: "tok-method",
  10: "tok-property",
  11: "tok-parameter",
  12: "tok-variable",
  13: "tok-constant",
  14: "tok-typeParameter",
  16: "tok-selfParameter",
};

export const MOD_DECLARATION = 1;

/** Expand the relative encoding into per-line spans. */
export function decodeAnalysis(analysis: CodeAnalysis, lineCount: number): LineSpans {
  const lines: LineSpans = Array.from({ length: lineCount }, () => []);
  const refs = new Map<number, number>();
  for (let index = 0; index + 1 < analysis.refs.length; index += 2) {
    refs.set(analysis.refs[index], analysis.refs[index + 1]);
  }
  const tokens = analysis.tokens;
  let line = 1;
  let col = 0;
  for (let index = 0, position = 0; index + 4 < tokens.length; index += 5, position += 1) {
    const deltaLine = tokens[index];
    const deltaCol = tokens[index + 1];
    if (deltaLine) {
      line += deltaLine;
      col = deltaCol;
    } else {
      col += deltaCol;
    }
    const length = tokens[index + 2];
    const type = tokens[index + 3];
    const mods = tokens[index + 4];
    if (line < 1 || line > lineCount) continue;
    let cls = TOKEN_CLASSES[type] ?? "";
    if (cls && mods & MOD_DECLARATION) cls += " tok-decl";
    const ref = refs.get(position);
    if (!cls && ref === undefined) continue;
    lines[line - 1].push(ref === undefined ? { start: col, end: col + length, cls } : { start: col, end: col + length, cls, ref });
  }
  return lines;
}

/* ── lowlight hast → per-line spans ───────────────────────────────────── */

type HastText = { type: "text"; value: string };
type HastElement = {
  type: "element";
  properties?: { className?: string[] | string };
  children: HastNode[];
};
type HastNode = HastText | HastElement | { type: string; children?: HastNode[] };
export type HastRoot = { children: HastNode[] };

function classOf(node: HastElement): string {
  const raw = node.properties?.className;
  const names = Array.isArray(raw) ? raw : raw ? [raw] : [];
  // `hljs-title function_` → `hljs-title.function_`, matching the CSS map.
  const [head, ...rest] = names;
  if (!head) return "";
  return [head, ...rest].join(".");
}

/** Split a hast tree into lines; a span crossing a newline is closed and
 *  reopened on the next line, so no rendered span crosses a line. */
export function hastToLines(root: HastRoot, lineCount: number): LineSpans {
  const lines: LineSpans = Array.from({ length: Math.max(1, lineCount) }, () => []);
  let line = 0;
  let col = 0;
  const walk = (nodes: HastNode[], stack: string[]) => {
    for (const node of nodes) {
      if (node.type === "text") {
        const value = (node as HastText).value;
        const parts = value.split("\n");
        parts.forEach((part, index) => {
          if (index > 0) {
            line += 1;
            col = 0;
          }
          if (part.length && stack.length && line < lines.length) {
            lines[line].push({ start: col, end: col + part.length, cls: stack[stack.length - 1] });
          }
          col += part.length;
        });
      } else if (node.type === "element") {
        const element = node as HastElement;
        const cls = classOf(element);
        walk(element.children, cls ? [...stack, cls] : stack);
      } else if ("children" in node && node.children) {
        walk(node.children, stack);
      }
    }
  };
  walk(root.children, []);
  return lines;
}

/* ── languages ────────────────────────────────────────────────────────── */

const LANGUAGE_BY_EXTENSION: Record<string, string> = {
  py: "python", pyi: "python", ts: "typescript", tsx: "typescript", mts: "typescript",
  cts: "typescript", js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
  json: "json", yaml: "yaml", yml: "yaml", md: "markdown", markdown: "markdown",
  sh: "bash", bash: "bash", zsh: "bash", toml: "ini", ini: "ini", cfg: "ini",
  css: "css", scss: "scss", less: "less", html: "xml", htm: "xml", xml: "xml", svg: "xml",
  rs: "rust", go: "go", java: "java", kt: "kotlin", c: "c", h: "c", cpp: "cpp", cc: "cpp",
  hpp: "cpp", cs: "csharp", rb: "ruby", php: "php", swift: "swift", sql: "sql",
  lua: "lua", pl: "perl", r: "r", diff: "diff", patch: "diff", mk: "makefile",
  graphql: "graphql", vb: "vbnet", m: "objectivec",
};

const LANGUAGE_BY_NAME: Record<string, string> = {
  Makefile: "makefile", Dockerfile: "dockerfile", "CMakeLists.txt": "cmake",
};

export function languageFor(path: string): string | null {
  const name = path.split("/").pop() ?? path;
  if (LANGUAGE_BY_NAME[name]) return LANGUAGE_BY_NAME[name];
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return null;
  return LANGUAGE_BY_EXTENSION[name.slice(dot + 1).toLowerCase()] ?? null;
}

/** Above this the page shows plain text: the design's highlight cut-off. */
export const HIGHLIGHT_MAX_BYTES = 1024 * 1024;
export const HIGHLIGHT_MAX_LINES = 20_000;
