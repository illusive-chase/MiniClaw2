import assert from "node:assert/strict";

import {
  NOTICE_TTL_MS,
  badgeCountable,
  countsTowardBadge,
  emitSystemNotification,
  isPersistentKind,
  noticeBody,
  noticeFromEvent,
  noticeKind,
  noticeTitle,
  noticeTtlMs,
  pushNotice,
  removeNotice,
  requestSystemNotificationPermission,
  systemNotificationPermission,
  type Notice,
} from "../src/notices";
import { mergeRailItems } from "../src/components/NoticeBannerRail";
import {
  isNotificationEligible,
  isUnread,
  notificationKey,
  rowContext,
  summarize,
  summaryParts,
} from "../src/activeNodes";
import type { ActiveNodeEntry, NodeState, WorkspaceEvent } from "../src/types";

function entry(
  nodeId: string,
  state: NodeState,
  opts: {
    project_id?: string;
    project_name?: string;
    kind?: ActiveNodeEntry["kind"];
    op_kind?: string | null;
  } = {},
): ActiveNodeEntry {
  return {
    project_id: opts.project_id ?? "p1",
    project_name: opts.project_name ?? "proj",
    node_id: nodeId,
    state,
    category: "regular",
    kind: opts.kind ?? "agent",
    op_kind: opts.op_kind ?? null,
    planspace_id: null,
    planspace_title: null,
    is_active_planspace: false,
    label: "",
    started_at: 0,
    finished_at: null,
    gate: null,
  };
}

function commitEntry(nodeId: string, state: NodeState): ActiveNodeEntry {
  return entry(nodeId, state, { kind: "op", op_kind: "commit" });
}

function updated(
  nodeId: string,
  state: NodeState,
  previous: NodeState | null,
  opts: { project_id?: string; created?: boolean } = {},
): WorkspaceEvent {
  return {
    type: "workspace_node_updated",
    project_id: opts.project_id ?? "p1",
    node_id: nodeId,
    entry: entry(nodeId, state, { project_id: opts.project_id }),
    previous_state: previous,
    created: opts.created ?? false,
    seq: 0,
  };
}

let seq = 0;
function derive(event: WorkspaceEvent, now = 1000): Notice | null {
  seq += 1;
  return noticeFromEvent(event, now, seq);
}

function push(notices: Notice[], event: WorkspaceEvent, now = 1000): Notice[] {
  const notice = derive(event, now);
  return notice ? pushNotice(notices, notice) : notices;
}

/* ---- T1: kind and lifetime per state ---- */

{
  assert.equal(noticeKind("waiting"), "blocking");
  assert.equal(noticeKind("awaiting_human_input"), "blocking");
  assert.equal(noticeKind("error"), "failure");
  assert.equal(noticeKind("done"), "success");
  assert.equal(noticeKind("cancelled"), "neutral");
  assert.equal(noticeKind("running"), "progress");
  assert.equal(noticeKind("queued"), "progress");
  /* A virtual node is a plan entry, not something that happened. */
  assert.equal(noticeKind("virtual"), null);

  /* Persistent kinds wait for the user; transient ones carry a finite TTL. */
  for (const kind of ["blocking", "failure", "success"] as const) {
    assert.equal(isPersistentKind(kind), true);
    assert.equal(noticeTtlMs(kind), null);
  }
  for (const kind of ["neutral", "progress"] as const) {
    assert.equal(isPersistentKind(kind), false);
    assert.ok((noticeTtlMs(kind) ?? 0) > 0);
  }
  /* progress must be the shortest: a stamped template emits it per node. */
  assert.ok((NOTICE_TTL_MS.progress ?? 0) < (NOTICE_TTL_MS.neutral ?? 0));

  /* Both blocking states share a kind, so the rail treats a gate and a review
   * request identically. */
  assert.equal(noticeKind("waiting"), noticeKind("awaiting_human_input"));
}

/* ---- T2: notices are stateless (the design's foundation) ---- */

