/* The read-only code renderer.
 *
 * The whole file stays in the DOM, in 100-line chunks under
 * `content-visibility: auto`: off-screen chunks skip layout and paint but
 * remain findable by the browser's own Cmd+F, selectable and copyable.
 * Lines never wrap and have a fixed pixel height, so a scroll offset and a
 * line number convert exactly.
 *
 * Hover and the modifier-key underline do not go through React. One set of
 * delegated listeners on the scroller toggles classes on the target span,
 * so moving the mouse never re-renders a chunk.
 */

import { memo, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, forwardRef } from "react";

import type { LineSpans, Span } from "../../codeTokens";
import type { CodeSymbol, CodeTarget } from "../../types";
import { HoverCard } from "./HoverCard";

export const CHUNK_LINES = 100;
const HOVER_DELAY = 350;
const HOVER_HIDE_DELAY = 200;
const FLASH_MS = 1200;

export function lineHeightFor(font: number): number {
  return Math.round(font * 1.5);
}

export function isMacPlatform(): boolean {
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  const platform = nav.userAgentData?.platform || nav.platform || "";
  return /Mac|iPhone|iPad/i.test(platform);
}

/* ── chunks ───────────────────────────────────────────────────────────── */

function renderLine(text: string, spans: Span[] | undefined): React.ReactNode {
  if (!text.length) return <br />;
  if (!spans || !spans.length) return text;
  const out: React.ReactNode[] = [];
  let cursor = 0;
  spans.forEach((span, index) => {
    if (span.start < cursor || span.start >= text.length) return;
    if (span.start > cursor) out.push(text.slice(cursor, span.start));
    const end = Math.min(span.end, text.length);
    out.push(
      <span
        key={index}
        className={span.cls || undefined}
        data-r={span.ref !== undefined ? span.ref : undefined}
        data-c={span.start}
      >
        {text.slice(span.start, end)}
      </span>,
    );
    cursor = end;
  });
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

type ChunkProps = {
  start: number;
  lines: string[];
  spans: LineSpans | null;
  version: number;
  lineHeight: number;
};

const Chunk = memo(
  function Chunk({ start, lines, spans, lineHeight }: ChunkProps) {
    return (
      <div
        className="code-chunk"
        style={{
          contentVisibility: "auto",
          containIntrinsicSize: `auto ${lines.length * lineHeight}px`,
        } as React.CSSProperties}
      >
        {lines.map((text, index) => {
          const line = start + index;
          return (
            <div key={line} className="cl" data-ln={line + 1}>
              {renderLine(text, spans?.[line])}
            </div>
          );
        })}
      </div>
    );
  },
  (a, b) => a.lines === b.lines && a.version === b.version && a.lineHeight === b.lineHeight,
);

/* ── the view ─────────────────────────────────────────────────────────── */

export type CodeViewHandle = {
  /** Scroll to a 1-based line; flash `[col, endCol)` when given. */
  reveal: (line: number, range?: { col: number; endCol: number } | null, flash?: boolean) => void;
  restore: (scrollTop: number, topLine?: number) => void;
  position: () => { scrollTop: number; topLine: number };
};

export type CodeViewProps = {
  text: string;
  spans: LineSpans | null;
  spansVersion: number;
  symbols: CodeSymbol[];
  font: number;
  onNavigate: (target: CodeTarget, symbol: CodeSymbol) => void;
  onScroll?: () => void;
};

type HoverState = { symbol: CodeSymbol; rect: DOMRect } | null;

export const CodeView = forwardRef<CodeViewHandle, CodeViewProps>(function CodeView(
  { text, spans, spansVersion, symbols, font, onNavigate, onScroll },
  ref,
) {
  const scroller = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<HoverState>(null);
  const [cardStyle, setCardStyle] = useState<React.CSSProperties>({ visibility: "hidden" });
  const card = useRef<HTMLDivElement>(null);
  const lineHeight = lineHeightFor(font);
  const lines = useLineChunks(text);
  const lineCount = lines.count;
  const gutter = `${Math.max(String(lineCount).length, 3) + 2}ch`;

  const symbolsRef = useRef(symbols);
  symbolsRef.current = symbols;
  const navigateRef = useRef(onNavigate);
  navigateRef.current = onNavigate;

  /* ── delegated hover / link ─────────────────────────────────────────── */

  const state = useRef({
    hovered: null as HTMLElement | null,
    linked: null as HTMLElement | null,
    showTimer: 0,
    hideTimer: 0,
    overCard: false,
    lastX: -1,
    lastY: -1,
    modifier: false,
    downX: 0,
    downY: 0,
  });
  const mac = useRef(isMacPlatform());

  const clearLink = useCallback(() => {
    state.current.linked?.classList.remove("link");
    state.current.linked = null;
  }, []);

  const hideCard = useCallback(() => {
    window.clearTimeout(state.current.showTimer);
    window.clearTimeout(state.current.hideTimer);
    state.current.overCard = false;
    setHover(null);
  }, []);

  const symbolOf = (element: HTMLElement | null): CodeSymbol | null => {
    const raw = element?.dataset.r;
    if (raw === undefined) return null;
    return symbolsRef.current[Number(raw)] ?? null;
  };

  const applyLink = useCallback((element: HTMLElement | null) => {
    const s = state.current;
    const symbol = s.modifier ? symbolOf(element) : null;
    const next = symbol?.target ? element : null;
    if (next === s.linked) return;
    clearLink();
    if (next) {
      next.classList.add("link");
      s.linked = next;
    }
  }, [clearLink]);

  useEffect(() => {
    const node = scroller.current;
    if (!node) return;
    const s = state.current;
    const spanAt = (target: EventTarget | null): HTMLElement | null => {
      const element = target instanceof HTMLElement ? target.closest<HTMLElement>("[data-r]") : null;
      return element && node.contains(element) ? element : null;
    };
    const onMove = (event: MouseEvent) => {
      s.lastX = event.clientX;
      s.lastY = event.clientY;
      s.modifier = mac.current ? event.metaKey : event.ctrlKey;
      const element = spanAt(event.target);
      applyLink(element);
      if (element === s.hovered) return;
      s.hovered = element;
      window.clearTimeout(s.showTimer);
      if (element && symbolOf(element)) {
        window.clearTimeout(s.hideTimer);
        s.showTimer = window.setTimeout(() => {
          const symbol = symbolOf(element);
          if (symbol && element.isConnected) setHover({ symbol, rect: element.getBoundingClientRect() });
        }, HOVER_DELAY);
      } else {
        window.clearTimeout(s.hideTimer);
        s.hideTimer = window.setTimeout(() => {
          if (!s.overCard) setHover(null);
        }, HOVER_HIDE_DELAY);
      }
    };
    const onLeave = () => {
      s.hovered = null;
      clearLink();
      window.clearTimeout(s.showTimer);
      s.hideTimer = window.setTimeout(() => {
        if (!s.overCard) setHover(null);
      }, HOVER_HIDE_DELAY);
    };
    const onKey = (event: KeyboardEvent) => {
      const modifier = mac.current ? event.metaKey : event.ctrlKey;
      if (event.key === "Escape") hideCard();
      if (modifier === s.modifier) return;
      s.modifier = modifier;
      const element = s.lastX < 0 ? null : spanAt(document.elementFromPoint(s.lastX, s.lastY));
      applyLink(element);
    };
    const onBlur = () => {
      s.modifier = false;
      clearLink();
      hideCard();
    };
    const onDown = (event: MouseEvent) => {
      s.downX = event.clientX;
      s.downY = event.clientY;
      if (!(event.target instanceof Node) || !card.current?.contains(event.target)) hideCard();
    };
    const onClick = (event: MouseEvent) => {
      const modifier = mac.current ? event.metaKey : event.ctrlKey;
      if (!modifier || event.button !== 0) return;
      const element = spanAt(event.target);
      const symbol = symbolOf(element);
      const selection = window.getSelection();
      const dragged = Math.abs(event.clientX - s.downX) > 3 || Math.abs(event.clientY - s.downY) > 3;
      if (dragged && selection && !selection.isCollapsed) return;
      event.preventDefault();
      if (!symbol?.target || !element) return;
      if (element.classList.contains("tok-decl")) return;
      clearLink();
      hideCard();
      navigateRef.current(symbol.target, symbol);
    };
    const onScrollEvent = () => hideCard();
    node.addEventListener("mousemove", onMove);
    node.addEventListener("mouseleave", onLeave);
    node.addEventListener("mousedown", onDown);
    node.addEventListener("click", onClick);
    node.addEventListener("scroll", onScrollEvent, { passive: true });
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    window.addEventListener("blur", onBlur);
    return () => {
      node.removeEventListener("mousemove", onMove);
      node.removeEventListener("mouseleave", onLeave);
      node.removeEventListener("mousedown", onDown);
      node.removeEventListener("click", onClick);
      node.removeEventListener("scroll", onScrollEvent);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
      window.removeEventListener("blur", onBlur);
      window.clearTimeout(s.showTimer);
      window.clearTimeout(s.hideTimer);
    };
  }, [applyLink, clearLink, hideCard]);

  // New colouring replaces the spans the hover state points at.
  useEffect(() => {
    state.current.hovered = null;
    state.current.linked = null;
    setHover(null);
  }, [spansVersion, text]);

  /* Place the card above the token, or below when there is no room. */
  useLayoutEffect(() => {
    if (!hover) return;
    const element = card.current;
    if (!element) return;
    const { rect } = hover;
    const width = element.offsetWidth;
    const height = element.offsetHeight;
    const margin = 8;
    let left = Math.min(rect.left, window.innerWidth - width - margin);
    left = Math.max(margin, left);
    let top = rect.top - height - 4;
    if (top < margin) top = Math.min(rect.bottom + 4, window.innerHeight - height - margin);
    setCardStyle({ left, top: Math.max(margin, top) });
  }, [hover]);

  /* ── positioning ────────────────────────────────────────────────────── */

  const flashTimer = useRef(0);
  const highlightLine = (line: number) => {
    const node = scroller.current;
    if (!node) return null;
    node.querySelectorAll(".cl.target-line").forEach((element) => element.classList.remove("target-line"));
    const element = node.querySelector<HTMLElement>(`.cl[data-ln="${line}"]`);
    element?.classList.add("target-line");
    return element;
  };

  useImperativeHandle(ref, () => ({
    reveal(line, range, flash = true) {
      const node = scroller.current;
      if (!node) return;
      const clamped = Math.min(Math.max(1, line), lineCount);
      const top = (clamped - 1) * lineHeight;
      const visible = top >= node.scrollTop && top + lineHeight <= node.scrollTop + node.clientHeight;
      if (!visible) node.scrollTop = Math.max(0, top - node.clientHeight / 3);
      const lineElement = highlightLine(clamped);
      if (!flash || !range || !lineElement) return;
      const token = lineElement.querySelector<HTMLElement>(`[data-c="${range.col}"]`);
      if (!token) return;
      token.classList.add("flash");
      window.clearTimeout(flashTimer.current);
      flashTimer.current = window.setTimeout(() => token.classList.remove("flash"), FLASH_MS);
    },
    restore(scrollTop, topLine) {
      const node = scroller.current;
      if (!node) return;
      node.scrollTop = topLine && topLine > 1 ? (Math.min(topLine, lineCount) - 1) * lineHeight : scrollTop;
    },
    position() {
      const node = scroller.current;
      const scrollTop = node?.scrollTop ?? 0;
      return { scrollTop, topLine: Math.floor(scrollTop / lineHeight) + 1 };
    },
  }), [lineCount, lineHeight]);

  /* Copy exactly the source: the browser's own serialization of block
   * lines is not guaranteed to keep empty lines and tabs intact. */
  const onCopy = (event: React.ClipboardEvent<HTMLDivElement>) => {
    const selection = window.getSelection();
    const root = scroller.current?.querySelector(".code-lines");
    if (!selection || selection.isCollapsed || !root || selection.rangeCount === 0) return;
    const range = selection.getRangeAt(0);
    if (!root.contains(range.startContainer) && range.startContainer !== root) return;
    if (!root.contains(range.endContainer) && range.endContainer !== root) return;
    const start = pointToPosition(root, range.startContainer, range.startOffset, lines.all);
    const end = pointToPosition(root, range.endContainer, range.endOffset, lines.all);
    if (!start || !end) return;
    const copied = sliceLines(lines.all, start, end);
    event.preventDefault();
    event.clipboardData.setData("text/plain", copied);
  };

  /* Select-all stays inside the code. */
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const modifier = mac.current ? event.metaKey : event.ctrlKey;
    if (modifier && event.key.toLowerCase() === "a") {
      event.preventDefault();
      const node = scroller.current?.querySelector(".code-lines");
      if (!node) return;
      const range = document.createRange();
      range.selectNodeContents(node);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    }
  };

  return (
    <div
      ref={scroller}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onCopy={onCopy}
      onScroll={onScroll}
      className="code-reader code-scroll relative min-h-0 flex-1 overflow-auto bg-surface font-mono text-ink outline-none"
      style={{
        fontSize: font,
        lineHeight: `${lineHeight}px`,
        ["--code-gutter" as string]: gutter,
        ["--code-line" as string]: `${lineHeight}px`,
      }}
    >
      <div className="code-lines">
        {lines.chunks.map((chunk, index) => (
          <Chunk
            key={index}
            start={index * CHUNK_LINES}
            lines={chunk}
            spans={spans}
            version={spansVersion}
            lineHeight={lineHeight}
          />
        ))}
      </div>
      {hover && (
        <HoverCard
          ref={card}
          key={`${hover.symbol.qualname}:${hover.rect.top}:${hover.rect.left}`}
          symbol={hover.symbol}
          style={cardStyle}
          onEnter={() => {
            state.current.overCard = true;
            window.clearTimeout(state.current.hideTimer);
          }}
          onLeave={() => {
            state.current.overCard = false;
            state.current.hideTimer = window.setTimeout(() => setHover(null), HOVER_HIDE_DELAY);
          }}
        />
      )}
    </div>
  );
});

