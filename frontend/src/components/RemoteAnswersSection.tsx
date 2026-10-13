import { useCallback, useEffect, useState } from "react";
import { getRemoteAnswers, setRemoteAnswerChannel } from "../api";
import type { RemoteAnswerChannel, RemoteAnswersStatus } from "../types";
import { FeishuSettingsSection } from "./FeishuSettingsSection";
import { WecomSettingsSection } from "./WecomSettingsSection";

type Props = {
  open: boolean;
  inputClass: string;
  labelClass: string;
  primaryButton: string;
  secondaryButton: string;
};

const CHANNELS: { value: RemoteAnswerChannel; label: string }[] = [
  { value: "feishu", label: "飞书" },
  { value: "wecom", label: "企业微信" },
  { value: "none", label: "不使用" },
];

/* Each host answers remotely through one channel; switching keeps the other
 * channel's credentials so switching back needs no new scan. */
export function RemoteAnswersSection(props: Props) {
  const { open, secondaryButton } = props;
  const [status, setStatus] = useState<RemoteAnswersStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getRemoteAnswers());
    } catch (err) {
      setError(String(err));
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setError(null);
    void load();
  }, [open, load]);

  const choose = (channel: RemoteAnswerChannel) => {
    if (!status || channel === status.channel) return;
    const from = CHANNELS.find((item) => item.value === status.channel)?.label;
    if (
      status.channel !== "none" &&
      !window.confirm(`切换后本机断开${from}，已推送的${from}提问会失效（凭证保留，切回时无需重新绑定）。继续？`)
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    setRemoteAnswerChannel(channel)
      .then(setStatus)
      .catch((err) => setError(String(err)))
      .finally(() => setBusy(false));
  };

  const channel = status?.channel ?? null;

  return (
    <section className="rounded-lg border border-line bg-surface-raised p-4 shadow-card">
      <div className="font-display text-sm font-semibold text-ink-strong">远程回答</div>
      <div className="mt-1 text-[11px] leading-relaxed text-ink-muted">
        agent 提问后在网页端等待一段时间仍未回答时，推送到手机，可在手机上作答；网页端照常可以作答，先答先生效。
        每台主机选一个通道。
      </div>
      <div className="mt-3 flex flex-wrap gap-2" role="radiogroup" aria-label="远程回答通道">
        {CHANNELS.map((item) => (
          <button
            key={item.value}
            type="button"
            role="radio"
            aria-checked={channel === item.value}
            disabled={busy || !status || (!status.runtime_owner && channel !== item.value)}
            onClick={() => choose(item.value)}
            className={
              channel === item.value
                ? "rounded-md border border-brand bg-brand/15 px-3 py-1.5 text-xs font-medium text-brand-ink dark:text-brand"
                : secondaryButton
            }
          >
            {item.label}
          </button>
        ))}
      </div>
      {error && <div className="mt-3 rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-[11px] text-state-error">{error}</div>}
      {channel && channel !== "none" && (
        <div className="mt-4 border-t border-line pt-4">
          {/* Keyed so a switch remounts the section and reloads its status. */}
          {channel === "feishu" ? (
            <FeishuSettingsSection key="feishu" {...props} />
          ) : (
            <WecomSettingsSection key="wecom" {...props} />
          )}
        </div>
      )}
      {channel === "none" && (
        <div className="mt-3 text-[11px] text-ink-muted">本机不推送提问，只在网页端作答。</div>
      )}
    </section>
  );
}
