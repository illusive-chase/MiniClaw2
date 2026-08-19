import assert from "node:assert/strict";
import type { SharingRequestInfo, SharingRequestStatus } from "../src/types";
import {
  SHARING_REQUEST_STATUS_LABELS,
  incomingCount,
  incomingRequests,
  isOpen,
  localOpenRequest,
  ownerLabel,
  requesterLabel,
} from "../src/sharingRequests";

const LOCAL = "local-mid";
const HOST = "host-mid";

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
testRequestsAreScopedByProject();
testIncomingCountsOnlyDecidableRequests();
testLabelsFallBackToMachineIds();

console.log("sharing-requests tests passed");
