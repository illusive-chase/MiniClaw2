import type { Tag } from "../types";
import { tagChipClass, tagDotClass } from "../tagPalette";

/* A tag chip is a rounded pill with a color dot; the card's other badges are
 * square-cornered and colorless. That keeps a tag from reading as node state,
 * which is what the `state-*` colors mean elsewhere in the app. */
export function TagChip({
  name,
  color,
  size = "sm",
  title,
}: {
  name: string;
  color: string;
  size?: "sm" | "md";
  title?: string;
}) {
  return (
    <span
      title={title ?? name}
      className={
        "inline-flex max-w-[140px] items-center gap-1 rounded-full border font-medium "
        + (size === "sm" ? "px-1.5 py-[1px] text-[10px] " : "px-2 py-0.5 text-[11px] ")
        + tagChipClass(color)
      }
    >
      <span
        className={"h-1.5 w-1.5 flex-none rounded-full " + tagDotClass(color)}
        aria-hidden="true"
      />
      <span className="truncate">{name}</span>
    </span>
  );
}

/** Row of tag chips for a project card. Renders nothing when untagged. */
export function TagChipRow({ tags }: { tags: Tag[] }) {
  if (tags.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {tags.map((tag) => (
        <TagChip key={tag.id} name={tag.name} color={tag.color} />
      ))}
    </div>
  );
}

/**
 * Multi-select AND filter (design §1.6). Selecting `work` + `research` narrows
 * to projects carrying both, so the counts shown are for the tag alone, not for
 * the current intersection — they would otherwise all collapse to 0 or 1 as
 * soon as two tags were active, which reads as broken.
 */
export function TagFilterBar({
  tags,
  selected,
  counts,
  onToggle,
  onClear,
  sortControl,
  totalLabel,
}: {
  tags: Tag[];
  selected: ReadonlySet<string>;
  counts: Map<string, number>;
  onToggle: (tagId: string) => void;
  onClear: () => void;
  sortControl: React.ReactNode;
  totalLabel: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2">
      <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
        {totalLabel}
      </div>

      {tags.length > 0 && (
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
          <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            筛选
          </span>
          {tags.map((tag) => {
            const active = selected.has(tag.id);
            return (
              <button
                key={tag.id}
                type="button"
                onClick={() => onToggle(tag.id)}
                aria-pressed={active}
                title={
                  active
                    ? `取消筛选「${tag.name}」`
                    : `只看带有「${tag.name}」的项目（多选为「同时具备」）`
                }
                className={
                  "inline-flex max-w-[150px] items-center gap-1 rounded-full border px-2 py-[2px] "
                  + "text-[10.5px] font-medium transition "
                  + (active
                    ? tagChipClass(tag.color) + " ring-1 ring-inset ring-current"
                    : "border-line bg-surface-raised text-ink-muted hover:border-line-strong hover:text-ink")
                }
              >
                <span
                  className={
                    "h-1.5 w-1.5 flex-none rounded-full "
                    + (active ? tagDotClass(tag.color) : "bg-ink-subtle")
                  }
                  aria-hidden="true"
                />
                <span className="truncate">{tag.name}</span>
                <span className="font-mono text-[9.5px] opacity-60">
                  {counts.get(tag.id) ?? 0}
                </span>
              </button>
            );
          })}
          {selected.size > 0 && (
            <button
              type="button"
              onClick={onClear}
              className="rounded-full border border-line px-2 py-[2px] text-[10.5px] text-ink-muted transition hover:border-line-strong hover:text-ink"
            >
              清除筛选
            </button>
          )}
        </div>
      )}

      <div className="ml-auto flex-none">{sortControl}</div>
    </div>
  );
}
