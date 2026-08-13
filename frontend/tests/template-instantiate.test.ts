import assert from "node:assert/strict";
import {
  argumentsComplete,
  buildInstantiateRequest,
  canSubmitInstantiation,
  initialArgumentValues,
  initialInputBindings,
  inputBindingsComplete,
  inputCandidates,
  isRetryableApplyStatus,
  missingRequiredArguments,
  pruneStaleBindings,
  templateInstanceFetchScope,
  templateNeedsInstantiateDialog,
  unboundInputPorts,
  warningText,
} from "../src/templateInstantiate";
import type {
  NodeInfo,
  TemplateArgumentMeta,
  TemplateSummary,
} from "../src/types";

function argument(
  name: string,
  overrides: Partial<TemplateArgumentMeta> = {},
): TemplateArgumentMeta {
  return {
    name,
    description: "",
    default: null,
    required: true,
    declared: true,
    ...overrides,
  };
}

/** An optional argument: the backend sets required=false and a non-null
 * default, including the empty string. */
function optional(name: string, defaultValue: string): TemplateArgumentMeta {
  return argument(name, { default: defaultValue, required: false });
}

function template(overrides: Partial<TemplateSummary> = {}): TemplateSummary {
  return {
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
    ...overrides,
  };
}

function node(id: string, overrides: Partial<NodeInfo> = {}): NodeInfo {
  return {
    id,
    project_id: "project",
    kind: "agent",
    state: "done",
    provider: null,
    prompt: id,
    scheduled_deps: [],
    created_at: 1,
    ...overrides,
  };
}

function testDialogOnlyOpensWhenThereIsSomethingToFill() {
  // The whole pre-schema-v2 template library has neither, and must keep
  // stamping straight through on drop.
  assert.equal(templateNeedsInstantiateDialog(template()), false);
  assert.equal(
    templateNeedsInstantiateDialog(template({ arguments: [argument("topic")] })),
    true,
  );
  assert.equal(
    templateNeedsInstantiateDialog(template({ inputs: [{ name: "alpha", description: "" }] })),
    true,
  );
  // A warning alone is the author's problem, not a reason to interrupt.
  assert.equal(
    templateNeedsInstantiateDialog(
      template({ warnings: [{ code: "dangling_argument", name: "gone", message: "" }] }),
    ),
    false,
  );
}

function testInstanceFetchScopeChangesWhenAnExistingLaneGetsAnotherInstance() {
  const first = templateInstanceFetchScope([
    node("n1", { planspace_id: "lane-a", template_instance_id: "instance-1" }),
    node("n2", { planspace_id: "lane-a", template_instance_id: "instance-1" }),
  ]);
  const sameInstances = templateInstanceFetchScope([
    node("n2", {
      planspace_id: "lane-a",
      template_instance_id: "instance-1",
      state: "running",
    }),
    node("n1", { planspace_id: "lane-a", template_instance_id: "instance-1" }),
  ]);
  const second = templateInstanceFetchScope([
    node("n1", { planspace_id: "lane-a", template_instance_id: "instance-1" }),
    node("n3", { planspace_id: "lane-a", template_instance_id: "instance-2" }),
  ]);

  assert.deepEqual(first.laneIds, ["lane-a"]);
  assert.equal(sameInstances.key, first.key);
  assert.notEqual(second.key, first.key);
}

function testDefaultsPrefillWithoutBecomingRequired() {
  const specs = [
    argument("topic"),
    optional("style", "简洁要点式"),
    optional("suffix", ""),
  ];
  assert.deepEqual(initialArgumentValues(specs), {
    topic: "",
    style: "简洁要点式",
    suffix: "",
  });

  // `suffix` starts empty exactly like the required `topic`, but only `topic`
  // blocks submission — requiredness comes from the flag, never the default.
  const values = initialArgumentValues(specs);
  assert.equal(argumentsComplete(specs, values), false);
  assert.deepEqual(missingRequiredArguments(specs, values), ["topic"]);

  assert.equal(
    argumentsComplete(specs, { ...values, topic: "支付重构" }),
    true,
  );
  assert.deepEqual(
    missingRequiredArguments(specs, { ...values, topic: "支付重构" }),
    [],
  );
}

function testWhitespaceIsNotAValueForARequiredArgument() {
  const specs = [argument("topic")];
  assert.equal(argumentsComplete(specs, { topic: "   " }), false);
  assert.equal(argumentsComplete(specs, { topic: " x " }), true);
  // A missing key is as empty as an empty string.
  assert.equal(argumentsComplete(specs, {}), false);
}

