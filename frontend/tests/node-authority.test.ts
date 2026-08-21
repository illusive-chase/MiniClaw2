import assert from "node:assert/strict";

import { nodeBelongsToHost, nodeMutationLock } from "../src/nodeUtil";

const HERE = "machine-here";
const ELSEWHERE = "machine-elsewhere";

const bound = { bound_here: true, read_only: false, local_machine_id: HERE };
const unbound = { bound_here: false, read_only: true, local_machine_id: HERE };
const lockedStore = { bound_here: true, read_only: true, local_machine_id: HERE };

/* A node stored in this host's partition is editable. Provenance — which
 * device created the project — is deliberately not part of the decision. */
{
  assert.equal(nodeBelongsToHost({ owner_host_id: HERE }, HERE), true);
  assert.equal(nodeMutationLock({ owner_host_id: HERE }, bound), null);
}

/* A record living in another host's partition stays viewable but not
 * writable, and says so for the right reason. */
{
  assert.equal(nodeBelongsToHost({ owner_host_id: ELSEWHERE }, HERE), false);
  assert.equal(
    nodeMutationLock({ owner_host_id: ELSEWHERE }, bound),
    "foreign_host",
  );
}

/* A missing owner means the server reported none — not that the node belongs
 * elsewhere. Treating absence as foreign is what silently locked every node
 * whose payload had lost its owner binding. */
{
  assert.equal(nodeBelongsToHost({}, HERE), true);
  assert.equal(nodeBelongsToHost({ owner_host_id: "" }, HERE), true);
  assert.equal(nodeMutationLock({}, bound), null);
  assert.equal(nodeMutationLock({ owner_host_id: "" }, bound), null);
}

/* An unknown local machine id must not turn every node foreign either: the
 * project-level binding is what decides authority. */
{
  assert.equal(nodeBelongsToHost({ owner_host_id: "" }, undefined), true);
}

/* With no workspace configured here, a local record is locked at the project
 * level. A remote record must retain its ownership reason because binding a
 * path does not transfer it into this host's partition. */
{
  assert.equal(
    nodeMutationLock({ owner_host_id: HERE }, unbound),
    "project_unbound",
  );
  assert.equal(
    nodeMutationLock({ owner_host_id: ELSEWHERE }, unbound),
    "foreign_host",
  );
}

/* A bound store can still be read-only for compatibility or hostname checks;
 * it must not be described as missing a project path. */
{
  assert.equal(
    nodeMutationLock({ owner_host_id: HERE }, lockedStore),
    "store_read_only",
  );
}

console.log("node-authority tests passed");
