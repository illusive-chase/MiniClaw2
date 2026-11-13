import { useCallback, useEffect, useState } from "react";
import { ArrowUpCircle, Check, CircleAlert, GitBranch, RefreshCw, X } from "lucide-react";

import {
  ApiError,
  applySelfUpdate,
  checkSelfUpdate,
  getHealth,
  listHosts,
  refreshSyncOverview,
  syncNow,
} from "../api";
import { ageSince, formatAge, projectDebtText, sourceBlockReason } from "../syncStatus";
import type { GlobalState, HostEntry, HostsResponse, SyncOverview } from "../types";
import { HostGlyph } from "./HostGlyph";
import { rememberPendingUpdate, type PendingUpdate } from "../selfUpdate";
import { HostTimeline } from "./HostTimeline";

type Props = {
  open: boolean;
  overview: SyncOverview | null;
  hostsVersion: number;
  onClose: () => void;
  onOverview: (next: SyncOverview) => void;
  onGlobalState: (next: GlobalState) => void;
  onOpenProject: (projectId: string) => void;
  onUpdateStarted: (update: PendingUpdate) => void;
};

function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.detail ?? err.message;
  return err instanceof Error ? err.message : String(err);
}

const DOT: Record<string, string> = {
  overdue: "bg-state-error",
  failed: "bg-state-error",
  pending: "bg-ink-subtle",
  clean: "bg-state-done",
  unconfigured: "bg-line-strong",
};

/**
 * One place for everything that syncs: the metadata store, bound project
 * checkouts, the MiniClaw2 source, and what other hosts last reported.
 * Opening it reads local refs only.
 */
