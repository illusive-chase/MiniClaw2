import { useEffect, useState } from "react";
import { getCodexEnv, updateCodexEnv } from "../api";
import type { CodexEnvSettings } from "../types";

type Props = {
  open: boolean;
  inputClass: string;
  labelClass: string;
  primaryButton: string;
};

/* Host-local: the script path names a file on this machine, so the setting is
 * stored outside the synced config and other devices keep their own. */
export function CodexEnvSection({ open, inputClass, labelClass, primaryButton }: Props) {
  const [status, setStatus] = useState<CodexEnvSettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [scriptPath, setScriptPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = (next: CodexEnvSettings) => {
    setStatus(next);
    setEnabled(next.enabled);
    setScriptPath(next.script_path);
  };

  useEffect(() => {
    if (!open) return;
    setError(null);
    getCodexEnv().then(apply).catch((err) => setError(String(err)));
  }, [open]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      apply(await updateCodexEnv({ enabled, script_path: scriptPath.trim() }));
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const dirty = status !== null && (enabled !== status.enabled || scriptPath.trim() !== status.script_path);

  return (
    <section className="rounded-lg border border-line bg-surface-raised p-4 shadow-card">
      <div className="font-display text-sm font-semibold text-ink-strong">Codex 环境 wrapper</div>
      <div className="mt-1 text-[11px] leading-relaxed text-ink-muted">
        开启后，启动 <span className="font-mono">codex app-server</span> 前先用
        <span className="font-mono"> {status?.shell ?? "bash"}</span> source 指定脚本，再在该环境中执行 Codex。
        仅保存在本机，不会跨设备同步。
      </div>
      {error && <div className="mt-2 rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-xs text-state-error">{error}</div>}
      <div className="mt-3 grid grid-cols-1 gap-3">
        <label className="flex items-center gap-2 rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink">
          <input type="checkbox" checked={enabled} disabled={!status} onChange={(event) => setEnabled(event.target.checked)} className="accent-brand" />
          启用环境 wrapper
        </label>
        <label className="flex flex-col gap-1">
          <span className={labelClass}>脚本路径（绝对路径，可用 ~）</span>
          <input
            value={scriptPath}
            disabled={!status}
            placeholder="~/.config/miniclaw2/codex-env.sh"
            onChange={(event) => setScriptPath(event.target.value)}
            className={`${inputClass} font-mono`}
          />
        </label>
        {status && status.script_path && !status.script_exists && (
          <div className="text-[11px] text-state-error">已保存的脚本路径在本机不存在{status.enabled ? "；启用状态下 Codex 节点将无法启动" : ""}。</div>
        )}
      </div>
      <div className="mt-3 flex justify-end">
        <button type="button" disabled={saving || !dirty} onClick={() => void save()} className={primaryButton}>保存</button>
      </div>
    </section>
  );
}
