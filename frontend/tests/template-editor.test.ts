import assert from "node:assert/strict";
import {
  addInputPort,
  addNode,
  argumentReferences,
  buildRewritePayload,
  connectInputPort,
  connectNodes,
  disconnectInputPort,
  disconnectNodes,
  inputReferences,
  isDirty,
  layoutTemplateGraph,
  pruneArguments,
  removeInputPort,
  removeNode,
  renameInputPort,
  resolveArguments,
  scanPlaceholders,
  setResumeFrom,
  templateEditorStateFromDetail,
  topologicalOrder,
  updateNode,
  upsertArgument,
  validateEditorState,
  warningNames,
  type EditorNode,
  type TemplateEditorState,
} from "../src/templateEditor";
import type { TemplateDetail } from "../src/types";
import { resolvedTemplateNodeModelPresetId } from "../src/templateModels";

function node(id: string, overrides: Partial<EditorNode> = {}): EditorNode {
  return {
    id,
    kind: "agent",
    category: "regular",
    subtype: null,
    brief: null,
    prompt: "",
    motivation: "",
    scheduled_deps: [],
    resume_from: null,
    model_preset_id: null,
    ...overrides,
  };
}

function state(overrides: Partial<TemplateEditorState> = {}): TemplateEditorState {
  return {
    slug: "demo",
    name: "Demo",
    brief: "",
    nodes: [node("n0")],
    arguments: [],
    inputs: [],
    warnings: [],
    ...overrides,
  };
}

function ok(result: TemplateEditorState | string): TemplateEditorState {
  assert.equal(typeof result, "object", `expected success, got: ${result}`);
  return result as TemplateEditorState;
}

function err(result: TemplateEditorState | string): string {
  assert.equal(typeof result, "string", "expected a rejection message");
  return result as string;
}

/* ───────── loading ───────── */

function testDetailLoadsPromptSourceNotThePreview() {
  // prompt_preview is a 160-char truncation. Editing it would silently
  // destroy the tail of every long prompt, so only `prompt` is read.
  const long = `${"x".repeat(400)} {{topic}}`;
  const detail: TemplateDetail = {
    slug: "demo",
    name: "Demo",
    brief: "brief",
    allowed_model_preset_ids: ["claude-sonnet"],
    auto_commit: false,
    node_count: 1,
    schema_version: 2,
    arguments: [
      { name: "topic", description: "主题", default: null, required: true, declared: true },
    ],
    inputs: [{ name: "alpha_branch", description: "" }],
    warnings: [],
    nodes: [
      {
        id: "n0",
        kind: "agent",
        category: "regular",
        scheduled_deps: ["in:alpha_branch"],
        resume_from: null,
        prompt_preview: long.slice(0, 160),
        prompt: long,
        motivation: "保留原始节点标签",
      },
    ],
  };
  const loaded = templateEditorStateFromDetail(detail);
  assert.equal(loaded.nodes[0].prompt, long);
  assert.equal(loaded.nodes[0].motivation, "保留原始节点标签");
  assert.deepEqual(loaded.nodes[0].scheduled_deps, ["in:alpha_branch"]);
  assert.equal(loaded.arguments[0].declared, true);
  assert.equal(loaded.inputs[0].name, "alpha_branch");
}

function testDetailWithoutPromptLoadsEmptyRatherThanTruncated() {
  const detail: TemplateDetail = {
    slug: "demo",
    name: "Demo",
    brief: "",
    allowed_model_preset_ids: [],
    auto_commit: false,
    node_count: 1,
    schema_version: 2,
    arguments: [],
    inputs: [],
    warnings: [],
    nodes: [
      {
        id: "n0",
        kind: "agent",
        category: "regular",
        prompt_preview: "truncated preview text",
      },
    ],
  };
  assert.equal(templateEditorStateFromDetail(detail).nodes[0].prompt, "");
}

/* ───────── placeholder scanning ───────── */

