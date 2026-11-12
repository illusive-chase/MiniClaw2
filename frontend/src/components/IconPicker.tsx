import { useState } from "react";
import { Check } from "lucide-react";

import {
  HUE_NAMES,
  PROJECT_HUES,
  glyphName,
  hueRingClass,
  hueSwatchClass,
  hueTextClass,
  searchGlyphs,
  type ProjectHue,
} from "../projectIcons";

/**
 * Glyph grid with search, plus the eight hue swatches. Shared by the home
 * sidebar, the new-project modal, and ProjectPanel.
 *
 * Either value may be null: the new-project form has no default, so nothing is
 * selected until the user picks.
 */
export function IconPicker({
  glyph,
  hue,
  disabled = false,
  onGlyph,
  onHue,
  glyphLabel = "图标",
  hueLabel = "颜色",
  missing = false,
}: {
  glyph: string | null;
  hue: string | null;
  disabled?: boolean;
  onGlyph: (glyph: string) => void;
  onHue: (hue: ProjectHue) => void;
  glyphLabel?: string;
  hueLabel?: string;
  /** Outline the fields still unset (new-project form only). */
  missing?: boolean;
}) {
  const [query, setQuery] = useState("");
  const categories = searchGlyphs(query);
  const selectedText = hue ? hueTextClass(hue) : "text-brand";
  return (
    <div className="flex flex-col gap-4">
      <div
        className={
          "rounded-xl " + (missing && !glyph ? "outline-dashed outline-[1.5px] outline-offset-4 outline-brand/70" : "")
        }
      >
        <div className="mb-1.5 flex items-baseline justify-between text-[12px] font-semibold text-ink-strong">
          {glyphLabel}
          <span className="text-[11px] font-normal text-ink-subtle">
            {glyph ? glyphName(glyph) : missing ? "必选" : ""}
          </span>
        </div>
        <div className="overflow-hidden rounded-[11px] border border-line bg-surface-sunken">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索图标：数据库、web、ai…"
            aria-label="搜索图标"
            disabled={disabled}
            className="h-[30px] w-full border-0 border-b border-line bg-transparent px-2.5 text-[12px] text-ink-strong placeholder:text-ink-subtle focus:outline-none disabled:opacity-55"
          />
          <div className="max-h-[228px] overflow-y-auto px-2 pb-2.5 pt-1">
            {categories.length === 0 && (
              <div className="mx-0.5 mt-2.5 text-[10.5px] text-ink-subtle">没有匹配的图标</div>
            )}
            {categories.map((category) => (
              <div key={category.id}>
                <div className="mx-0.5 mb-1.5 mt-2.5 text-[10.5px] tracking-[0.06em] text-ink-subtle">
                  {category.label}
                </div>
                <div className="grid grid-cols-[repeat(auto-fill,minmax(34px,1fr))] gap-1">
                  {category.glyphs.map((entry) => {
                    const Icon = entry.icon;
                    const on = entry.key === glyph;
                    return (
                      <button
                        key={entry.key}
                        type="button"
                        disabled={disabled}
                        onClick={() => onGlyph(entry.key)}
                        title={entry.keywords.split(" ")[0]}
                        aria-label={entry.keywords}
                        aria-pressed={on}
                        className={
                          "grid h-[34px] place-items-center rounded-lg transition-[background-color,color,box-shadow,transform] duration-[90ms] active:scale-[.94] disabled:cursor-not-allowed disabled:opacity-50 "
                          + (on
                            ? `bg-surface-raised ring-[1.5px] ring-inset ${hue ? hueRingClass(hue) : "ring-brand"} ${selectedText}`
                            : "text-ink-muted hover:bg-surface-raised hover:text-ink-strong")
                        }
                      >
                        <Icon size={20} strokeWidth={1.9} />
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div
        className={
          "rounded-xl " + (missing && !hue ? "outline-dashed outline-[1.5px] outline-offset-4 outline-brand/70" : "")
        }
      >
        <div className="mb-1.5 flex items-baseline justify-between text-[12px] font-semibold text-ink-strong">
          {hueLabel}
          <span className="text-[11px] font-normal text-ink-subtle">
            {hue && (PROJECT_HUES as readonly string[]).includes(hue)
              ? HUE_NAMES[hue as ProjectHue]
              : missing ? "必选" : ""}
          </span>
        </div>
        <div className="grid grid-cols-8 gap-2" role="radiogroup" aria-label={hueLabel}>
          {PROJECT_HUES.map((value) => {
            const on = value === hue;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={HUE_NAMES[value]}
                title={HUE_NAMES[value]}
                disabled={disabled}
                onClick={() => onHue(value)}
                className={
                  `grid aspect-square place-items-center rounded-full text-white transition-[box-shadow,transform] duration-[140ms] active:scale-[.92] disabled:cursor-not-allowed disabled:opacity-50 ${hueSwatchClass(value)} `
                  + (on ? `ring-2 ring-offset-2 ring-offset-surface-raised ${hueRingClass(value)}` : "")
                }
              >
                {on && <Check size={14} strokeWidth={3} className="home-badge-pop" />}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
