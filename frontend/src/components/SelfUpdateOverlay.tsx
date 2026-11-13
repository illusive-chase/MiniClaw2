import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";

import { getHealth } from "../api";
import { forgetPendingUpdate, type PendingUpdate } from "../selfUpdate";
import type { DaemonUpdateRun } from "../types";

export type { PendingUpdate };

const POLL_MS = 2000;
const GIVE_UP_MS = 5 * 60 * 1000;

const PHASE_LABELS: Record<string, string> = {
  verify: "校验目标版本",
  plan: "计算需要的安装与构建",
  stop: "停止后端",
  checkout: "快进源码",
  pip: "安装后端依赖",
  npm: "安装前端依赖",
  build: "构建前端",
  self: "重新加载守护进程",
  start: "启动新后端",
  health: "等待新后端就绪",
  finish: "收尾",
};

/**
 * Covers the page while the daemon replaces the backend. Progress comes from
 * the daemon while the old backend still answers; after that only `/health`
 * does, and the page reloads once it reports the target head (or the old
 * one, which means the daemon rolled back).
 */
export function SelfUpdateOverlay({ update, onClose }: { update: PendingUpdate; onClose: () => void }) {
  const [run, setRun] = useState<DaemonUpdateRun | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [gaveUp, setGaveUp] = useState(false);

  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    const tick = async () => {
      if (disposed) return;
      const spent = Date.now() - update.startedAt;
      setElapsed(Math.floor(spent / 1000));
      if (spent > GIVE_UP_MS) {
        setGaveUp(true);
        return;
      }
      try {
        /* A raw fetch: the replacement backend answers 401 under a passcode,
         * and the shared API helper would turn that into the login screen. */
        const response = await fetch("/self-update/progress", { cache: "no-store" });
        const progress = response.ok
          ? (await response.json()) as { update: DaemonUpdateRun | null }
          : null;
        if (!response.ok) setRestarting(true);
        if (!disposed && progress?.update) {
          setRun(progress.update);
          if (progress.update.status === "rejected") return;
        }
      } catch {
        if (!disposed) setRestarting(true);
      }
      try {
        const health = await getHealth(controller.signal);
        const replaced = health.boot_id !== update.bootId;
        if (!disposed && replaced && (health.head === update.target || health.head === update.oldHead)) {
          window.location.reload();
        }
      } catch {
        if (!disposed) setRestarting(true);
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      disposed = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [update]);

  const rejected = run?.status === "rejected";
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-surface-scrim/70 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="正在更新 MiniClaw2">
      <div className="w-[420px] max-w-[92vw] rounded-xl border border-line bg-surface-raised p-5 shadow-modal">
        <div className="flex items-center gap-2 font-display text-sm font-semibold text-ink-strong">
          {!rejected && !gaveUp && <LoaderCircle size={16} className="animate-spin text-brand" aria-hidden="true" />}
          正在更新 MiniClaw2 → <span className="font-mono">{update.target.slice(0, 8)}</span>
        </div>
        <div className="mt-3 text-[12px] leading-relaxed text-ink-muted">
          {rejected ? (
            <span className="text-state-error">更新被拒绝：{run?.error}</span>
          ) : gaveUp ? (
            <>已等待超过 5 分钟。请在终端运行 <code className="font-mono">miniclaw2 daemon status</code> 或 <code className="font-mono">miniclaw2 daemon logs update</code> 查看。</>
          ) : restarting ? (
            <>服务重启中 · 已用时 {elapsed} 秒。新后端就绪后页面会自动刷新。</>
          ) : (
            <>{PHASE_LABELS[run?.phase ?? ""] ?? "正在准备"} · 已用时 {elapsed} 秒</>
          )}
        </div>
        {(rejected || gaveUp) && (
          <div className="mt-4 flex justify-end">
            <button type="button" onClick={() => { forgetPendingUpdate(); onClose(); }} className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-[11px] text-ink-muted hover:text-ink">关闭</button>
          </div>
        )}
      </div>
    </div>
  );
}