function testScanMirrorsTheLoaderRules() {
  const scanned = scanPlaceholders(
    "{{topic}} {{ report_style }} {{input.alpha}} {{Bad-Name}} {{topic}} {{input.Bad}}",
  );
  // Whitespace inside braces is trimmed; duplicates collapse; anything not
  // matching the name pattern stays literal text and is never collected.
  assert.deepEqual(scanned.argumentNames, ["topic", "report_style"]);
  assert.deepEqual(scanned.inputPorts, ["alpha"]);
}

function testReferencesReportWhichNodesUseEachName() {
  const nodes = [
    node("n0", { prompt: "关于 {{topic}}，见 {{input.alpha}}" }),
    node("n1", { prompt: "再看 {{topic}}", scheduled_deps: ["in:alpha", "n0"] }),
    node("n2", { prompt: "无占位符" }),
  ];
  assert.deepEqual(argumentReferences(nodes), { topic: ["n0", "n1"] });
  // n1 references the port two ways (dep + placeholder) but appears once.
  assert.deepEqual(inputReferences(nodes), { alpha: ["n0", "n1"] });
}

/* ───────── arguments ───────── */

function testScannedArgumentsAppearUndeclaredAfterDeclaredOnes() {
  const resolved = resolveArguments(
    state({
      arguments: [
        { name: "report_style", description: "风格", default: "简洁", declared: true },
      ],
      nodes: [node("n0", { prompt: "{{topic}} {{report_style}}" })],
    }),
  );
  assert.deepEqual(
    resolved.map((argument) => [argument.name, argument.declared]),
    [
      ["report_style", true],
      ["topic", false],
    ],
  );
  // A scanned argument has no default, which is exactly "required".
  assert.equal(resolved[1].default, null);
}

function testMissingDefaultAndEmptyDefaultStayDistinct() {
  const base = state({ nodes: [node("n0", { prompt: "{{topic}}" })] });
  const required = buildRewritePayload(base).arguments[0];
  assert.equal(required.default, null, "no default means required");

  const optional = buildRewritePayload(
    ok(upsertArgument(base, "topic", { default: "" })),
  ).arguments[0];
  assert.equal(optional.default, "", "empty string is an optional value");
}

function testEditingAScannedArgumentDeclaresIt() {
  const edited = upsertArgument(
    state({ nodes: [node("n0", { prompt: "{{topic}}" })] }),
    "topic",
    { description: "对比主题" },
  );
  assert.equal(edited.arguments.length, 1);
  assert.equal(edited.arguments[0].declared, true);
  assert.equal(edited.arguments[0].description, "对比主题");
}

function testEditingAnUnknownArgumentIsANoOp() {
  const base = state();
  assert.equal(upsertArgument(base, "nope", { description: "x" }), base);
}

function testDanglingCleanupUsesBackendWarningNames() {
  const base = state({
    arguments: [
      { name: "stale", description: "", default: null, declared: true },
      { name: "topic", description: "", default: null, declared: true },
    ],
    nodes: [node("n0", { prompt: "{{topic}}" })],
    warnings: [
      { code: "dangling_argument", name: "stale", message: "..." },
      { code: "unreferenced_input", name: "alpha", message: "..." },
    ],
  });
  assert.deepEqual(warningNames(base.warnings, "dangling_argument"), ["stale"]);

  const pruned = pruneArguments(base, warningNames(base.warnings, "dangling_argument"));
  assert.deepEqual(pruned.arguments.map((argument) => argument.name), ["topic"]);
  // Only the resolved warning clears; the unrelated one survives.
  assert.deepEqual(pruned.warnings.map((warning) => warning.code), ["unreferenced_input"]);
  assert.equal(pruneArguments(base, []), base);
}

/* ───────── input ports ───────── */

function testPortNamesFollowTheLoaderPattern() {
  const base = state({ inputs: [{ name: "alpha", description: "" }] });
  assert.match(err(addInputPort(base, "Alpha")), /\[a-z\]/);
  assert.match(err(addInputPort(base, "1alpha")), /\[a-z\]/);
  assert.match(err(addInputPort(base, "")), /\[a-z\]/);
  assert.match(err(addInputPort(base, "alpha")), /已存在/);
  assert.deepEqual(
    ok(addInputPort(base, " beta_2 ")).inputs.map((input) => input.name),
    ["alpha", "beta_2"],
  );
}

