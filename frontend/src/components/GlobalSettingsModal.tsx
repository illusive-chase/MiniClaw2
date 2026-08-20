import { useEffect, useState } from "react";
import {
  createModelPreset,
  applySelfUpdate,
  checkSyncRemote,
  checkSelfUpdate,
  getSelfUpdate,
  deleteModelPreset,
  getGlobalState,
  replaceModelPreset,
  setupSync,
  syncNow,
  updateCodeReviewSettings,
  updateGlobalDefaults,
  updateToolRequestSettings,
  updateUpdateSettings,
} from "../api";
import { canApplyUpdate } from "../selfUpdate";
import { LANGUAGE_OPTIONS } from "../languages";
import type {
  CodeReviewSettings,
  GlobalDefaults,
  GlobalState,
  ModelPreset,
  SelfUpdateApplyResult,
  SelfUpdateState,
  ToolRequestSettings,
  UpdateSettings,
} from "../types";

type Props = {
  open: boolean;
  state: GlobalState | null;
  onClose: () => void;
  onChanged: (state: GlobalState) => void;
};

const EMPTY_PRESET: ModelPreset = {
  id: "",
  label: "",
  provider: "codex",
  model: "",
  description: "",
  model_provider: null,
  service_tier: null,
  reasoning_effort: null,
  status: "active",
};