function testUndeclaredArgumentsAreStillRequired() {
  // Scanned out of a prompt but absent from template.yaml: the loader marks
  // them declared=false and required=true, and the form must respect that.
  const specs = [argument("topic", { declared: false })];
  assert.equal(argumentsComplete(specs, {}), false);
  assert.deepEqual(initialArgumentValues(specs), { topic: "" });
}

function testEveryPortMustBeBound() {
  const inputs = [
    { name: "alpha", description: "" },
    { name: "beta", description: "" },
  ];
  assert.equal(inputBindingsComplete(inputs, {}), false);
  assert.equal(inputBindingsComplete(inputs, { alpha: "n1" }), false);
  assert.deepEqual(unboundInputPorts(inputs, { alpha: "n1" }), ["beta"]);
  assert.equal(inputBindingsComplete(inputs, { alpha: "n1", beta: "n2" }), true);
  assert.deepEqual(unboundInputPorts(inputs, { alpha: "n1", beta: "n2" }), []);
  // Binding the same node to both ports is legal — nothing says the two
  // upstreams have to differ.
  assert.equal(inputBindingsComplete(inputs, { alpha: "n1", beta: "n1" }), true);
}

function testHoveredNodePrefillsTheFirstPortOnly() {
  const inputs = [
    { name: "alpha", description: "" },
    { name: "beta", description: "" },
  ];
  const candidates = [{ id: "n1" }, { id: "n2" }];
  assert.deepEqual(initialInputBindings(inputs, "n2", candidates), {
    alpha: "n2",
    beta: "",
  });
  // Dropping on the pane, or on something that is not a legal candidate
  // (another lane, an obsolete node, a commit tile), prefills nothing.
  assert.deepEqual(initialInputBindings(inputs, null, candidates), {
    alpha: "",
    beta: "",
  });
  assert.deepEqual(initialInputBindings(inputs, "elsewhere", candidates), {
    alpha: "",
    beta: "",
  });
  assert.deepEqual(initialInputBindings([], "n1", candidates), {});
}

function testCandidatesMirrorTheBackendBindingRule() {
  const nodes = [
    node("n1", { planspace_id: "lane-a", summary: "  build   the thing " }),
    node("n2", { planspace_id: "lane-b" }),
    node("n3", { planspace_id: "lane-a", obsolete_reason: "superseded" }),
    node("n4", { planspace_id: "lane-a", state: "virtual", prompt_draft: "draft" }),
  ];
  const candidates = inputCandidates(nodes, "lane-a");
  // Cross-lane bindings are rejected by the backend, so they are never
  // offered; obsolete nodes are never a sensible upstream.
  assert.deepEqual(candidates.map((c) => c.id), ["n1", "n4"]);
  assert.equal(candidates[0].shortId, "n1");
  assert.equal(candidates[0].label, "build the thing");
  assert.equal(candidates[1].label, "draft");

  // A node with no lane belongs to no lane, and matches only the unset case.
  const laneless = inputCandidates([node("n5")], null);
  assert.deepEqual(laneless.map((c) => c.id), ["n5"]);
  assert.deepEqual(inputCandidates([node("n5")], "lane-a"), []);
}

function testCandidateLabelFallsBackAndTruncates() {
  const long = "x".repeat(200);
  const [candidate] = inputCandidates([node("n1", { prompt: long })], null);
  assert.equal(candidate.label.length, 61);
  assert.ok(candidate.label.endsWith("…"));

  const [empty] = inputCandidates([node("n1", { prompt: "" })], null);
  assert.equal(empty.label, "(无提示词)");
}

function testStaleBindingsAreCleared() {
  const candidates = [{ id: "n1" }, { id: "n2" }];
  // A node deleted or obsoleted while the dialog was open must not stay
  // bound: the selector would show a blank row with "create" still enabled.
  assert.deepEqual(
    pruneStaleBindings({ alpha: "n1", beta: "gone" }, candidates),
    { alpha: "n1", beta: "" },
  );
  // Unchanged input is returned by identity, so feeding this through
  // setBindings on every lane refresh cannot loop.
  const stable = { alpha: "n1", beta: "" };
  assert.equal(pruneStaleBindings(stable, candidates), stable);
  // Losing every candidate clears every port rather than failing at submit.
  assert.deepEqual(pruneStaleBindings({ alpha: "n1" }, []), { alpha: "" });
}

function testSubmitNeedsBothHalves() {
  const tpl = template({
    arguments: [argument("topic"), optional("style", "简洁")],
    inputs: [{ name: "alpha", description: "" }],
  });
  assert.equal(canSubmitInstantiation(tpl, { topic: "" }, { alpha: "n1" }), false);
  assert.equal(canSubmitInstantiation(tpl, { topic: "t" }, {}), false);
  assert.equal(
    canSubmitInstantiation(tpl, { topic: "t" }, { alpha: "n1" }),
    true,
  );
}