function testRenamingAPortRewritesBothReferenceForms() {
  const base = state({
    inputs: [{ name: "alpha", description: "" }],
    nodes: [
      node("n0", {
        prompt: "alpha 结果：{{input.alpha}}，另见 {{ input.alpha }} 与 {{alpha}}",
        scheduled_deps: ["in:alpha"],
      }),
    ],
  });
  const renamed = ok(renameInputPort(base, "alpha", "alpha_branch"));
  assert.deepEqual(renamed.nodes[0].scheduled_deps, ["in:alpha_branch"]);
  assert.match(renamed.nodes[0].prompt, /\{\{input\.alpha_branch\}\}/);
  assert.equal(renamed.nodes[0].prompt.includes("input.alpha}}"), false);
  // `{{alpha}}` is an argument, not the port — a shared spelling in two
  // independent namespaces must survive the rename untouched.
  assert.match(renamed.nodes[0].prompt, /\{\{alpha\}\}/);
  // Which means the rename left the template loadable.
  assert.deepEqual(validateEditorState(renamed), []);
}

function testRenameRejectsIllegalAndCollidingNames() {
  const base = state({
    inputs: [
      { name: "alpha", description: "" },
      { name: "beta", description: "" },
    ],
  });
  assert.match(err(renameInputPort(base, "alpha", "beta")), /已存在/);
  assert.match(err(renameInputPort(base, "alpha", "Beta")), /\[a-z\]/);
  assert.match(err(renameInputPort(base, "ghost", "gamma")), /不存在/);
  assert.equal(ok(renameInputPort(base, "alpha", "alpha")), base);
}

function testDeletingAPortDropsDepsAndSurfacesLeftoverPlaceholders() {
  const base = state({
    inputs: [{ name: "alpha", description: "" }],
    nodes: [
      node("n0", { scheduled_deps: ["in:alpha"] }),
      node("n1", { prompt: "见 {{input.alpha}}" }),
    ],
    warnings: [{ code: "unreferenced_input", name: "alpha", message: "..." }],
  });
  const removed = removeInputPort(base, "alpha");
  assert.deepEqual(removed.inputs, []);
  assert.deepEqual(removed.nodes[0].scheduled_deps, []);
  assert.deepEqual(removed.warnings, []);
  // The prompt body is left alone on purpose, so validation must block the
  // save rather than let the backend reject it after the fact.
  assert.match(
    validateEditorState(removed)[0].message,
    /未声明的输入端口 \{\{input\.alpha\}\}/,
  );
}

function testConnectingAPortAddsTheDepOnce() {
  const base = state({ inputs: [{ name: "alpha", description: "" }] });
  const once = connectInputPort(base, "alpha", "n0");
  const twice = connectInputPort(once, "alpha", "n0");
  assert.deepEqual(twice.nodes[0].scheduled_deps, ["in:alpha"]);
}

function testCuttingAPortEdgeKeepsThePortDeclared() {
  const base = connectInputPort(
    state({ inputs: [{ name: "alpha", description: "" }] }),
    "alpha",
    "n0",
  );
  const cut = disconnectInputPort(base, "alpha", "n0");
  assert.deepEqual(cut.nodes[0].scheduled_deps, []);
  // The declaration survives — cutting an edge is not deleting the port.
  assert.deepEqual(cut.inputs.map((input) => input.name), ["alpha"]);
}

/* ───────── nodes and dependencies ───────── */

function testAddedNodesGetUnusedIds() {
  const first = addNode(state());
  assert.equal(first.node.id, "n1");
  const second = addNode(first.state);
  assert.equal(second.node.id, "n2");
  // An id already taken out of sequence does not produce a duplicate.
  const collided = addNode(state({ nodes: [node("n0"), node("n1")] }));
  assert.equal(collided.node.id, "n2");
}