export function GlobalSettingsModal({ open, state, onClose, onChanged }: Props) {
  const [defaults, setDefaults] = useState<GlobalDefaults | null>(null);
  const [codeReview, setCodeReview] = useState<CodeReviewSettings | null>(null);
  const [toolRequests, setToolRequests] = useState<ToolRequestSettings | null>(null);
  const [updates, setUpdates] = useState<UpdateSettings | null>(null);
  const [selfUpdate, setSelfUpdate] = useState<SelfUpdateState | null>(null);
  const [selfUpdateResult, setSelfUpdateResult] = useState<SelfUpdateApplyResult | null>(null);
  const [draft, setDraft] = useState<ModelPreset | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [checkingRemote, setCheckingRemote] = useState(false);
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [applyingUpdate, setApplyingUpdate] = useState(false);
  const [remoteUrl, setRemoteUrl] = useState("");
  const [privacyAcknowledged, setPrivacyAcknowledged] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setDefaults(state?.defaults ?? null);
    setCodeReview(state?.code_review ?? null);
    setToolRequests(state?.tool_requests ?? null);
    setUpdates(state?.updates ?? null);
    setSelfUpdateResult(null);
    setDraft(null);
    setEditingId(null);
    setRemoteUrl(state?.sync.remote_url ?? "");
    setPrivacyAcknowledged(state?.sync.configured ?? false);
    setSyncError(null);
    setError(null);
    if (!state) {
      getGlobalState().then(onChanged).catch((err) => setError(String(err)));
    }
    getSelfUpdate().then(setSelfUpdate).catch((err) => setError(String(err)));
  }, [open, state, onChanged]);

  useEffect(() => {
    if (!open || !selfUpdate?.checking) return;
    const timer = window.setInterval(() => {
      getSelfUpdate().then(setSelfUpdate).catch((err) => setError(String(err)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [open, selfUpdate?.checking]);

  if (!open) return null;
  const presets = state?.model_presets ?? [];

  const saveDefaults = async () => {
    if (!defaults) return;
    setSaving(true);
    setError(null);
    try {
      onChanged(await updateGlobalDefaults(defaults));
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const savePreset = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const normalized: ModelPreset = {
        ...draft,
        id: draft.id.trim(),
        label: draft.label.trim(),
        model: draft.model.trim(),
        description: draft.description?.trim() ?? "",
        model_provider: draft.model_provider?.trim() || null,
        service_tier: draft.service_tier?.trim() || null,
        reasoning_effort: draft.reasoning_effort?.trim() || null,
      };
      const next = editingId
        ? await replaceModelPreset(normalized)
        : await createModelPreset(normalized);
      onChanged(next);
      setDraft(null);
      setEditingId(null);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const saveCodeReview = async () => {
    if (!codeReview) return;
    setSaving(true);
    setError(null);
    try {
      onChanged(await updateCodeReviewSettings(codeReview));
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const saveToolRequests = async () => {
    if (!toolRequests) return;
    setSaving(true);
    setError(null);
    try {
      onChanged(await updateToolRequestSettings(toolRequests));
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const saveUpdates = async () => {
    if (!updates) return;
    setSaving(true);
    setError(null);
    try {
      onChanged(await updateUpdateSettings(updates));
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const runUpdateCheck = async (preserveError = false) => {
    setCheckingUpdate(true);
    if (!preserveError) setError(null);
    try {
      setSelfUpdate(await checkSelfUpdate());
    } catch (err) {
      if (!preserveError) setError(String(err));
    } finally {
      setCheckingUpdate(false);
    }
  };

  const runUpdateApply = async () => {
    setApplyingUpdate(true);
    setError(null);
    try {
      setSelfUpdateResult(await applySelfUpdate());
    } catch (err) {
      setError(String(err));
      void runUpdateCheck(true);
    } finally {
      setApplyingUpdate(false);
    }
  };

  const removePreset = async (preset: ModelPreset) => {
    if (!window.confirm(`Delete model preset “${preset.label}”?`)) return;
    setSaving(true);
    setError(null);
    try {
      await deleteModelPreset(preset.id);
      onChanged(await getGlobalState());
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const runSync = async () => {
    setSyncing(true);
    setSyncError(null);
    setError(null);
    try {
      const next = state?.sync.configured
        ? await syncNow()
        : await setupSync(remoteUrl.trim());
      onChanged(next);
      setRemoteUrl(next.sync.remote_url ?? remoteUrl);
    } catch (err) {
      setSyncError(String(err));
    } finally {
      setSyncing(false);
    }
  };

  const runRemoteCheck = async () => {
    setCheckingRemote(true);
    setSyncError(null);
    try {
      onChanged(await checkSyncRemote());
    } catch (err) {
      setSyncError(String(err));
    } finally {
      setCheckingRemote(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm">
      <div className="flex max-h-[92vh] w-[860px] max-w-[96vw] flex-col overflow-hidden rounded-xl border border-line bg-surface-raised shadow-modal">
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div>
            <div className="font-display text-sm font-semibold text-ink-strong">Global settings</div>
            <div className="mt-0.5 font-mono text-[10px] text-ink-subtle">
              {state?.config_path ?? "Loading configuration…"}
            </div>
          </div>
          <button type="button" onClick={onClose} className="rounded px-2 py-1 text-xs text-ink-muted hover:bg-surface-sunken">Esc</button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto bg-surface-sunken p-5">
          {error && <div className="rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-xs text-state-error">{error}</div>}

          <section className="rounded-lg border border-line bg-surface-raised p-4 shadow-card">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="font-display text-sm font-semibold text-ink-strong">Metadata sync</div>
                <div className="mt-1 text-[11px] leading-relaxed text-ink-muted">
                  Manual-only Git sync. MiniClaw2 never contacts the remote until you press Sync now.
                </div>
              </div>
              <span className={`rounded border px-2 py-1 text-[10px] font-medium ${state?.sync.status === "changed" ? "border-state-waiting/40 bg-state-waiting-soft text-state-waiting" : "border-state-done/40 bg-state-done-soft text-state-done"}`}>
                {state?.sync.configured ? state.sync.status : "not configured"}
              </span>
            </div>
            {!state?.sync.configured && (
              <div className="mt-3 space-y-3">
                <Field label="Git remote URL">
                  <input value={remoteUrl} onChange={(event) => setRemoteUrl(event.target.value)} className={inputClass} placeholder="git@github.com:you/miniclaw-metadata.git" />
                </Field>
                <label className="flex items-start gap-2 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] leading-relaxed text-ink">
                  <input type="checkbox" checked={privacyAcknowledged} onChange={(event) => setPrivacyAcknowledged(event.target.checked)} className="mt-0.5 accent-brand" />
                  <span>{state?.sync.privacy_notice ?? "The remote contains full agent transcripts, prompts, tool output, and code. Use a private remote."}</span>
                </label>
              </div>
            )}
            {state?.sync.configured && (
              <div className="mt-3 space-y-3">
                <div className="space-y-1 font-mono text-[10px] text-ink-muted">
                  <div className="truncate">{state.sync.remote_url}</div>
                  <div>Machine: {state.sync.machine_label}</div>
                  <div>Last sync: {state.sync.last_sync_at ? new Date(state.sync.last_sync_at * 1000).toLocaleString() : "never"}</div>
                </div>
                <div className="border-t border-line pt-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div className="text-[11px] font-medium text-ink">远端状态</div>
                      <div className="mt-0.5 font-mono text-[10px] text-ink-muted">
                        本地领先 {state.sync.remote?.ahead ?? 0} · 远端领先 {state.sync.remote?.behind ?? 0}
                        {state.sync.remote?.ref_at
                          ? ` · 引用更新于 ${new Date(state.sync.remote.ref_at * 1000).toLocaleString()}`
                          : " · 引用时间未知"}
                      </div>
                    </div>
                    <button
                      type="button"
                      disabled={checkingRemote || syncing}
                      onClick={() => void runRemoteCheck()}
                      className={secondaryButton}
                    >
                      {checkingRemote ? "检查中…" : "检查远端"}
                    </button>
                  </div>
                  {state.sync.remote?.error ? (
                    <div className="mt-2 text-[11px] text-state-error">{state.sync.remote.error}</div>
                  ) : null}
                </div>
              </div>
            )}
            {syncError ? <div className="mt-2 text-[11px] text-state-error">{syncError}</div> : null}
            <div className="mt-3 flex justify-end">
              <button type="button" disabled={syncing || checkingRemote || (!state?.sync.configured && (!remoteUrl.trim() || !privacyAcknowledged))} onClick={() => void runSync()} className={primaryButton}>
                {syncing ? "Syncing…" : state?.sync.configured ? "Sync now" : "Set up sync"}
              </button>
            </div>
          </section>

          {selfUpdate?.is_repo && updates ? (
            <section className="rounded-lg border border-line bg-surface-raised p-4 shadow-card">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="font-display text-sm font-semibold text-ink-strong">版本</div>
                  <div className="mt-1 font-mono text-[10px] text-ink-muted">
                    {selfUpdate.head?.slice(0, 8) ?? "未知"} · {selfUpdate.branch ?? "未知分支"}
                  </div>
                </div>
                <span className={`rounded border px-2 py-1 text-[10px] font-medium ${selfUpdate.behind > 0 || selfUpdate.error ? "border-state-waiting/40 bg-state-waiting-soft text-state-waiting" : "border-state-done/40 bg-state-done-soft text-state-done"}`}>
                  {selfUpdate.checking || checkingUpdate
                    ? "检查中"
                    : selfUpdate.behind > 0
                      ? `落后 ${selfUpdate.behind} 个提交${selfUpdate.fast_forward ? "" : "，不可快进"}`
                      : selfUpdate.error
                        ? "检查失败"
                      : "已是最新"}
                </span>
              </div>
              <div className="mt-3 grid grid-cols-1 gap-2 font-mono text-[10px] text-ink-muted sm:grid-cols-2">
                <div>远端：{selfUpdate.upstream ?? "未配置"}</div>
                <div>快进：{selfUpdate.fast_forward ? "可以" : "不可以"}</div>
                <div>工作区：{selfUpdate.dirty ? "有未提交改动" : "干净"}</div>
                <div>活跃节点：{selfUpdate.blockers.length}</div>
                <div className="sm:col-span-2">
                  上次检查：{selfUpdate.last_checked_at ? new Date(selfUpdate.last_checked_at * 1000).toLocaleString() : "尚未检查"}
                </div>
              </div>
              {selfUpdate.error ? <div className="mt-2 text-[11px] text-state-error">{selfUpdate.error}</div> : null}
              {selfUpdateResult ? (
                <div className="mt-2 rounded-md border border-state-done/30 bg-state-done-soft px-3 py-2 text-[11px] text-state-done">
                  {selfUpdateResult.message}
                  {selfUpdateResult.restart_commands.map((command) => (
                    <div key={command} className="mt-1 font-mono text-[10px]">{command}</div>
                  ))}
                </div>
              ) : null}
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <label className="flex items-center gap-2 text-xs text-ink">
                  <input
                    type="checkbox"
                    checked={updates.check_on_startup}
                    onChange={(event) => setUpdates({ check_on_startup: event.target.checked })}
                    className="accent-brand"
                  />
                  启动时检查更新
                </label>
                <div className="flex gap-2">
                  <button type="button" disabled={saving} onClick={() => void saveUpdates()} className={secondaryButton}>保存偏好</button>
                  <button type="button" disabled={checkingUpdate} onClick={() => void runUpdateCheck(false)} className={secondaryButton}>{checkingUpdate ? "检查中…" : "检查更新"}</button>
                  <button type="button" disabled={!canApplyUpdate(selfUpdate) || applyingUpdate} onClick={() => void runUpdateApply()} className={primaryButton}>{applyingUpdate ? "正在更新…" : "更新并退出"}</button>
                </div>
              </div>
            </section>
          ) : null}

          {defaults && (
            <section className="rounded-lg border border-line bg-surface-raised p-4 shadow-card">
              <div className="mb-3 font-display text-sm font-semibold text-ink-strong">New project defaults</div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Model preset">
                  <select value={defaults.default_model_preset_id} onChange={(event) => setDefaults({ ...defaults, default_model_preset_id: event.target.value })} className={inputClass}>
                    {presets.filter((preset) => preset.status === "active").map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}
                  </select>
                </Field>
                <Field label="Language">
                  <select value={defaults.preferred_language ?? ""} onChange={(event) => setDefaults({ ...defaults, preferred_language: event.target.value || null })} className={inputClass}>
                    {LANGUAGE_OPTIONS.map((option) => <option key={option.value || "none"} value={option.value}>{option.label}</option>)}
                  </select>
                </Field>
                <Field label="Concurrency">
                  <input type="number" min={1} value={defaults.concurrency} onChange={(event) => setDefaults({ ...defaults, concurrency: Math.max(1, Number(event.target.value) || 1) })} className={inputClass} />
                </Field>
                <label className="flex items-center gap-2 self-end rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink">
                  <input type="checkbox" checked={defaults.auto_commit} onChange={(event) => setDefaults({ ...defaults, auto_commit: event.target.checked })} className="accent-brand" /> Auto commit completed work
                </label>
              </div>
              <div className="mt-3 flex justify-end"><button type="button" disabled={saving} onClick={() => void saveDefaults()} className={primaryButton}>Save defaults</button></div>
            </section>
          )}

          {toolRequests && (
            <section className="rounded-lg border border-line bg-surface-raised p-4 shadow-card">
              <div className="mb-3 font-display text-sm font-semibold text-ink-strong">Tool requests</div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Timeout (seconds)">
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={toolRequests.timeout_seconds}
                    onChange={(event) => setToolRequests({
                      ...toolRequests,
                      timeout_seconds: Math.max(1, Number(event.target.value) || 1),
                    })}
                    className={inputClass}
                  />
                </Field>
                <Field label="On timeout">
                  <select
                    value={toolRequests.timeout_action}
                    onChange={(event) => setToolRequests({
                      ...toolRequests,
                      timeout_action: event.target.value as ToolRequestSettings["timeout_action"],
                    })}
                    className={inputClass}
                  >
                    <option value="accept">Automatically accept</option>
                    <option value="reject">Automatically reject</option>
                  </select>
                </Field>
              </div>
              <div className="mt-3 flex justify-end">
                <button type="button" disabled={saving} onClick={() => void saveToolRequests()} className={primaryButton}>Save tool requests</button>
              </div>
            </section>
          )}

          {codeReview && (
            <section className="rounded-lg border border-line bg-surface-raised p-4 shadow-card">
              <div className="mb-3 font-display text-sm font-semibold text-ink-strong">Code review</div>
              <Field label="Default model preset">
                <select
                  value={codeReview.model_preset_id}
                  onChange={(event) => setCodeReview({ model_preset_id: event.target.value })}
                  className={inputClass}
                >
                  {presets.filter((preset) => preset.status === "active").map((preset) => (
                    <option key={preset.id} value={preset.id}>{preset.label}</option>
                  ))}
                </select>
              </Field>
              <div className="mt-3 flex justify-end">
                <button type="button" disabled={saving} onClick={() => void saveCodeReview()} className={primaryButton}>Save code review</button>
              </div>
            </section>
          )}

          <section className="rounded-lg border border-line bg-surface-raised p-4 shadow-card">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <div className="font-display text-sm font-semibold text-ink-strong">Model presets</div>
                <div className="text-[11px] text-ink-muted">All entries come from the global configuration and are editable.</div>
              </div>
              <button type="button" onClick={() => { setEditingId(null); setDraft({ ...EMPTY_PRESET }); }} className={primaryButton}>+ Add preset</button>
            </div>
            <div className="space-y-2">
              {presets.map((preset) => (
                <div key={preset.id} className="flex items-start justify-between gap-3 rounded-md border border-line bg-surface-sunken px-3 py-2.5">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-ink-strong"><span>{preset.label}</span>{preset.is_default && <span className="rounded bg-brand-soft px-1.5 py-0.5 text-[9px] text-brand-ink">project default</span>}{preset.id === codeReview?.model_preset_id && <span className="rounded bg-state-done-soft px-1.5 py-0.5 text-[9px] text-state-done">review default</span>}<span className="rounded border border-line px-1.5 py-0.5 font-mono text-[9px] text-ink-muted">{preset.status}</span></div>
                    <div className="mt-1 font-mono text-[10px] text-ink-muted">{preset.id} · {preset.provider} · {preset.model}{preset.reasoning_effort ? ` · ${preset.reasoning_effort}` : ""}</div>
                    {preset.description && <div className="mt-1 text-[11px] text-ink-muted">{preset.description}</div>}
                  </div>
                  <div className="flex gap-1">
                    <button type="button" onClick={() => { setEditingId(preset.id); setDraft({ ...preset }); }} className={secondaryButton}>Edit</button>
                    <button type="button" disabled={saving} onClick={() => void removePreset(preset)} className={`${secondaryButton} hover:text-state-error`}>Delete</button>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {draft && (
            <section className="rounded-lg border border-brand/40 bg-surface-raised p-4 shadow-card">
              <div className="mb-3 font-display text-sm font-semibold text-ink-strong">{editingId ? "Edit preset" : "Add preset"}</div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="ID"><input value={draft.id} disabled={editingId !== null} onChange={(event) => setDraft({ ...draft, id: event.target.value })} className={inputClass} placeholder="my-model-high" /></Field>
                <Field label="Label"><input value={draft.label} onChange={(event) => setDraft({ ...draft, label: event.target.value })} className={inputClass} placeholder="My model (High)" /></Field>
                <Field label="Provider"><select value={draft.provider} onChange={(event) => setDraft({ ...draft, provider: event.target.value as ModelPreset["provider"] })} className={inputClass}><option value="codex">Codex</option><option value="claude">Claude</option></select></Field>
                <Field label="Model"><input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} className={inputClass} /></Field>
                <Field label="Reasoning effort"><input value={draft.reasoning_effort ?? ""} onChange={(event) => setDraft({ ...draft, reasoning_effort: event.target.value || null })} className={inputClass} placeholder="high" /></Field>
                <Field label="Status"><select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value as ModelPreset["status"] })} className={inputClass}><option value="active">Active</option><option value="compatibility">Compatibility</option></select></Field>
                <Field label="Model provider"><input value={draft.model_provider ?? ""} onChange={(event) => setDraft({ ...draft, model_provider: event.target.value || null })} className={inputClass} /></Field>
                <Field label="Service tier"><input value={draft.service_tier ?? ""} onChange={(event) => setDraft({ ...draft, service_tier: event.target.value || null })} className={inputClass} /></Field>
                <label className="flex flex-col gap-1 sm:col-span-2"><span className={labelClass}>Description</span><textarea value={draft.description ?? ""} onChange={(event) => setDraft({ ...draft, description: event.target.value })} className={`${inputClass} min-h-16 resize-y`} /></label>
              </div>
              <div className="mt-3 flex justify-end gap-2"><button type="button" onClick={() => { setDraft(null); setEditingId(null); }} className={secondaryButton}>Cancel</button><button type="button" disabled={saving || !draft.id.trim() || !draft.label.trim() || !draft.model.trim()} onClick={() => void savePreset()} className={primaryButton}>{saving ? "Saving…" : "Save preset"}</button></div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1"><span className={labelClass}>{label}</span>{children}</label>;
}

const labelClass = "text-[10px] font-medium uppercase tracking-[0.12em] text-ink-subtle";
const inputClass = "rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink-strong focus:border-brand focus:outline-none disabled:opacity-50";
const primaryButton = "rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white shadow-card transition hover:brightness-95 disabled:opacity-40";
const secondaryButton = "rounded-md border border-line bg-surface px-2.5 py-1.5 text-[11px] text-ink-muted transition hover:text-ink disabled:opacity-40";
