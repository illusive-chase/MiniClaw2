/* The code-reading tab strip: one tab per file, italic for the preview. */

import { X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import { tabTitles, type CodeTab } from "../../codeTabs";

export type TabBarProps = {
  tabs: CodeTab[];
  active: string | null;
  stale: Set<string>;
  onActivate: (path: string) => void;
  onPin: (path: string) => void;
  onClose: (path: string) => void;
};

export function TabBar({ tabs, active, stale, onActivate, onPin, onClose }: TabBarProps) {
  const titles = useMemo(() => tabTitles(tabs.map((tab) => tab.path)), [tabs]);
  const strip = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!active) return;
    strip.current
      ?.querySelector<HTMLElement>(`[data-tab="${CSS.escape(active)}"]`)
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [active, tabs]);

  if (!tabs.length) return null;
  return (
    <div
      ref={strip}
      role="tablist"
      aria-label="打开的文件"
      className="code-tabs flex h-9 shrink-0 items-stretch overflow-x-auto overflow-y-hidden border-b border-line bg-surface-sunken"
    >
      {tabs.map((tab) => {
        const title = titles.get(tab.path) ?? { name: tab.path, hint: "" };
        const isActive = tab.path === active;
        return (
          <div
            key={tab.path}
            data-tab={tab.path}
            role="tab"
            aria-selected={isActive}
            title={tab.path}
            onClick={() => onActivate(tab.path)}
            onDoubleClick={() => onPin(tab.path)}
            onAuxClick={(event) => {
              if (event.button === 1) {
                event.preventDefault();
                onClose(tab.path);
              }
            }}
            onMouseDown={(event) => {
              if (event.button === 1) event.preventDefault();
            }}
            className={
              "group relative flex shrink-0 cursor-pointer select-none items-center gap-1.5 border-r border-line pl-3 pr-1.5 text-[12px] " +
              (isActive ? "bg-surface text-ink-strong" : "text-ink-muted hover:bg-surface-raised hover:text-ink")
            }
          >
            {isActive && <span className="absolute inset-x-0 top-0 h-[2px] bg-brand" aria-hidden="true" />}
            <span className={tab.preview ? "italic" : ""}>{title.name}</span>
            {title.hint && <span className="text-[11px] text-ink-subtle">{title.hint}</span>}
            {stale.has(tab.path) && (
              <span
                className="h-1.5 w-1.5 rounded-full bg-state-waiting"
                title="分析结果与当前内容不一致，重新打开以刷新"
                aria-label="分析结果与当前内容不一致，重新打开以刷新"
              />
            )}
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onClose(tab.path);
              }}
              className={
                "ml-0.5 inline-flex h-5 w-5 items-center justify-center rounded text-ink-muted hover:bg-surface-sunken hover:text-ink " +
                (isActive ? "" : "opacity-0 group-hover:opacity-100 focus:opacity-100")
              }
              title="关闭"
              aria-label={`关闭 ${tab.path}`}
            >
              <X size={12} strokeWidth={2} aria-hidden="true" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
