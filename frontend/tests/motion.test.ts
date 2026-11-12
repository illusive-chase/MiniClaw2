/* Motion bookkeeping that is easy to get subtly wrong and impossible to see in
 * types: which ids count as "fresh", and which canvas marks survive, replace,
 * or yield to one another.
 *
 * The rules under test are the ones the UI depends on to stay quiet:
 *   - nothing present at the baseline is ever marked fresh (page loads and
 *     project switches must not animate everything at once);
 *   - a birth is never downgraded to an arrival, and a locate never
 *     interrupts a birth (one emphasis at a time);
 *   - every mark expires on its own, even if no animation ever ran.
 */

import assert from "node:assert/strict";

/* The stores schedule with `window.setTimeout`; node has the same function on
 * the global object. */
Object.assign(globalThis, { window: globalThis });

const { collectFreshIds, staggerDelay, EXIT_MS } = await import("../src/motion");
const store = await import("../src/canvas/nodeMotionStore");

/* collectFreshIds: only unseen ids, in order, and the seen set grows. */
{
  const seen = new Set(["a", "b"]);
  assert.deepEqual(collectFreshIds(seen, ["a", "b", "c", "d"]), ["c", "d"]);
  assert.deepEqual(collectFreshIds(seen, ["a", "b", "c", "d"]), []);
  assert.deepEqual(collectFreshIds(seen, ["e", "a"]), ["e"]);
  assert.ok(seen.has("e"));
}

/* Stagger caps so the last row of a long list never waits long. */
{
  assert.equal(staggerDelay(0), "0ms");
  assert.equal(staggerDelay(2), "60ms");
  assert.equal(staggerDelay(50), "180ms");
}

/* Exit durations stay inside the 160ms ceiling the principles set. */
for (const [name, ms] of Object.entries(EXIT_MS)) {
  assert.ok(ms <= 160, `${name} exit ${ms}ms exceeds 160ms`);
}

/* Birth wins over a later arrival and a later locate; the other id in the
 * same batch still arrives, staggered after its position. */
{
  store.markNodeBorn("n1", "0% 0%");
  store.markNodesArrived(["n0", "n1", "n2"]);
  store.markNodeLocated("n1");
  assert.equal(store.peekNodeMotion("n1")?.kind, "born");
  assert.equal(store.peekNodeMotion("n1")?.origin, "0% 0%");
  assert.equal(store.peekNodeMotion("n0")?.kind, "arrive");
  assert.equal(store.peekNodeMotion("n0")?.delayMs, 0);
  assert.equal(store.peekNodeMotion("n2")?.kind, "arrive");
  assert.equal(store.peekNodeMotion("n2")?.delayMs, 60);
  for (const id of ["n0", "n1", "n2"]) store.clearNodeMotion(id);
}

/* A locate on an idle node does mark it. */
{
  store.markNodeLocated("idle");
  assert.equal(store.peekNodeMotion("idle")?.kind, "locate");
  store.clearNodeMotion("idle");
}

/* Leaving is sticky (no timer) and cleared explicitly. */
{
  store.markNodeLeaving("gone");
  assert.equal(store.isNodeLeaving("gone"), true);
  store.clearNodeMotion("gone");
  assert.equal(store.isNodeLeaving("gone"), false);
}

/* clearNodeMotion with a stale sequence leaves a newer mark alone. */
{
  store.markNodeLeaving("x");
  store.clearNodeMotion("x", -1);
  assert.equal(store.isNodeLeaving("x"), true);
  store.clearNodeMotion("x");
}

/* Pending-create placeholder: an older request's settle does not clear a
 * newer placeholder. */
{
  const first = store.showPendingCreate(10, 10);
  const second = store.showPendingCreate(20, 20);
  store.clearPendingCreate(first);
  assert.equal(store.peekPendingCreate()?.seq, second);
  store.clearPendingCreate(second);
  assert.equal(store.peekPendingCreate(), null);
}

/* Marks and placeholders expire without any animation having run. */
{
  store.markNodeBorn("ttl");
  store.markNodesArrived(["ttl-arrive"]);
  store.showPendingCreate(0, 0);
  await new Promise((resolve) => setTimeout(resolve, 2100));
  assert.equal(store.peekNodeMotion("ttl"), null);
  assert.equal(store.peekNodeMotion("ttl-arrive"), null);
  assert.equal(store.peekPendingCreate(), null);
}

console.log("motion: ok");