function testRemovingANodeCleansUpEveryReferenceToIt() {
  const base = state({
    nodes: [
      node("n0"),
      node("n1", { scheduled_deps: ["n0"], resume_from: "n0" }),
    ],
  });
  const removed = removeNode(base, "n0");
  assert.deepEqual(removed.nodes.map((item) => item.id), ["n1"]);
  assert.deepEqual(removed.nodes[0].scheduled_deps, []);
  assert.equal(removed.nodes[0].resume_from, null);
  assert.deepEqual(validateEditorState(removed), []);
}

function testCyclesAreRefusedAtTheGesture() {
  const base = state({ nodes: [node("n0"), node("n1")] });
  const chained = ok(connectNodes(base, "n0", "n1"));
  assert.match(err(connectNodes(chained, "n1", "n0")), /环/);
  assert.match(err(connectNodes(base, "n0", "n0")), /自己/);
  assert.match(err(connectNodes(base, "n0", "ghost")), /不存在/);
  assert.equal(ok(connectNodes(chained, "n0", "n1")), chained);
}

function testCuttingADepAlsoCutsTheResumeLinkItCarried() {
  // The loader requires resume_from to appear in scheduled_deps, so a resume
  // link cannot outlive its dependency edge.
  const base = state({
    nodes: [node("n0"), node("n1", { scheduled_deps: ["n0"], resume_from: "n0" })],
  });
  const cut = disconnectNodes(base, "n0", "n1");
  assert.deepEqual(cut.nodes[1].scheduled_deps, []);
  assert.equal(cut.nodes[1].resume_from, null);
  assert.deepEqual(validateEditorState(cut), []);
}

function testSettingResumeAddsTheRequiredDependency() {
  const base = state({ nodes: [node("n0"), node("n1")] });
  const resumed = ok(setResumeFrom(base, "n1", "n0"));
  assert.deepEqual(resumed.nodes[1].scheduled_deps, ["n0"]);
  assert.equal(resumed.nodes[1].resume_from, "n0");
  assert.deepEqual(validateEditorState(resumed), []);
  assert.match(err(setResumeFrom(base, "n1", "n1")), /自己/);
  assert.match(err(setResumeFrom(base, "n1", "ghost")), /不存在/);
  assert.equal(ok(setResumeFrom(resumed, "n1", null)).nodes[1].resume_from, null);
}

/* ───────── validation ───────── */

function testValidationMirrorsTheLoadersHardRules() {
  const issues = validateEditorState(
    state({
      name: "  ",
      inputs: [{ name: "alpha", description: "" }],
      nodes: [
        node("n0", { scheduled_deps: ["in:ghost"] }),
        node("n1", { prompt: "{{input.missing}}" }),
        node("n2", { scheduled_deps: ["nope"] }),
        node("n3", { category: "review" }),
        node("n4", { subtype: "code_review" }),
        node("n5", { kind: "verifier" }),
        node("n6", { resume_from: "n0" }),
      ],
    }),
  );
  const messages = issues.map((issue) => issue.message);
  assert.ok(messages.some((message) => /模板名/.test(message)));
  assert.ok(messages.some((message) => /未声明的输入端口 ghost/.test(message)));
  assert.ok(messages.some((message) => /\{\{input\.missing\}\}/.test(message)));
  assert.ok(messages.some((message) => /依赖的节点 nope 不存在/.test(message)));
  assert.ok(messages.some((message) => /review 节点必须有 subtype/.test(message)));
  assert.ok(messages.some((message) => /非 review 节点不能带 subtype/.test(message)));
  assert.ok(messages.some((message) => /verifier/.test(message)));
  assert.ok(messages.some((message) => /必须同时是依赖/.test(message)));
}

function testDuplicateIdsAndReservedPrefixesAreRejected() {
  const messages = validateEditorState(
    state({ nodes: [node("n0"), node("n0"), node("in:alpha")] }),
  ).map((issue) => issue.message);
  assert.ok(messages.some((message) => /节点 id n0 重复/.test(message)));
  assert.ok(messages.some((message) => /不能以 in: 开头/.test(message)));
}

