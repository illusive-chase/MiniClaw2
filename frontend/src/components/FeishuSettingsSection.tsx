import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import qrcode from "qrcode-generator";
import {
  ApiError,
  FEISHU_CARDS_UNRETIRED,
  beginFeishuRegistration,
  cancelFeishuRegistration,
  getFeishuRegistration,
  getFeishuStatus,
  sendFeishuTest,
  startFeishuPairing,
  unbindFeishu,
  updateFeishuSettings,
} from "../api";
import type { FeishuRegistration, FeishuStatus } from "../types";

type Props = {
  open: boolean;
  inputClass: string;
  labelClass: string;
  primaryButton: string;
  secondaryButton: string;
};

/* Feishu text_tag colours, approximated so the settings page shows the same
 * host colour the cards do. */
const TAG_COLORS: Record<string, string> = {
  indigo: "#4954e6",
  turquoise: "#14b8a6",
  orange: "#f97316",
  violet: "#8b5cf6",
  carmine: "#d83931",
  lime: "#65a30d",
  purple: "#a855f7",
  blue: "#3b82f6",
};

const CONNECTION_LABEL: Record<FeishuStatus["connection"]["state"], string> = {
  off: "未连接",
  connecting: "连接中",
  connected: "已连接",
  error: "连接失败",
};

const BINDING_LABEL: Record<NonNullable<FeishuStatus["binding_source"]>, string> = {
  created: "扫码创建",
  joined: "扫码加入",
  manual: "手动填写",
};

