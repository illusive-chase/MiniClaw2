import { useEffect, useState } from "react";
import type { SharingTopology } from "../api";

type Props = {
  open: boolean;
  mode: "enable" | "join";
  pending: boolean;
  onCancel: () => void;
  onConfirm: (topology: SharingTopology) => void;
};

export function UnverifiedSharingDialog({
  open,
  mode,
  pending,
  onCancel,
  onConfirm,
}: Props) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [topology, setTopology] = useState<SharingTopology>("unknown");

  useEffect(() => {
    if (!open) return;
    setAcknowledged(false);
    setTopology("unknown");
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-surface-scrim/60 p-4 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="unverified-sharing-title"
        className="flex max-h-[92vh] w-[620px] max-w-[96vw] flex-col overflow-hidden rounded-lg border border-state-waiting/40 bg-surface-raised shadow-modal"
      >
        <div className="border-b border-line px-5 py-4">
          <div id="unverified-sharing-title" className="font-display text-sm font-semibold text-ink-strong">
            身份未校验的跨设备共享
          </div>
          <div className="mt-1 text-[11px] leading-relaxed text-ink-muted">
            此项目没有 Git 仓库，MiniClaw2 无法验证各设备指向的是同一份代码。
          </div>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4 text-xs leading-relaxed text-ink">
          <ol className="list-decimal space-y-2 pl-5 text-ink-muted">
            <li>请确认每台设备的路径都指向等价目录树，且依赖、数据和配置严格对齐。</li>
            <li>设备间文件分歧不会被发现，画布仍会把它们呈现为同一个项目。</li>
            <li>没有回滚；code review、提交主干和提交视图不可用。</li>
            <li>共享挂载上的并发额度不跨主机生效，可能有多个 agent 同时写入。</li>
            <li>当前版本无法解除主机绑定，也无法关闭共享。</li>
          </ol>

          <label className="flex flex-col gap-1.5">
            <span className="text-[10px] font-medium uppercase text-ink-subtle">目录拓扑</span>
            <select
              value={topology}
              onChange={(event) => setTopology(event.target.value as SharingTopology)}
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink-strong focus:border-brand focus:outline-none"
            >
              <option value="unknown">不确定，按复制目录处理</option>
              <option value="shared-filesystem">所有设备共享同一挂载目录</option>
              <option value="replicated">每台设备有独立的目录副本</option>
            </select>
          </label>

          <label className="flex items-start gap-2 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2.5">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
              className="mt-0.5 accent-brand"
            />
            <span>
              我已核对本次{mode === "enable" ? "开启共享" : "设备绑定"}使用的目录和环境，并接受上述持续风险。
            </span>
          </label>
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-5 py-3.5">
          <button
            type="button"
            disabled={pending}
            onClick={onCancel}
            className="rounded-md border border-line bg-surface px-3 py-1.5 text-xs text-ink-muted transition hover:border-line-strong hover:text-ink disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            disabled={pending || !acknowledged}
            onClick={() => onConfirm(topology)}
            className="rounded-md border border-state-waiting/60 bg-state-waiting-soft px-3 py-1.5 text-xs font-medium text-state-waiting transition hover:border-state-waiting disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending ? "正在处理..." : mode === "enable" ? "确认开启共享" : "确认绑定设备"}
          </button>
        </div>
      </div>
    </div>
  );
}
