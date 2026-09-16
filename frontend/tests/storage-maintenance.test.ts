import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { getMigrationPlan, ApiError } from "../src/api";
import { guidanceNotes, storageGuidance } from "../src/storageMaintenance";

/* Every `MigrationError.state` the backend can raise must render its own
   guidance; falling through to the generic text is the bug this covers. */
const STATES = [
  "migration_required",
  "schema_conflict",
  "waiting_for_idle",
  "schema_too_old",
  "schema_too_new",
  "migration_failed",
];

const titles = new Set<string>();
for (const state of STATES) {
  const guidance = storageGuidance(state);
  assert.ok(guidance.title.length > 0, `${state} has a title`);
  assert.ok(guidance.summary.length > 0, `${state} has a summary`);
  assert.ok(guidance.steps.length > 0, `${state} lists at least one step`);
  titles.add(guidance.title);
}
assert.equal(titles.size, STATES.length, "each state renders distinct copy");

/* The dead end this replaces: the page listed status/plan/recover, none of which
   clears `migration_required`, and never said the backend must be stopped. */
const required = storageGuidance("migration_required");
const requiredText = [...required.commands, ...required.steps].join("\n");
assert.ok(requiredText.includes("--accept-data-loss"), "names the only effective command");
assert.ok(requiredText.includes("停止"), "says the backend must be stopped");
assert.equal(required.requiresShutdown, true);
assert.ok(
  !required.commands.some((command) => command.includes("recover")),
  "does not send the user to recover, which hits the same gate",
);

/* `schema_too_old` is not fixed by `apply`, so it must not suggest it. */
const tooOld = storageGuidance("schema_too_old");
assert.ok(
  !tooOld.commands.some((command) => command.includes("--accept-data-loss")),
  "schema_too_old does not suggest apply",
);
assert.ok(tooOld.steps.join("\n").includes("中间版本"), "points at an intermediate version");

/* `schema_too_new` needs a program update, and nothing else. */
assert.equal(storageGuidance("schema_too_new").commands.length, 0);
assert.ok(storageGuidance("schema_too_new").steps.join("\n").includes("更新"));

/* `waiting_for_idle` is another process holding the storage: waiting is the
   fix, so the page must not send the user to the CLI. */
const busy = storageGuidance("waiting_for_idle");
assert.equal(busy.commands.length, 0, "waiting_for_idle lists no commands");
assert.equal(busy.requiresShutdown, false);

/* `migration_failed` keeps the originals; recover exports them, and it needs
   the backend stopped. */
const failed = storageGuidance("migration_failed");
assert.ok(failed.commands.join("\n").includes("recover --transaction"));
assert.equal(failed.requiresShutdown, true);
assert.ok(failed.steps.join("\n").includes("migration-backups"));

/* Only `apply`/`recover` take the coordinator lock, so only those pages explain
   the distinction; a page with no commands should not mention it. */
assert.ok(guidanceNotes(failed).some((note) => note.includes("抢锁")));
assert.ok(!guidanceNotes(busy).some((note) => note.includes("抢锁")));
assert.ok(guidanceNotes(busy).some((note) => note.includes("migration-backups")));

/* An unknown or absent state still has to produce usable, read-only guidance —
   an older backend, or a failure that is not a MigrationError. */
for (const unknown of [null, "something_new"]) {
  const guidance = storageGuidance(unknown);
  assert.equal(guidance.requiresShutdown, false, "the fallback never demands a shutdown");
  assert.ok(
    guidance.commands.every((command) => !command.includes("--accept-data-loss")),
    "the fallback never suggests a destructive confirmation",
  );
  assert.ok(guidance.steps.length > 0);
}

/* P2-1: the backend's `state` reaches the client. It used to be dropped by
   `ApiError`, which collapsed six distinct outcomes into one string. */
