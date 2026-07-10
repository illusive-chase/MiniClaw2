import { useCallback, useEffect, useState } from "react";
import { deleteUserTemplate, listUserTemplates } from "../api";
import type { ModelPreset, TemplateSummary } from "../types";
import { modelPresetLabel } from "../modelPresets";

type Props = {
  /** Bumped by callers after save/apply so the dock refetches. */
  refreshToken: number;
  modelPresets: ModelPreset[];
  onError?: (message: string) => void;
  onClose: () => void;
};

/**
 * Right-side dock listing user templates. Cards are draggable via the
 * ``application/x-miniclaw-template`` MIME type; the Canvas listens for
 * that MIME on its drop handler and calls ``applyUserTemplate`` with the
 * slug carried in ``dataTransfer``.
 */
export function TemplateLibraryDock({
  refreshToken,
  modelPresets,
  onError,
  onClose,
}: Props) {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const items = await listUserTemplates();
      setTemplates(items);
    } catch (err) {
      onError?.(String(err instanceof Error ? err.message : err));
    } finally {
      setLoading(false);
    }
  }, [onError]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshToken]);

  const remove = useCallback(
    async (slug: string) => {
      try {
        await deleteUserTemplate(slug);
        void refresh();
      } catch (err) {
        onError?.(String(err instanceof Error ? err.message : err));
      }
    },
    [refresh, onError],
  );

  const slugFor = (name: string): string =>
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-ink-subtle">
          Template library
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void refresh()}
            className="flex h-6 w-6 items-center justify-center rounded text-[11px] text-ink-muted transition hover:bg-surface-raised hover:text-ink"
            title="Refresh"
            aria-label="Refresh templates"
          >
            ↻
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex h-6 w-6 items-center justify-center rounded text-[13px] leading-none text-ink-muted transition hover:bg-surface-raised hover:text-ink"
            title="Close panel"
            aria-label="Close panel"
          >
            ×
          </button>
        </div>
      </div>
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-3">
        {loading && templates.length === 0 && (
          <div className="text-[11px] text-ink-subtle">Loading…</div>
        )}
        {!loading && templates.length === 0 && (
          <div className="text-[11px] leading-relaxed text-ink-subtle">
            Select nodes on the canvas and right-click →{" "}
            <span className="text-ink-muted">Save as template…</span> to build
            your library.
          </div>
        )}
        {templates.map((tpl) => {
          const slug = slugFor(tpl.name);
          return (
            <div
              key={slug}
              draggable
              onDragStart={(event) => {
                event.dataTransfer.setData(
                  "application/x-miniclaw-template",
                  slug,
                );
                event.dataTransfer.effectAllowed = "copy";
              }}
              className="group cursor-grab rounded-md border border-line bg-surface-raised px-3 py-2 text-xs shadow-card transition hover:border-brand/60 active:cursor-grabbing"
              title="Drag onto the canvas to stamp this template into the active direction."
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-ink-strong">
                    {tpl.name}
                  </div>
                  {tpl.brief && (
                    <div className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-ink-muted">
                      {tpl.brief}
                    </div>
                  )}
                  <div className="mt-1 text-[10px] uppercase tracking-[0.12em] text-ink-subtle">
                    {tpl.node_count} {tpl.node_count === 1 ? "node" : "nodes"}
                  </div>
                  {tpl.allowed_model_preset_ids.length > 0 && (
                    <div className="mt-1 truncate text-[10px] text-ink-subtle">
                      档位：{" "}
                      {tpl.allowed_model_preset_ids
                        .map((id) => modelPresetLabel(modelPresets, id))
                        .join(", ")}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    if (window.confirm(`Delete template "${tpl.name}"?`)) {
                      void remove(slug);
                    }
                  }}
                  className="rounded px-1 text-[10px] text-ink-subtle opacity-0 transition hover:bg-state-error-soft hover:text-state-error group-hover:opacity-100"
                  title="Delete template"
                >
                  ×
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