{
  /* A node blocks, then the user answers the gate and it resumes running.
   * Both banners must remain: the first records something that really did
   * happen, and nothing retracts it. If a future change adds retraction, this
   * assertion is what fails. */
  let notices: Notice[] = [];
  notices = push(notices, updated("n1", "waiting", "running"));
  notices = push(notices, updated("n1", "running", "waiting"));
  assert.equal(notices.length, 2);
  assert.deepEqual(
    notices.map((notice) => notice.kind),
    ["progress", "blocking"],
  );
  /* Newest first, purely by arrival. */
  assert.equal(notices[0].entry.state, "running");

  /* Three transitions on one node yield three independent banners with
   * distinct ids, not one banner that mutates. */
  notices = push(notices, updated("n1", "done", "running"));
  assert.equal(notices.length, 3);
  assert.equal(new Set(notices.map((notice) => notice.id)).size, 3);

  /* The blocking banner still describes the state it was born with. */
  const blocking = notices.find((notice) => notice.kind === "blocking");
  assert.equal(blocking?.entry.state, "waiting");
}

/* ---- T3: a non-transition produces nothing ---- */

{
  assert.equal(derive(updated("n1", "running", "running")), null);
  /* An absent previous_state is a real transition (first sighting). */
  assert.ok(derive(updated("n1", "running", null)));
}

/* ---- T4: removals are not events ---- */

{
  const removal: WorkspaceEvent = {
    type: "workspace_node_removed",
    project_id: "p1",
    node_id: "n1",
    previous_state: "done",
    deleted: true,
    seq: 0,
  };
  assert.equal(derive(removal), null);
}

/* ---- T4b: commit ops are activity, not notifications ---- */

{
  /* A manual commit is user-initiated, while an automatic commit immediately
   * follows the agent result. Neither should produce a second notification for
   * the work, at any point in the commit node's short lifecycle. */
  for (const [state, previous] of [
    ["queued", null],
    ["running", "queued"],
    ["done", "running"],
    ["error", "running"],
  ] as Array<[NodeState, NodeState | null]>) {
    const event = updated("commit-node", state, previous);
    event.entry = commitEntry("commit-node", state);
    assert.equal(derive(event), null);
  }

  const doneCommit = commitEntry("commit-node", "done");
  assert.equal(isNotificationEligible(doneCommit), false);
  assert.equal(isUnread(doneCommit, new Set()), false);
  assert.equal(countsTowardBadge(doneCommit), false);

  /* Other op nodes can be slow or actionable and retain normal behavior. */
  const pull = entry("pull-node", "done", { kind: "op", op_kind: "pull" });
  const pullEvent = updated("pull-node", "done", "running");
  pullEvent.entry = pull;
  assert.ok(derive(pullEvent));
  assert.equal(isUnread(pull, new Set()), true);
  assert.equal(countsTowardBadge(pull), true);
}

/* ---- T5: only persistent kinds reach the badge ---- */

{
  const entries = [
    entry("n1", "waiting"),
    entry("n2", "error"),
    entry("n3", "done"),
    entry("n4", "running"),
    entry("n5", "queued"),
    entry("n6", "cancelled"),
    entry("n7", "virtual"),
  ];
  assert.deepEqual(
    badgeCountable(entries).map((item) => item.node_id),
    ["n1", "n2", "n3"],
  );
  assert.equal(countsTowardBadge(entry("n4", "running")), false);
  assert.equal(countsTowardBadge(entry("n6", "cancelled")), false);
  assert.equal(countsTowardBadge(entry("n1", "awaiting_human_input")), true);
}

/* ---- T5b: the panel's own accounting covers every unread row ---- */

{
  /* The badge filter and the panel's row list answer different questions, and
   * driving the panel's header text and bulk action off the badge's filtered
   * set made the panel contradict itself: it said "无未读" while displaying
   * unread running rows that "全部标为已读" was disabled and could not clear.
   *
   * So: `badgeCountable` narrows, and `summaryParts` over the *unfiltered*
   * unread set must still describe those rows. */
  const unread = [
    entry("n1", "running"),
    entry("n2", "queued"),
    entry("n3", "cancelled"),
  ];
  assert.deepEqual(badgeCountable(unread), []);

  const parts = summaryParts(summarize(unread));
  assert.deepEqual(parts, ["1 在跑", "1 排队", "1 取消"]);
  /* The point of the test: rows exist, so the panel must not read as empty. */
  assert.ok(parts.length > 0);

  /* Every state the feed can hold gets a part, in urgency order. */
  assert.deepEqual(
    summaryParts(
      summarize([
        entry("a", "done"),
        entry("b", "waiting"),
        entry("c", "queued"),
        entry("d", "error"),
        entry("e", "running"),
        entry("f", "cancelled"),
      ]),
    ),
    ["1 等我", "1 出错", "1 在跑", "1 排队", "1 已完成", "1 取消"],
  );
  /* Nothing unread spells nothing, rather than a row of zeroes. */
  assert.deepEqual(summaryParts(summarize([])), []);
}

