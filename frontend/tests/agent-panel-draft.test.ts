import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";

import {
  AgentPanel,
  agentInputText,
  candidateDependencies,
  mergeVirtualDraft,
  nodeIntentRows,
  virtualDraftAfterSave,
  virtualDraftFromNode,
  virtualDraftWithClassification,
  virtualDraftValidationError,
  virtualPayloadFromDraft,
  type AgentPanelProps,
} from "../src/panel/AgentPanel";
import { toNodeInfo } from "../src/nodeProjection";
import type { NodeDetail, NodeInfo } from "../src/types";

function node(over: Partial<NodeDetail> = {}): NodeDetail {
  return {
    id: "n1",
    project_id: "p1",
    kind: "agent",
    state: "virtual",
    provider: "claude",
    model_preset_id: "gpt-5.5",
    prompt: "",
    system_context_snapshot: "",
    launch_instructions_snapshot: "",
    prompt_draft: "do the thing",
    category: "regular",
    created_at: 0,
    ...over,
  } as NodeDetail;
}

{
  const cold = node({ agent_op_kind: "cold_start", artifact_mode: "svg" });
  const props: AgentPanelProps = {
    sessionId: "p1",
    node: cold,
    detail: cold,
    detailLoading: false,
    detailError: null,
    onRetryDetail: () => {},
    nodesById: new Map([[cold.id, cold]]),
    modelPresets: [],
    events: [],
    eventsLoading: false,
    diff: null,
    diffLoading: false,
    contextBundle: null,
    contextBundleLoading: false,
    pendingGate: null,
    pendingReview: null,
    onResolveReview: () => {},
    onCreateContinuationVirtual: () => {},
    onPromoteVirtual: async () => {},
    onDequeueNode: async () => {},
    onUpdateVirtual: async () => undefined,
    onInterruptNode: () => {},
    onRerunNode: () => {},
    canInterrupt: false,
    canRerun: false,
    canMutate: true,
    canDiffReview: true,
    mutationLock: null,
    isManualPlanspace: () => true,
    focusRequestVersion: 0,
    activityFocusRequestVersion: 0,
    onSelectArtifact: () => {},
  };
  const markup = renderToStaticMarkup(createElement(AgentPanel, props));
  for (const label of ["Default", "MD", "HTML", "SVG", "Custom"]) {
    assert.match(markup, new RegExp(`<button[^>]*>${label}</button>`));
  }
  assert.match(markup, /<button[^>]*class="[^"]*bg-surface-raised[^"]*">SVG<\/button>/);
  assert.doesNotMatch(markup, />Q\/A 模式</);
  assert.doesNotMatch(markup, />Dependencies</);
  assert.match(markup, /你可以显式选择产物模式/);
  assert.doesNotMatch(markup, /产出物约定都不可用/);
  const loadingMarkup = renderToStaticMarkup(createElement(AgentPanel, {
    ...props, node: toNodeInfo(cold), detail: null, detailLoading: true,
  }));
  assert.match(loadingMarkup, /正在读取节点详情/);
  assert.match(loadingMarkup, /do the thing/);
  assert.doesNotMatch(loadingMarkup, /<textarea/);
  assert.match(loadingMarkup, /<button[^>]*disabled=""[^>]*>Promote<\/button>/);
  const errorMarkup = renderToStaticMarkup(createElement(AgentPanel, {
    ...props, node: toNodeInfo(cold), detail: null, detailError: "详情读取失败",
  }));
  assert.match(errorMarkup, /详情读取失败/);
  assert.match(errorMarkup, /重试/);
  assert.match(errorMarkup, /<button[^>]*disabled=""[^>]*>Promote<\/button>/);
  const refreshingMarkup = renderToStaticMarkup(createElement(AgentPanel, {
    ...props, detailLoading: true,
  }));
  assert.match(refreshingMarkup, /<textarea/);
  assert.doesNotMatch(refreshingMarkup, /正在读取节点详情/);
  assert.doesNotMatch(refreshingMarkup, /<fieldset[^>]*disabled/);
  const refreshErrorMarkup = renderToStaticMarkup(createElement(AgentPanel, {
    ...props, detailError: "详情读取失败",
  }));
  assert.doesNotMatch(refreshErrorMarkup, /<fieldset[^>]*disabled/);
  const readOnlyMarkup = renderToStaticMarkup(createElement(AgentPanel, {
    ...props, canMutate: false,
  }));
  assert.match(readOnlyMarkup, /<fieldset[^>]*disabled/);
}

