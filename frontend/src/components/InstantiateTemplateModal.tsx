import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, applyUserTemplate } from "../api";
import type { NodeInfo, TemplateSummary } from "../types";
import {
  buildInstantiateRequest,
  canSubmitInstantiation,
  initialArgumentValues,
  initialInputBindings,
  inputCandidates,
  isRetryableApplyStatus,
  missingRequiredArguments,
  pruneStaleBindings,
  unboundInputPorts,
  warningText,
} from "../templateInstantiate";

type Props = {
  open: boolean;
  sessionId: string | null;
  template: TemplateSummary | null;
  nodes: NodeInfo[];
  activePlanspaceId: string | null;
  /** Node the card was dropped onto, prefilled into the first input port. */
  anchorNodeId: string | null;
  onCancel: () => void;
  onApplied: (result: { instanceId: string; nodeIds: string[] }) => void;
};

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.detail ?? err.message;
  return err instanceof Error ? err.message : String(err);
}

export function InstantiateTemplateModal({
  open,
  sessionId,
  template,
  nodes,
  activePlanspaceId,
  anchorNodeId,
  onCancel,
  onApplied,
}: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [bindings, setBindings] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryable, setRetryable] = useState(false);
  const firstFieldRef = useRef<HTMLInputElement | HTMLSelectElement | null>(null);

  const candidates = useMemo(
    () => inputCandidates(nodes, activePlanspaceId),
    [nodes, activePlanspaceId],
  );

  /* Reset per opening, not per render: the dialog keeps whatever the user
   * typed across a failed submit so a 400 from the backend is fixable in
   * place. `template.slug` is in the deps so reopening for a different
   * template rebuilds the form. */
  useEffect(() => {
    if (!open || !template) return;
    setValues(initialArgumentValues(template.arguments));
    setBindings(initialInputBindings(template.inputs, anchorNodeId, candidates));
    setSubmitting(false);
    setError(null);
    setRetryable(false);
    window.setTimeout(() => firstFieldRef.current?.focus(), 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, template?.slug]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) {
        event.stopPropagation();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [open, submitting, onCancel]);

  /* The lane keeps refreshing underneath the dialog, so a bound node can be
   * deleted or obsoleted mid-edit. Clear those ports rather than submitting an
   * id the backend will reject. */
  useEffect(() => {
    if (!open) return;
    setBindings((prev) => pruneStaleBindings(prev, candidates));
  }, [open, candidates]);

  if (!open || !template) return null;

  const ready = canSubmitInstantiation(template, values, bindings);
  const missingArguments = missingRequiredArguments(template.arguments, values);
  const missingPorts = unboundInputPorts(template.inputs, bindings);

  const submit = async () => {
    if (!sessionId || !ready || submitting) return;
    setSubmitting(true);
    setError(null);
    setRetryable(false);
    try {
      const res = await applyUserTemplate(
        sessionId,
        template.slug,
        buildInstantiateRequest(template, values, bindings, anchorNodeId),
      );
      onApplied({ instanceId: res.instance_id, nodeIds: res.node_ids });
    } catch (err) {
      setError(errorMessage(err));
      setRetryable(
        isRetryableApplyStatus(err instanceof ApiError ? err.status : null),
      );
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm">
      <div className="flex max-h-[88vh] w-[560px] max-w-[95vw] flex-col rounded-xl border border-line bg-surface-raised shadow-modal">
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="truncate font-display text-sm font-semibold text-ink-strong">
              实例化模板 · {template.name}
            </div>
            <div className="text-[11px] text-ink-muted">
              {template.node_count === 1
                ? "将创建 1 个虚拟节点。"
                : `将创建 ${template.node_count} 个虚拟节点。`}
              {template.brief ? ` ${template.brief}` : ""}
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded px-2 py-1 text-[11px] font-medium text-ink-muted transition hover:bg-surface-sunken hover:text-ink disabled:opacity-40"
          >
            Esc
          </button>
        </div>

        <div className="flex flex-1 flex-col gap-5 overflow-y-auto px-5 py-4 text-sm">
          {template.arguments.length > 0 && (
            <section className="flex flex-col gap-3">
              <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                参数
              </div>
              {template.arguments.map((argument, index) => (
                <label key={argument.name} className="flex flex-col gap-1">
                  <span className="flex items-center gap-1.5 text-xs font-medium text-ink-strong">
                    <span className="font-mono">{argument.name}</span>
                    {argument.required && (
                      <span className="text-state-error" title="必填">
                        *
                      </span>
                    )}
                    {!argument.declared && (
                      <span
                        className="rounded border border-line bg-surface/70 px-1 py-0.5 text-[9px] font-medium text-ink-muted"
                        title="模板作者尚未在 template.yaml 中声明该参数，它来自提示词扫描"
                      >
                        未声明
                      </span>
                    )}
                  </span>
                  {argument.description && (
                    <span className="text-[11px] leading-snug text-ink-muted">
                      {argument.description}
                    </span>
                  )}
                  <input
                    ref={
                      index === 0
                        ? (el) => {
                            firstFieldRef.current = el;
                          }
                        : undefined
                    }
                    type="text"
                    value={values[argument.name] ?? ""}
                    onChange={(e) =>
                      setValues((prev) => ({
                        ...prev,
                        [argument.name]: e.target.value,
                      }))
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && ready && !submitting) void submit();
                    }}
                    placeholder={argument.required ? "必填" : "可留空"}
                    className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
                  />
                </label>
              ))}
            </section>
          )}

          {template.inputs.length > 0 && (
            <section className="flex flex-col gap-3">
              <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                输入端口
              </div>
              {candidates.length === 0 && (
                <div className="rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-xs text-state-waiting">
                  当前方向里没有可绑定的节点。请先在本方向创建节点，再实例化该模板。
                </div>
              )}
              {template.inputs.map((port, index) => (
                <label key={port.name} className="flex flex-col gap-1">
                  <span className="flex items-center gap-1.5 text-xs font-medium text-ink-strong">
                    <span className="font-mono">{port.name}</span>
                    <span className="text-state-error" title="必绑">
                      *
                    </span>
                  </span>
                  {port.description && (
                    <span className="text-[11px] leading-snug text-ink-muted">
                      {port.description}
                    </span>
                  )}
                  <select
                    ref={
                      template.arguments.length === 0 && index === 0
                        ? (el) => {
                            firstFieldRef.current = el;
                          }
                        : undefined
                    }
                    value={bindings[port.name] ?? ""}
                    onChange={(e) =>
                      setBindings((prev) => ({
                        ...prev,
                        [port.name]: e.target.value,
                      }))
                    }
                    disabled={candidates.length === 0}
                    className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong focus:border-brand focus:outline-none disabled:opacity-40"
                  >
                    <option value="">选择上游节点…</option>
                    {candidates.map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.shortId} · {candidate.label}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </section>
          )}

          {template.warnings.length > 0 && (
            <section className="flex flex-col gap-1 rounded-md border border-line bg-surface-sunken px-3 py-2">
              <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                模板提示
              </div>
              {template.warnings.map((warning, index) => (
                <div
                  key={`${warning.code}:${warning.name}:${index}`}
                  className="text-[11px] leading-snug text-ink-muted"
                >
                  {warningText(warning)}
                </div>
              ))}
            </section>
          )}

          {error && (
            <div
              className={
                retryable
                  ? "rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-xs text-state-waiting"
                  : "rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-xs text-state-error"
              }
            >
              {error}
              {retryable && (
                <div className="mt-1 text-[11px] opacity-80">
                  项目正忙，已填内容保留，稍后再点「创建」即可。
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 rounded-b-xl border-t border-line bg-surface-sunken px-5 py-3">
          <div className="min-w-0 truncate text-[11px] text-ink-subtle">
            {missingArguments.length > 0 &&
              `待填参数：${missingArguments.join("、")}`}
            {missingArguments.length > 0 && missingPorts.length > 0 && " · "}
            {missingPorts.length > 0 && `待绑端口：${missingPorts.join("、")}`}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={submitting}
              className="rounded-md border border-line bg-surface px-3 py-1.5 text-xs text-ink-muted transition hover:bg-surface-sunken hover:text-ink disabled:opacity-40"
            >
              取消
            </button>
            <button
              type="button"
              disabled={submitting || !ready || !sessionId}
              onClick={() => void submit()}
              className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
            >
              {submitting ? "创建中…" : "创建"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
