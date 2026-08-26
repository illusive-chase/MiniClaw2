import assert from "node:assert/strict";

import {
  draftShapeMatches,
  draftStashKey,
  pruneDraftStash,
  readStashedDraft,
  shouldAutosaveDraft,
  stashRestoreDecision,
  writeStashedDraft,
  type StashMap,
} from "../src/draftStash";
import {
  mergeVirtualDraft,
  virtualDraftFromNode,
} from "../src/panel/AgentPanel";
import type { NodeInfo } from "../src/types";

function node(over: Partial<NodeInfo> = {}): NodeInfo {
  return {
    id: "n1",
    project_id: "p1",
    kind: "agent",
    state: "virtual",
    provider: "claude",
    model_preset_id: "gpt-5.5",
    prompt: "",
    prompt_draft: "do the thing",
    category: "regular",
    created_at: 0,
    ...over,
  } as NodeInfo;
}

function withLocalStorage(
  storage: { getItem(key: string): string | null; setItem(key: string, value: string): void },
  run: () => void,
): void {
  const previous = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = { localStorage: storage };
  try {
    run();
  } finally {
    if (previous === undefined) {
      delete (globalThis as { window?: unknown }).window;
    } else {
      (globalThis as { window?: unknown }).window = previous;
    }
  }
}

/* Two nodes in two sessions must never share a stash slot: the same node id can
 * exist in a re-imported project, and restoring one into the other would put a
 * stranger's prompt in the editor. */
{
  assert.notEqual(draftStashKey("s1", "n1"), draftStashKey("s2", "n1"));
  assert.equal(draftStashKey("s1", "n1"), draftStashKey("s1", "n1"));
}

/* Pruning drops the oldest first, so the entry being typed right now outlives
 * the stale ones it shares the budget with. */
{
  const entries: StashMap = {};
  for (let i = 0; i < 5; i += 1) {
    entries[`k${i}`] = { savedAt: i * 1000, baseline: {}, draft: {} };
  }
  const kept = pruneDraftStash(entries, 5000, { maxEntries: 2 });
  assert.deepEqual(Object.keys(kept).sort(), ["k3", "k4"]);
}

/* An entry past the age limit is dropped even when there is room for it — a
 * month-old draft restored into the editor is a surprise, not a rescue. */
{
  const entries: StashMap = {
    fresh: { savedAt: 9_000, baseline: {}, draft: {} },
    ancient: { savedAt: 0, baseline: {}, draft: {} },
  };
  const kept = pruneDraftStash(entries, 10_000, { maxAgeMs: 5_000 });
  assert.deepEqual(Object.keys(kept), ["fresh"]);
}

/* Expiry is enforced when restoring too. A browser with no later writes must
 * not resurrect a draft after the seven-day retention window. */
{
  let raw: string | null = null;
  withLocalStorage(
    {
      getItem: () => raw,
      setItem: (_key, value) => {
        raw = value;
      },
    },
    () => {
      const key = draftStashKey("s1", "expired");
      assert.equal(
        writeStashedDraft(key, { savedAt: 1, baseline: {}, draft: {} }),
        true,
      );
      assert.equal(readStashedDraft(key, 8 * 24 * 60 * 60 * 1000), null);
    },
  );
}

/* Storage failures are observable so the editor never claims that a draft is
 * durable when the browser rejected the write. */
{
  withLocalStorage(
    {
      getItem: () => null,
      setItem: () => {
        throw new Error("quota exceeded");
      },
    },
    () => {
      assert.equal(
        writeStashedDraft("s1:n1", { savedAt: 1, baseline: {}, draft: {} }),
        false,
      );
    },
  );
}

/* A record with no timestamp is treated as infinitely old rather than kept
 * forever, so a malformed write cannot pin a slot permanently. */
{
  const entries = {
    broken: { baseline: {}, draft: {} },
  } as unknown as StashMap;
  assert.deepEqual(Object.keys(pruneDraftStash(entries, 10_000)), []);
}