/* The inspector separates provider system context, MiniClaw's per-node rules,
 * and the user's prompt. The launch snapshot includes category-specific
 * preview/artifact rules and must win over the older turn-text fallback. */
{
  const input = agentInputText(
    node({
      state: "done",
      prompt: "implement the feature",
      system_context_snapshot: "project context",
      launch_instructions_snapshot:
        "preview.json contract\nPublishing artifacts",
    }),
    {
      bundle_id: "bundle-1",
      created_at: 0,
      sources: [],
      system_text: "full system context",
      turn_text: "principle turn injection",
    },
  );
  assert.deepEqual(input, {
    systemText: "full system context",
    nodeInstructions: "preview.json contract\nPublishing artifacts",
    userPrompt: "implement the feature",
  });
}

/* Nodes launched before launch-instruction snapshots existed still expose
 * their recorded turn injection instead of showing an empty disclosure. */
{
  const input = agentInputText(node({ state: "done" }), {
    bundle_id: "bundle-1",
    created_at: 0,
    sources: [],
    turn_text: "legacy turn injection",
  });
  assert.equal(input.nodeInstructions, "legacy turn injection");
}

/* A node the backend has already normalized must round-trip unchanged. The
 * dirty check compares whole-object JSON, so both a missing key and a
 * different key order read as an unsaved edit and leave the panel permanently
 * showing a Save button that changes nothing. */
{
  for (const sample of [
    node(),
    node({ artifact_mode: "markdown" }),
    node({ artifact_mode: "custom", artifact_spec: "one table" }),
    node({ qa_mode: true }),
    node({ category: "planning", artifact_mode: "html", qa_mode: true }),
    ...(["default", "markdown", "html", "svg", "custom"] as const).map(
      (mode) => node({
        agent_op_kind: "cold_start",
        artifact_mode: mode,
        artifact_spec: mode === "custom" ? "一份风险清单" : "",
      }),
    ),
    node({
      category: "review",
      subtype: "agentic_review",
      brief: { check_what: "c", expected: "e", abnormal: "a" },
    }),
  ]) {
    const draft = virtualDraftFromNode(sample);
    assert.equal(
      JSON.stringify(virtualDraftAfterSave(draft)),
      JSON.stringify(draft),
      `draft is falsely dirty for ${JSON.stringify(sample.artifact_mode)}`,
    );
  }
}

/* Absent wire fields are the historical shape, not a third state. */
{
  const draft = virtualDraftFromNode(node());
  assert.equal(draft.artifactMode, "default");
  assert.equal(draft.artifactSpec, "");
  assert.equal(draft.qaMode, false);
  assert.equal(draft.diffReview, false);
}

/* 工作、规划和冷启动节点的所有产物模式均应原样进入请求。 */
{
  for (const classification of ["work", "planning", "cold"] as const) {
    for (const mode of ["default", "markdown", "html", "svg", "custom"] as const) {
      const draft = {
        ...virtualDraftFromNode(node()),
        classification,
        artifactMode: mode,
        artifactSpec: "  一份风险清单  ",
        qaMode: true,
      };
      const payload = virtualPayloadFromDraft(draft, node());
      assert.equal(payload.artifact_mode, mode);
      assert.equal(payload.artifact_spec, mode === "custom" ? "一份风险清单" : "");
      assert.equal(payload.qa_mode, classification !== "cold");
    }
  }
}

