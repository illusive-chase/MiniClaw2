import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { Tag } from "../types";
import {
  TAG_COLORS,
  defaultColorForName,
  tagDotClass,
  tagSwatchClass,
  type TagColor,
} from "../tagPalette";

const PANEL_WIDTH = 244;
const VIEWPORT_MARGIN = 8;

export type TagEditPopoverProps = {
  /** Element the panel is positioned against — usually the button that opened it. */
  anchor: HTMLElement | null;
  tags: Tag[];
  selectedIds: readonly string[];
  onClose: () => void;
  /** Persist the project's tag set. Rejections surface inline. */
  onApply: (tagIds: string[]) => Promise<void>;
  /** Create a global tag, then attach it to this project. */
  onCreateTag: (name: string, color: TagColor) => Promise<Tag>;
  /** Recolor an existing global tag; affects every project using it. */
  onRecolorTag?: (tagId: string, color: TagColor) => Promise<void>;
};

/**
 * Tag assignment panel: check existing tags, create one, or recolor one.
 *
 * Portalled to `document.body` because it can be opened from inside the side
 * panel, and that panel animates its slide-in with `transform` + `will-change`
 * (`App.tsx:2600`). Either property makes the panel a containing block for
 * `position: fixed`, which would clip this popover to the panel's 380px column
 * instead of the viewport. `EntryPickerModal` was changed for the same reason.
 */
