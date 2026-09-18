import type { RemoteAccessConfig } from "../types";

export function RemoteExecutionFields({ value, onChange }: {
  value: RemoteAccessConfig;
  onChange: (value: RemoteAccessConfig) => void;
}) {
  return <div className="col-span-2 flex flex-col gap-3">
    <label className="flex items-center gap-2 text-xs text-ink">
      <input type="checkbox" checked={value.codex_remote_experimental ?? false}
        onChange={(event) => onChange({ ...value, codex_remote_experimental: event.target.checked })}
        className="h-4 w-4 accent-brand" />
      Codex 远端执行（实验）
    </label>
    {value.codex_remote_experimental && <>
      <label className="flex flex-col gap-1 text-xs text-ink-muted">
        远端 Codex 可执行路径
        <input value={value.codex_path ?? "codex"}
          onChange={(event) => onChange({ ...value, codex_path: event.target.value })}
          className="min-w-0 rounded-md border border-line bg-surface-sunken px-3 py-2 font-mono text-xs text-ink" />
      </label>
      <label className="flex flex-col gap-1 text-xs text-ink-muted">
        远端隔离
        <select value={value.sandbox ?? "workspaceWrite"}
          onChange={(event) => onChange({ ...value, sandbox: event.target.value as RemoteAccessConfig["sandbox"] })}
          className="min-w-0 rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink">
          <option value="workspaceWrite">Codex 工作区沙箱</option>
          <option value="externalSandbox">远端容器 / 账号承担隔离</option>
        </select>
      </label>
      {value.sandbox === "externalSandbox" && <p role="note" className="text-xs text-state-error">
        Codex 不提供进程级文件隔离；权限边界由远端容器或账号承担。
      </p>}
    </>}
  </div>;
}
