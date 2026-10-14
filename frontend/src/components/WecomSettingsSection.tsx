import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import qrcode from "qrcode-generator";
import {
  beginWecomRegistration,
  cancelWecomRegistration,
  getWecomRegistration,
  getWecomStatus,
  reconnectWecom,
  sendWecomTest,
  startWecomPairing,
  unbindWecom,
  updateWecomSettings,
} from "../api";
import type { WecomRegistration, WecomStatus } from "../types";

type Props = {
  open: boolean;
  inputClass: string;
  labelClass: string;
  primaryButton: string;
  secondaryButton: string;
};

/* source.desc_color on the cards: grey, black, red, green. */
const SOURCE_COLORS = ["#8f959e", "#1f2329", "#d83931", "#2ea121"];

const CONNECTION_LABEL: Record<WecomStatus["connection"]["state"], string> = {
  off: "未连接",
  connecting: "连接中",
  connected: "已连接",
  error: "连接失败",
  kicked: "已被挤下线",
};

const BINDING_LABEL: Record<NonNullable<WecomStatus["binding_source"]>, string> = {
  created: "扫码创建",
  manual: "手动填写",
};

export function WecomSettingsSection({
  open,
  inputClass,
  labelClass,
  primaryButton,
  secondaryButton,
}: Props) {
  const [status, setStatus] = useState<WecomStatus | null>(null);
  const [registration, setRegistration] = useState<WecomRegistration | null>(null);
  const [showManual, setShowManual] = useState(false);
  const [manualBotId, setManualBotId] = useState("");
  const [manualSecret, setManualSecret] = useState("");
  const [delaySeconds, setDelaySeconds] = useState(60);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const settingsSeeded = useRef(false);

  const load = useCallback(async () => {
    try {
      const next = await getWecomStatus();
      setStatus(next);
      setRegistration(next.registration);
      if (!settingsSeeded.current) {
        settingsSeeded.current = true;
        setDelaySeconds(next.delay_seconds);
      }
    } catch (err) {
      setError(String(err));
    }
  }, []);

  useEffect(() => {
    if (!open) {
      settingsSeeded.current = false;
      return;
    }
    setNotice(null);
    setError(null);
    void load();
  }, [open, load]);

  /* While a pairing code is out, poll so the page notices the claim. */
  const awaitingClaim = Boolean(status?.configured && !status.bound);
  useEffect(() => {
    if (!open || !awaitingClaim) return;
    const timer = window.setInterval(() => void load(), 3_000);
    return () => window.clearInterval(timer);
  }, [open, awaitingClaim, load]);

  const polling = registration?.state === "polling" || registration?.state === "starting";
  useEffect(() => {
    if (!open || !polling) return;
    const timer = window.setInterval(() => {
      getWecomRegistration()
        .then((next) => {
          setRegistration(next);
          if (next && next.state !== "polling" && next.state !== "starting") void load();
        })
        .catch((err) => setError(String(err)));
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [open, polling, load]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const beginScan = () =>
    run(async () => {
      setRegistration(await beginWecomRegistration());
    });

  const cancelScan = () =>
    run(async () => {
      setRegistration(await cancelWecomRegistration());
    });

  const saveManual = () =>
    run(async () => {
      setStatus(await updateWecomSettings({ bot_id: manualBotId.trim(), secret: manualSecret }));
      setManualSecret("");
    });

  const pair = () =>
    run(async () => {
      await startWecomPairing();
      await load();
    });

  const reconnect = () =>
    run(async () => {
      setStatus(await reconnectWecom());
    });

  const saveTiming = () =>
    run(async () => {
      setStatus(await updateWecomSettings({ delay_seconds: delaySeconds }));
      setNotice("已保存");
    });

  const toggleEnabled = (enabled: boolean) =>
    run(async () => {
      setStatus(await updateWecomSettings({ enabled }));
    });

  const test = () =>
    run(async () => {
      await sendWecomTest();
      setNotice("测试卡已发送，请在企业微信中查看并点击。");
    });

  const unbind = () => {
    if (!window.confirm("解绑后本机会断开企业微信连接并删除机器人凭证，已推送的卡片会失效。继续？")) return;
    void run(async () => {
      setStatus(await unbindWecom());
      setRegistration(null);
      setNotice("已解绑。不再需要这个机器人时，可以在企业微信「工作台 → 智能机器人」里删除它。");
    });
  };

  const hostColor = status ? SOURCE_COLORS[status.host.color] ?? "#64748b" : "#64748b";
  const pairing = status?.pairing ?? null;
  const kicked = status?.connection.state === "kicked";

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <div className="text-[11px] leading-relaxed text-ink-muted">
          每台主机使用自己的企业微信智能机器人（同一个机器人同时只能有一条连接）。
          问题、选项、项目名和节点名会经过企业微信服务器；敏感输入不会发送。
        </div>
        {status && (
          <span
            className={`shrink-0 rounded border px-2 py-1 text-[10px] font-medium ${
              status.connection.state === "connected"
                ? "border-state-done/40 bg-state-done-soft text-state-done"
                : status.connection.state === "error" || kicked
                  ? "border-state-error/40 bg-state-error-soft text-state-error"
                  : "border-line bg-surface-sunken text-ink-muted"
            }`}
          >
            {status.configured ? CONNECTION_LABEL[status.connection.state] : "未绑定"}
          </span>
        )}
      </div>

      {error && <div className="mt-3 rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-[11px] text-state-error">{error}</div>}
      {notice && <div className="mt-3 rounded-md border border-state-done/30 bg-state-done-soft px-3 py-2 text-[11px] text-state-done">{notice}</div>}

      {status && !status.installed && (
        <div className="mt-3 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] leading-relaxed text-ink">
          后端未安装 websockets。在后端环境中执行
          <code className="mx-1 rounded bg-surface-sunken px-1 font-mono">pip install &apos;miniclaw2[wecom]&apos;</code>
          后重启后端。
        </div>
      )}
      {status && !status.runtime_owner && (
        <div className="mt-3 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] leading-relaxed text-ink">
          这个后端不是本机存储的运行时属主（同一存储上已有另一个后端在运行），不会连接企业微信。
        </div>
      )}

      {status && (
        <div className="mt-3 flex items-center gap-2 text-[11px] text-ink-muted">
          <span>本机</span>
          <span className="rounded px-1.5 py-0.5 font-medium text-white" style={{ backgroundColor: hostColor }}>
            {status.host.label}
          </span>
          <span className="font-mono text-[10px] text-ink-subtle">卡片左上角的来源用这个名字区分主机</span>
        </div>
      )}

      {registration && registration.state !== "succeeded" && (
        <WecomRegistrationPanel
          registration={registration}
          busy={busy}
          onCancel={() => void cancelScan()}
          onDismiss={() => setRegistration(null)}
          secondaryButton={secondaryButton}
        />
      )}

      {status && !status.configured && !polling && status.installed && status.runtime_owner && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy} onClick={() => void beginScan()} className={primaryButton}>
              扫码创建机器人（推荐）
            </button>
          </div>
          <div className="text-[11px] leading-relaxed text-ink-muted">
            用企业微信扫码后一路确认，会在你的企业里新建一个智能机器人，并把凭证交给本机。
            扫码页如果让你选择「已有机器人」，请选新建：一个机器人同时只能连接一台主机。
            这个接口是企业微信 CLI 使用的未公开接口，失效时请改用手动填写。
          </div>
          <details open={showManual} onToggle={(event) => setShowManual((event.target as HTMLDetailsElement).open)}>
            <summary className="cursor-pointer text-[11px] text-ink-muted">手动填写（扫码不可用，或复用自建的机器人）</summary>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className={labelClass}>Bot ID</span>
                <input value={manualBotId} onChange={(event) => setManualBotId(event.target.value)} className={inputClass} placeholder="aib…" />
              </label>
              <label className="flex flex-col gap-1">
                <span className={labelClass}>Secret</span>
                <input type="password" autoComplete="off" value={manualSecret} onChange={(event) => setManualSecret(event.target.value)} className={inputClass} />
              </label>
            </div>
            <div className="mt-2 text-[11px] leading-relaxed text-ink-muted">
              在企业微信「工作台 → 智能机器人 → 创建机器人」中选择 API 模式、使用长连接，复制 Bot ID 和 Secret。
              不要在多台主机上填写同一组凭证，否则它们会互相挤下线。
            </div>
            <div className="mt-2 flex justify-end">
              <button type="button" disabled={busy || !manualBotId.trim() || !manualSecret} onClick={() => void saveManual()} className={primaryButton}>
                保存并连接
              </button>
            </div>
          </details>
        </div>
      )}

      {status?.configured && (
        <div className="mt-3 space-y-3">
          <div className="grid grid-cols-1 gap-x-4 gap-y-1 font-mono text-[10px] text-ink-muted sm:grid-cols-2">
            <div>Bot ID：{status.bot_id}</div>
            <div>绑定方式：{status.binding_source ? BINDING_LABEL[status.binding_source] : "—"}</div>
            <div>认领账号：{status.bound ? status.bound.userid : "未认领"}</div>
            <div>待答提问：{status.pending}</div>
          </div>
          {status.connection.detail && (status.connection.state === "error" || kicked || status.connection.state === "connecting") && (
            <div className={`text-[11px] ${kicked || status.connection.state === "error" ? "text-state-error" : "text-ink-muted"}`}>
              {status.connection.detail}
            </div>
          )}
          {kicked && (
            <div className="rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-[11px] leading-relaxed text-ink">
              本机不会自动重连，以免和另一处互相挤下线。确认另一处已停止使用这组凭证后，再点「重新连接」。
              <button type="button" disabled={busy} onClick={() => void reconnect()} className={`${secondaryButton} ml-2`}>
                重新连接
              </button>
            </div>
          )}
          {status.last_error && <div className="text-[11px] text-ink-muted">最近错误：{status.last_error}</div>}
          {!status.bound && (
            <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11px] leading-relaxed text-ink">
              {pairing ? (
                <>
                  在企业微信里找到这个机器人（通常叫「你的名字的机器人」），给它发送配对码
                  <span className="mx-1 font-mono text-sm font-semibold text-ink-strong">{pairing.code}</span>
                  （{new Date(pairing.expires_at * 1000).toLocaleTimeString()} 前有效）。第一个发来正确配对码的人会成为接收提问的账号。
                </>
              ) : (
                <>还需要认领一次：机器人只能给先和它说过话的人推送消息。</>
              )}
              <button type="button" disabled={busy || status.connection.state !== "connected"} onClick={() => void pair()} className={`${secondaryButton} ml-2`}>
                {pairing ? "重新生成" : "生成配对码"}
              </button>
            </div>
          )}
          <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11px] leading-relaxed text-ink-muted">
            企业微信卡片没有输入框，所以每道题单独一张卡片：选了「其他（文字回答）」的题目，请长按该题的卡片，选择「引用」后回复文字；没有选项的题目，引用它的提示消息回复。
            没有引用提问的普通消息不会被记录为答案。
          </div>
          <div className="text-[11px] leading-relaxed text-ink-muted">
            这组机器人凭证等同于该机器人的全部权限（包括企业微信办公接口），只保存在本机的 0600 文件里，不会同步，也不会经接口返回。
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="flex items-center gap-2 self-end rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink">
              <input type="checkbox" checked={status.enabled} disabled={busy} onChange={(event) => void toggleEnabled(event.target.checked)} className="accent-brand" />
              为本机提问推送卡片
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>未回答多久后推送（秒）</span>
              <input type="number" min={0} value={delaySeconds} onChange={(event) => setDelaySeconds(Math.max(0, Number(event.target.value) || 0))} className={inputClass} />
            </label>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" disabled={busy} onClick={unbind} className={`${secondaryButton} hover:text-state-error`}>解绑</button>
            <button type="button" disabled={busy || !status.bound || status.connection.state !== "connected"} onClick={() => void test()} className={secondaryButton}>发送测试卡</button>
            <button type="button" disabled={busy} onClick={() => void saveTiming()} className={primaryButton}>保存</button>
          </div>
        </div>
      )}
    </div>
  );
}