export function TagEditPopover({
  anchor,
  tags,
  selectedIds,
  onClose,
  onApply,
  onCreateTag,
  onRecolorTag,
}: TagEditPopoverProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paletteFor, setPaletteFor] = useState<string | null>(null);

  const selected = new Set(selectedIds);

  /* Anchored to the trigger and clamped into the viewport, measured after paint
   * so the real panel height is known — the panel grows with the tag list, and
   * flipping above the trigger needs that height to be accurate. */
  useLayoutEffect(() => {
    if (!anchor) return;
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const height = panelRef.current?.offsetHeight ?? 260;
      const spaceBelow = window.innerHeight - rect.bottom;
      const top = spaceBelow >= height + VIEWPORT_MARGIN
        ? rect.bottom + 6
        : Math.max(VIEWPORT_MARGIN, rect.top - height - 6);
      const left = Math.min(
        Math.max(VIEWPORT_MARGIN, rect.left),
        Math.max(VIEWPORT_MARGIN, window.innerWidth - PANEL_WIDTH - VIEWPORT_MARGIN),
      );
      setPosition({ top, left });
    };
    place();
    window.addEventListener("resize", place);
    /* Capture phase: the landing grid and the side panel each scroll in their
     * own container, so a bubbling listener on window would miss them. */
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [anchor, tags.length, paletteFor]);

  useEffect(() => {
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (panelRef.current?.contains(target)) return;
      if (anchor?.contains(target)) return;
      onClose();
    };
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("mousedown", onPointerDown, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("mousedown", onPointerDown, true);
    };
  }, [anchor, onClose]);

  const run = useCallback(
    async (action: () => Promise<void>) => {
      if (busy) return;
      setBusy(true);
      setError(null);
      try {
        await action();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [busy],
  );

  const toggle = (tagId: string) =>
    void run(async () => {
      const next = selected.has(tagId)
        ? selectedIds.filter((id) => id !== tagId)
        : [...selectedIds, tagId];
      await onApply(next);
    });

  const create = () => {
    const name = draft.trim();
    if (!name) return;
    void run(async () => {
      const created = await onCreateTag(name, defaultColorForName(name));
      setDraft("");
      await onApply([...selectedIds, created.id]);
    });
  };

  const recolor = (tagId: string, color: TagColor) => {
    if (!onRecolorTag) return;
    void run(async () => {
      await onRecolorTag(tagId, color);
      setPaletteFor(null);
    });
  };

  /* The name a user is typing previews in its assigned color, so the default
   * color is visible before the tag exists. */
  const draftColor = defaultColorForName(draft.trim() || "x");
  const duplicate = tags.some(
    (tag) => tag.name.toLowerCase() === draft.trim().toLowerCase(),
  );

  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label="编辑项目 tag"
      style={{
        position: "fixed",
        top: position?.top ?? -9999,
        left: position?.left ?? -9999,
        width: PANEL_WIDTH,
        visibility: position ? "visible" : "hidden",
      }}
      className="z-50 flex max-h-[min(380px,80vh)] flex-col overflow-hidden rounded-lg border border-line bg-surface-raised shadow-modal"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex items-center justify-between border-b border-line px-2.5 py-1.5">
        <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Tag
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded px-1.5 py-0.5 text-[10px] font-medium text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
        >
          Esc
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-1.5">
        {tags.length === 0 ? (
          <p className="px-1.5 py-2 text-[11px] leading-relaxed text-ink-muted">
            还没有任何 tag。在下面输入名称即可新建。
          </p>
        ) : (
          <ul className="space-y-0.5">
            {tags.map((tag) => (
              <li key={tag.id}>
                <div className="flex items-center gap-1">
                  <label
                    className={
                      "flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-[11.5px] transition hover:bg-surface-sunken "
                      + (busy ? "opacity-60" : "")
                    }
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(tag.id)}
                      disabled={busy}
                      onChange={() => toggle(tag.id)}
                      className="h-3 w-3 flex-none accent-brand"
                    />
                    <span
                      className={"h-2 w-2 flex-none rounded-full " + tagDotClass(tag.color)}
                      aria-hidden="true"
                    />
                    <span className="truncate text-ink">{tag.name}</span>
                  </label>
                  {onRecolorTag && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        setPaletteFor((current) => (current === tag.id ? null : tag.id))
                      }
                      title="换颜色（影响所有使用该 tag 的项目）"
                      aria-expanded={paletteFor === tag.id}
                      className="flex-none rounded px-1 py-1 text-[10px] text-ink-subtle transition hover:bg-surface-sunken hover:text-ink disabled:opacity-40"
                    >
                      改色
                    </button>
                  )}
                </div>
                {paletteFor === tag.id && (
                  <div className="mb-1 ml-6 flex flex-wrap gap-1 rounded border border-line bg-surface-sunken p-1.5">
                    {TAG_COLORS.map((color) => (
                      <button
                        key={color}
                        type="button"
                        disabled={busy}
                        onClick={() => recolor(tag.id, color)}
                        title={color}
                        aria-label={color}
                        className={
                          "h-4 w-4 rounded-full transition hover:scale-110 disabled:opacity-40 "
                          + tagSwatchClass(color)
                          + (tag.color === color
                            ? " ring-2 ring-ink-strong/50 ring-offset-1 ring-offset-surface-sunken"
                            : "")
                        }
                      />
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="border-t border-line px-2 py-2">
        <div className="flex items-center gap-1.5">
          <span
            className={"h-2 w-2 flex-none rounded-full " + tagDotClass(draftColor)}
            aria-hidden="true"
          />
          <input
            ref={inputRef}
            type="text"
            value={draft}
            maxLength={24}
            disabled={busy}
            placeholder="新建 tag…"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              event.stopPropagation();
              if (event.key === "Enter") {
                event.preventDefault();
                if (!duplicate) create();
              }
            }}
            className="min-w-0 flex-1 rounded border border-line bg-surface px-1.5 py-1 text-[11.5px] text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none disabled:opacity-50"
          />
          <button
            type="button"
            disabled={busy || draft.trim().length === 0 || duplicate}
            onClick={create}
            className="flex-none rounded border border-line bg-surface px-1.5 py-1 text-[11px] text-ink-muted transition hover:border-brand hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
          >
            新建
          </button>
        </div>
        {duplicate && (
          <p className="mt-1 text-[10.5px] text-state-waiting">
            已存在同名 tag，勾选上面那一个即可。
          </p>
        )}
        {error && <p className="mt-1 text-[10.5px] text-state-error">{error}</p>}
      </div>
    </div>,
    document.body,
  );
}