/* ---- T6: overflow drops transient banners first ---- */

{
  const limit = 4;
  let notices: Notice[] = [];
  /* Two results the user has not acknowledged, then a run of scheduler
   * chatter that pushes past the cap. */
  for (const [node, state, previous] of [
    ["a", "done", "running"],
    ["b", "error", "running"],
    ["c", "running", "queued"],
    ["d", "running", "queued"],
  ] as Array<[string, NodeState, NodeState]>) {
    seq += 1;
    const notice = noticeFromEvent(updated(node, state, previous), 1000, seq);
    assert.ok(notice);
    notices = pushNotice(notices, notice, limit);
  }
  assert.equal(notices.length, limit);

  seq += 1;
  const overflowing = noticeFromEvent(updated("e", "queued", "virtual"), 1000, seq);
  assert.ok(overflowing);
  notices = pushNotice(notices, overflowing, limit);
  assert.equal(notices.length, limit);
  /* Both persistent banners survive; the oldest progress banner is the one
   * that went. */
  assert.deepEqual(
    notices.filter((notice) => isPersistentKind(notice.kind)).map((n) => n.entry.node_id),
    ["b", "a"],
  );
  assert.equal(
    notices.some((notice) => notice.entry.node_id === "c"),
    false,
  );

  /* With nothing transient left to sacrifice, eviction falls back to
   * oldest-first so the cap still holds. */
  let persistentOnly: Notice[] = [];
  for (const node of ["p1", "p2", "p3", "p4", "p5"]) {
    seq += 1;
    const notice = noticeFromEvent(updated(node, "done", "running"), 1000, seq);
    assert.ok(notice);
    persistentOnly = pushNotice(persistentOnly, notice, limit);
  }
  assert.equal(persistentOnly.length, limit);
  assert.equal(persistentOnly[0].entry.node_id, "p5");
  assert.equal(
    persistentOnly.some((notice) => notice.entry.node_id === "p1"),
    false,
  );
}

/* ---- T7: the read key is shared with the history panel ---- */

{
  const notice = derive(updated("n1", "waiting", "running"));
  assert.ok(notice);
  /* Same key derivation, so dismissing a banner marks the matching history row
   * read too — the two surfaces must not fork their bookkeeping. */
  assert.equal(notice.readKey, notificationKey(entry("n1", "waiting")));

  const readKeys = new Set<string>();
  readKeys.add(notice.readKey);
  assert.equal(readKeys.has(notificationKey(entry("n1", "waiting"))), true);
  /* A different state on the same node is a different notification. */
  assert.equal(readKeys.has(notificationKey(entry("n1", "done"))), false);

  assert.deepEqual(removeNotice([notice], notice.id), []);
  /* Removing an unknown id is a no-op rather than an error: expiry and a
   * click can both land on the same banner. */
  assert.deepEqual(removeNotice([notice], "nope"), [notice]);
}

/* ---- T8: a snapshot refresh produces no banners ---- */

{
  /* Reconnect and tab-focus both re-fetch the whole entry list. Banners are
   * derived only from pushed transitions, so there is no path from a snapshot
   * row to a banner — a dropped socket must not redeliver a screenful of
   * banners for work already seen. This test states that as a property: given
   * only entries, there is nothing to call.
   *
   * The guard that makes it hold in practice is the equal-state check, since
   * anything synthesizing an event from a snapshot row would carry the row's
   * own state as `previous_state`. */
  const snapshot = [
    entry("n1", "waiting"),
    entry("n2", "done"),
    entry("n3", "running"),
  ];
  const synthesized = snapshot.map((item) =>
    derive({
      type: "workspace_node_updated",
      project_id: item.project_id,
      node_id: item.node_id,
      entry: item,
      previous_state: item.state,
      seq: 0,
    }),
  );
  assert.deepEqual(synthesized, [null, null, null]);
}