/* Switching a work node that carried an artifact intent over to review has to
 * zero both fields in the same payload. Sending the new category while leaving
 * the old intent set trips the backend's paired invariant, and the user sees a
 * 400 that names artifact_mode after an action about classification. */
{
  const draft = {
    ...virtualDraftFromNode(node()),
    classification: "review" as const,
    subtype: "agentic_review" as const,
    brief: { check_what: "c", expected: "e", abnormal: "a" },
    artifactMode: "markdown" as const,
    artifactSpec: "leftover",
    qaMode: true,
    diffReview: true,
  };
  const payload = virtualPayloadFromDraft(draft, node());
  assert.equal(payload.category, "review");
  assert.equal(payload.artifact_mode, "default");
  assert.equal(payload.artifact_spec, "");
  assert.equal(payload.qa_mode, false);
  assert.equal(payload.diff_review, false);
}

/* Library nodes get no artifact intent — their deliverable is one library
 * entry — but they may still need to ask which entry the user meant. */
{
  const draft = {
    ...virtualDraftFromNode(node()),
    classification: "library" as const,
    artifactMode: "html" as const,
    qaMode: true,
    diffReview: true,
  };
  const payload = virtualPayloadFromDraft(draft, node());
  assert.equal(payload.artifact_mode, "default");
  assert.equal(payload.qa_mode, true);
}

/* Custom sends a trimmed spec; every other mode sends none, so the client
 * never has to reason about when the backend will clear it. */
{
  const base = virtualDraftFromNode(node());
  const custom = virtualPayloadFromDraft(
    { ...base, artifactMode: "custom", artifactSpec: "  three files  " },
    node(),
  );
  assert.equal(custom.artifact_spec, "three files");

  const html = virtualPayloadFromDraft(
    { ...base, artifactMode: "html", artifactSpec: "stray" },
    node(),
  );
  assert.equal(html.artifact_spec, "");
}

/* An empty custom spec is an incomplete form, not a judgment about the work:
 * the prompt block would render an empty requirement. Catching it here turns a
 * backend 400 into a sentence next to the field. */
{
  const base = virtualDraftFromNode(node());
  assert.match(
    virtualDraftValidationError(
      { ...base, artifactMode: "custom", artifactSpec: "   " },
      node(),
    ) ?? "",
    /custom/,
  );
  assert.equal(
    virtualDraftValidationError(
      { ...base, artifactMode: "custom", artifactSpec: "a table" },
      node(),
    ),
    null,
  );
  // The same empty spec on a review node is unreachable state, not an error.
  assert.equal(
    virtualDraftValidationError(
      {
        ...base,
        classification: "review",
        subtype: "code_review",
        artifactMode: "custom",
        artifactSpec: "",
      },
      node(),
    ),
    null,
  );
}

/* Normalization after save mirrors what the backend does, including the
 * classification-driven zeroing. */
{
  const normalized = virtualDraftAfterSave({
    ...virtualDraftFromNode(node()),
    classification: "review",
    artifactMode: "markdown",
    artifactSpec: "leftover",
    qaMode: true,
  });
  assert.equal(normalized.artifactMode, "default");
  assert.equal(normalized.artifactSpec, "");
  assert.equal(normalized.qaMode, false);
  assert.equal(normalized.diffReview, false);
}

/* The canvas writes scheduled_deps alone. Reconciling that against a draft
 * whose prompt is still being typed must keep both: adopting the whole
 * persisted object would discard the unsaved prompt, which is the data loss
 * that drag-to-connect would otherwise ship with. */
{
  const persistedBefore = virtualDraftFromNode(node());
  const persistedAfter = { ...persistedBefore, scheduledDeps: ["dep-1"] };
  const local = { ...persistedBefore, promptDraft: "half-typed prompt" };

  const { draft, conflicts } = mergeVirtualDraft(
    local,
    persistedBefore,
    persistedAfter,
  );
  assert.deepEqual(draft.scheduledDeps, ["dep-1"]);
  assert.equal(draft.promptDraft, "half-typed prompt");
  assert.deepEqual(conflicts, []);
}

