import { useState } from "react";

import { canApplyUpdate } from "../selfUpdate";
import type { SelfUpdateApplyResult, SelfUpdateBlocker, SelfUpdateState } from "../types";

type Props = {
  state: SelfUpdateState | null;
  visible: boolean;
  applying: boolean;
  error: string | null;
  onApply: () => Promise<SelfUpdateApplyResult>;
  onDismiss: () => void;
  onJump: (blocker: SelfUpdateBlocker) => void;
};

const STATE_LABELS: Record<string, string> = {
  running: "在跑",
  queued: "排队",
  waiting: "等我",
  awaiting_human_input: "等我",
  finalizing: "收尾",
};

export function UpdateBanner({
  state,
  visible,
  applying,
  error,
  onApply,
  onDismiss,
  onJump,
}: Props) {
  const [result, setResult] = useState<SelfUpdateApplyResult | null>(null);
  if (!visible || !state) return null;

  const blocked = state.blockers.length > 0;
  const disabledReason = state.dirty
    ? "工作区有未提交改动，请先提交或贮藏。"
    : state.ahead > 0
      ? "本地存在尚未推送的提交，无法自动快进。"
      : blocked
        ? `${state.blockers.length} 个节点仍在进行，结束后才能更新。`
        : null;

  const runApply = async () => {
    try {
      setResult(await onApply());
    } catch {
      /* The shared controller exposes the actionable error below. */
    }
  };

  return (
    <div className="shrink-0 border-b border-state-waiting/35 bg-state-waiting-soft px-4 py-2 text-ink">
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold text-ink-strong">
            有新版本：落后 {state.behind} 个提交，可快进更新
          </div>
          {disabledReason ? (
            <div className="mt-0.5 text-[11px] text-state-waiting">{disabledReason}</div>
          ) : (
            <div className="mt-0.5 text-[11px] text-ink-muted">
              更新完成后进程会退出，请按终端提示完成构建并重启。
            </div>
          )}
          {blocked ? (
            <div className="mt-1 flex flex-wrap gap-1.5">
              {state.blockers.map((blocker) => (
                <button
                  key={`${blocker.project_id}:${blocker.node_id}`}
                  type="button"
                  onClick={() => onJump(blocker)}
                  className="rounded border border-state-waiting/35 bg-surface-raised px-2 py-0.5 font-mono text-[10px] text-ink-muted hover:text-ink"
                >
                  {blocker.project_name} / {blocker.node_id.slice(0, 8)} · {STATE_LABELS[blocker.state] ?? blocker.state}
                </button>
              ))}
            </div>
          ) : null}
          {error ? <div className="mt-1 text-[11px] text-state-error">{error}</div> : null}
          {result ? (
            <div className="mt-1 text-[11px] text-state-done">
              {result.message}{result.restart_commands.length > 0 ? `：${result.restart_commands.join("；")}` : "。"}
            </div>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            disabled={!canApplyUpdate(state) || applying}
            onClick={() => void runApply()}
            className="rounded-md bg-state-waiting px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {applying ? "正在更新…" : "更新并退出"}
          </button>
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-md border border-state-waiting/40 bg-surface-raised px-3 py-1.5 text-xs text-ink-muted hover:text-ink"
          >
            稍后
          </button>
        </div>
      </div>
    </div>
  );
}
