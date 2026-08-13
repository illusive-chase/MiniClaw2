import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { buildHierarchy } from "../hierarchy";
import { pathKey, HierarchyTree, type HierarchyEntry } from "./HierarchyTree";
import { readRecentUsed, recordRecentUsed, type RecentUsedKind } from "../recentUsed";

type Props = {
  open: boolean;
  kind: RecentUsedKind;
  /** Dialog heading, e.g. `选择 skill`. */
  title: string;
  entries: HierarchyEntry[];
  emptyLabel?: string;
  onCancel: () => void;
  /** Called with the picked entry. The dialog closes itself afterwards. */
  onPick: (entry: HierarchyEntry) => void;
};

/** Recent chips shown, of the 8 the MRU retains (design §2.3). */
const RECENT_VISIBLE = 5;

export function EntryPickerModal({
  open,
  kind,
  title,
  entries,
  emptyLabel,
  onCancel,
  onPick,
}: Props) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [recentIds, setRecentIds] = useState<string[]>([]);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const seededRef = useRef(false);

  const roots = useMemo(
    () => buildHierarchy(entries.map((entry) => entry.name)),
    [entries],
  );

  /* Reset per opening. Reading the MRU here rather than on every render keeps
   * the chip order stable while the dialog is open, so a pick does not reorder
   * the row under the cursor. */
  useEffect(() => {
    if (!open) {
      seededRef.current = false;
      return;
    }
    setQuery("");
    setRecentIds(readRecentUsed()[kind]);
    window.setTimeout(() => searchRef.current?.focus(), 0);
  }, [open, kind]);

  /* Top-level directories start open so the dialog reads as a browsable list
   * rather than a row of closed folders; nested ones stay shut so `lark` does
   * not drag `vc` and `workflow` open with it. When every name is a single
   * segment there are no directories at all, so this seeds nothing and the
   * degenerate shallow case renders as a plain flat list.
   *
   * Seeded once per opening, and only once the entries have actually arrived —
   * a library refresh mid-session hands us a new `roots` identity, which must
   * not collapse whatever the user has expanded by hand. */
  useEffect(() => {
    if (!open || seededRef.current || roots.length === 0) return;
    seededRef.current = true;
    setExpanded(
      new Set(
        roots.filter((node) => node.children.length > 0).map((node) => pathKey(node.fullPath)),
      ),
    );
  }, [open, roots]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [open, onCancel]);

  if (!open) return null;

  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  const recent = recentIds
    .map((id) => byId.get(id))
    .filter((entry): entry is HierarchyEntry => entry !== undefined && entry.disabled !== true)
    .slice(0, RECENT_VISIBLE);

  const pick = (entry: HierarchyEntry) => {
    if (entry.disabled === true) return;
    recordRecentUsed(kind, entry.id);
    onPick(entry);
  };

  const toggle = (path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  /* Portalled to `document.body` because the dialog is opened from inside the
   * side panel, and that panel animates its slide-in with `transform` +
   * `will-change` (`App.tsx:2600`). Either property makes the panel a containing
   * block for `position: fixed`, which would pin this scrim to the panel's
   * 380px column instead of the viewport. The app's other modals render above
   * that aside, so they never hit this. */
  return createPortal(
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm">
      <div className="flex max-h-[80vh] w-[440px] max-w-[95vw] flex-col rounded-xl border border-line bg-surface-raised shadow-modal">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div className="truncate font-display text-sm font-semibold text-ink-strong">
            {title}
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-2 py-1 text-[11px] font-medium text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
          >
            Esc
          </button>
        </div>

        <div className="border-b border-line px-4 py-2.5">
          <input
            ref={searchRef}
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="按名称搜索…"
            className="w-full rounded-md border border-line bg-surface-sunken px-2.5 py-1.5 text-[12px] text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
          />
        </div>

        {recent.length > 0 && query.trim().length === 0 && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-4 py-2">
            <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-ink-subtle">
              最近
            </span>
            {recent.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => pick(entry)}
                title={entry.name}
                className="max-w-[150px] truncate rounded-md border border-line bg-surface px-1.5 py-0.5 text-[10.5px] text-ink transition hover:border-brand/60 hover:text-ink-strong"
              >
                {entry.name}
              </button>
            ))}
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-2.5 py-2">
          <HierarchyTree
            entries={entries}
            query={query}
            expanded={expanded}
            onToggle={toggle}
            onSelect={pick}
            emptyLabel={emptyLabel}
          />
        </div>
      </div>
    </div>,
    document.body,
  );
}