/* When the remote moved nothing, the merge must be a no-op down to the byte —
 * this is what makes a canvas write invisible to the side panel. */
{
  const persisted = virtualDraftFromNode(node());
  const local = { ...persisted, promptDraft: "mine", motivation: "why" };
  const { draft, conflicts } = mergeVirtualDraft(local, persisted, persisted);
  assert.equal(JSON.stringify(draft), JSON.stringify(local));
  assert.deepEqual(conflicts, []);
}

/* A field both sides moved resolves to the persisted value — the only
 * authoritative copy — but never silently: the field is reported so the panel
 * can say which edit it replaced. */
{
  const persistedBefore = virtualDraftFromNode(node());
  const persistedAfter = { ...persistedBefore, promptDraft: "theirs" };
  const local = { ...persistedBefore, promptDraft: "mine" };
  const { draft, conflicts } = mergeVirtualDraft(
    local,
    persistedBefore,
    persistedAfter,
  );
  assert.equal(draft.promptDraft, "theirs");
  assert.deepEqual(conflicts, ["promptDraft"]);
}

/* A field the user never touched adopts the remote value quietly: there is no
 * local edit to warn about. */
{
  const persistedBefore = virtualDraftFromNode(node());
  const persistedAfter = { ...persistedBefore, motivation: "server reason" };
  const local = { ...persistedBefore, promptDraft: "mine" };
  const { draft, conflicts } = mergeVirtualDraft(
    local,
    persistedBefore,
    persistedAfter,
  );
  assert.equal(draft.motivation, "server reason");
  assert.equal(draft.promptDraft, "mine");
  assert.deepEqual(conflicts, []);
}

/* Object and array fields compare by value, not identity, so a re-fetched but
 * unchanged brief must not read as a remote edit. */
{
  const persistedBefore = virtualDraftFromNode(
    node({ brief: { check_what: "c", expected: "e", abnormal: "a" } }),
  );
  const persistedAfter = virtualDraftFromNode(
    node({ brief: { check_what: "c", expected: "e", abnormal: "a" } }),
  );
  const local = { ...persistedBefore, promptDraft: "mine" };
  const { draft, conflicts } = mergeVirtualDraft(
    local,
    persistedBefore,
    persistedAfter,
  );
  assert.equal(draft.promptDraft, "mine");
  assert.deepEqual(conflicts, []);
}

/* Obsolete nodes are kept out of the dependency picker so they are not newly
 * chosen — but one already in the draft must stay listed, or its id sits in
 * scheduled_deps with no checkbox left to clear it. */
{
  const target = node({ id: "target", planspace_id: "lane-a" });
  const nodesById = new Map<string, NodeInfo>([
    ["target", target],
    ["plain", node({ id: "plain", planspace_id: "lane-a" })],
    [
      "stale",
      node({ id: "stale", planspace_id: "lane-a", obsolete_reason: "gone" }),
    ],
    [
      "picked",
      node({ id: "picked", planspace_id: "lane-a", obsolete_reason: "gone" }),
    ],
    ["other-lane", node({ id: "other-lane", planspace_id: "lane-b" })],
  ]);

  const ids = candidateDependencies(target, nodesById, ["picked"]).map(
    (candidate) => candidate.id,
  );
  assert.deepEqual(ids.sort(), ["picked", "plain"]);
}

/* A cold start is carried by agent_op_kind while its category stays regular.
 * Editing one must preserve that marker: the earlier payload builder sent
 * agent_op_kind: null for everything that was not a library node, which would
 * have silently turned a cold start into an ordinary work node on the first
 * unrelated edit. */
{
  const cold = node({ agent_op_kind: "cold_start" });
  const draft = virtualDraftFromNode(cold);
  assert.equal(draft.classification, "cold");
  const payload = virtualPayloadFromDraft(draft, cold);
  assert.equal(payload.category, "regular");
  assert.equal(payload.agent_op_kind, undefined);
}

