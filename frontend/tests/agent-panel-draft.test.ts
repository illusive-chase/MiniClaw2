import assert from "node:assert/strict";

import {
  agentInputText,
  virtualDraftAfterSave,
  virtualDraftFromNode,
  virtualDraftValidationError,
  virtualPayloadFromDraft,
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
}

/* Work and planning carry the intent through to the wire. */
{
  for (const classification of ["work", "planning"] as const) {
    const draft = {
      ...virtualDraftFromNode(node()),
      classification,
      artifactMode: "markdown" as const,
      qaMode: true,
    };
    const payload = virtualPayloadFromDraft(draft, node());
    assert.equal(payload.artifact_mode, "markdown");
    assert.equal(payload.artifact_spec, "");
    assert.equal(payload.qa_mode, true);
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
  };
  const payload = virtualPayloadFromDraft(draft, node());
  assert.equal(payload.category, "review");
  assert.equal(payload.artifact_mode, "default");
  assert.equal(payload.artifact_spec, "");
  assert.equal(payload.qa_mode, false);
}

/* Library nodes get no artifact intent — their deliverable is one library
 * entry — but they may still need to ask which entry the user meant. */
{
  const draft = {
    ...virtualDraftFromNode(node()),
    classification: "library" as const,
    artifactMode: "html" as const,
    qaMode: true,
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
}

console.log("agent-panel-draft: ok");
