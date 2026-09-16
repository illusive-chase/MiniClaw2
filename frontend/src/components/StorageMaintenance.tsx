import { useEffect, useState } from "react";

import { ApiError, getMigrationPlan, type MigrationPlan, type MigrationStep } from "../api";
import { guidanceNotes, storageGuidance } from "../storageMaintenance";

/* The maintenance page is what the user sees instead of their projects, so it
   carries the whole recovery path: what state the storage is in, which command
   clears it, and whether the backend has to be stopped first. The plan below is
   read-only — fetching it never records a confirmation. */

function StepList({ steps }: { steps: MigrationStep[] }) {
  return (
    <ul className="space-y-1">
      {steps.map((step) => (
        <li key={step.contract} className="text-[12px]">
          <span className="font-medium">
            v{step.source} → v{step.target}
          </span>
          {step.destructive && <span className="ml-2 text-state-error">有损</span>}
          <span className="ml-2 text-ink-muted">{step.summary}</span>
        </li>
      ))}
    </ul>
  );
}

function PlanDetails({ plan }: { plan: MigrationPlan }) {
  return (
    <div className="space-y-3 rounded border p-3 text-[12px]">
      <p className="font-medium">
        迁移计划（只读）：当前 v{plan.source} → 目标 v{plan.target}，本程序最低支持 v{plan.minimum}
      </p>
      {plan.steps.length > 0 ? (
        <div className="space-y-1">
          <p className="text-ink-muted">本机这条链要执行的步骤</p>
          <StepList steps={plan.steps} />
        </div>
      ) : (
        <p className="text-ink-muted">本机已是目标版本，没有待执行的步骤。</p>
      )}
      {plan.sync_confirmation_contracts.length > 0 && (
        <div className="space-y-1">
          <p className="text-ink-muted">本次确认将同时授予的契约（用于规范化远端旧快照）</p>
          <StepList steps={plan.sync_confirmation_contracts} />
          <p className="text-ink-muted">{plan.sync_confirmation_note}</p>
        </div>
      )}
      {plan.confirmation_hosts.length > 0 && (
        <div className="space-y-1">
          <p className="text-ink-muted">确认覆盖的设备记录</p>
          <ul className="flex flex-wrap gap-2">
            {plan.confirmation_hosts.map((host) => (
              <li key={host.host_id} className="rounded border px-2 py-0.5">
                {host.label}
                {host.local && <span className="ml-1 text-ink-muted">（本机）</span>}
              </li>
            ))}
          </ul>
          <p className="text-ink-muted">{plan.confirmation_hosts_note}</p>
        </div>
      )}
      <p className="text-ink-muted">{plan.layout_note}</p>
      <p className="text-ink-muted">{plan.note}</p>
    </div>
  );
}

export function StorageMaintenance({
  error,
}: {
  error: { state: string | null; detail: string };
}) {
  const guidance = storageGuidance(error.state);
  const [plan, setPlan] = useState<MigrationPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);

  /* `/migrations/` is exempt from the storage admission middleware, so the plan
     is still reachable while everything else answers 503. Some states have no
     computable plan at all (data newer than the program); the guidance above
     stands on its own, so a failure here is reported, not fatal. */
  useEffect(() => {
    const controller = new AbortController();
    getMigrationPlan(controller.signal)
      .then(setPlan)
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setPlanError(
          err instanceof ApiError ? err.detail ?? err.message
            : err instanceof Error ? err.message
            : String(err),
        );
      });
    return () => controller.abort();
  }, []);

  return (
    <>
      <p className="whitespace-pre-wrap break-words">{error.detail}</p>
      <p>{guidance.summary}</p>
      <p>业务数据尚未加载，这不是空项目列表。</p>
      <ol className="list-decimal space-y-1 pl-5">
        {guidance.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      {guidance.commands.length > 0 && (
        <>
          {guidance.requiresShutdown && (
            <p className="text-state-error">
              以下命令需要独占存储：请先在可中断的时间窗口停止本机后端，再执行。
            </p>
          )}
          <pre className="overflow-auto rounded border p-3">{guidance.commands.join("\n")}</pre>
        </>
      )}
      {guidanceNotes(guidance).map((note) => (
        <p key={note} className="text-[12px] text-ink-muted">
          {note}
        </p>
      ))}
      {plan && <PlanDetails plan={plan} />}
      {planError && <p className="text-[12px] text-ink-muted">无法读取迁移计划：{planError}</p>}
      <button className="rounded border px-4 py-2" onClick={() => window.location.reload()}>
        重新检查
      </button>
    </>
  );
}