type Lines = { all: string[]; chunks: string[][]; count: number };

/** Split the text into 100-line chunks once per text. */
function useLineChunks(text: string): Lines {
  const cache = useRef<{ text: string; value: Lines } | null>(null);
  if (cache.current?.text !== text) {
    const all = splitLines(text);
    const chunks: string[][] = [];
    for (let start = 0; start < all.length; start += CHUNK_LINES) chunks.push(all.slice(start, start + CHUNK_LINES));
    cache.current = { text, value: { all, chunks, count: all.length } };
  }
  return cache.current.value;
}

/** A trailing newline ends the last line rather than starting another. */
export function splitLines(text: string): string[] {
  return text.endsWith("\n") ? text.slice(0, -1).split("\n") : text.split("\n");
}

type Position = { line: number; col: number };

/** Map a DOM selection point inside `.code-lines` to a source position. */
function pointToPosition(root: Element, container: Node, offset: number, all: string[]): Position | null {
  const element = container instanceof Element ? container : container.parentElement;
  const line = element?.closest<HTMLElement>(".cl");
  if (line && root.contains(line)) {
    const index = Number(line.dataset.ln) - 1;
    const range = document.createRange();
    range.setStart(line, 0);
    range.setEnd(container, offset);
    return { line: index, col: Math.min(range.toString().length, all[index]?.length ?? 0) };
  }
  // A boundary between chunks or lines (e.g. select-all): pick the line there.
  const host = container instanceof Element ? container : null;
  if (!host) return null;
  const child = host.childNodes[offset] as Element | undefined;
  const next = child instanceof Element ? (child.matches(".cl") ? child : child.querySelector(".cl")) : null;
  if (next instanceof HTMLElement) return { line: Number(next.dataset.ln) - 1, col: 0 };
  const lines = host.querySelectorAll<HTMLElement>(".cl");
  const last = lines[lines.length - 1];
  if (!last) return null;
  const index = Number(last.dataset.ln) - 1;
  return { line: index, col: all[index]?.length ?? 0 };
}

export function sliceLines(all: string[], start: Position, end: Position): string {
  if (start.line === end.line) return (all[start.line] ?? "").slice(start.col, end.col);
  const parts = [(all[start.line] ?? "").slice(start.col)];
  for (let line = start.line + 1; line < end.line; line += 1) parts.push(all[line] ?? "");
  parts.push((all[end.line] ?? "").slice(0, end.col));
  return parts.join("\n");
}
