import type { NodeSearchField } from "./types";

/* Mirrors `node_search.split_terms` on the backend: whitespace plus the CJK
 * punctuation people type between words. Kept identical so the terms the
 * panel highlights are exactly the ones the server matched on. */
const TERM_SPLIT = /[\s，。、；：！？,;]+/u;
const MAX_TERMS = 8;

export function splitSearchTerms(query: string): string[] {
  const out: string[] = [];
  for (const raw of query.split(TERM_SPLIT)) {
    const term = raw.trim().toLowerCase();
    if (term && !out.includes(term)) out.push(term);
  }
  return out.slice(0, MAX_TERMS);
}

export type HighlightSegment = { text: string; hit: boolean };

/** Split `text` into alternating plain / matched runs, case-insensitively.
 * Overlapping hits merge, so "abc" with terms ["ab", "bc"] is one run. */
export function highlightSegments(text: string, terms: string[]): HighlightSegment[] {
  const ranges = termRanges(text, terms);
  if (ranges.length === 0) return text ? [{ text, hit: false }] : [];
  const out: HighlightSegment[] = [];
  let cursor = 0;
  for (const [start, end] of ranges) {
    if (start > cursor) out.push({ text: text.slice(cursor, start), hit: false });
    out.push({ text: text.slice(start, end), hit: true });
    cursor = end;
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor), hit: false });
  return out;
}

/** Sorted, merged `[start, end)` offsets of every term occurrence. */
export function termRanges(text: string, terms: string[]): Array<[number, number]> {
  const lowered = text.toLowerCase();
  /* `toLowerCase` can change length for a few code points; offsets found in
   * the lowered copy only map back onto the original when it did not. */
  if (lowered.length !== text.length) return [];
  const raw: Array<[number, number]> = [];
  for (const term of terms) {
    if (!term) continue;
    let from = 0;
    for (;;) {
      const index = lowered.indexOf(term, from);
      if (index < 0) break;
      raw.push([index, index + term.length]);
      from = index + term.length;
    }
  }
  raw.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged: Array<[number, number]> = [];
  for (const range of raw) {
    const last = merged.at(-1);
    if (last && range[0] <= last[1]) {
      last[1] = Math.max(last[1], range[1]);
    } else {
      merged.push([range[0], range[1]]);
    }
  }
  return merged;
}

export function searchFieldLabel(field: NodeSearchField): string {
  switch (field) {
    case "motivation":
      return "运行原因";
    case "summary":
      return "结果摘要";
    case "next_implications":
      return "后续影响";
    case "prompt_draft":
      return "Prompt 草稿";
    case "text":
      return "回复";
  }
}

/** Where the inspector should scroll once a search hit opens its node. */
export type NodeSearchFocusRequest = {
  nodeId: string;
  target: "preview" | "text";
  /** Which matching text block, counted in transcript order. */
  textIndex: number;
  terms: string[];
  version: number;
};

export const SEARCH_HIGHLIGHT_NAME = "node-search";

/** Paint every term occurrence under `root` with the CSS Custom Highlight
 * API. A no-op where the API is missing: the flash on the block still
 * shows where the hit is. */
export function paintSearchHighlights(root: Element, terms: string[]): void {
  const registry = typeof CSS !== "undefined" ? CSS.highlights : undefined;
  if (!registry || typeof Highlight === "undefined") return;
  const ranges: Range[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue ?? "";
    for (const [start, end] of termRanges(text, terms)) {
      const range = document.createRange();
      range.setStart(node, start);
      range.setEnd(node, end);
      ranges.push(range);
    }
  }
  registry.set(SEARCH_HIGHLIGHT_NAME, new Highlight(...ranges));
}

export function clearSearchHighlights(): void {
  if (typeof CSS === "undefined") return;
  CSS.highlights?.delete(SEARCH_HIGHLIGHT_NAME);
}