/* ---- T9: the two-part headline says what happened, then what about ---- */

{
  /* Each state gets its own headline, and the two blocking states differ even
   * though they share a kind: a gate wants an answer, a review wants a
   * reading, and the title is the only place that distinction is visible. */
  const gate = derive(updated("n1", "waiting", "running"));
  const review = derive(updated("n2", "awaiting_human_input", "running"));
  assert.ok(gate && review);
  assert.equal(gate.kind, review.kind);
  assert.notEqual(noticeTitle(gate), noticeTitle(review));

  /* Every state that earns a notice earns a title, so no banner ever renders
   * a raw state name as its headline. */
  for (const state of [
    "waiting",
    "awaiting_human_input",
    "error",
    "done",
    "cancelled",
    "running",
    "queued",
  ] as const) {
    seq += 1;
    const notice = noticeFromEvent(updated("n", state, "virtual"), 1000, seq);
    assert.ok(notice);
    assert.notEqual(noticeTitle(notice), state);
    assert.ok(noticeTitle(notice).length > 0);
  }
}

/* ---- T10: the description prefers the most specific thing it has ---- */

{
  /* A gate summary is the actual question being asked, so it outranks the
   * label — a banner showing "实现登录" when the node is asking "覆盖已有文件？"
   * hides the only part the user must act on. */
  const asking = updated("n1", "waiting", "running");
  asking.entry.label = "实现登录";
  asking.entry.gate = { summary: "覆盖已有文件？" } as ActiveNodeEntry["gate"];
  const gate = derive(asking);
  assert.ok(gate);
  assert.equal(noticeBody(gate), "覆盖已有文件？");

  /* No gate: the label says what the turn was for. */
  const labelled = updated("n2", "done", "running");
  labelled.entry.label = "实现登录";
  const done = derive(labelled);
  assert.ok(done);
  assert.equal(noticeBody(done), "实现登录");

  /* Neither: fall back to context rather than rendering an empty line. The
   * fixture's blank label is the realistic case for a fresh node. */
  const bare = derive(updated("n3", "queued", "virtual"));
  assert.ok(bare);
  assert.equal(noticeBody(bare), rowContext(bare.entry, bare.createdAt));
  assert.ok(noticeBody(bare).length > 0);
}

/* ---- T11: the rail holds dismissed banners in place while they exit ---- */

{
  /* Presentation-only bookkeeping: the reducer drops a notice the instant it
   * is dismissed, but the rail must keep rendering it for the length of its
   * exit animation — and at its *own* index, or the banners below jump upward
   * while it is still sliding out. */
  const a = derive(updated("a", "done", "running"));
  const b = derive(updated("b", "error", "running"));
  const c = derive(updated("c", "waiting", "running"));
  assert.ok(a && b && c);

  /* Newest-first, matching `pushNotice`. */
  let items = mergeRailItems([], [c, b, a]);
  assert.deepEqual(
    items.map((item) => item.notice.id),
    [c.id, b.id, a.id],
  );
  assert.equal(
    items.every((item) => !item.leaving),
    true,
  );

  /* Dismiss the middle one: it stays at index 1, marked leaving. */
  items = mergeRailItems(items, [c, a]);
  assert.deepEqual(
    items.map((item) => item.notice.id),
    [c.id, b.id, a.id],
  );
  assert.deepEqual(
    items.map((item) => item.leaving),
    [false, true, false],
  );

  /* A banner arriving mid-exit goes to the head and does not disturb the
   * leaving one's position. */
  const d = derive(updated("d", "done", "running"));
  assert.ok(d);
  items = mergeRailItems(items, [d, c, a]);
  assert.deepEqual(
    items.map((item) => item.notice.id),
    [d.id, c.id, b.id, a.id],
  );
  assert.equal(items[2].leaving, true);

  /* Ids carry a push sequence, so a dismissed id never returns — a leaving
   * item can never be resurrected by a later push. */
  assert.equal(new Set([a, b, c, d].map((notice) => notice.id)).size, 4);

  /* Reaping is the caller's timer, so merge alone never removes anything;
   * an empty incoming list marks everything leaving rather than emptying. */
  items = mergeRailItems(items, []);
  assert.equal(items.length, 4);
  assert.equal(
    items.every((item) => item.leaving),
    true,
  );
}

