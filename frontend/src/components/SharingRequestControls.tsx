import { useState } from "react";
import type { SessionInfo, SharingRequestInfo } from "../types";
import {
  canRequestSharing,
  incomingRequests,
  localOpenRequest,
  ownerLabel,
  requesterLabel,
} from "../sharingRequests";
import { pendingSyncMessage, type PendingSync } from "../useSharingRequests";

type Props = {
  session: SessionInfo;
  requests: readonly SharingRequestInfo[];
  syncConfigured: boolean;
  busy: boolean;
  pendingSync: PendingSync | null;
  onRequest: () => void;
  onAccept: (requestId: string) => void;
  onReject: (requestId: string) => void;
  onCancel: (requestId: string) => void;
  onCheckForUpdates: () => void;
  onRetrySync: () => void;
};

const CHIP =
  "rounded border px-1.5 py-0.5 font-sans transition disabled:cursor-not-allowed disabled:opacity-50";
const ACTION = `${CHIP} border-brand/50 bg-brand-soft text-brand-ink hover:border-brand`;
const QUIET = `${CHIP} border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink`;

/** Sharing-request controls for the project header.
 *
 * Three audiences share this strip: a non-host device that can ask, the same
 * device once it has asked, and the native host that has to answer. */
export function SharingRequestControls({
  session,
  requests,
  syncConfigured,
  busy,
  pendingSync,
  onRequest,
  onAccept,
  onReject,
  onCancel,
  onCheckForUpdates,
  onRetrySync,
}: Props) {
  const [confirmingAccept, setConfirmingAccept] = useState<string | null>(null);
  const requestStateIsCurrent = session.sharing === "device-native";
  const mine = requestStateIsCurrent ? localOpenRequest(requests, session.id) : null;
  const incoming = incomingRequests(requests, session.id, session.sharing);
  const canRequest = canRequestSharing(session, requests, { syncConfigured });
  const stalled = pendingSync?.sessionId === session.id ? pendingSync : null;

  if (!canRequest && !mine && incoming.length === 0 && !stalled) return null;

  return (
    <>
      {canRequest && (
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            if (
              !window.confirm(
                "将向该项目所属设备发送开启共享的请求，并立即执行一次元数据同步。\n\n"
                  + "对方确认后，项目会整体转为共享，同一元数据远端上持有匹配仓库的设备都可以申请在本地启用，"
                  + "而不只是当前这台设备。继续吗？",
              )
            ) {
              return;
            }
            onRequest();
          }}
          className={ACTION}
        >
          {busy ? "正在请求..." : "请求开启共享"}
        </button>
      )}

      {mine && (
        <span className="inline-flex items-center gap-1.5">
          <span className="rounded border border-state-waiting/40 bg-state-waiting-soft px-1.5 py-0.5 font-sans text-state-waiting">
            {mine.status === "invalid"
              ? `记录不一致 · 等待 ${ownerLabel(mine)} 重新确认`
              : `等待 ${ownerLabel(mine)} 确认`}
          </span>
          <button type="button" disabled={busy} onClick={onCheckForUpdates} className={QUIET}>
            同步检查状态
          </button>
          <button type="button" disabled={busy} onClick={() => onCancel(mine.id)} className={QUIET}>
            取消请求
          </button>
        </span>
      )}

      {incoming.map((request) => (
        <span key={request.id} className="inline-flex items-center gap-1.5">
          <span className="max-w-[14rem] truncate rounded border border-brand/40 bg-brand-soft px-1.5 py-0.5 font-sans text-brand-ink">
            {requesterLabel(request)} 请求开启共享
          </span>
          {confirmingAccept === request.id ? (
            <>
              <span className="max-w-[20rem] font-sans text-ink-muted">
                接受后项目整体转为共享，且当前不支持关闭。
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setConfirmingAccept(null);
                  onAccept(request.id);
                }}
                className={ACTION}
              >
                确认接受
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setConfirmingAccept(null)}
                className={QUIET}
              >
                返回
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                disabled={busy || session.active_count > 0}
                title={
                  session.active_count > 0
                    ? "项目有正在运行的节点，需先空闲才能迁移"
                    : undefined
                }
                onClick={() => setConfirmingAccept(request.id)}
                className={ACTION}
              >
                接受
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => onReject(request.id)}
                className={QUIET}
              >
                拒绝
              </button>
            </>
          )}
        </span>
      ))}

      {stalled && (
        <span className="inline-flex items-center gap-1.5">
          <span
            className="max-w-[20rem] truncate rounded border border-state-error/40 bg-state-error-soft px-1.5 py-0.5 font-sans text-state-error"
            title={pendingSyncMessage(stalled)}
          >
            {pendingSyncMessage(stalled)}
          </span>
          <button type="button" disabled={busy} onClick={onRetrySync} className={QUIET}>
            重试同步
          </button>
        </span>
      )}
    </>
  );
}