export function FeishuSettingsSection({
  open,
  inputClass,
  labelClass,
  primaryButton,
  secondaryButton,
}: Props) {
  const [status, setStatus] = useState<FeishuStatus | null>(null);
  const [registration, setRegistration] = useState<FeishuRegistration | null>(null);
  const [joinAppId, setJoinAppId] = useState("");
  const [showJoin, setShowJoin] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const [manualAppId, setManualAppId] = useState("");
  const [manualSecret, setManualSecret] = useState("");
  const [manualBrand, setManualBrand] = useState<"feishu" | "lark">("feishu");
  const [delaySeconds, setDelaySeconds] = useState(60);
  const [pollSeconds, setPollSeconds] = useState(30);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const settingsSeeded = useRef(false);

  const load = useCallback(async (refresh: boolean) => {
    try {
      const next = await getFeishuStatus(refresh);
      setStatus(next);
      setRegistration(next.registration);
      if (!settingsSeeded.current) {
        settingsSeeded.current = true;
        setDelaySeconds(next.delay_seconds);
        setPollSeconds(next.poll_seconds);
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
    void load(true);
    /* §7.5: the online connection count is how a user notices a foreign
     * program on the same app; refresh it while the page is open. */
    const timer = window.setInterval(() => void load(true), 60_000);
    return () => window.clearInterval(timer);
  }, [open, load]);

  const polling = registration?.state === "polling" || registration?.state === "starting";
  useEffect(() => {
    if (!open || !polling) return;
    const timer = window.setInterval(() => {
      getFeishuRegistration()
        .then((next) => {
          setRegistration(next);
          if (next && next.state !== "polling" && next.state !== "starting") void load(true);
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

  const beginScan = (mode: "create" | "join") =>
    run(async () => {
      setRegistration(await beginFeishuRegistration(mode, mode === "join" ? joinAppId.trim() : undefined));
    });

  const cancelScan = () =>
    run(async () => {
      setRegistration(await cancelFeishuRegistration());
    });

  const saveManual = () =>
    run(async () => {
      const next = await updateFeishuSettings({
        app_id: manualAppId.trim(),
        app_secret: manualSecret,
        brand: manualBrand,
      });
      setStatus(next);
      setManualSecret("");
    });

  const pair = () =>
    run(async () => {
      await startFeishuPairing();
      await load(false);
    });

  const saveTiming = () =>
    run(async () => {
      setStatus(await updateFeishuSettings({ delay_seconds: delaySeconds, poll_seconds: pollSeconds }));
      setNotice("已保存");
    });

  const toggleEnabled = (enabled: boolean) =>
    run(async () => {
      setStatus(await updateFeishuSettings({ enabled }));
    });

  const test = () =>
    run(async () => {
      await sendFeishuTest();
      setNotice("测试卡已发送，请在飞书中查看并点击。");
    });

  const unbind = () => {
    if (!window.confirm("解绑后本机会断开飞书连接、删除凭证，已推送的卡片会被标记为失效。继续？")) return;
    void run(async () => {
      try {
        setStatus(await unbindFeishu());
      } catch (err) {
        if (!(err instanceof ApiError) || err.state !== FEISHU_CARDS_UNRETIRED) throw err;
        if (!window.confirm(`${err.detail ?? err.message}\n\n仍要解绑？`)) {
          await load(false);
          return;
        }
        setStatus(await unbindFeishu(true));
      }
      setRegistration(null);
    });
  };

  const copyAppId = async () => {
    if (!status?.app_id) return;
    try {
      await navigator.clipboard.writeText(status.app_id);
      setNotice("App ID 已复制");
    } catch {
      setNotice(status.app_id);
    }
  };

  const hostColor = status ? TAG_COLORS[status.host.color] ?? "#64748b" : "#64748b";
  const connections = status?.online_connections?.count ?? null;
  const unexpectedScopes = status?.scopes?.unexpected ?? [];
  const missingScopes = status?.scopes?.missing ?? [];
  const pairing = status?.pairing ?? null;
  const needsPairing = status?.configured && !status.bound && status.binding_source === "manual";

  return (
    <section className="rounded-lg border border-line bg-surface-raised p-4 shadow-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="font-display text-sm font-semibold text-ink-strong">飞书远程回答</div>
          <div className="mt-1 text-[11px] leading-relaxed text-ink-muted">
            agent 提问后在网页端等待一段时间仍未回答时，推送到飞书，可在手机上作答。
            问题、选项、项目名和节点名会经过飞书服务器；敏感输入不会发送。
          </div>
        </div>
        {status && (
          <span
            className={`shrink-0 rounded border px-2 py-1 text-[10px] font-medium ${
              status.connection.state === "connected"
                ? "border-state-done/40 bg-state-done-soft text-state-done"
                : status.connection.state === "error"
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
          后端未安装飞书 SDK。在后端环境中执行
          <code className="mx-1 rounded bg-surface-sunken px-1 font-mono">pip install &apos;miniclaw2[feishu]&apos;</code>
          （即 <span className="font-mono">{status.sdk}</span>）后重启后端。
        </div>
      )}
      {status && !status.runtime_owner && (
        <div className="mt-3 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] leading-relaxed text-ink">
          这个后端不是本机存储的运行时属主（同一存储上已有另一个后端在运行），不会连接飞书。
        </div>
      )}

      {status && (
        <div className="mt-3 flex items-center gap-2 text-[11px] text-ink-muted">
          <span>本机</span>
          <span className="rounded px-1.5 py-0.5 font-medium text-white" style={{ backgroundColor: hostColor }}>
            {status.host.label}
          </span>
          <span className="font-mono text-[10px] text-ink-subtle">卡片头部用这个标签区分主机</span>
        </div>
      )}

      {registration && registration.state !== "succeeded" && (
        <RegistrationPanel
          registration={registration}
          busy={busy}
          onCancel={() => void cancelScan()}
          onDismiss={() => setRegistration(null)}
          secondaryButton={secondaryButton}
        />
      )}
      {registration?.state === "succeeded" && (
        <div className="mt-3 rounded-md border border-state-done/30 bg-state-done-soft px-3 py-2 text-[11px] text-state-done">
          扫码完成，已绑定。飞书里应已收到一张「已连接」欢迎卡。
        </div>
      )}

      {status && !status.configured && !polling && status.installed && status.runtime_owner && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy} onClick={() => void beginScan("create")} className={primaryButton}>
              扫码创建（推荐）
            </button>
            <button type="button" disabled={busy} onClick={() => setShowJoin((value) => !value)} className={secondaryButton}>
              加入已有应用
            </button>
          </div>
          <div className="text-[11px] leading-relaxed text-ink-muted">
            第一台主机选「扫码创建」，会自动新建一个只开最小权限的专用应用。其他主机选「加入已有应用」，
            粘贴第一台主机设置页上的 App ID，再用同一个飞书账号扫码。多台主机共用一个应用时，卡片点击会经其他在线主机中转，最多晚约 {status.poll_seconds} 秒送达。
          </div>
          {showJoin && (
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex min-w-[240px] flex-1 flex-col gap-1">
                <span className={labelClass}>App ID</span>
                <input value={joinAppId} onChange={(event) => setJoinAppId(event.target.value)} className={inputClass} placeholder="cli_…" />
              </label>
              <button type="button" disabled={busy || !joinAppId.trim().startsWith("cli_")} onClick={() => void beginScan("join")} className={primaryButton}>
                扫码加入
              </button>
            </div>
          )}
          <details open={showManual} onToggle={(event) => setShowManual((event.target as HTMLDetailsElement).open)}>
            <summary className="cursor-pointer text-[11px] text-ink-muted">手动填写（企业不允许扫码创建，或复用已有应用）</summary>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
              <label className="flex flex-col gap-1">
                <span className={labelClass}>App ID</span>
                <input value={manualAppId} onChange={(event) => setManualAppId(event.target.value)} className={inputClass} placeholder="cli_…" />
              </label>
              <label className="flex flex-col gap-1">
                <span className={labelClass}>App Secret</span>
                <input type="password" autoComplete="off" value={manualSecret} onChange={(event) => setManualSecret(event.target.value)} className={inputClass} />
              </label>
              <label className="flex flex-col gap-1">
                <span className={labelClass}>版本</span>
                <select value={manualBrand} onChange={(event) => setManualBrand(event.target.value as "feishu" | "lark")} className={inputClass}>
                  <option value="feishu">飞书</option>
                  <option value="lark">Lark</option>
                </select>
              </label>
            </div>
            <div className="mt-2 text-[11px] leading-relaxed text-ink-muted">
              手动模式需要在开放平台为应用开启机器人能力和权限 im:message:send_as_bot、im:message:readonly、im:message.p2p_msg:readonly，
              订阅方式选「长连接」，添加事件 im.message.receive_v1 和回调 card.action.trigger，发布版本后再回到这里配对。
            </div>
            <div className="mt-2 flex justify-end">
              <button type="button" disabled={busy || !manualAppId.trim().startsWith("cli_") || !manualSecret} onClick={() => void saveManual()} className={primaryButton}>
                保存并连接
              </button>
            </div>
          </details>
        </div>
      )}

      {status?.configured && (
        <div className="mt-3 space-y-3">
          <div className="grid grid-cols-1 gap-x-4 gap-y-1 font-mono text-[10px] text-ink-muted sm:grid-cols-2">
            <div className="flex items-center gap-2">
              App ID：{status.app_id}
              <button type="button" onClick={() => void copyAppId()} className="rounded border border-line px-1 text-[9px] hover:text-ink">复制</button>
            </div>
            <div>绑定方式：{status.binding_source ? BINDING_LABEL[status.binding_source] : "—"}{status.brand === "lark" ? " · Lark" : ""}</div>
            <div>绑定账号：{status.bound ? `${status.bound.open_id.slice(0, 10)}…` : "未绑定"}</div>
            <div>
              在线连接：{connections ?? "未知"}
              {status.online_connections ? ` · ${new Date(status.online_connections.checked_at * 1000).toLocaleTimeString()}` : ""}
            </div>
            <div>待答卡片：{status.relay.pending}{status.relay.last_poll_at ? ` · 上次读取信箱 ${new Date(status.relay.last_poll_at * 1000).toLocaleTimeString()}` : ""}</div>
            <div>本月本机调用：约 {status.usage_this_month.calls} 次</div>
          </div>
          {status.connection.detail && status.connection.state === "error" && (
            <div className="text-[11px] text-state-error">{status.connection.detail}</div>
          )}
          {connections !== null && connections > 1 && (
            <div className="rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] leading-relaxed text-ink">
              当前有 {connections} 个连接在线。如果你只开了这一台主机，可能有其他程序也在使用这个应用，部分点击会失败。请确认这个应用是 MiniClaw2 专用的。
            </div>
          )}
          {(unexpectedScopes.length > 0 || missingScopes.length > 0) && (
            <div className="rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] leading-relaxed text-ink">
              {missingScopes.length > 0 && <div>缺少权限：{missingScopes.join("、")}</div>}
              {unexpectedScopes.length > 0 && (
                <div>
                  应用多出 {unexpectedScopes.length} 项非必需权限：{unexpectedScopes.slice(0, 8).join("、")}
                  {unexpectedScopes.length > 8 ? " 等" : ""}。可在
                  <a className="mx-1 underline" href={`https://open.${status.brand === "lark" ? "larksuite.com" : "feishu.cn"}/app/${status.app_id}/auth`} target="_blank" rel="noreferrer">开放平台权限页</a>
                  手动收回。
                </div>
              )}
            </div>
          )}
          {status.quota_exhausted && (
            <div className="rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-[11px] text-state-error">
              本月飞书 API 额度已用完（99991403），已停止推送和读取信箱。网页端照常可以作答。
            </div>
          )}
          {status.last_error && !status.quota_exhausted && (
            <div className="text-[11px] text-ink-muted">最近错误：{status.last_error}</div>
          )}
          {needsPairing && (
            <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11px] leading-relaxed text-ink">
              {pairing ? (
                <>在飞书里私聊这个机器人，发送配对码 <span className="font-mono text-sm font-semibold text-ink-strong">{pairing.code}</span>（{new Date(pairing.expires_at * 1000).toLocaleTimeString()} 前有效）。</>
              ) : (
                <>手动模式需要配对一次飞书账号。</>
              )}
              <button type="button" disabled={busy || status.connection.state !== "connected"} onClick={() => void pair()} className={`${secondaryButton} ml-2`}>
                {pairing ? "重新生成" : "生成配对码"}
              </button>
            </div>
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="flex items-center gap-2 self-end rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink">
              <input type="checkbox" checked={status.enabled} disabled={busy} onChange={(event) => void toggleEnabled(event.target.checked)} className="accent-brand" />
              为本机提问推送卡片
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>未回答多久后推送（秒）</span>
              <input type="number" min={0} value={delaySeconds} onChange={(event) => setDelaySeconds(Math.max(0, Number(event.target.value) || 0))} className={inputClass} />
            </label>
            <label className="flex flex-col gap-1">
              <span className={labelClass}>信箱轮询间隔（秒）</span>
              <input type="number" min={5} value={pollSeconds} onChange={(event) => setPollSeconds(Math.max(5, Number(event.target.value) || 5))} className={inputClass} />
            </label>
          </div>
          {!status.enabled && (
            <div className="text-[11px] text-ink-muted">关闭推送后本机仍保持连接，替其他主机转交落到这里的点击；完全停用请解绑。</div>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" disabled={busy} onClick={unbind} className={`${secondaryButton} hover:text-state-error`}>解绑</button>
            <button type="button" disabled={busy || !status.bound || status.connection.state !== "connected"} onClick={() => void test()} className={secondaryButton}>发送测试卡</button>
            <button type="button" disabled={busy} onClick={() => void saveTiming()} className={primaryButton}>保存</button>
          </div>
        </div>
      )}
    </section>
  );
}

function RegistrationPanel({
  registration,
  busy,
  onCancel,
  onDismiss,
  secondaryButton,
}: {
  registration: FeishuRegistration;
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

  /* Rendered here, never by a QR web service: the URL carries the device
   * code, and whoever holds it can approve the registration. */
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
        <div className="font-medium text-ink-strong">
          {registration.mode === "create" ? "用飞书扫码，创建 MiniClaw2 专用应用" : "用飞书扫码，把本机加入已有应用"}
        </div>
        <div>扫码后在飞书页面上确认，页面可能会让你选择机器人头像。扫码人就是之后接收卡片的账号。</div>
        {registration.url && (
          <div>
            在这台电脑上操作：
            <a className="ml-1 break-all underline" href={registration.url} target="_blank" rel="noreferrer">打开链接</a>
          </div>
        )}
        {registration.detail && <div className="text-ink-muted">{registration.detail}</div>}
        {remaining !== null && <div className="font-mono text-[10px] text-ink-muted">二维码剩余 {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, "0")}</div>}
        <button type="button" disabled={busy} onClick={onCancel} className={secondaryButton}>取消</button>
      </div>
    </div>
  );
}