function testAWellFormedTemplateHasNoIssues() {
  assert.deepEqual(
    validateEditorState(
      state({
        inputs: [{ name: "alpha_branch", description: "alpha 末端" }],
        arguments: [
          { name: "topic", description: "主题", default: null, declared: true },
        ],
        nodes: [
          node("n0", {
            prompt: "围绕 {{topic}} 看 {{input.alpha_branch}}",
            scheduled_deps: ["in:alpha_branch"],
          }),
          node("n1", {
            category: "review",
            subtype: "agentic_review",
            brief: { check_what: "c", expected: "e", abnormal: "a" },
            scheduled_deps: ["n0"],
          }),
        ],
      }),
    ),
    [],
  );
}

function testEmptyTemplateIsRejected() {
  assert.match(
    validateEditorState(state({ nodes: [] }))[0].message,
    /至少需要一个节点/,
  );
}

/* ───────── serialization ───────── */

function testPayloadSortsNodesSoEveryDepComesEarlier() {
  // lane.yaml carries topology in file order: the loader demands each dep name
  // an earlier entry. Drawing an edge "backwards" is a legal graph edit, so the
  // payload must reorder rather than let the backend reject it.
  const base = state({
    nodes: [node("n0", { scheduled_deps: ["n1"] }), node("n1")],
  });
  const payload = buildRewritePayload(base);
  assert.deepEqual(payload.nodes.map((item) => item.id), ["n1", "n0"]);
  assert.deepEqual(topologicalOrder(base.nodes).map((item) => item.id), ["n1", "n0"]);
}

function testTopologicalOrderKeepsIndependentNodesInPlace() {
  const nodes = [node("n0"), node("n1"), node("n2")];
  assert.deepEqual(
    topologicalOrder(nodes).map((item) => item.id),
    ["n0", "n1", "n2"],
  );
}

function testTopologicalOrderStillEmitsEveryNodeInACycle() {
  // Validation blocks the save, but the payload must name real ids so the
  // backend error is about the actual cycle rather than a missing node.
  const nodes = [
    node("n0", { scheduled_deps: ["n1"] }),
    node("n1", { scheduled_deps: ["n0"] }),
  ];
  assert.deepEqual(
    topologicalOrder(nodes).map((item) => item.id).sort(),
    ["n0", "n1"],
  );
}

function testPayloadShapeMatchesTheWriteEndpoint() {
  const payload = buildRewritePayload(
    state({
      name: "  AlphaBeta  ",
      brief: "  对比两个分支  ",
      inputs: [{ name: "alpha_branch", description: "  alpha 末端  " }],
      arguments: [
        { name: "report_style", description: "  风格  ", default: "简洁", declared: true },
      ],
      nodes: [
        node("n0", {
          prompt: "{{topic}} / {{report_style}} / {{input.alpha_branch}}",
          motivation: "比较两个实现",
          scheduled_deps: ["in:alpha_branch"],
        }),
      ],
    }),
  );
  assert.equal(payload.name, "AlphaBeta");
  assert.equal(payload.brief, "对比两个分支");
  assert.deepEqual(Object.keys(payload).sort(), [
    "arguments",
    "brief",
    "inputs",
    "name",
    "nodes",
  ]);
  // Node keys must match UserTemplateNodeWrite exactly — it forbids extras.
  assert.deepEqual(Object.keys(payload.nodes[0]).sort(), [
    "brief",
    "category",
    "id",
    "kind",
    "model_preset_id",
    "motivation",
    "prompt",
    "resume_from",
    "scheduled_deps",
    "subtype",
  ]);
  assert.deepEqual(Object.keys(payload.arguments[0]).sort(), [
    "default",
    "description",
    "name",
  ]);
  assert.deepEqual(payload.inputs, [
    { name: "alpha_branch", description: "alpha 末端" },
  ]);
  assert.equal(payload.nodes[0].motivation, "比较两个实现");
  // Scanned-only arguments are persisted; that is how a typed placeholder
  // becomes a declared parameter.
  assert.deepEqual(payload.arguments.map((argument) => argument.name), [
    "report_style",
    "topic",
  ]);
}

