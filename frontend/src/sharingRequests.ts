import type { SessionInfo, SharingRequestInfo, SharingRequestStatus } from "./types";

/** Statuses the UI can still act on. Everything else is history. */
const OPEN_STATUSES: ReadonlySet<SharingRequestStatus> = new Set<SharingRequestStatus>([
  "pending",
  "invalid",
]);

export const SHARING_REQUEST_STATUS_LABELS: Record<SharingRequestStatus, string> = {
  pending: "等待确认",
  fulfilled: "已开启共享",
  rejected: "已拒绝",
  cancelled: "已取消",
  orphaned: "已失效",
  invalid: "记录不一致",
};

export function isOpen(request: SharingRequestInfo): boolean {
  return OPEN_STATUSES.has(request.status);
}

export function forProject(
  requests: readonly SharingRequestInfo[],
  projectId: string,
): SharingRequestInfo[] {
  return requests.filter((request) => request.project_id === projectId);
}

/** Open requests this device raised — at most one per project by construction. */
export function localOpenRequest(
  requests: readonly SharingRequestInfo[],
  projectId: string,
): SharingRequestInfo | null {
  return (
    forProject(requests, projectId).find(
      (request) => request.is_local_request && isOpen(request),
    ) ?? null
  );
}

/** Open requests waiting on this device's decision as native host. */
export function incomingRequests(
  requests: readonly SharingRequestInfo[],
  projectId: string,
  sharing: SessionInfo["sharing"] = "device-native",
): SharingRequestInfo[] {
  if (sharing !== "device-native") return [];
  return forProject(requests, projectId).filter(
    (request) => isOpen(request) && !!request.can_accept,
  );
}

export function incomingCount(
  requests: readonly SharingRequestInfo[],
  projectId: string,
  sharing: SessionInfo["sharing"] = "device-native",
): number {
  return incomingRequests(requests, projectId, sharing).length;
}

export function requesterLabel(request: SharingRequestInfo): string {
  return request.requester_machine_label || request.requester_machine_id;
}

export function ownerLabel(request: SharingRequestInfo): string {
  return request.owner_machine_label || request.owner_machine_id;
}
