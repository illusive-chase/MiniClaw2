import { useEffect, useRef, useState } from "react";
import { ApiError, createSession } from "../api";
import { LANGUAGE_OPTIONS } from "../languages";
import type { GlobalDefaults, ModelPreset, SessionInfo } from "../types";
import {
  defaultModelPresetId,
  modelPresetDetail,
  selectableModelPresets,
} from "../modelPresets";

type Props = {
  open: boolean;
  modelPresets: ModelPreset[];
  defaults: GlobalDefaults | null;
  onCancel: () => void;
  onCreated: (session: SessionInfo) => void;
};

const MISSING_CWD_PREFIX = "cwd does not exist:";
type ProjectMode = "durable" | "ephemeral" | "remote";

function missingCwdPath(err: unknown): string | null {
  if (
    err instanceof ApiError &&
    err.status === 400 &&
    err.detail?.startsWith(MISSING_CWD_PREFIX)
  ) {
    return err.detail.slice(MISSING_CWD_PREFIX.length).trim();
  }
  return null;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function NewProjectModal({
  open,
  modelPresets,
  defaults,
  onCancel,
  onCreated,
}: Props) {
  const [name, setName] = useState("");
  const [preferredLanguage, setPreferredLanguage] = useState("");
  const [modelPresetId, setModelPresetId] = useState("");
  const [concurrency, setConcurrency] = useState(1);
  const [autoCommit, setAutoCommit] = useState(false);
  const [cwd, setCwd] = useState("");
  const [mode, setMode] = useState<ProjectMode>("durable");
  const [remoteTargetId, setRemoteTargetId] = useState("");
  const [remoteRootPath, setRemoteRootPath] = useState("");
  const [sshTarget, setSshTarget] = useState("");
  const [connectVia, setConnectVia] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement | null>(null);
  const activeModelPresets = selectableModelPresets(modelPresets);

  useEffect(() => {
    if (open) {
      setName("");
      setPreferredLanguage(defaults?.preferred_language ?? "");
      setModelPresetId(
        defaultModelPresetId(modelPresets, defaults?.default_model_preset_id),
      );
      setConcurrency(defaults?.concurrency ?? 1);
      setAutoCommit(defaults?.auto_commit ?? false);
      setCwd("");
      setMode("durable");
      setRemoteTargetId("");
      setRemoteRootPath("");
      setSshTarget("");
      setConnectVia("");
      setSubmitting(false);
      setError(null);
      window.setTimeout(() => nameRef.current?.focus(), 0);
    }
  }, [open, modelPresets, defaults]);

  if (!open) return null;

  const submit = async () => {
    const cwdInput = cwd.trim();
    const payload = (createMissingCwd: boolean) => {
      const common = {
        name: name.trim() || undefined,
        preferred_language: preferredLanguage || null,
        model_preset_id: modelPresetId || undefined,
        concurrency,
        auto_commit: mode === "remote" ? false : autoCommit,
        persistence_mode: mode,
      };
      if (mode === "remote") {
        return {
          ...common,
          remote: {
            target_id: remoteTargetId.trim(),
            root_path: remoteRootPath.trim(),
          },
          remote_access: {
            ssh_target: sshTarget.trim(),
            ...(connectVia.trim() ? { connect_via: connectVia.trim() } : {}),
          },
          remote_initialization: "existing" as const,
        };
      }
      return {
        ...common,
        cwd: mode === "ephemeral" ? undefined : (cwdInput || undefined),
        temporary: mode === "ephemeral",
        create_missing_cwd: createMissingCwd,
      };
    };

    setSubmitting(true);
    setError(null);
    try {
      const session = await createSession(payload(false));
      onCreated(session);
    } catch (err) {
      const missingPath = missingCwdPath(err);
      if (
        mode === "durable" &&
        cwdInput &&
        missingPath &&
        window.confirm(
          [
            "Working directory does not exist:",
            missingPath,
            "",
            "Create it and continue?",
          ].join("\n"),
        )
      ) {
        try {
          const session = await createSession(payload(true));
          onCreated(session);
          return;
        } catch (retryErr) {
          setError(errorMessage(retryErr));
          setSubmitting(false);
          return;
        }
      }
      setError(errorMessage(err));
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm">
      <div className="flex max-h-[92vh] w-[520px] max-w-[95vw] flex-col rounded-xl border border-line bg-surface-raised shadow-modal">
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="font-display text-sm font-semibold text-ink-strong">
              New project
            </div>
            <div className="text-[11px] text-ink-muted">
              A project pins one git working tree; nodes are launched explicitly inside it.
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-2 py-1 text-[11px] font-medium text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
          >
            Esc
          </button>
        </div>

        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto px-5 py-4 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Name <span className="text-ink-subtle/70">(optional)</span>
            </span>
            <input
              ref={nameRef}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My experiment"
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Language
            </span>
            <select
              value={preferredLanguage}
              onChange={(e) => setPreferredLanguage(e.target.value)}
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong focus:border-brand focus:outline-none"
            >
              {LANGUAGE_OPTIONS.map((option) => (
                <option key={option.value || "none"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              模型档位
            </span>
            <select
              value={modelPresetId}
              onChange={(e) => setModelPresetId(e.target.value)}
              disabled={activeModelPresets.length === 0}
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong focus:border-brand focus:outline-none disabled:opacity-40"
            >
              {activeModelPresets.length === 0 && (
                <option value="">没有可用模型档位</option>
              )}
              {activeModelPresets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.label}
                </option>
              ))}
            </select>
            {modelPresetId && (
              <span className="text-[11px] text-ink-muted">
                {modelPresetDetail(modelPresets, modelPresetId) || modelPresetId}
              </span>
            )}
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Concurrency
            </span>
            <input
              type="number"
              min={1}
              step={1}
              value={concurrency}
              onChange={(event) => setConcurrency(Math.max(1, Number(event.target.value) || 1))}
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong focus:border-brand focus:outline-none"
            />
            <span className="text-[11px] text-ink-muted">
              Maximum nodes that may actually run at once. Extra work stays queued.
            </span>
          </label>

          <div className="flex flex-col gap-1.5">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              项目位置
            </span>
            <div className="grid grid-cols-3 overflow-hidden rounded-md border border-line bg-surface-sunken p-0.5">
              {([
                ["durable", "本机目录"],
                ["ephemeral", "临时目录"],
                ["remote", "远端目录"],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setMode(value)}
                  className={`min-h-8 rounded px-2 text-xs transition ${
                    mode === value
                      ? "bg-surface-raised font-medium text-ink-strong shadow-card"
                      : "text-ink-muted hover:text-ink"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {mode === "durable" && (
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                Working directory{" "}
                <span className="text-ink-subtle/70">(optional)</span>
              </span>
              <input
                type="text"
                value={cwd}
                onChange={(e) => setCwd(e.target.value)}
                placeholder="leave blank to use server cwd"
                className="rounded-md border border-line bg-surface-sunken px-3 py-2 font-mono text-xs text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
              />
            </label>
          )}

          {mode === "ephemeral" && (
            <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink-muted">
              后端会创建临时工作区；项目删除时一并清理。
            </div>
          )}

          {mode === "remote" && (
            <div className="grid grid-cols-2 gap-3">
              <label className="col-span-2 flex flex-col gap-1 sm:col-span-1">
                <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">远端标识</span>
                <input value={remoteTargetId} onChange={(event) => setRemoteTargetId(event.target.value)} placeholder="autodl-a100" className="rounded-md border border-line bg-surface-sunken px-3 py-2 font-mono text-xs text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none" />
              </label>
              <label className="col-span-2 flex flex-col gap-1 sm:col-span-1">
                <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">SSH 目标</span>
                <input value={sshTarget} onChange={(event) => setSshTarget(event.target.value)} placeholder="user@gpu-box" className="rounded-md border border-line bg-surface-sunken px-3 py-2 font-mono text-xs text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none" />
              </label>
              <label className="col-span-2 flex flex-col gap-1">
                <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">远端仓库绝对路径</span>
                <input value={remoteRootPath} onChange={(event) => setRemoteRootPath(event.target.value)} placeholder="/root/autodl-tmp/project" className="rounded-md border border-line bg-surface-sunken px-3 py-2 font-mono text-xs text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none" />
              </label>
              <label className="col-span-2 flex flex-col gap-1">
                <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">跳板机 <span className="text-ink-subtle/70">(optional)</span></span>
                <input value={connectVia} onChange={(event) => setConnectVia(event.target.value)} placeholder="bastion" className="rounded-md border border-line bg-surface-sunken px-3 py-2 font-mono text-xs text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none" />
              </label>
              <p className="col-span-2 text-[11px] leading-5 text-ink-muted">
                当前仅接入已有 Git 工作树。远端是唯一权威，本机只保留单向只读投影；并发上限仅约束当前设备。
              </p>
            </div>
          )}

          <label className="flex items-start gap-2 text-xs text-ink">
            <input
              type="checkbox"
              checked={autoCommit}
              onChange={(e) => setAutoCommit(e.target.checked)}
              disabled={mode === "remote"}
              className="mt-0.5 h-4 w-4 accent-brand"
            />
            <span>
              Auto commit
              <span className="ml-1 text-ink-muted">
                {mode === "remote"
                  ? "— 远端项目当前不提供提交操作。"
                  : "— append a commit node after completed agent work."}
              </span>
            </span>
          </label>

          {error && (
            <div className="rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-xs text-state-error">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line bg-surface-sunken px-5 py-3 rounded-b-xl">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded-md border border-line bg-surface px-3 py-1.5 text-xs text-ink-muted transition hover:bg-surface-sunken hover:text-ink disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={
              submitting
              || !modelPresetId
              || (mode === "remote" && (
                !remoteTargetId.trim()
                || !remoteRootPath.trim()
                || !sshTarget.trim()
              ))
            }
            onClick={() => void submit()}
            className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
          >
            {submitting ? "Creating…" : "Create project"}
          </button>
        </div>
      </div>
    </div>
  );
}