function testPayloadDropsReviewOnlyFieldsFromNonReviewNodes() {
  const payload = buildRewritePayload(
    state({
      nodes: [
        node("n0", {
          category: "regular",
          subtype: "code_review",
          brief: { check_what: "c", expected: "e", abnormal: "a" },
        }),
      ],
    }),
  );
  assert.equal(payload.nodes[0].subtype, null);
  assert.equal(payload.nodes[0].brief, null);
}

function testDirtyTracksTheSerializedPayload() {
  const base = state({ nodes: [node("n0", { prompt: "hello" })] });
  const saved = buildRewritePayload(base);
  assert.equal(isDirty(base, saved), false);
  assert.equal(isDirty(updateNode(base, "n0", { prompt: "changed" }), saved), true);
  // Reordering that the payload normalizes away is not a change.
  assert.equal(isDirty(base, null), false);
}

/* ───────── layout ───────── */

function testLayoutPutsDepsLeftOfDependents() {
  const positions = layoutTemplateGraph(
    state({
      inputs: [{ name: "alpha", description: "" }, { name: "beta", description: "" }],
      nodes: [
        node("n0", { scheduled_deps: ["in:alpha"] }),
        node("n1", { scheduled_deps: ["n0"] }),
        node("n2", { scheduled_deps: ["n1"] }),
      ],
    }),
  );
  assert.ok(positions.nodes.n0.x < positions.nodes.n1.x);
  assert.ok(positions.nodes.n1.x < positions.nodes.n2.x);
  // Ports sit in their own column ahead of the graph.
  assert.ok(positions.ports.alpha.x < positions.nodes.n0.x);
  assert.ok(positions.ports.alpha.y < positions.ports.beta.y);
}

function testLayoutStacksSiblingsInTheSameColumn() {
  const positions = layoutTemplateGraph(
    state({ nodes: [node("n0"), node("n1"), node("n2", { scheduled_deps: ["n0"] })] }),
  );
  assert.equal(positions.nodes.n0.x, positions.nodes.n1.x);
  assert.notEqual(positions.nodes.n0.y, positions.nodes.n1.y);
  assert.ok(positions.nodes.n2.x > positions.nodes.n0.x);
}

function testEachNodeCarriesItsOwnModelThroughASave() {
  // Per-node models are the whole point: a template stamps each node on the
  // model it names, so the payload must carry them independently and keep
  // "inherit the project preset" (null) distinct from a chosen one.
  const edited = updateNode(
    state({
      nodes: [
        node("n0", { model_preset_id: "opus-4-7" }),
        node("n1", { scheduled_deps: ["n0"] }),
      ],
    }),
    "n1",
    { model_preset_id: "gpt-5.6-x" },
  );
  const payload = buildRewritePayload(edited);
  assert.deepEqual(
    payload.nodes.map((item) => item.model_preset_id),
    ["opus-4-7", "gpt-5.6-x"],
  );

  const inheriting = buildRewritePayload(state({ nodes: [node("n0")] }));
  assert.equal(inheriting.nodes[0].model_preset_id, null);
}

function testModelChangeMarksTheEditorDirty() {
  const before = state({ nodes: [node("n0", { model_preset_id: "opus-4-7" })] });
  const saved = buildRewritePayload(before);
  assert.equal(isDirty(before, saved), false);
  const after = updateNode(before, "n0", { model_preset_id: "gpt-5.6-x" });
  assert.equal(isDirty(after, saved), true);
}

function testPreviewModelResolutionFollowsResumeSource() {
  const nodes = [
    node("n0", { model_preset_id: "opus-4-7" }),
    node("n1", {
      scheduled_deps: ["n0"],
      resume_from: "n0",
      model_preset_id: "gpt-5.6-x",
    }),
    node("n2", {
      scheduled_deps: ["n1"],
      resume_from: "n1",
      model_preset_id: "gpt-5.6-x",
    }),
  ];

  assert.equal(resolvedTemplateNodeModelPresetId(nodes, nodes[0]), "opus-4-7");
  assert.equal(resolvedTemplateNodeModelPresetId(nodes, nodes[1]), "opus-4-7");
  assert.equal(resolvedTemplateNodeModelPresetId(nodes, nodes[2]), "opus-4-7");
}