function WecomRegistrationPanel({
  registration,
  busy,
  onCancel,
  onDismiss,
  secondaryButton,
}: {
  registration: WecomRegistration;
  busy: boolean;
  onCancel: () => void;
  onDismiss: () => void;
  secondaryButton: string;
}) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (registration.state !== "polling") return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1_000);
    return () => window.clearInterval(timer);
  }, [registration.state]);

  /* Rendered here, never by a QR web service: whoever scans the code can
   * claim the bot it creates. */
  const svg = useMemo(() => {
    if (!registration.url) return null;
    const code = qrcode(0, "M");
    code.addData(registration.url);
    code.make();
    return code.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  }, [registration.url]);

  const remaining = registration.expires_at ? Math.max(0, Math.round(registration.expires_at - now)) : null;

  if (registration.state === "failed" || registration.state === "cancelled") {
    return (
      <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11px] text-ink">
        <span>{registration.state === "failed" ? `扫码未完成：${registration.error ?? "未知原因"}` : "已取消扫码"}</span>
        <button type="button" onClick={onDismiss} className={secondaryButton}>关闭</button>
      </div>
    );
  }

  return (
    <div className="mt-3 flex flex-wrap items-start gap-4 rounded-md border border-brand/30 bg-brand-soft px-3 py-3">
      {svg ? (
        <div className="h-[168px] w-[168px] shrink-0 rounded bg-white p-1" dangerouslySetInnerHTML={{ __html: svg }} />
      ) : (
        <div className="flex h-[168px] w-[168px] shrink-0 items-center justify-center rounded bg-surface-raised text-[11px] text-ink-muted">正在获取二维码…</div>
      )}
      <div className="min-w-[200px] flex-1 space-y-2 text-[11px] leading-relaxed text-ink">
        <div className="font-medium text-ink-strong">用企业微信扫码，创建本机专用的智能机器人</div>
        <div>扫码后在企业微信里一路确认即可。完成后，这里会给出一个配对码，发给新机器人完成认领。</div>
        {registration.page_url && (
          <div>
            在这台电脑上操作：
            <a className="ml-1 break-all underline" href={registration.page_url} target="_blank" rel="noreferrer">打开扫码页</a>
          </div>
        )}
        {registration.detail && <div className="text-ink-muted">{registration.detail}</div>}
        {remaining !== null && <div className="font-mono text-[10px] text-ink-muted">二维码剩余 {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, "0")}</div>}
        <button type="button" disabled={busy} onClick={onCancel} className={secondaryButton}>取消</button>
      </div>
    </div>
  );
}
