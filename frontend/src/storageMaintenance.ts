/* Maintenance guidance keyed on the backend's `MigrationError.state`.
   The page is reachable only while business data is unloaded, so the copy has
   to be self-sufficient: whichever command it names must be the one that
   actually clears that state, and it must say when the backend has to be
   stopped first. `migrations status` does not take the coordinator lock and
   runs alongside a live app; `apply` and `recover` do, and cannot. */

export type StorageGuidance = {
  title: string;
  summary: string;
  commands: string[];
  /** Whether the listed commands require stopping the backend first. */
  requiresShutdown: boolean;
  steps: string[];
};

const PRESERVE = "请保留原数据及 migration-backups，不要手工 git merge 存储。";
const STATUS_NOTE =
  "migrations status 与 plan 只读、不抢协调器锁，应用运行时即可执行；apply 与 recover 会抢锁，必须先停止后端。";

const GUIDANCE: Record<string, StorageGuidance> = {
  migration_required: {
    title: "需要确认一次有损迁移",
    summary:
      "本次升级链上有一个步骤无法完整保留全部数据，需你确认一次后才会执行。只读查看计划不会记录确认。",
    commands: [
      "python -m miniclaw2 migrations plan",
      "python -m miniclaw2 migrations apply --accept-data-loss",
    ],
    requiresShutdown: true,
    steps: [
      "先运行 migrations plan，核对 layout_impact 与 layout_recovery，以及本次确认将同时授予的契约。",
      "在可中断的时间窗口停止本机后端（apply 需要独占存储，应用运行时无法执行）。",
      "运行 migrations apply --accept-data-loss 完成确认与迁移。",
      "重启后端后重新检查；确认凭据仅保存在本机，不随同步传播。",
    ],
  },
  schema_conflict: {
    title: "同步快照与本机布局冲突",
    summary:
      "同一条记录在本机与远端被分别修改或删除，系统无法自动裁决，已停下以免静默丢弃坐标。",
    commands: ["python -m miniclaw2 migrations plan"],
    requiresShutdown: false,
    steps: [
      "查看错误信息中的项目与节点，确认应保留哪一侧。",
      "在保留侧重新落位或删除，使两侧意图一致后重试同步。",
      "不要手工 git merge 存储：两侧格式可能不同，文本合并会破坏结构。",
    ],
  },
  waiting_for_idle: {
    title: "存储正被另一进程占用",
    summary:
      "另一个进程（可能是另一个 MiniClaw2 后端或一次正在进行的同步）持有存储，等待其结束即可，无需执行任何命令。",
    commands: [],
    requiresShutdown: false,
    steps: [
      "等待正在进行的同步或迁移结束，然后重新检查。",
      "若确认没有同步在跑，请找出并停止另一个占用同一存储的后端进程。",
    ],
  },
  schema_too_old: {
    title: "数据版本过旧，超出本程序的迁移窗口",
    summary:
      "本程序只携带最近若干版本的迁移步骤，这份数据早于窗口起点，无法由它直接升级。apply 不解决这个问题。",
    commands: ["python -m miniclaw2 migrations status"],
    requiresShutdown: false,
    steps: [
      "先用覆盖该数据版本的中间版本程序升级一次，再用本版本继续。",
      "不要对这份数据运行 apply --accept-data-loss：缺少对应步骤，它不会让数据变得可用。",
    ],
  },
  schema_too_new: {
    title: "数据版本比程序更新",
    summary: "这份数据由更新版本的 MiniClaw2 写入，本程序不认识它的格式，降级读取会破坏数据。",
    commands: [],
    requiresShutdown: false,
    steps: [
      "先把程序更新到写入这份数据的版本或更新版本，再重新打开。",
      "不要尝试迁移或修复：本程序无法安全解释更高版本的格式。",
    ],
  },
  migration_failed: {
    title: "迁移执行失败，原数据已保留",
    summary:
      "迁移在事务中失败并已回滚，原始文件保留在 migration-backups 中，可导出核对后再决定下一步。",
    commands: [
      "python -m miniclaw2 migrations status",
      "python -m miniclaw2 migrations recover --transaction <事务ID> --output <导出目录>",
    ],
    requiresShutdown: true,
    steps: [
      "运行 migrations status 找到失败事务的 ID。",
      "停止本机后端（recover 需要独占存储）。",
      "用 recover --transaction … --output … 把原始快照导出到存储之外的目录核对。",
      "核对后再决定重试迁移还是回退程序版本；不要删除 migration-backups。",
    ],
  },
  internal_error: {
    title: "服务端处理请求时发生异常",
    summary: "这不是格式问题，后端日志里有完整堆栈。数据未被改写。",
    commands: [],
    requiresShutdown: false,
    steps: ["查看后端日志中的异常堆栈。", "重新检查；若持续失败，请携带日志反馈。"],
  },
};

const UNKNOWN: StorageGuidance = {
  title: "存储维护模式",
  summary: "业务数据尚未加载，这不是空项目列表。下面的只读命令可用于判断当前状态。",
  commands: ["python -m miniclaw2 migrations status", "python -m miniclaw2 migrations plan"],
  requiresShutdown: false,
  steps: [
    "先运行只读的 status 与 plan 查看存储版本与待执行步骤。",
    "两者都不会修改数据，也不会记录确认。",
  ],
};

/** Guidance for a backend `state`; falls back to read-only diagnosis when the
    state is absent (an older backend, or a non-`MigrationError` failure). */
export function storageGuidance(state: string | null): StorageGuidance {
  const guidance = state ? GUIDANCE[state] : undefined;
  return guidance ?? UNKNOWN;
}

export function guidanceNotes(guidance: StorageGuidance): string[] {
  return guidance.commands.length > 0 ? [PRESERVE, STATUS_NOTE] : [PRESERVE];
}

/** A failure carrying the backend's `state`, as both the maintenance page and
    the settings panel receive it. `state` is null for an older backend, or for
    any failure that is not a `MigrationError`. */
export type StorageFailure = { state: string | null; detail: string };
