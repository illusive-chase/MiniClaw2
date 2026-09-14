import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { Tag } from "../types";
import { renameConflicts, shouldCommitRename } from "../tagEdits";
import {
  TAG_COLORS,
  defaultColorForName,
  tagDotClass,
  tagSwatchClass,
  type TagColor,
} from "../tagPalette";

const PANEL_WIDTH = 268;
const VIEWPORT_MARGIN = 8;
/** Mirrors `MAX_TAG_NAME_LENGTH` in `tags.py`; the backend rejects longer. */
const MAX_NAME_LENGTH = 24;
/** Delete arms for this long, matching the project card's confirm window. */
const CONFIRM_WINDOW_MS = 3500;

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
  /** Rename an existing global tag; affects every project using it. */
  onRenameTag?: (tagId: string, name: string) => Promise<void>;
  /** Delete a global tag; the server also strips it from every project. */
  onDeleteTag?: (tagId: string) => Promise<void>;
};

/**
 * Tag assignment panel: check existing tags, create one, rename, recolor, delete.
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
  onRenameTag,
  onDeleteTag,
}: TagEditPopoverProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const renameRef = useRef<HTMLInputElement | null>(null);
  const confirmTimerRef = useRef<number | null>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paletteFor, setPaletteFor] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);

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
  }, [anchor, tags.length, paletteFor, renamingId]);

  useEffect(() => {
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, []);

  useEffect(() => {
    return () => {
      if (confirmTimerRef.current !== null) {
        window.clearTimeout(confirmTimerRef.current);
      }
    };
  }, []);

  const cancelRename = useCallback(() => {
    setRenamingId(null);
    setRenameDraft("");
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      /* This listener runs in capture phase, so it sees Escape before the
       * rename input does. Escape has to back out of the innermost open thing
       * first, or a mistyped rename could only be escaped by losing the whole
       * panel. */
      if (renamingId) {
        cancelRename();
        return;
      }
      if (confirmingDeleteId) {
        setConfirmingDeleteId(null);
        return;
      }
      if (paletteFor) {
        setPaletteFor(null);
        return;
      }
      onClose();
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
  }, [anchor, onClose, renamingId, confirmingDeleteId, paletteFor, cancelRename]);

  /* Selected on focus so the common case — replacing the name outright — takes
   * one keystroke, while an edit-in-place is still possible with an arrow key. */
  useEffect(() => {
    if (!renamingId) return;
    const input = renameRef.current;
    if (!input) return;
    input.focus();
    input.select();
  }, [renamingId]);

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

  const beginRename = (tag: Tag) => {
    if (!onRenameTag || busy) return;
    setPaletteFor(null);
    setConfirmingDeleteId(null);
    setError(null);
    setRenamingId(tag.id);
    setRenameDraft(tag.name);
  };

  /* An unchanged or empty name closes the editor without a request: the tag
   * already has that name, and the backend would reject the empty one. */
  const commitRename = (tag: Tag) => {
    if (!onRenameTag) return;
    const name = renameDraft.trim();
    if (!shouldCommitRename(tag.name, renameDraft)) {
      cancelRename();
      return;
    }
    void run(async () => {
      await onRenameTag(tag.id, name);
      cancelRename();
    });
  };

  /* Two-step, because a delete lands on every project carrying the tag and
   * there is no undo. The armed state lapses so it cannot be clicked much
   * later by accident. */
  const requestDelete = (tag: Tag) => {
    if (!onDeleteTag || busy) return;
    if (confirmingDeleteId !== tag.id) {
      setPaletteFor(null);
      cancelRename();
      setError(null);
      setConfirmingDeleteId(tag.id);
      if (confirmTimerRef.current !== null) {
        window.clearTimeout(confirmTimerRef.current);
      }
      confirmTimerRef.current = window.setTimeout(
        () => setConfirmingDeleteId(null),
        CONFIRM_WINDOW_MS,
      );
      return;
    }
    void run(async () => {
      await onDeleteTag(tag.id);
      setConfirmingDeleteId(null);
    });
  };

  /* The name a user is typing previews in its assigned color, so the default
   * color is visible before the tag exists. */
  const draftColor = defaultColorForName(draft.trim() || "x");
  const duplicate = tags.some(
    (tag) => tag.name.toLowerCase() === draft.trim().toLowerCase(),
  );
  /* Case-insensitive to match the backend's uniqueness rule, and excluding the
   * tag being renamed so re-casing its own name is allowed. */
  const renameDuplicate =
    renamingId !== null && renameConflicts(tags, renamingId, renameDraft);

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
          <>
            {/* Double-click has no affordance of its own, so the panel says so
              * once rather than per row. */}
            {onRenameTag && (
              <p className="px-1.5 pb-1 text-[10px] text-ink-subtle">
                双击名称可重命名
              </p>
            )}
            <ul className="space-y-0.5">
              {tags.map((tag) => (
                <li key={tag.id}>
                  <div
                    className={
                      "flex items-center gap-1 rounded px-1.5 py-1 transition "
                      + (renamingId === tag.id ? "bg-surface-sunken" : "hover:bg-surface-sunken")
                    }
                  >
                    {/* The checkbox and dot form the toggle target; the name is
                      * excluded from it because a double-click there would
                      * otherwise fire a spurious assignment write before the
                      * rename editor opened. */}
                    <label
                      className={
                        "flex flex-none cursor-pointer items-center gap-2 "
                        + (busy ? "opacity-60" : "")
                      }
                      title={selected.has(tag.id) ? "从此项目移除" : "添加到此项目"}
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
                    </label>

                    {renamingId === tag.id ? (
                      <input
                        ref={renameRef}
                        type="text"
                        value={renameDraft}
                        maxLength={MAX_NAME_LENGTH}
                        disabled={busy}
                        aria-label={`重命名 tag「${tag.name}」`}
                        onChange={(event) => setRenameDraft(event.target.value)}
                        /* A duplicate name reverts on blur instead of sending a
                         * request the backend rejects; the inline warning has
                         * already said why. Enter keeps the editor open in that
                         * case so the name can still be corrected. */
                        onBlur={() =>
                          renameDuplicate ? cancelRename() : commitRename(tag)
                        }
                        onKeyDown={(event) => {
                          event.stopPropagation();
                          if (event.key === "Enter") {
                            event.preventDefault();
                            if (!renameDuplicate) commitRename(tag);
                          }
                        }}
                        className="min-w-0 flex-1 rounded border border-brand bg-surface px-1 py-0.5 text-[11.5px] text-ink-strong focus:outline-none disabled:opacity-50"
                      />
                    ) : (
                      <span
                        onDoubleClick={() => beginRename(tag)}
                        title={onRenameTag ? "双击重命名" : tag.name}
                        className={
                          "min-w-0 flex-1 truncate text-[11.5px] text-ink "
                          + (onRenameTag ? "cursor-text" : "")
                        }
                      >
                        {tag.name}
                      </span>
                    )}

                    {onRecolorTag && renamingId !== tag.id && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          setPaletteFor((current) => (current === tag.id ? null : tag.id))
                        }
                        title="换颜色（影响所有使用该 tag 的项目）"
                        aria-expanded={paletteFor === tag.id}
                        className="flex-none rounded px-1 py-0.5 text-[10px] text-ink-subtle transition hover:bg-surface hover:text-ink disabled:opacity-40"
                      >
                        改色
                      </button>
                    )}
                    {onDeleteTag && renamingId !== tag.id && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => requestDelete(tag)}
                        title={
                          confirmingDeleteId === tag.id
                            ? "再点一次删除，所有项目都会失去这个 tag"
                            : "删除 tag（影响所有使用该 tag 的项目）"
                        }
                        className={
                          "flex-none rounded px-1 py-0.5 text-[10px] transition disabled:opacity-40 "
                          + (confirmingDeleteId === tag.id
                            ? "bg-state-error-soft text-state-error"
                            : "text-ink-subtle hover:bg-surface hover:text-state-error")
                        }
                      >
                        {confirmingDeleteId === tag.id ? "确认" : "删除"}
                      </button>
                    )}
                  </div>

                  {renamingId === tag.id && renameDuplicate && (
                    <p className="mb-1 ml-6 text-[10.5px] text-state-waiting">
                      已存在同名 tag。
                    </p>
                  )}
                  {confirmingDeleteId === tag.id && (
                    <p className="mb-1 ml-6 text-[10.5px] leading-snug text-state-error">
                      再点一次「确认」删除「{tag.name}」，所有项目都会失去这个 tag。
                    </p>
                  )}
                  {paletteFor === tag.id && renamingId !== tag.id && (
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
          </>
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
            maxLength={MAX_NAME_LENGTH}
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