/* The three fields a cold start rejects are all injected prompt. The payload
 * zeroes them rather than forwarding a draft the backend would 400 on. */
{
  const draft = {
    ...virtualDraftFromNode(node()),
    classification: "cold" as const,
    scheduledDeps: ["upstream"],
    pendingExtraPrinciples: ["principles.evidence"],
    pendingExtraSkills: [{ id: "skills.release" }],
    qaMode: true,
    artifactMode: "markdown" as const,
    artifactSpec: "leftover",
  };
  const payload = virtualPayloadFromDraft(draft, node());
  assert.equal(payload.agent_op_kind, "cold_start");
  assert.equal(payload.category, "regular");
  assert.deepEqual(payload.scheduled_deps, []);
  assert.deepEqual(payload.pending_extra_principles, []);
  assert.equal(payload.qa_mode, false);
  assert.equal(payload.artifact_mode, "markdown");
  assert.equal(payload.artifact_spec, "");
  // Skills stay: mounting one supplies a capability, it injects no prompt.
  assert.deepEqual(payload.pending_extra_skills, [{ id: "skills.release" }]);
}

/* 切换到冷启动时清空隐式上下文设置，但保留用户显式选择的产物模式。 */
{
  const draft = {
    ...virtualDraftFromNode(node()),
    scheduledDeps: ["upstream"],
    pendingExtraPrinciples: ["principles.evidence"],
    qaMode: true,
    artifactMode: "svg" as const,
    artifactSpec: "stale spec",
  };
  const cold = virtualDraftWithClassification(draft, "cold");
  assert.deepEqual(cold.scheduledDeps, []);
  assert.deepEqual(cold.pendingExtraPrinciples, []);
  assert.equal(cold.qaMode, false);
  assert.equal(cold.artifactMode, "svg");
  assert.equal(virtualDraftAfterSave(cold).artifactSpec, "");
  assert.deepEqual(cold.pendingExtraSkills, draft.pendingExtraSkills);
}

{
  const coldNode = node({ agent_op_kind: "cold_start" });
  const draft = {
    ...virtualDraftFromNode(node()),
    artifactMode: "custom" as const,
    artifactSpec: "  一份风险清单  ",
  };
  const cold = virtualDraftWithClassification(draft, "cold");
  assert.equal(cold.artifactMode, "custom");
  assert.equal(cold.artifactSpec, draft.artifactSpec);
  assert.equal(virtualDraftAfterSave(cold).artifactSpec, "一份风险清单");
  assert.equal(virtualDraftValidationError(cold, coldNode), null);
  assert.match(
    virtualDraftValidationError({ ...cold, artifactSpec: "  " }, coldNode) ?? "",
    /选择 custom 时请先描述期望的产出物/,
  );
  const work = virtualDraftWithClassification(cold, "work");
  assert.equal(work.artifactMode, "custom");
  assert.equal(work.artifactSpec, draft.artifactSpec);
}

/* A stale/restored draft cannot bypass the disabled Cold control on a
 * continuation virtual. Validation blocks autosave, Save, and Promote too. */
{
  const continuation = node({ resume_from_node_id: "source" });
  const cold = virtualDraftWithClassification(
    virtualDraftFromNode(continuation),
    "cold",
  );
  assert.match(
    virtualDraftValidationError(cold, continuation) ?? "",
    /延续节点不能使用冷启动/,
  );
}

