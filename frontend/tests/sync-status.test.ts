import assert from "node:assert/strict";

import { hostStatusText } from "../src/components/SyncCenter";
import { hostGlyph, hostName } from "../src/hostIcons";
import { formatAge, projectDebtText, syncChips, titleMark } from "../src/syncStatus";
import type { HostEntry, ProjectSyncEntry, SyncOverview } from "../src/types";

const NOW = 1_800_000_000;

function overview(overrides: {
  metadata?: Partial<SyncOverview["metadata"]>;
  source?: Partial<SyncOverview["source"]>;
  projects?: ProjectSyncEntry[];
} = {}): SyncOverview {
  const projects = overrides.projects ?? [];
  return {
    generated_at: NOW,
    metadata: {
      state: "clean", unpushed_commits: 0, unpushed_since: null, since: null, dirty: false,
      last_sync_at: NOW - 60, last_sync_failed: false, remote_ahead: 0,
      remote_probe: { enabled: true, failures: 0 }, overdue_after_seconds: 3 * 86400,
      ...overrides.metadata,
    },
    projects,
    projects_overdue: projects.filter((entry) => entry.state === "overdue").length,
    projects_pending: projects.filter((entry) => entry.state === "pending").length,
    project_overdue_after_seconds: 86400,
    source: {
      state: "current", behind: 0, ahead: 0, dirty: false, head: "a".repeat(40), upstream: "origin/main",
      commits: [], error: null, probe: { enabled: true, failures: 0 }, daemon: { active: true },
      ...overrides.source,
    },
    settings: {
      metadata_overdue_hours: 72, project_overdue_hours: 24, metadata_probe_enabled: true,
      metadata_probe_minutes: 10, source_probe_enabled: true, source_probe_minutes: 30,
    },
  };
}

function project(state: ProjectSyncEntry["state"], extra: Partial<ProjectSyncEntry> = {}): ProjectSyncEntry {
  return {
    id: state, name: state, state, dirty_count: 0, ahead: 0, behind: 0, branch: "main",
    upstream: "origin/main", no_upstream: false, since: null, reasons: [], scanned_at: NOW, error: null,
    ...extra,
  };
}

// Nothing to say when everything is pushed and current.
assert.deepEqual(syncChips(overview(), NOW), []);
assert.equal(titleMark(overview()), "");

// Pending metadata debt is a quiet grey count, never a red dot.
const pending = syncChips(overview({ metadata: { state: "pending", unpushed_commits: 27, since: NOW - 3600 } }), NOW);
assert.equal(pending.length, 1);
assert.equal(pending[0].tone, "quiet");
assert.equal(pending[0].label, "↑27");
assert.equal(titleMark(overview({ metadata: { state: "pending" } })), "");

// Overdue turns red and says how long.
const overdue = syncChips(overview({ metadata: { state: "overdue", unpushed_commits: 3, since: NOW - 4 * 86400 } }), NOW);
assert.equal(overdue[0].tone, "alert");
assert.match(overdue[0].label, /4 天/);
assert.equal(titleMark(overview({ metadata: { state: "overdue" } })), "● ");

// Projects: only overdue ones alert; pending ones are counted quietly.
const projects = syncChips(overview({
  projects: [project("overdue", { dirty_count: 5, ahead: 2, since: NOW - 26 * 3600 }), project("pending"), project("clean")],
}), NOW);
assert.equal(projects[0].key, "projects");
assert.equal(projects[0].tone, "alert");
assert.equal(projects[0].label, "1 个项目待推送");
assert.equal(
  projectDebtText(project("overdue", { dirty_count: 5, ahead: 2, since: NOW - 26 * 3600 }), NOW),
  "未提交 5 个文件 · 未推送 2 个提交 · 最早 26 小时前",
);
assert.equal(projectDebtText(project("clean", { no_upstream: true, upstream: null }), NOW), "当前分支未配置 upstream");

// A new release is a brand pill when it can be applied, muted when blocked.
const release = syncChips(overview({ source: { state: "available", behind: 3 } }), NOW);
assert.deepEqual(release.map((chip) => [chip.key, chip.tone, chip.label]), [["source", "brand", "新版本 · 3"]]);
const blocked = syncChips(overview({ source: { state: "blocked", behind: 2, dirty: true } }), NOW);
assert.equal(blocked[0].tone, "muted");
assert.match(blocked[0].title, /未提交改动/);

assert.equal(formatAge(30), "刚刚");
assert.equal(formatAge(3 * 3600), "3 小时");
assert.equal(formatAge(5 * 86400), "5 天");

// Host vocabulary: unknown keys fall back instead of throwing.
assert.equal(hostGlyph("laptop").key, "laptop");
assert.equal(hostGlyph("hovercraft").key, "server");
assert.equal(hostName({ mid: "0123456789abcdef", label: "" }), "01234567");

function host(overrides: Partial<HostEntry>): HostEntry {
  return {
    machine_id: "peer", is_self: false, label: "实验室", icon: null, color: null, hostname: null,
    has_profile: true, beacon: null, history: [], status: "no_beacon", behind: null, missing_by: [],
    includes_my_push: null, source: null, last_push_at: null, stale: false, ...overrides,
  };
}
const beacon = {
  sha: "b", integrated: "c".repeat(40), synced_at: NOW - 600, contributed: 1, store_schema: 23,
  miniclaw: {}, first_seen_at: NOW - 500, clock_skew: false,
};
assert.equal(hostStatusText(host({ is_self: true }), NOW).text, "本机");
assert.match(hostStatusText(host({ last_push_at: NOW - 5 * 86400 }), NOW).text, /无信标.*5 天前（推断）/);
const current = hostStatusText(host({ beacon, status: "current", includes_my_push: true, behind: 0 }), NOW);
assert.equal(current.tone, "ok");
assert.match(current.text, /已含本机最近推送/);
const behind = hostStatusText(host({
  beacon, status: "behind", behind: 41, includes_my_push: false,
  missing_by: [{ machine_id: "me", count: 27, label: "办公室", is_self: true }, { machine_id: "lab", count: 14, label: "实验室", is_self: false }],
}), NOW);
assert.equal(behind.tone, "warn");
assert.match(behind.text, /落后 41（本机 27 · 实验室 14） · 尚未拉取本机推送/);

console.log("sync status tests passed");
