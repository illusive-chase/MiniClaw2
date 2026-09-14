import { scanPlaceholders } from "./templateEditor";
import type { NodeDetail, NodeInfo } from "./types";

export function toNodeInfo(node: NodeInfo | NodeDetail): NodeInfo {
  if (
    typeof node.prompt_truncated === "boolean" &&
    node.prompt_argument_names &&
    !("system_context_snapshot" in node) &&
    !("launch_instructions_snapshot" in node)
  ) return node;
  const {
    system_context_snapshot: _system,
    launch_instructions_snapshot: _instructions,
    ...info
  } = node as NodeDetail;
  const prompt = node.prompt || "";
  const promptCharacters = Array.from(prompt);
  return {
    ...info,
    prompt: promptCharacters.slice(0, 120).join(""),
    prompt_truncated: Boolean(node.prompt_truncated) || promptCharacters.length > 120,
    prompt_argument_names: node.prompt_argument_names ??
      scanPlaceholders(node.prompt_draft || prompt).argumentNames,
  };
}
