import { useEffect, useRef, useState } from "react";
import { Archive, ArchiveRestore, CloudOff, ExternalLink, Trash2, X } from "lucide-react";

import { MAX_FOLDER_NAME_LENGTH, NEW_FOLDER_NAME } from "../../homeLayout";
import { languageLabel } from "../../languages";
import { modelPresetLabel } from "../../modelPresets";
import type { HomeFolder, ModelPreset, ProjectIcon, SessionInfo } from "../../types";
import { IconPicker } from "../IconPicker";
import { ProjectGlyph } from "../ProjectGlyph";
import { TileFaceView } from "./HomeCell";

export const SIDEBAR_WIDTH = 360;
export const NEW_FOLDER_OPTION = "__new";
const RENAME_DEBOUNCE_MS = 400;

const PERSISTENCE_LABELS: Record<string, string> = {
  durable: "持久",
  ephemeral: "临时",
  remote: "远端",
};

export function formatRelative(ts: number): string {
  const delta = Date.now() / 1000 - ts;
  if (delta < 60) return "刚刚";
  if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} 小时前`;
  if (delta < 86400 * 30) return `${Math.floor(delta / 86400)} 天前`;
  return new Date(ts * 1000).toLocaleDateString();
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="mb-[18px]">
      <div className="mb-[7px] flex items-baseline justify-between text-[12px] font-semibold text-ink-strong">
        {label}
        {hint && <small className="text-[11px] font-normal text-ink-subtle">{hint}</small>}
      </div>
      {children}
    </div>
  );
}

const INPUT =
  "h-9 w-full rounded-[9px] border border-line bg-surface-sunken px-[11px] text-[13px] text-ink-strong outline-none transition-colors duration-[140ms] focus:border-brand focus:bg-surface-raised disabled:opacity-55";

const BUTTON =
  "inline-flex h-[34px] items-center justify-center gap-1.5 rounded-[9px] border border-line bg-surface-raised px-3.5 text-[13px] font-medium text-ink transition-[border-color,color,background-color,transform] duration-[140ms] hover:border-line-strong hover:text-ink-strong active:scale-[.97] disabled:cursor-not-allowed disabled:opacity-45";

export type ProjectSidebarProps = {
  session: SessionInfo;
  folders: HomeFolder[];
  currentFolderId: string | null;
  modelPresets: ModelPreset[];
  statusLine: string;
  onRename: (name: string) => void;
  onIcon: (icon: ProjectIcon) => void;
  onPlace: (target: string) => void;
  onOpen: () => void;
  onArchive: (archived: boolean) => void;
  onDelete: () => void;
};

function ProjectSidebar({
  session,
  folders,
  currentFolderId,
  modelPresets,
  statusLine,
  onRename,
  onIcon,
  onPlace,
  onOpen,
  onArchive,
  onDelete,
}: ProjectSidebarProps) {
  const locked = !session.bound_here || session.read_only;
  const archived = !!session.archived_at;
  const [draft, setDraft] = useState(session.name ?? "");
  const [confirming, setConfirming] = useState(false);
  const renameTimer = useRef<number | null>(null);
  const lastSent = useRef(session.name ?? "");

  useEffect(() => {
    if (renameTimer.current === null) {
      setDraft(session.name ?? "");
      lastSent.current = session.name ?? "";
    }
  }, [session.name]);

  useEffect(() => () => {
    if (renameTimer.current !== null) window.clearTimeout(renameTimer.current);
  }, []);

  useEffect(() => {
    if (!confirming) return;
    const timer = window.setTimeout(() => setConfirming(false), 3500);
    return () => window.clearTimeout(timer);
  }, [confirming]);

  /* Typing saves after a pause; an empty name is never sent — the server
   * refuses it, and the field simply keeps the last saved one on blur. */
  const flushRename = (value: string) => {
    if (renameTimer.current !== null) window.clearTimeout(renameTimer.current);
    renameTimer.current = null;
    const trimmed = value.trim();
    if (!trimmed || trimmed === lastSent.current) return;
    lastSent.current = trimmed;
    onRename(trimmed);
  };

  const preview = draft.trim() || "未命名";
  return (
    <>
      <div className="flex flex-col items-center gap-2.5 pb-[18px] pt-1.5" aria-hidden="true">
        <ProjectGlyph icon={session.icon} size={88} />
        <div className="max-w-full truncate text-[14px] font-semibold text-ink-strong">{preview}</div>
      </div>
      {locked && (
        <div className="mb-4 flex gap-[9px] rounded-[10px] bg-state-waiting/10 px-3 py-2.5 text-[12px] leading-relaxed text-ink">
          <CloudOff size={15} className="mt-px flex-none text-state-waiting" />
          <div>
            此设备尚未绑定该项目的路径（打开项目即可绑定）。名称、图标和颜色存在项目记录里，绑定后才能修改；
            <b className="font-semibold">位置和分组随时可以调整。</b>
          </div>
        </div>
      )}
      <Field label="名称">
        <input
          className={INPUT}
          value={draft}
          maxLength={80}
          placeholder="未命名"
          disabled={locked}
          onChange={(event) => {
            const value = event.target.value;
            setDraft(value);
            if (renameTimer.current !== null) window.clearTimeout(renameTimer.current);
            renameTimer.current = window.setTimeout(() => flushRename(value), RENAME_DEBOUNCE_MS);
          }}
          onBlur={() => {
            flushRename(draft);
            if (!draft.trim()) setDraft(lastSent.current);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") (event.target as HTMLInputElement).blur();
          }}
        />
      </Field>
      <div className="mb-[18px]">
        <IconPicker
          glyph={session.icon.glyph}
          hue={session.icon.hue}
          disabled={locked}
          onGlyph={(glyph) => onIcon({ glyph, hue: session.icon.hue })}
          onHue={(hue) => onIcon({ glyph: session.icon.glyph, hue })}
        />
      </div>
      {!archived && (
        <Field label="所在组" hint="也可以直接拖动">
          <select
            className={INPUT + " appearance-auto pr-1.5"}
            value={currentFolderId ?? ""}
            onChange={(event) => onPlace(event.target.value)}
          >
            <option value="">不在组内（主屏）</option>
            {folders.map((folder) => (
              <option key={folder.id} value={folder.id}>
                {folder.name.trim() || NEW_FOLDER_NAME}
              </option>
            ))}
            <option value={NEW_FOLDER_OPTION}>新建组…</option>
          </select>
        </Field>
      )}
      <Field label="概况">
        <dl className="m-0 grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-[7px] rounded-[10px] bg-surface-sunken px-3.5 py-3 text-[12px]">
          <dt className="text-ink-subtle">状态</dt>
          <dd className="m-0 truncate text-ink-strong">{statusLine || "空闲"}</dd>
          <dt className="text-ink-subtle">{archived ? "归档于" : "最近活动"}</dt>
          <dd className="m-0 truncate text-ink-strong">
            {formatRelative(archived ? session.archived_at! : session.last_activity_at ?? session.created_at)}
          </dd>
          <dt className="text-ink-subtle">节点</dt>
          <dd className="m-0 text-ink-strong">{session.turns}</dd>
          <dt className="text-ink-subtle">默认模型</dt>
          <dd className="m-0 truncate text-ink-strong">{modelPresetLabel(modelPresets, session.model_preset_id)}</dd>
          <dt className="text-ink-subtle">持久化</dt>
          <dd className="m-0 text-ink-strong">
            {PERSISTENCE_LABELS[session.persistence_mode ?? (session.temporary ? "ephemeral" : "durable")]}
          </dd>
          {session.template_id && (
            <>
              <dt className="text-ink-subtle">模板</dt>
              <dd className="m-0 truncate text-ink-strong">{session.template_id}</dd>
            </>
          )}
          {session.preferred_language && (
            <>
              <dt className="text-ink-subtle">语言</dt>
              <dd className="m-0 truncate text-ink-strong">{languageLabel(session.preferred_language)}</dd>
            </>
          )}
          <dt className="text-ink-subtle">本机路径</dt>
          <dd
            className={"m-0 truncate " + (session.bound_here ? "text-ink-strong" : "text-state-waiting")}
            title={session.root_path || undefined}
          >
            {session.bound_here ? session.root_path || "—" : "未绑定"}
          </dd>
          <dt className="text-ink-subtle">ID</dt>
          <dd className="m-0 truncate font-mono text-[11px] text-ink-strong">{session.id}</dd>
        </dl>
      </Field>
      <div className="mt-1.5 flex flex-col gap-2">
        {!archived && (
          <button
            type="button"
            onClick={onOpen}
            className={BUTTON + " border-transparent bg-brand text-white hover:text-white hover:brightness-95"}
          >
            <ExternalLink size={15} />
            打开项目
          </button>
        )}
        <div className="flex gap-2">
          <button
            type="button"
            className={BUTTON + " flex-1"}
            disabled={archived ? !session.can_unarchive : locked}
            title={locked ? "未绑定的项目不能归档" : undefined}
            onClick={() => onArchive(!archived)}
          >
            {archived ? <ArchiveRestore size={15} /> : <Archive size={15} />}
            {archived ? "取消归档" : "归档"}
          </button>
          {session.can_delete && (
            <button
              type="button"
              className={
                BUTTON + " flex-1 "
                + (confirming ? "border-transparent bg-state-error text-white hover:text-white" : "text-state-error")
              }
              onClick={() => {
                if (!confirming) {
                  setConfirming(true);
                  return;
                }
                onDelete();
              }}
            >
              <Trash2 size={15} />
              {confirming ? "再点一次删除" : "删除"}
            </button>
          )}
        </div>
        {confirming && (
          <div className="home-sidebar-hint text-[11.5px] leading-relaxed text-ink-subtle">
            删除会移除所有设备上的项目记录和历史，不影响磁盘上的代码仓库。
          </div>
        )}
      </div>
    </>
  );
}

export type FolderSidebarProps = {
  folder: HomeFolder;
  members: SessionInfo[];
  autoFocusName: boolean;
  onRename: (name: string) => void;
  onCommitName: () => void;
  onSelectMember: (projectId: string) => void;
  onDissolve: () => void;
};

function FolderSidebar({
  folder,
  members,
  autoFocusName,
  onRename,
  onCommitName,
  onSelectMember,
  onDissolve,
}: FolderSidebarProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (!autoFocusName) return;
    const input = inputRef.current;
    input?.focus({ preventScroll: true });
    input?.select();
  }, [autoFocusName, folder.id]);
  return (
    <>
      <div className="flex flex-col items-center gap-2.5 pb-[18px] pt-1.5" aria-hidden="true">
        <TileFaceView face={{ kind: "folder", folder, members }} large />
        <div className="max-w-full truncate text-[14px] font-semibold text-ink-strong">
          {folder.name.trim() || "未命名组"}
        </div>
      </div>
      <Field label="组名">
        <input
          ref={inputRef}
          className={INPUT}
          value={folder.name}
          maxLength={MAX_FOLDER_NAME_LENGTH}
          onChange={(event) => onRename(event.target.value)}
          onBlur={onCommitName}
          onKeyDown={(event) => {
            if (event.key === "Enter") (event.target as HTMLInputElement).blur();
          }}
        />
      </Field>
      <Field label="组内项目" hint={`${members.length} 个`}>
        <div className="flex flex-col gap-0.5 rounded-[10px] bg-surface-sunken p-1.5">
          {members.map((session) => (
            <button
              key={session.id}
              type="button"
              onClick={() => onSelectMember(session.id)}
              className="flex items-center gap-2.5 rounded-lg p-1.5 text-left text-[12.5px] text-ink-strong transition-colors duration-[140ms] hover:bg-surface-raised"
            >
              <ProjectGlyph icon={session.icon} size={28} />
              <span className="truncate">{session.name?.trim() || "未命名"}</span>
            </button>
          ))}
        </div>
        <div className="mt-1.5 text-[11.5px] leading-relaxed text-ink-subtle">
          组没有自己的图标：缩略图取自组内前 9 个项目。在组面板里拖动可以调整顺序；拖出面板并停留片刻即可移出。最后一个项目移出后，组会自动消失。
        </div>
      </Field>
      <div className="mt-1.5 flex flex-col gap-2">
        <button type="button" className={BUTTON} onClick={onDissolve}>
          解散组
        </button>
        <div className="text-[11.5px] leading-relaxed text-ink-subtle">
          解散后，组内项目按原顺序回到主屏上组所在的位置。
        </div>
      </div>
    </>
  );
}

export type SidebarSubject =
  | ({ kind: "project" } & ProjectSidebarProps)
  | ({ kind: "folder" } & FolderSidebarProps);

/**
 * The editing sidebar. It is bound to edit mode: open exactly while editing,
 * so closing it is leaving edit mode. Its content rises in each time the
 * subject changes; the panel itself only slides on open and close.
 */
export function HomeSidebar({
  open,
  subject,
  subjectKey,
  onClose,
}: {
  open: boolean;
  subject: SidebarSubject | null;
  subjectKey: string | null;
  onClose: () => void;
}) {
  /* Keep Tab out while it is translated offscreen; React 18's typings do not
   * expose `inert`, so it is toggled through the ref (as App's side panel does). */
  const asideRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const el = asideRef.current;
    if (!el) return;
    if (open) el.removeAttribute("inert");
    else el.setAttribute("inert", "");
  }, [open]);
  return (
    <aside
      ref={asideRef}
      className="home-sidebar absolute bottom-0 right-0 top-0 z-40 flex w-full flex-col border-l border-line bg-surface-raised shadow-[-12px_0_32px_rgb(var(--shadow)/0.08)] min-[720px]:w-[360px]"
      data-open={open ? "true" : "false"}
      aria-label="编辑"
      aria-hidden={!open}
    >
      <div className="flex h-[52px] flex-none items-center justify-between border-b border-line pl-5 pr-3">
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-ink-subtle">
          {subject?.kind === "folder"
            ? "编辑组"
            : subject?.kind === "project" && subject.session.archived_at
              ? "已归档的项目"
              : subject ? "编辑项目" : "编辑主屏"}
        </span>
        <button
          type="button"
          onClick={onClose}
          title="完成（Esc）"
          aria-label="完成编辑"
          className="inline-grid h-[34px] w-[34px] place-items-center rounded-[9px] text-ink-muted transition-colors duration-[140ms] hover:bg-surface-sunken hover:text-ink-strong"
        >
          <X size={16} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-5">
        <div key={subjectKey ?? "none"} className="home-sidebar-swap">
          {subject?.kind === "project" && <ProjectSidebar {...subject} />}
          {subject?.kind === "folder" && <FolderSidebar {...subject} />}
          {!subject && (
            <div className="pt-10 text-center text-[12.5px] leading-relaxed text-ink-muted">
              点一个项目或组来编辑它。
              <br />
              拖动可以调整位置，拖到另一个项目上就会建组。
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}
