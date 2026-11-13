import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";

import { listHosts, updateOwnHost, updateSyncLocalSettings } from "../api";
import { HOST_GLYPHS, HOST_HUES, HUE_NAMES, hueSwatchClass } from "../hostIcons";
import { hueRingClass } from "../projectIcons";
import type { HostEntry, SyncLocalSettings } from "../types";
import { HostGlyph } from "./HostGlyph";

const MAX_LABEL = 40;

/**
 * This host's name, icon, and color, which every device shows for it. Only
 * this host writes its own profile, so editing can never conflict on sync.
 */
export function HostProfileSection({ open, onChanged }: { open: boolean; onChanged?: () => void }) {
  const [self, setSelf] = useState<HostEntry | null>(null);
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [duplicate, setDuplicate] = useState(false);

  useEffect(() => {
    if (!open) return;
    listHosts()
      .then((payload) => {
        const own = payload.hosts.find((entry) => entry.is_self) ?? null;
        setSelf(own);
        setLabel(own?.label ?? "");
        setDuplicate(Boolean(own && payload.hosts.some((entry) => !entry.is_self && entry.label === own.label)));
      })
      .catch((err) => setError(String(err)));
  }, [open]);

  if (!self) {
    return error ? <div className="text-[11px] text-state-error">{error}</div> : null;
  }

  const save = async (changes: { label?: string; icon?: string; color?: string }) => {
    setSaving(true);
    setError(null);
    try {
      const profile = await updateOwnHost(changes);
      setSelf({ ...self, label: profile.label, icon: profile.icon, color: profile.color, has_profile: true });
      setLabel(profile.label);
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const trimmed = label.trim();
  return (
    <section className="rounded-lg border border-line bg-surface-raised p-4 shadow-card">
      <div className="mb-3 flex items-start justify-between gap-4">
        <div>
          <div className="font-display text-sm font-semibold text-ink-strong">本机 Host</div>
          <div className="mt-1 text-[11px] leading-relaxed text-ink-muted">
            所有设备都用这个名字和图标显示本机；只有本机能修改，同步时不会冲突。
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <span className="grid h-12 w-12 flex-none place-items-center rounded-xl border border-line bg-surface">
          <HostGlyph icon={self.icon} color={self.color} size={24} />
        </span>
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <input
            value={label}
            maxLength={MAX_LABEL}
            onChange={(event) => setLabel(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && trimmed && trimmed !== self.label) void save({ label: trimmed });
            }}
            className="min-w-0 flex-1 rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm font-medium text-ink-strong focus:border-brand focus:outline-none"
            aria-label="本机名称"
          />
          <button
            type="button"
            disabled={saving || !trimmed || trimmed === self.label}
            onClick={() => void save({ label: trimmed })}
            className="rounded-md bg-brand px-3 py-2 text-xs font-medium text-white shadow-card transition hover:brightness-95 disabled:opacity-40"
          >
            保存名称
          </button>
        </div>
      </div>
      {duplicate && <div className="mt-1.5 text-[11px] text-state-waiting">另一台设备也叫这个名字，建议换一个便于区分。</div>}
      <div className="mt-3 flex flex-wrap gap-1.5" role="radiogroup" aria-label="图标">
        {HOST_GLYPHS.map((glyph) => {
          const selected = glyph.key === self.icon;
          const Icon = glyph.icon;
          return (
            <button
              key={glyph.key}
              type="button"
              role="radio"
              aria-checked={selected}
              title={glyph.name}
              disabled={saving}
              onClick={() => void save({ icon: glyph.key })}
              className={`grid h-8 w-8 place-items-center rounded-md border transition ${selected ? "border-brand bg-brand-soft text-brand-ink" : "border-line bg-surface text-ink-muted hover:text-ink"}`}
            >
              <Icon size={15} />
            </button>
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-2" role="radiogroup" aria-label="颜色">
        {HOST_HUES.map((hue) => {
          const selected = hue === self.color;
          return (
            <button
              key={hue}
              type="button"
              role="radio"
              aria-checked={selected}
              title={HUE_NAMES[hue]}
              disabled={saving}
              onClick={() => void save({ color: hue })}
              className={`grid h-6 w-6 place-items-center rounded-full ${hueSwatchClass(hue)} ${selected ? `ring-2 ring-offset-2 ring-offset-surface-raised ${hueRingClass(hue)}` : ""}`}
            >
              {selected && <Check size={12} className="text-white" />}
            </button>
          );
        })}
      </div>
      <div className="mt-3 space-y-0.5 font-mono text-[10px] text-ink-muted">
        <div className="flex items-center gap-1.5">
          machine id {self.machine_id}
          <button
            type="button"
            className="rounded p-0.5 hover:bg-surface-sunken"
            title="复制"
            onClick={() => {
              void navigator.clipboard?.writeText(self.machine_id).then(() => {
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1200);
              });
            }}
          >
            {copied ? <Check size={11} /> : <Copy size={11} />}
          </button>
        </div>
        {self.hostname && <div>hostname {self.hostname}</div>}
      </div>
      {error && <div className="mt-2 text-[11px] text-state-error">{error}</div>}
    </section>
  );
}

/** Host-local cadence: thresholds and probe switches never sync. */
export function SyncLocalSettingsFields({
  settings,
  onSaved,
}: {
  settings: SyncLocalSettings;
  onSaved: (next: SyncLocalSettings) => void;
}) {
  const [draft, setDraft] = useState(settings);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setDraft(settings), [settings]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(settings);
  const number = (key: keyof SyncLocalSettings, min: number) => (
    <input
      type="number"
      min={min}
      value={draft[key] as number}
      onChange={(event) => setDraft({ ...draft, [key]: Math.max(min, Number(event.target.value) || min) })}
      className="w-16 rounded border border-line bg-surface-sunken px-1.5 py-0.5 text-[11px] text-ink-strong focus:border-brand focus:outline-none"
    />
  );
  return (
    <div className="space-y-1.5 text-[11px] text-ink-muted">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        元数据超过 {number("metadata_overdue_hours", 1)} 小时、项目超过 {number("project_overdue_hours", 1)} 小时未推送时亮红点
      </div>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={draft.metadata_probe_enabled} onChange={(event) => setDraft({ ...draft, metadata_probe_enabled: event.target.checked })} className="accent-brand" />
        每 {number("metadata_probe_minutes", 5)} 分钟只读探测一次元数据远端（只拉取，不合并、不推送）
      </label>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={draft.source_probe_enabled} onChange={(event) => setDraft({ ...draft, source_probe_enabled: event.target.checked })} className="accent-brand" />
        每 {number("source_probe_minutes", 10)} 分钟探测一次 MiniClaw2 源码是否有新版本
      </label>
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-ink-subtle">这些设置只保存在本机，不会同步。</span>
        <button
          type="button"
          disabled={!dirty || saving}
          onClick={() => {
            setSaving(true);
            setError(null);
            updateSyncLocalSettings(draft)
              .then(onSaved)
              .catch((err) => setError(err instanceof Error ? err.message : String(err)))
              .finally(() => setSaving(false));
          }}
          className="rounded-md border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-muted transition hover:text-ink disabled:opacity-40"
        >
          保存
        </button>
      </div>
      {error && <div className="text-state-error">{error}</div>}
    </div>
  );
}