const originalFetch = globalThis.fetch;
try {
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ state: "schema_conflict", detail: "方向位置冲突：planspace:lane" }), {
      status: 409,
      headers: { "content-type": "application/json" },
    });
  await assert.rejects(getMigrationPlan(), (err: unknown) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.state, "schema_conflict");
    assert.equal(err.detail, "方向位置冲突：planspace:lane");
    assert.equal(storageGuidance(err.state).requiresShutdown, false);
    return true;
  });

  /* The 500 fallback handler's body must also branch, not read as a bare string. */
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ state: "internal_error", detail: "服务端处理请求时发生异常" }), {
      status: 500,
      headers: { "content-type": "application/json" },
    });
  await assert.rejects(getMigrationPlan(), (err: unknown) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.state, "internal_error");
    return true;
  });

  /* An older backend answers without `state`; `detail` must still survive. */
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: "旧后端的错误信息" }), {
      status: 409,
      headers: { "content-type": "application/json" },
    });
  await assert.rejects(getMigrationPlan(), (err: unknown) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.state, null);
    assert.equal(err.detail, "旧后端的错误信息");
    return true;
  });

  /* Plain-text bodies (a proxy, or a backend without the handler) still read. */
  globalThis.fetch = async () => new Response("Internal Server Error", { status: 500 });
  await assert.rejects(getMigrationPlan(), (err: unknown) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.state, null);
    assert.equal(err.detail, "Internal Server Error");
    return true;
  });

  /* A successful plan round-trips the fields the maintenance page renders. */
  globalThis.fetch = async () =>
    Response.json({
      source: 15, target: 17, minimum: 14,
      steps: [{ source: 15, target: 16, summary: "有损步骤", contract: "c16", destructive: true }],
      local_destructive_steps: [], sync_confirmation: ["有损步骤"],
      sync_confirmation_contracts: [{ source: 15, target: 16, summary: "有损步骤", contract: "c16", destructive: true }],
      sync_confirmation_note: "note", confirmation_hosts: [{ host_id: "h", label: "本机", local: true }],
      confirmation_hosts_note: "hosts", layout_impact: [], layout_recovery: [],
      layout_note: "layout", note: "只读预览",
    });
  const plan = await getMigrationPlan();
  assert.equal(plan.source, 15);
  assert.equal(plan.sync_confirmation_contracts[0].destructive, true);
  assert.equal(plan.confirmation_hosts[0].local, true);
} finally {
  globalThis.fetch = originalFetch;
}

/* The old page hardcoded three commands, none of which cleared the block. Guard
   against them returning as a literal block, and against the gate dropping
   `state` again. */
const page = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
assert.ok(!page.includes("miniclaw2 migrations status\\nminiclaw2 migrations plan"));
assert.ok(page.includes("StorageBlocked"), "the migration gate keeps the backend state");
assert.ok(page.includes("StorageMaintenance"), "the page delegates to the guidance component");

/* A sync failure raises the same `state` vocabulary as a blocked startup, so the
   settings panel must branch on it too. It used to flatten every failure with
   `String(err)` into one line of 11px red text, which is the dead end the whole
   presentation layer exists to remove: the user could not tell
   `migration_required` (stop the backend, confirm once) from `waiting_for_idle`
   (just wait), and retrying produced the identical string. */
const settings = readFileSync(
  new URL("../src/components/GlobalSettingsModal.tsx", import.meta.url),
  "utf8",
);
assert.ok(
  !/setSyncError\(String\(err\)\)/.test(settings),
  "the settings panel no longer flattens a sync failure into a bare string",
);
assert.ok(
  settings.includes("StorageGuidanceBody"),
  "the settings panel renders the same guidance body as the maintenance page",
);
assert.ok(
  settings.includes("err instanceof ApiError ? err.state"),
  "the settings panel keeps the backend `state` off the ApiError",
);

/* Both surfaces render one component, so a state cannot gain guidance on the
   maintenance page while staying a bare error string in settings. */
const body = readFileSync(
  new URL("../src/components/StorageGuidanceBody.tsx", import.meta.url),
  "utf8",
);
assert.ok(body.includes("guidance.steps"), "the shared body renders the steps");
assert.ok(body.includes("requiresShutdown"), "the shared body keeps the shutdown warning");
for (const surface of ["../src/components/StorageMaintenance.tsx", "../src/components/GlobalSettingsModal.tsx"]) {
  const source = readFileSync(new URL(surface, import.meta.url), "utf8");
  assert.ok(
    source.includes("StorageGuidanceBody"),
    `${surface} renders guidance through the shared body`,
  );
  assert.ok(
    !source.includes("guidance.commands.join"),
    `${surface} does not re-implement the command block`,
  );
}

/* The panel stays open on live data, so a sync failure must not tell the user to
   stop the backend unless that state genuinely requires it. */
assert.equal(storageGuidance("schema_conflict").requiresShutdown, false);
assert.equal(storageGuidance("waiting_for_idle").requiresShutdown, false);
assert.equal(storageGuidance("migration_required").requiresShutdown, true);

console.log("storage maintenance tests passed");