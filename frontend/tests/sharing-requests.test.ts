import assert from "node:assert/strict";
import type { SessionInfo, SharingRequestInfo, SharingRequestStatus } from "../src/types";
import {
  SHARING_REQUEST_STATUS_LABELS,
  canRequestSharing,
  incomingCount,
  incomingRequests,
  isOpen,
  localOpenRequest,
  ownerLabel,
  requesterLabel,
} from "../src/sharingRequests";

const LOCAL = "local-mid";
const HOST = "host-mid";

function project(
  id: string,
  opts: {
    sharing?: "device-native" | "shared";
    is_native?: boolean;
    temporary?: boolean;
    machine_id?: string;
  } = {},
): SessionInfo {
  return {
    id,
    created_at: 1000,
    turns: 0,
    model_preset_id: "p",
    concurrency: 1,
    active_count: 0,
    queued_count: 0,
    machine_id: opts.machine_id ?? HOST,
    local_machine_id: LOCAL,
    native_machine_label: "there",
    is_native: opts.is_native ?? false,
    read_only: !(opts.is_native ?? false),
    can_delete: false,
    sharing: opts.sharing ?? "device-native",
    can_join_here: false,
    temporary: opts.temporary,
    hosts: [],
  };
}

function request(
  id: string,
  opts: {
    project_id?: string;
    status?: SharingRequestStatus;
    is_local_request?: boolean;
    can_accept?: boolean;
    can_cancel?: boolean;
    requester_machine_label?: string;
    owner_machine_label?: string;
  } = {},
): SharingRequestInfo {
  return {
    id,
    project_id: opts.project_id ?? "p1",
    status: opts.status ?? "pending",
    requester_machine_id: opts.is_local_request === false ? "other-mid" : LOCAL,
    requester_machine_label: opts.requester_machine_label,
    owner_machine_id: HOST,
    owner_machine_label: opts.owner_machine_label,
    requested_at: 1,
    is_local_request: opts.is_local_request ?? true,
    can_accept: opts.can_accept ?? false,
    can_reject: opts.can_accept ?? false,
    can_cancel: opts.can_cancel ?? false,
  };
}

/* Only `pending` and `invalid` are still actionable. `invalid` stays open on
 * purpose: an accepted record whose project never migrated has to be
 * retryable by the host, not presented to the requester as done. */
function testOpenStatuses(): void {
  assert.equal(isOpen(request("r", { status: "pending" })), true);
  assert.equal(isOpen(request("r", { status: "invalid" })), true);
  for (const status of ["fulfilled", "rejected", "cancelled", "orphaned"] as const) {
    assert.equal(isOpen(request("r", { status })), false, status);
  }
  for (const status of Object.keys(SHARING_REQUEST_STATUS_LABELS)) {
    assert.ok(SHARING_REQUEST_STATUS_LABELS[status as SharingRequestStatus].length > 0);
  }
}

function testRequestEntryNeedsANonHostDeviceAndSync(): void {
  const requests: SharingRequestInfo[] = [];
  assert.equal(
    canRequestSharing(project("p1"), requests, { syncConfigured: true }),
    true,
  );
  /* Without a metadata remote the request has no way to reach the host, so
   * offering the action would strand it on this device. */
  assert.equal(
    canRequestSharing(project("p1"), requests, { syncConfigured: false }),
    false,
  );
  assert.equal(
    canRequestSharing(project("p1", { is_native: true }), requests, {
      syncConfigured: true,
    }),
    false,
  );
  assert.equal(
    canRequestSharing(project("p1", { sharing: "shared" }), requests, {
      syncConfigured: true,
    }),
    false,
  );
  assert.equal(
    canRequestSharing(project("p1", { temporary: true }), requests, {
      syncConfigured: true,
    }),
    false,
  );
  assert.equal(canRequestSharing(null, requests, { syncConfigured: true }), false);
}

/* One open request per device: the entry point disappears while it stands, and
 * comes back once the request reaches a terminal status. */
function testPendingRequestHidesTheEntryPoint(): void {
  const pending = [request("r1")];
  assert.equal(
    canRequestSharing(project("p1"), pending, { syncConfigured: true }),
    false,
  );
  assert.equal(localOpenRequest(pending, "p1")?.id, "r1");

  const rejected = [request("r1", { status: "rejected" })];
  assert.equal(
    canRequestSharing(project("p1"), rejected, { syncConfigured: true }),
    true,
  );
  assert.equal(localOpenRequest(rejected, "p1"), null);
}

function testRequestsAreScopedByProject(): void {
  const requests = [
    request("r1", { project_id: "p1" }),
    request("r2", { project_id: "p2" }),
  ];
  assert.equal(localOpenRequest(requests, "p1")?.id, "r1");
  assert.equal(localOpenRequest(requests, "p2")?.id, "r2");
  assert.equal(localOpenRequest(requests, "p3"), null);
}

/* The host badge counts only requests it can actually decide, so a request
 * this device raised never shows up as work waiting on this device. */
function testIncomingCountsOnlyDecidableRequests(): void {
  const requests = [
    request("mine", { is_local_request: true, can_cancel: true }),
    request("theirs", { is_local_request: false, can_accept: true }),
    request("done", { is_local_request: false, status: "fulfilled" }),
  ];
  assert.deepEqual(
    incomingRequests(requests, "p1").map((item) => item.id),
    ["theirs"],
  );
  assert.equal(incomingCount(requests, "p1"), 1);
  assert.equal(incomingCount(requests, "p2"), 0);
  assert.deepEqual(incomingRequests(requests, "p1", "shared"), []);
  assert.equal(
    incomingCount(requests, "p1", "shared"),
    0,
    "stale request capabilities must disappear once the project is shared",
  );
}

function testLabelsFallBackToMachineIds(): void {
  assert.equal(requesterLabel(request("r")), LOCAL);
  assert.equal(
    requesterLabel(request("r", { requester_machine_label: "laptop" })),
    "laptop",
  );
  assert.equal(ownerLabel(request("r")), HOST);
  assert.equal(ownerLabel(request("r", { owner_machine_label: "desktop" })), "desktop");
}

testOpenStatuses();
testRequestEntryNeedsANonHostDeviceAndSync();
testPendingRequestHidesTheEntryPoint();
testRequestsAreScopedByProject();
testIncomingCountsOnlyDecidableRequests();
testLabelsFallBackToMachineIds();

console.log("sharing-requests tests passed");