/* Turning a cold start back into ordinary work must clear the marker, and a
 * historical principle_edit node must not be migrated to library_edit by an
 * unrelated edit. */
{
  const cold = node({ agent_op_kind: "cold_start" });
  const toWork = { ...virtualDraftFromNode(cold), classification: "work" as const };
  assert.equal(virtualPayloadFromDraft(toWork, cold).agent_op_kind, null);

  const legacy = node({ agent_op_kind: "principle_edit" });
  const legacyDraft = virtualDraftFromNode(legacy);
  assert.equal(legacyDraft.classification, "library");
  assert.equal(
    virtualPayloadFromDraft(legacyDraft, legacy).agent_op_kind,
    undefined,
  );
}

/* The artifact contract and Q/A mode decide what a node was asked to deliver,
 * but on an executed node the draft form is gone and they were previously
 * unreadable anywhere in the UI. Basic information carries them so a finished
 * node that published nothing can be told apart from one never asked to. */
{
  assert.deepEqual(nodeIntentRows(node({ state: "done" })), [
    ["Artifact", "不要求产出物"],
    ["Q/A mode", "关闭"],
    ["Diff review", "关闭"],
  ]);
  assert.deepEqual(
    nodeIntentRows(node({ state: "done", artifact_mode: "html", qa_mode: true })),
    [
      ["Artifact", "HTML"],
      ["Q/A mode", "开启"],
      ["Diff review", "关闭"],
    ],
  );
}

/* A custom spec is agent-facing prose that may run to several lines. It is
 * flattened to a single line because the row sits in a 120px/1fr grid whose
 * other values are single tokens. */
{
  assert.deepEqual(
    nodeIntentRows(
      node({
        state: "done",
        artifact_mode: "custom",
        artifact_spec: " a summary\nand  a risk list ",
      }),
    ),
    [
      ["Artifact", "自定义"],
      ["Artifact spec", "a summary and a risk list"],
      ["Q/A mode", "关闭"],
      ["Diff review", "关闭"],
    ],
  );
  // Custom without a spec is unreachable on a saved node; no empty row either way.
  assert.deepEqual(
    nodeIntentRows(node({ state: "done", artifact_mode: "custom" })),
    [
      ["Artifact", "自定义"],
      ["Q/A mode", "关闭"],
      ["Diff review", "关闭"],
    ],
  );
}

/* 审阅不展示这两项设置；库节点仅展示 Q/A，冷启动仅展示产物要求。 */
{
  assert.deepEqual(
    nodeIntentRows(
      node({ state: "done", category: "review", subtype: "agentic_review" }),
    ),
    [],
  );
  assert.deepEqual(
    nodeIntentRows(node({ state: "done", agent_op_kind: "library_edit" })),
    [["Q/A mode", "关闭"], ["Diff review", "关闭"]],
  );
  assert.deepEqual(
    nodeIntentRows(node({ state: "done", agent_op_kind: "cold_start" })),
    [["Artifact", "不要求产出物"], ["Diff review", "关闭"]],
  );
  assert.deepEqual(
    nodeIntentRows(node({ state: "done", agent_op_kind: "cold_start", artifact_mode: "svg" })),
    [["Artifact", "SVG"], ["Diff review", "关闭"]],
  );
  assert.deepEqual(
    nodeIntentRows(node({
      state: "done",
      agent_op_kind: "cold_start",
      artifact_mode: "custom",
      artifact_spec: "一份风险清单",
    })),
    [["Artifact", "自定义"], ["Artifact spec", "一份风险清单"], ["Diff review", "关闭"]],
  );
  assert.deepEqual(
    nodeIntentRows(node({ state: "done", category: "planning", qa_mode: true })),
    [
      ["Artifact", "不要求产出物"],
      ["Q/A mode", "开启"],
      ["Diff review", "关闭"],
    ],
  );
}

/* A virtual node renders both fields as live controls in the draft editor
 * below. The card reads the persisted node, so repeating them here would
 * contradict the form for as long as an edit stayed unsaved. */
{
  assert.deepEqual(
    nodeIntentRows(node({ artifact_mode: "markdown", qa_mode: true })),
    [],
  );
}

console.log("agent-panel-draft: ok");