function testRequestBodySendsEveryShownArgument() {
  const tpl = template({
    arguments: [argument("topic"), optional("style", "简洁"), optional("suffix", "")],
    inputs: [
      { name: "alpha", description: "" },
      { name: "beta", description: "" },
    ],
  });
  const request = buildInstantiateRequest(
    tpl,
    { topic: "支付重构", style: "简洁", suffix: "" },
    { alpha: "n1", beta: "n2" },
    "n1",
  );
  // Substitution is frozen into prompt_draft at stamp time, so what the form
  // displayed is what the nodes get — including untouched defaults.
  assert.deepEqual(request.arguments, {
    topic: "支付重构",
    style: "简洁",
    suffix: "",
  });
  assert.deepEqual(request.input_bindings, { alpha: "n1", beta: "n2" });
  // Ports are explicit upstreams; the legacy anchor would be redundant, and
  // the backend ignores it whenever a template declares inputs.
  assert.equal(request.anchor_node_id, null);
}

function testRequestBodyKeepsTheAnchorWhenThereAreNoPorts() {
  const tpl = template({ arguments: [argument("topic")] });
  const request = buildInstantiateRequest(tpl, { topic: "t" }, {}, "n7");
  assert.equal(request.anchor_node_id, "n7");
  assert.deepEqual(request.input_bindings, {});
  assert.deepEqual(request.arguments, { topic: "t" });
}

function testRequestBodyOmitsUnknownKeys() {
  // The backend rejects any argument or port it does not know, so stale form
  // state must never leak into the payload.
  const tpl = template({
    arguments: [argument("topic")],
    inputs: [{ name: "alpha", description: "" }],
  });
  const request = buildInstantiateRequest(
    tpl,
    { topic: "t", removed: "stale" },
    { alpha: "n1", gone: "n2" },
    null,
  );
  assert.deepEqual(request.arguments, { topic: "t" });
  assert.deepEqual(request.input_bindings, { alpha: "n1" });
}

function testWarningTextLocalizesKnownCodes() {
  // The backend always supplies an English message; known codes get a
  // localized line instead so the dialog stays in the user's language.
  assert.ok(
    warningText({
      code: "dangling_argument",
      name: "topic",
      message: "argument 'topic' is declared but no prompt uses {{topic}}",
    }).includes("topic"),
  );
  assert.ok(
    warningText({ code: "dangling_argument", name: "topic", message: "" }).includes(
      "参数",
    ),
  );
  assert.ok(
    warningText({ code: "unreferenced_input", name: "alpha", message: "" }).includes(
      "alpha",
    ),
  );
  // A code added later still renders something useful without a frontend
  // change, preferring the backend's own wording.
  assert.equal(
    warningText({ code: "future_code", name: "x", message: "something new" }),
    "something new",
  );
  assert.equal(
    warningText({ code: "future_code", name: "x", message: "" }),
    "future_code: x",
  );
  assert.equal(warningText({ code: "future_code", name: "", message: "" }), "future_code");
}

function testOnlyConflictsInviteAPlainRetry() {
  // 409 is "the project is busy" — the form is fine as filled. A 400 means
  // the user has to change something first.
  assert.equal(isRetryableApplyStatus(409), true);
  assert.equal(isRetryableApplyStatus(400), false);
  assert.equal(isRetryableApplyStatus(404), false);
  assert.equal(isRetryableApplyStatus(500), false);
  assert.equal(isRetryableApplyStatus(null), false);
}

testDialogOnlyOpensWhenThereIsSomethingToFill();
testInstanceFetchScopeChangesWhenAnExistingLaneGetsAnotherInstance();
testDefaultsPrefillWithoutBecomingRequired();
testWhitespaceIsNotAValueForARequiredArgument();
testUndeclaredArgumentsAreStillRequired();
testEveryPortMustBeBound();
testHoveredNodePrefillsTheFirstPortOnly();
testCandidatesMirrorTheBackendBindingRule();
testCandidateLabelFallsBackAndTruncates();
testStaleBindingsAreCleared();
testSubmitNeedsBothHalves();
testRequestBodySendsEveryShownArgument();
testRequestBodyKeepsTheAnchorWhenThereAreNoPorts();
testRequestBodyOmitsUnknownKeys();
testWarningTextLocalizesKnownCodes();
testOnlyConflictsInviteAPlainRetry();
console.log("template instantiate tests passed");
