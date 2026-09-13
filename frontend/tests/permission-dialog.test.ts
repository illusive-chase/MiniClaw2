import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { PermissionDialog } from "../src/components/PermissionDialog";
import type { InteractionRequest } from "../src/types";

const request: InteractionRequest = {
  type: "interaction_request",
  id: "gate-1",
  interaction_type: "permission",
  tool_name: "commandExecution",
  tool_input: { command: "rm example.txt" },
  suggestions: [],
  node_id: "node-1",
};

function render(provider: "claude" | "codex"): string {
  return renderToStaticMarkup(
    React.createElement(PermissionDialog, {
      provider,
      request,
      onRespond: () => undefined,
    }),
  );
}

{
  const codex = render("codex");
  assert.match(codex, /可选备注（仅记录）/);
  assert.match(codex, /不会发送给 Codex/);
}

{
  const claude = render("claude");
  assert.match(claude, /Optional message/);
  assert.doesNotMatch(claude, /不会发送给 Codex/);
}