function testPreviewModelResolutionOmitsInvalidResumeMetadata() {
  const missingSource = node("n0", {
    resume_from: "missing",
    model_preset_id: "stale-model",
  });
  const cycle = [
    node("n0", { resume_from: "n1", model_preset_id: "stale-a" }),
    node("n1", { resume_from: "n0", model_preset_id: "stale-b" }),
  ];

  assert.equal(resolvedTemplateNodeModelPresetId([missingSource], missingSource), null);
  assert.equal(resolvedTemplateNodeModelPresetId(cycle, cycle[0]), null);
}

function testDetailLoadsPerNodeModelIncludingAbsentAsInherit() {
  const detail: TemplateDetail = {
    slug: "mixed",
    name: "Mixed",
    brief: "",
    allowed_model_preset_ids: [],
    auto_commit: false,
    node_count: 2,
    schema_version: 2,
    arguments: [],
    inputs: [],
    warnings: [],
    nodes: [
      {
        id: "n0",
        kind: "agent",
        category: "regular",
        prompt_preview: "",
        prompt: "a",
        model_preset_id: "opus-4-7",
      },
      // A template authored before per-node models omits the field entirely.
      { id: "n1", kind: "agent", category: "regular", prompt_preview: "", prompt: "b" },
    ],
  };
  const loaded = templateEditorStateFromDetail(detail);
  assert.equal(loaded.nodes[0].model_preset_id, "opus-4-7");
  assert.equal(loaded.nodes[1].model_preset_id, null);
}

testDetailLoadsPromptSourceNotThePreview();
testDetailWithoutPromptLoadsEmptyRatherThanTruncated();
testScanMirrorsTheLoaderRules();
testReferencesReportWhichNodesUseEachName();
testScannedArgumentsAppearUndeclaredAfterDeclaredOnes();
testMissingDefaultAndEmptyDefaultStayDistinct();
testEditingAScannedArgumentDeclaresIt();
testEditingAnUnknownArgumentIsANoOp();
testDanglingCleanupUsesBackendWarningNames();
testPortNamesFollowTheLoaderPattern();
testRenamingAPortRewritesBothReferenceForms();
testRenameRejectsIllegalAndCollidingNames();
testDeletingAPortDropsDepsAndSurfacesLeftoverPlaceholders();
testConnectingAPortAddsTheDepOnce();
testCuttingAPortEdgeKeepsThePortDeclared();
testAddedNodesGetUnusedIds();
testRemovingANodeCleansUpEveryReferenceToIt();
testCyclesAreRefusedAtTheGesture();
testCuttingADepAlsoCutsTheResumeLinkItCarried();
testSettingResumeAddsTheRequiredDependency();
testValidationMirrorsTheLoadersHardRules();
testDuplicateIdsAndReservedPrefixesAreRejected();
testAWellFormedTemplateHasNoIssues();
testEmptyTemplateIsRejected();
testPayloadSortsNodesSoEveryDepComesEarlier();
testTopologicalOrderKeepsIndependentNodesInPlace();
testTopologicalOrderStillEmitsEveryNodeInACycle();
testPayloadShapeMatchesTheWriteEndpoint();
testEachNodeCarriesItsOwnModelThroughASave();
testModelChangeMarksTheEditorDirty();
testPreviewModelResolutionFollowsResumeSource();
testPreviewModelResolutionOmitsInvalidResumeMetadata();
testDetailLoadsPerNodeModelIncludingAbsentAsInherit();
testPayloadDropsReviewOnlyFieldsFromNonReviewNodes();
testDirtyTracksTheSerializedPayload();
testLayoutPutsDepsLeftOfDependents();
testLayoutStacksSiblingsInTheSameColumn();
console.log("template editor tests passed");
