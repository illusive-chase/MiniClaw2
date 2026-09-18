import { useState } from "react";
import { X } from "lucide-react";
import { bindProjectHere, configureRemoteAccess } from "../api";
import type { RemoteAccessConfig, SessionInfo } from "../types";
import { RemoteExecutionFields } from "./RemoteExecutionFields";

export function RemoteAccessModal({ session, onClose, onSaved }: {
  session: SessionInfo;
  onClose: () => void;
  onSaved: (session: SessionInfo) => void;
}) {
  const [value, setValue] = useState<RemoteAccessConfig>(session.remote_access ?? {
    ssh_target: session.remote?.target_id ?? "",
  });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setPending(true);
    setError("");
    try {
      const access = { ...value, ssh_target: value.ssh_target.trim(), connect_via: value.connect_via?.trim() || null };
      onSaved(session.bound_here
        ? await configureRemoteAccess(session.id, access)
        : await bindProjectHere(session.id, { remote: access }));
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-surface-scrim/60 p-4">
    <section role="dialog" aria-modal="true" aria-labelledby="remote-access-title"
      onKeyDown={(event) => { if (event.key === "Escape" && !pending) onClose(); }}
      className="flex max-h-[92vh] w-full max-w-md flex-col overflow-y-auto rounded-lg border border-line bg-surface-raised p-5 shadow-modal">
      <header className="mb-4 flex items-center justify-between gap-2">
        <h2 id="remote-access-title" className="text-sm font-semibold text-ink">本机远端接入</h2>
        <button type="button" title="关闭" aria-label="关闭" disabled={pending} onClick={onClose} className="p-1 text-ink-muted"><X size={16} /></button>
      </header>
      <form onSubmit={(event) => { event.preventDefault(); void save(); }} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1 text-xs text-ink-muted">SSH 目标
          <input autoFocus required value={value.ssh_target} onChange={(event) => setValue({ ...value, ssh_target: event.target.value })} className="min-w-0 rounded border border-line bg-surface-sunken px-3 py-2 text-ink" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted">跳板机
          <input value={value.connect_via ?? ""} onChange={(event) => setValue({ ...value, connect_via: event.target.value })} className="min-w-0 rounded border border-line bg-surface-sunken px-3 py-2 text-ink" />
        </label>
        <RemoteExecutionFields value={value} onChange={setValue} />
        {error && <p role="alert" className="break-words text-xs text-state-error">{error}</p>}
        <button type="submit" disabled={pending} className="self-end rounded bg-brand px-3 py-2 text-xs text-white disabled:opacity-50">{pending ? "验证中..." : "验证并保存"}</button>
      </form>
    </section>
  </div>;
}