export function SyncCenter({
  open, overview, hostsVersion, onClose, onOverview, onGlobalState, onOpenProject, onUpdateStarted,
}: Props) {
  const [hosts, setHosts] = useState<HostsResponse | null>(null);
  const [busy, setBusy] = useState<"sync" | "check" | "update" | "scan" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadHosts = useCallback(() => {
    listHosts().then(setHosts).catch(() => {});
  }, []);

  useEffect(() => {
    if (open) loadHosts();
  }, [open, hostsVersion, loadHosts]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  const now = Date.now() / 1000;

  const run = async (kind: NonNullable<typeof busy>, action: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const syncMetadata = () => run("sync", async () => {
    onGlobalState(await syncNow());
    onOverview(await refreshSyncOverview());
    loadHosts();
  });
  const checkSource = () => run("check", async () => {
    await checkSelfUpdate();
    onOverview(await refreshSyncOverview());
  });
  const scanProjects = () => run("scan", async () => {
    onOverview(await refreshSyncOverview());
  });
  const updateSource = () => run("update", async () => {
    const before = await getHealth();
    const result = await applySelfUpdate();
    if (result.mode === "daemon") {
      const pending = {
        target: result.new_head, oldHead: result.old_head, bootId: before.boot_id, startedAt: Date.now(),
      };
      rememberPendingUpdate(pending);
      onUpdateStarted(pending);
      return;
    }
    setNotice(`${result.message}${result.restart_commands.length ? `\n${result.restart_commands.join("\n")}` : ""}`);
  });

  const meta = overview?.metadata;
  const source = overview?.source;
  const self = hosts?.hosts.find((entry) => entry.is_self) ?? null;
  const peers = hosts?.hosts.filter((entry) => !entry.is_self) ?? [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-surface-scrim/40" onClick={onClose}>
      <aside
        className="flex h-full w-[560px] max-w-[96vw] flex-col border-l border-line bg-surface-raised shadow-modal"
        onClick={(event) => event.stopPropagation()}
        aria-label="同步中心"
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div className="flex items-center gap-2">
            {self && <HostGlyph icon={self.icon} color={self.color} size={16} />}
            <div>
              <div className="font-display text-sm font-semibold text-ink-strong">同步中心</div>
              <div className="text-[11px] text-ink-subtle">
                {self ? `本机：${self.label}` : "本机"} · 打开此面板不会访问远端
              </div>
            </div>
          </div>
          <button type="button" onClick={onClose} className="rounded p-1 text-ink-muted hover:bg-surface-sunken" aria-label="关闭">
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto bg-surface-sunken p-4">
          {error && (
            <div className="whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-[11px] text-state-error">{error}</div>
          )}
          {notice && (
            <div className="whitespace-pre-wrap rounded-md border border-state-done/30 bg-state-done-soft px-3 py-2 font-mono text-[11px] text-state-done">{notice}</div>
          )}

          <Section
            title="元数据"
            dot={meta ? DOT[meta.state] : undefined}
            action={(
              <button type="button" disabled={busy !== null || meta?.state === "unconfigured"} onClick={() => void syncMetadata()} className={primaryButton}>
                {busy === "sync" ? "正在同步…" : "立即同步"}
              </button>
            )}
          >
            {meta ? (
              <div className="space-y-1 text-[11px] text-ink-muted">
                {meta.state === "unconfigured" ? (
                  <div>尚未配置元数据同步，在 Global settings 中设置远端。</div>
                ) : (
                  <>
                    <div className={meta.state === "overdue" || meta.state === "failed" ? "font-medium text-state-error" : "text-ink"}>
                      {meta.state === "failed"
                        ? "上次同步失败"
                        : meta.unpushed_commits || meta.dirty
                          ? `↑${meta.unpushed_commits} 个提交未推送${meta.dirty ? "（另有未提交改动）" : ""} · 最早 ${ageSince(meta.since, now) ?? "刚刚"}前`
                          : "没有未推送的内容"}
                    </div>
                    <div>
                      上次同步 {meta.last_sync_at ? `${ageSince(meta.last_sync_at, now)}前` : "从未"}
                      {" · "}超过 {formatAge(meta.overdue_after_seconds)} 未推送会亮红点
                    </div>
                    <div>
                      远端：{meta.remote_ahead ? `领先 ${meta.remote_ahead} 个提交` : "无已知新内容"}
                      {" · "}{probeText(meta.remote_probe, now)}
                    </div>
                  </>
                )}
              </div>
            ) : <Loading />}
          </Section>

          <Section
            title="项目仓库"
            dot={overview ? (overview.projects_overdue ? DOT.overdue : overview.projects_pending ? DOT.pending : DOT.clean) : undefined}
            action={(
              <button type="button" disabled={busy !== null} onClick={() => void scanProjects()} className={secondaryButton} title="重新读取本机已绑定项目的 Git 状态（不联网）">
                <RefreshCw size={12} className={busy === "scan" ? "animate-spin" : ""} /> 重新扫描
              </button>
            )}
          >
            {overview ? (
              overview.projects.length === 0 ? (
                <div className="text-[11px] text-ink-muted">本机没有已绑定的 Git 项目。</div>
              ) : (
                <ul className="divide-y divide-line">
                  {overview.projects
                    .slice()
                    .sort((a, b) => rank(a.state) - rank(b.state) || a.name.localeCompare(b.name))
                    .map((entry) => (
                      <li key={entry.id}>
                        <button
                          type="button"
                          onClick={() => onOpenProject(entry.id)}
                          className="flex w-full items-center gap-2 py-1.5 text-left text-[11px] hover:bg-surface-sunken"
                        >
                          <span className={`h-1.5 w-1.5 flex-none rounded-full ${DOT[entry.state]}`} />
                          <span className="min-w-0 flex-1 truncate font-medium text-ink">{entry.name}</span>
                          <span className={`truncate ${entry.state === "overdue" ? "text-state-error" : "text-ink-muted"}`}>
                            {projectDebtText(entry, now)}
                          </span>
                        </button>
                      </li>
                    ))}
                </ul>
              )
            ) : <Loading />}
          </Section>

          <Section
            title="MiniClaw2 源码"
            dot={source ? (source.state === "available" ? "bg-brand" : source.state === "error" || source.state === "blocked" ? "bg-state-waiting" : "bg-state-done") : undefined}
            action={(
              <div className="flex gap-1.5">
                <button type="button" disabled={busy !== null || source?.state === "disabled"} onClick={() => void checkSource()} className={secondaryButton}>
                  {busy === "check" ? "检查中…" : "立即检查"}
                </button>
                <button
                  type="button"
                  disabled={busy !== null || source?.state !== "available"}
                  onClick={() => void updateSource()}
                  className={primaryButton}
                  title={source?.daemon.active ? "daemon 会停止后端、更新、重建并重启，页面自动刷新" : "当前不是 daemon 模式：更新后进程会退出，需要手动重启"}
                >
                  <ArrowUpCircle size={12} /> {busy === "update" ? "正在提交…" : source?.daemon.active ? "更新" : "更新并退出"}
                </button>
              </div>
            )}
          >
            {source ? (
              <div className="space-y-1.5 text-[11px] text-ink-muted">
                <div className="text-ink">
                  {source.state === "available" && `有 ${source.behind} 个新提交可更新`}
                  {source.state === "blocked" && `有 ${source.behind} 个新提交，但无法自动更新：${sourceBlockReason(overview!)}`}
                  {source.state === "current" && "已是本机所知的最新版本"}
                  {source.state === "error" && `探测失败：${source.probe.error ?? source.error ?? "未知原因"}`}
                  {source.state === "disabled" && (source.error ?? "自动探测已关闭或不是 Git 工作区")}
                </div>
                <div className="font-mono text-[10px]">
                  {source.head?.slice(0, 8) ?? "?"} · {source.upstream ?? "未配置 upstream"} · {probeText(source.probe, now)}
                </div>
                {!source.daemon.active && (
                  <div>当前不是 daemon 模式；运行 <code className="font-mono">miniclaw2 daemon start</code> 后可一键更新并自动重启。</div>
                )}
                {source.commits.length > 0 && (
                  <ul className="mt-1 max-h-32 space-y-0.5 overflow-y-auto rounded border border-line bg-surface px-2 py-1">
                    {source.commits.slice().reverse().map((commit) => (
                      <li key={commit.sha} className="truncate"><span className="font-mono text-ink-subtle">{commit.sha.slice(0, 7)}</span> {commit.title}</li>
                    ))}
                  </ul>
                )}
              </div>
            ) : <Loading />}
          </Section>

          <Section title="各 Host 同步情况" hint="对方在自己同步时发布的快照；只说明截至那一刻">
            {hosts ? (
              peers.length === 0 && !self ? (
                <div className="text-[11px] text-ink-muted">还没有其他设备的记录。</div>
              ) : (
                <div className="space-y-2">
                  {[self, ...peers].filter((entry): entry is HostEntry => entry !== null).map((entry) => (
                    <HostRow key={entry.machine_id} entry={entry} now={now} />
                  ))}
                  <HostTimeline hosts={hosts.hosts} now={now} />
                </div>
              )
            ) : <Loading />}
          </Section>
        </div>
      </aside>
    </div>
  );
}

function rank(state: string): number {
  return state === "overdue" ? 0 : state === "pending" ? 1 : 2;
}

function probeText(probe: { enabled: boolean; last_checked_at?: number | null; ok?: boolean | null; error?: string | null }, now: number): string {
  if (!probe.enabled) return "自动探测已关闭";
  if (!probe.last_checked_at) return "尚未探测";
  const age = ageSince(probe.last_checked_at, now);
  return probe.ok === false ? `${age}前探测失败` : `${age}前探测 ✓`;
}

export function hostStatusText(entry: HostEntry, now: number): { text: string; tone: "ok" | "warn" | "muted" } {
  if (entry.is_self) return { text: "本机", tone: "muted" };
  if (!entry.beacon) {
    const pushed = entry.last_push_at ? `最近推送 ${ageSince(entry.last_push_at, now)}前（推断）` : "没有推送记录";
    return { text: `无信标（旧版本或未用新版同步） · ${pushed}`, tone: "muted" };
  }
  const when = entry.beacon.synced_at ? `同步于 ${ageSince(entry.beacon.synced_at, now)}前` : "同步时间未知";
  if (entry.status === "current") {
    return { text: `✓ 最新${entry.includes_my_push ? " · 已含本机最近推送" : ""} · ${when}`, tone: "ok" };
  }
  if (entry.status === "behind") {
    const parts = entry.missing_by.map((item) => `${item.is_self ? "本机" : item.label} ${item.count}`).join(" · ");
    const mine = entry.includes_my_push === false ? " · 尚未拉取本机推送" : "";
    return { text: `落后 ${entry.behind}${parts ? `（${parts}）` : ""}${mine} · ${when}`, tone: "warn" };
  }
  return { text: `${when} · 无法与本机所知的远端比较`, tone: "muted" };
}

function HostRow({ entry, now }: { entry: HostEntry; now: number }) {
  const status = hostStatusText(entry, now);
  const source = entry.source;
  return (
    <div className={`rounded-md border border-line bg-surface px-3 py-2 ${entry.stale ? "opacity-60" : ""}`}>
      <div className="flex items-center gap-2">
        <HostGlyph icon={entry.icon} color={entry.color} size={15} />
        <span className="truncate text-[12px] font-medium text-ink-strong">{entry.label}</span>
        {entry.is_self && <span className="rounded bg-brand-soft px-1.5 py-0.5 text-[9px] text-brand-ink">本机</span>}
        {!entry.has_profile && !entry.is_self && <span className="text-[10px] text-ink-subtle" title="该设备尚未发布 host 资料，名称来自旧记录">未命名</span>}
        <span className="flex-1" />
        {status.tone === "ok" ? <Check size={13} className="text-state-done" /> : status.tone === "warn" ? <CircleAlert size={13} className="text-state-waiting" /> : null}
      </div>
      <div className={`mt-1 text-[11px] ${status.tone === "warn" ? "text-state-waiting" : "text-ink-muted"}`}>{status.text}</div>
      {entry.beacon && (
        <div className="mt-0.5 flex flex-wrap gap-x-2 font-mono text-[10px] text-ink-subtle">
          <span><GitBranch size={10} className="mr-0.5 inline" />{entry.beacon.integrated?.slice(0, 8) ?? "?"}</span>
          {source?.head && (
            <span title="对方自报的 MiniClaw2 版本">
              MiniClaw2 {source.head.slice(0, 7)}
              {typeof source.behind_upstream === "number" && source.behind_upstream > 0 ? ` · 落后 ${source.behind_upstream}` : ""}
            </span>
          )}
          {entry.beacon.store_schema !== null && <span>store v{entry.beacon.store_schema}</span>}
          {entry.beacon.clock_skew && <span className="text-state-waiting">对方时钟可能不准</span>}
        </div>
      )}
    </div>
  );
}

function Section({
  title, hint, dot, action, children,
}: { title: string; hint?: string; dot?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-surface-raised p-3.5 shadow-card">
      <div className="mb-2 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5 font-display text-[13px] font-semibold text-ink-strong">
            {dot && <span className={`h-2 w-2 rounded-full ${dot}`} />}
            {title}
          </div>
          {hint && <div className="mt-0.5 text-[10.5px] text-ink-subtle">{hint}</div>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Loading() {
  return <div className="text-[11px] text-ink-subtle">读取中…</div>;
}

const primaryButton = "inline-flex items-center gap-1 rounded-md bg-brand px-2.5 py-1 text-[11px] font-medium text-white shadow-card transition hover:brightness-95 disabled:opacity-40";
const secondaryButton = "inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-muted transition hover:text-ink disabled:opacity-40";
