import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AskUserDialog } from "../src/components/AskUserDialog";
import {
  applyGateRelayEvent,
  gateRelayLabel,
  replaceGateRelays,
} from "../src/gateRelays";
import type { GateRelayUpdated, InteractionRequest } from "../src/types";

const request: InteractionRequest = {
  type: "interaction_request",
  id: "gate-1",
  interaction_type: "ask_user",
  tool_name: "AskUserQuestion",
  tool_input: { questions: [{ question: "Which?", options: [{ label: "A" }] }] },
  suggestions: [],
  node_id: "node-1",
};

function relay(state: GateRelayUpdated["state"], extra: Partial<GateRelayUpdated> = {}): GateRelayUpdated {
  return { type: "gate_relay_updated", node_id: "node-1", gate_id: "gate-1", state, at: 0, seq: 0, ...extra };
}

function render(): string {
  return renderToStaticMarkup(
    React.createElement(AskUserDialog, { request, onRespond: () => undefined }),
  );
}

replaceGateRelays({});
assert.doesNotMatch(render(), /飞书/);

applyGateRelayEvent(relay("sent", { at: 1_791_800_000 }));
assert.match(render(), /已推送到飞书/);

applyGateRelayEvent(relay("cancelled"));
assert.doesNotMatch(render(), /飞书/);

replaceGateRelays({ "gate-1": relay("failed", { detail: "quota" }) });
assert.match(render(), /飞书推送失败：quota/);

assert.equal(gateRelayLabel(null), null);
assert.match(gateRelayLabel(relay("scheduled")) ?? "", /即将推送到飞书/);
assert.equal(gateRelayLabel(relay("cancelled")), null);

console.log("飞书推送标记校验通过");