/* ---- system notifications: every degradation path stays quiet ---- */
{
  const globalScope = globalThis as unknown as {
    window?: unknown;
    document?: unknown;
  };
  const originalWindow = globalScope.window;
  const originalDocument = globalScope.document;

  /* No Notification API at all (insecure context, older browser). */
  globalScope.window = {};
  assert.equal(systemNotificationPermission(), "unsupported");
  assert.equal(await requestSystemNotificationPermission(), "unsupported");

  /* A property access that throws must read as unsupported, not crash. */
  globalScope.window = {
    Notification: {
      get permission(): string {
        throw new Error("blocked");
      },
    },
  };
  assert.equal(systemNotificationPermission(), "unsupported");

  seq += 1;
  const blocking = noticeFromEvent(updated("n1", "waiting", "running"), 1000, seq);
  assert.ok(blocking);
  seq += 1;
  const success = noticeFromEvent(updated("n2", "done", "running"), 1000, seq);
  assert.ok(success);

  let constructed = 0;
  const activated: Notice[] = [];
  const instances: FakeNotification[] = [];
  let closed = 0;
  class FakeNotification {
    static permission = "granted";
    static requestPermission = async () => "granted";
    onclick: (() => void) | null = null;
    constructor(
      public title: string,
      public options: { body?: string; tag?: string },
    ) {
      constructed += 1;
      instances.push(this);
    }
    close() {
      closed += 1;
    }
  }
  globalScope.window = { Notification: FakeNotification, focus: () => {} };
  globalScope.document = { visibilityState: "hidden" };

  assert.equal(systemNotificationPermission(), "granted");

  /* Only blocking earns a system notification: it is the only class whose
   * being missed stalls a project. */
  emitSystemNotification(success, (item) => activated.push(item));
  assert.equal(constructed, 0);

  emitSystemNotification(blocking, (item) => activated.push(item));
  assert.equal(constructed, 1);

  /* One live notification per node, so a node flipping between blocking states
   * replaces its own rather than stacking up. */
  assert.equal(instances[0].options.tag, blocking.entry.node_id);

  /* Clicking the notification hands back the whole notice, not just its entry.
   * The caller needs the banner's id and read key to acknowledge it — routing
   * only the entry through left the banner and unread badge still asking about
   * something the user had just answered. */
  assert.equal(activated.length, 0);
  instances[0].onclick?.();
  assert.equal(activated.length, 1);
  assert.equal(activated[0].id, blocking.id);
  assert.equal(activated[0].readKey, blocking.readKey);
  assert.equal(activated[0].readKey, notificationKey(entry("n1", "waiting")));
  assert.equal(activated[0].entry.node_id, "n1");
  /* The click closes the OS-level notification on its way through. */
  assert.equal(closed, 1);

  /* Visible tab: the in-page rail is strictly better, so stay quiet. */
  globalScope.document = { visibilityState: "visible" };
  emitSystemNotification(blocking, (item) => activated.push(item));
  assert.equal(constructed, 1);

  /* Denied permission never retries and never throws. */
  globalScope.document = { visibilityState: "hidden" };
  class DeniedNotification extends FakeNotification {
    static permission = "denied";
  }
  globalScope.window = { Notification: DeniedNotification, focus: () => {} };
  emitSystemNotification(blocking, (item) => activated.push(item));
  assert.equal(constructed, 1);

  /* Granted but construction throws — some embedded webviews do this. */
  class ThrowingNotification {
    static permission = "granted";
    constructor() {
      throw new Error("no notifications here");
    }
  }
  globalScope.window = { Notification: ThrowingNotification, focus: () => {} };
  emitSystemNotification(blocking, (item) => activated.push(item));

  /* Nothing after the one real click ever activated. */
  assert.equal(activated.length, 1);

  globalScope.window = originalWindow;
  globalScope.document = originalDocument;
}

console.log("notices tests passed");