/* The three restore outcomes.
 *
 * `drop`: the stash says nothing new, so reopening the node must not claim a
 * restore happened. `adopt`: nobody moved the node, so the draft comes back
 * verbatim. `merge`: the node moved elsewhere while the draft sat in storage,
 * so the two must be reconciled rather than one overwriting the other. */
{
  assert.equal(
    stashRestoreDecision({
      stashedSignature: "a",
      baselineSignature: "a",
      persistedSignature: "a",
    }),
    "drop",
  );
  // A stash that has drifted back to matching the server is also nothing.
  assert.equal(
    stashRestoreDecision({
      stashedSignature: "a",
      baselineSignature: "b",
      persistedSignature: "a",
    }),
    "drop",
  );
  assert.equal(
    stashRestoreDecision({
      stashedSignature: "local",
      baselineSignature: "a",
      persistedSignature: "a",
    }),
    "adopt",
  );
  assert.equal(
    stashRestoreDecision({
      stashedSignature: "local",
      baselineSignature: "a",
      persistedSignature: "remote",
    }),
    "merge",
  );
}

/* The merge case end to end: a prompt typed and then lost, while the canvas
 * wired a dependency onto the same node in the meantime. Restoring must keep
 * both — this is the pairing that makes a stashed draft safe to reopen against
 * a node that has since moved. */
{
  const baseline = virtualDraftFromNode(node());
  const stashed = { ...baseline, promptDraft: "half-typed prompt" };
  const persisted = { ...baseline, scheduledDeps: ["dep-1"] };

  assert.equal(
    stashRestoreDecision({
      stashedSignature: JSON.stringify(stashed),
      baselineSignature: JSON.stringify(baseline),
      persistedSignature: JSON.stringify(persisted),
    }),
    "merge",
  );
  const { draft, conflicts } = mergeVirtualDraft(stashed, baseline, persisted);
  assert.equal(draft.promptDraft, "half-typed prompt");
  assert.deepEqual(draft.scheduledDeps, ["dep-1"]);
  assert.deepEqual(conflicts, []);
}

/* A stash whose shape predates the current draft type must be discarded, not
 * restored: saving it would post fields the API rejects with `extra="forbid"`,
 * turning a rescue into a 400 the user cannot act on. */
{
  const template = virtualDraftFromNode(node()) as unknown as Record<
    string,
    unknown
  >;
  assert.equal(draftShapeMatches({ ...template }, template), true);

  const { artifactSpec, ...missingKey } = template;
  void artifactSpec;
  assert.equal(draftShapeMatches(missingKey, template), false);
  assert.equal(
    draftShapeMatches({ ...template, unexpected: 1 }, template),
    false,
  );
  // Same keys, wrong kinds — an array where a string belongs would break the
  // editor before it ever reached the wire.
  assert.equal(
    draftShapeMatches({ ...template, promptDraft: ["x"] }, template),
    false,
  );
  assert.equal(
    draftShapeMatches({ ...template, scheduledDeps: "dep-1" }, template),
    false,
  );
  assert.equal(draftShapeMatches(null, template), false);
  assert.equal(draftShapeMatches([], template), false);
  assert.equal(draftShapeMatches("draft", template), false);
}

/* The autosave gate. Each `false` here is a request the timer must not send. */
{
  const base = {
    enabled: true,
    dirty: true,
    saving: false,
    incomplete: false,
    signature: "sig",
    lastAttemptSignature: null,
  };
  assert.equal(shouldAutosaveDraft(base), true);

  // Auto-mode lanes: saving there promotes and launches the node, so the timer
  // must never push. The local stash is the whole protection.
  assert.equal(shouldAutosaveDraft({ ...base, enabled: false }), false);
  // Nothing to write.
  assert.equal(shouldAutosaveDraft({ ...base, dirty: false }), false);
  // A write is already in flight; a second would race it.
  assert.equal(shouldAutosaveDraft({ ...base, saving: true }), false);
  // An empty prompt is someone mid-edit, not a failure to report.
  assert.equal(shouldAutosaveDraft({ ...base, incomplete: true }), false);
  // Unchanged since the last attempt: this is what stops a failing write from
  // retrying every 10s and pinning an error banner open.
  assert.equal(
    shouldAutosaveDraft({ ...base, lastAttemptSignature: "sig" }),
    false,
  );
  // An edit after that failure re-arms the timer for the new text.
  assert.equal(
    shouldAutosaveDraft({ ...base, lastAttemptSignature: "older" }),
    true,
  );
}

console.log("draft-stash: ok");
