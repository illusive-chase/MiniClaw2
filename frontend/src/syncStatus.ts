/* Presentation rules for the sync overview, kept pure so they are testable.
 *
 * The one rule that matters: a red dot means `overdue` and nothing else.
 * `pending` debt is shown as a quiet grey count — visible, but not asking for
 * attention — because the metadata store almost always holds unpushed
 * commits (durable writes commit locally every ~30 seconds).
 */
import type { ProjectSyncEntry, SyncOverview } from "./types";

export function formatAge(seconds: number): string {
  if (seconds < 90) return "刚刚";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.round(seconds / 3600);
  if (hours < 48) return `${hours} 小时`;
  return `${Math.round(seconds / 86400)} 天`;
}

export function ageSince(at: number | null | undefined, now = Date.now() / 1000): string | null {
  return typeof at === "number" ? formatAge(Math.max(0, now - at)) : null;
}

export type SyncChip = {
  key: "metadata" | "projects" | "source";
  /** Short text on the chip. */
  label: string;
  tone: "quiet" | "alert" | "brand" | "muted";
  title: string;
};

/** What the landing header shows; an empty list means "nothing to say". */
export function syncChips(overview: SyncOverview | null, now = Date.now() / 1000): SyncChip[] {
  if (!overview) return [];
  const chips: SyncChip[] = [];
  const meta = overview.metadata;
  const metaAge = ageSince(meta.since, now);
  if (meta.state === "overdue") {
    chips.push({
      key: "metadata", tone: "alert",
      label: `元数据 ${metaAge ?? ""}未推送`.replace("  ", " "),
      title: `元数据有 ${meta.unpushed_commits} 个提交未推送，最早一条在 ${metaAge} 前`,
    });
  } else if (meta.state === "failed") {
    chips.push({ key: "metadata", tone: "alert", label: "元数据同步失败", title: "上次元数据同步失败，打开同步中心重试" });
  } else if (meta.state === "pending") {
    chips.push({
      key: "metadata", tone: "quiet", label: `↑${meta.unpushed_commits || "·"}`,
      title: `元数据有 ${meta.unpushed_commits} 个提交未推送（最早 ${metaAge ?? "刚刚"} 前）；超过 ${formatAge(meta.overdue_after_seconds)} 才会提醒`,
    });
  }
  if (overview.projects_overdue > 0) {
    chips.push({
      key: "projects", tone: "alert", label: `${overview.projects_overdue} 个项目待推送`,
      title: overview.projects.filter((entry) => entry.state === "overdue").map((entry) => `${entry.name}：${projectDebtText(entry, now)}`).join("\n"),
    });
  } else if (overview.projects_pending > 0) {
    chips.push({
      key: "projects", tone: "quiet", label: `项目 ${overview.projects_pending}`,
      title: `${overview.projects_pending} 个项目有未推送内容，尚未超时`,
    });
  }
  const source = overview.source;
  if (source.state === "available" || source.state === "blocked") {
    chips.push({
      key: "source", tone: source.state === "available" ? "brand" : "muted",
      label: `新版本 · ${source.behind}`,
      title: source.state === "available"
        ? `MiniClaw2 有 ${source.behind} 个新提交可更新`
        : `MiniClaw2 有 ${source.behind} 个新提交，但当前无法自动更新（${sourceBlockReason(overview)}）`,
    });
  }
  return chips;
}

export function sourceBlockReason(overview: SyncOverview): string {
  const source = overview.source;
  if (source.dirty) return "源码工作区有未提交改动";
  if (source.ahead > 0) return "源码有尚未推送的本地提交";
  return source.error ?? "本地与远端历史已分叉";
}

export function projectDebtText(entry: ProjectSyncEntry, now = Date.now() / 1000): string {
  const parts: string[] = [];
  if (entry.dirty_count) parts.push(`未提交 ${entry.dirty_count} 个文件`);
  if (entry.ahead && entry.upstream) parts.push(`未推送 ${entry.ahead} 个提交`);
  const age = ageSince(entry.since, now);
  if (age && parts.length) parts.push(`最早 ${age}前`);
  if (entry.no_upstream) parts.push("当前分支未配置 upstream");
  return parts.join(" · ") || "已同步";
}

/** Prefix for the tab title so a background tab shows there is something to do. */
export function titleMark(overview: SyncOverview | null): string {
  if (!overview) return "";
  const attention = overview.metadata.state === "overdue"
    || overview.metadata.state === "failed"
    || overview.projects_overdue > 0
    || overview.source.state === "available";
  return attention ? "● " : "";
}
