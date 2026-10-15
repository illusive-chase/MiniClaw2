/* The hover card: signature, then (for definitions) the docstring.
 *
 * No source excerpt and no file/line — the user chose signature and doc only.
 */

import { forwardRef, useState } from "react";

import type { CodeSymbol } from "../../types";

const DOC_FOLD_LINES = 20;
const DOC_KINDS = new Set(["class", "function", "method", "module"]);

/** Colour the leading `(kind)` and the defined name in a signature line. */
function SignatureLine({ text, kind, name }: { text: string; kind: string; name: string }) {
  const match = /^(\([^)]*\))\s(async\s)?/.exec(text);
  if (!match) return <>{text}</>;
  const head = match[0];
  const rest = text.slice(head.length);
  const at = rest.search(new RegExp(`(^|[.\\s])${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?=[(:\\s]|$)`));
  const nameStart = at < 0 ? -1 : rest[at] === "." || /\s/.test(rest[at]) ? at + 1 : at;
  const cls = `tok-${kind === "variable" || kind === "constant" ? (kind === "constant" ? "constant" : "variable") : kind}`;
  return (
    <>
      <span className="text-ink-muted">{match[1]}</span>{" "}
      {match[2] && <span className="tok-keyword">async </span>}
      {nameStart < 0 ? rest : (
        <>
          {rest.slice(0, nameStart)}
          <span className={cls}>{rest.slice(nameStart, nameStart + name.length)}</span>
          {rest.slice(nameStart + name.length)}
        </>
      )}
    </>
  );
}

export type HoverCardProps = {
  symbol: CodeSymbol;
  style: React.CSSProperties;
  onEnter: () => void;
  onLeave: () => void;
};

export const HoverCard = forwardRef<HTMLDivElement, HoverCardProps>(function HoverCard(
  { symbol, style, onEnter, onLeave },
  ref,
) {
  const [expanded, setExpanded] = useState(false);
  const lines = symbol.signature.split("\n");
  const doc = DOC_KINDS.has(symbol.kind) ? symbol.doc : null;
  const docLines = doc ? doc.split("\n") : [];
  const folded = !expanded && docLines.length > DOC_FOLD_LINES;
  return (
    <div
      ref={ref}
      role="tooltip"
      className="code-hover fixed z-50 max-w-[560px] overflow-hidden rounded-md border border-line-strong bg-surface-raised text-[12px] text-ink shadow-lg"
      style={style}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
    >
      <pre className="max-h-[6.5rem] overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[12px] leading-[1.6] text-ink-strong">
        {lines.map((line, index) => (
          <div key={index}>
            {line.startsWith("@")
              ? <span className="tok-decorator">{line}</span>
              : <SignatureLine text={line} kind={symbol.kind} name={symbol.name} />}
          </div>
        ))}
      </pre>
      {doc && (
        <div className="max-h-[22rem] overflow-auto border-t border-line px-3 py-2">
          <pre className="whitespace-pre-wrap break-words font-mono text-[11.5px] leading-[1.55] text-ink">
            {folded ? docLines.slice(0, DOC_FOLD_LINES).join("\n") : doc}
          </pre>
          {folded && (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="mt-1 text-[11px] text-brand hover:underline"
            >
              展开全部
            </button>
          )}
        </div>
      )}
      {symbol.external && (
        <div className="border-t border-line px-3 py-1 text-[10.5px] text-ink-subtle">
          {symbol.origin === "builtin" ? `内置 · Python ${symbol.python ?? "3"}` : "项目外部"}
        </div>
      )}
    </div>
  );
});
