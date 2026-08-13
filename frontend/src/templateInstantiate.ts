/** Pure logic behind the template instantiation dialog.
 *
 * The dialog itself is a thin shell around these functions: whether it opens
 * at all, whether "create" is enabled, and what request body the confirmation
 * sends. Keeping them here makes the rules testable without DOM plumbing.
 */
import type {
  NodeInfo,
  TemplateArgumentMeta,
  TemplateSummary,
  TemplateWarningMeta,
} from "./types";

export type TemplateInstantiateRequest = {
  anchor_node_id: string | null;
  arguments: Record<string, string>;
  input_bindings: Record<string, string>;
};

/** A node offered in an input port's selector. */
export type InputCandidate = {
  id: string;
  /** Short id shown as the stable handle, matching the canvas card. */
  shortId: string;
  label: string;
};

/** Stable fetch scope for the immutable instance records referenced by nodes.
 *
 * A lane can gain another instance without changing its lane id, so the effect
 * that loads records must key on both levels. JSON keeps arbitrary ids
 * unambiguous while sorting and de-duplicating prevents ordinary node-state
 * refreshes from issuing the same request again.
 */
export function templateInstanceFetchScope(nodes: NodeInfo[]): {
  laneIds: string[];
  key: string;
} {
  const instanceIdsByLane = new Map<string, Set<string>>();
  for (const node of nodes) {
    const laneId = node.planspace_id;
    const instanceId = node.template_instance_id;
    if (!laneId || !instanceId) continue;
    const instanceIds = instanceIdsByLane.get(laneId);
    if (instanceIds) instanceIds.add(instanceId);
    else instanceIdsByLane.set(laneId, new Set([instanceId]));
  }
  const laneIds = Array.from(instanceIdsByLane.keys()).sort();
  return {
    laneIds,
    key: JSON.stringify(
      laneIds.map((laneId) => [
        laneId,
        Array.from(instanceIdsByLane.get(laneId) ?? []).sort(),
      ]),
    ),
  };
}

/** Templates with neither arguments nor input ports stamp straight away.
 *
 * This keeps every pre-schema-v2 template behaving exactly as before: drag,
 * drop, stamped, with the hovered node still acting as the implicit anchor.
 */
export function templateNeedsInstantiateDialog(
  template: Pick<TemplateSummary, "arguments" | "inputs">,
): boolean {
  return template.arguments.length > 0 || template.inputs.length > 0;
}

/** Initial text for one argument field.
 *
 * A null default means required, so there is nothing to prefill; `""` is an
 * optional empty value and also starts empty. Both paths agree, but they mean
 * different things to {@link argumentsComplete}.
 */
export function initialArgumentValues(
  argumentSpecs: TemplateArgumentMeta[],
): Record<string, string> {
  const values: Record<string, string> = {};
  for (const spec of argumentSpecs) values[spec.name] = spec.default ?? "";
  return values;
}

/** Every required argument holds a non-blank value.
 *
 * Requiredness comes from the backend's `required` flag, never from inspecting
 * `default`. Optional arguments may stay empty: an explicit empty string is a
 * legitimate value the user may have chosen.
 */
export function argumentsComplete(
  argumentSpecs: TemplateArgumentMeta[],
  values: Record<string, string>,
): boolean {
  return argumentSpecs.every(
    (spec) => !spec.required || (values[spec.name] ?? "").trim() !== "",
  );
}

/** Names of required arguments still missing a value, in declaration order. */
export function missingRequiredArguments(
  argumentSpecs: TemplateArgumentMeta[],
  values: Record<string, string>,
): string[] {
  return argumentSpecs
    .filter((spec) => spec.required && (values[spec.name] ?? "").trim() === "")
    .map((spec) => spec.name);
}

/** Every declared port is bound to exactly one node.
 *
 * Ports are all-or-nothing by design (proposal §8: no optional inputs, no
 * multi-bind), so this is a plain completeness check.
 */
export function inputBindingsComplete(
  inputSpecs: Array<{ name: string }>,
  bindings: Record<string, string>,
): boolean {
  return inputSpecs.every((spec) => (bindings[spec.name] ?? "") !== "");
}

/** Ports still waiting for a node, in declaration order. */
export function unboundInputPorts(
  inputSpecs: Array<{ name: string }>,
  bindings: Record<string, string>,
): string[] {
  return inputSpecs
    .filter((spec) => (bindings[spec.name] ?? "") === "")
    .map((spec) => spec.name);
}

/** Both halves of the form are satisfied, so "create" may fire. */
export function canSubmitInstantiation(
  template: Pick<TemplateSummary, "arguments" | "inputs">,
  values: Record<string, string>,
  bindings: Record<string, string>,
): boolean {
  return (
    argumentsComplete(template.arguments, values) &&
    inputBindingsComplete(template.inputs, bindings)
  );
}

/** Prefill the hovered drop target into the first port.
 *
 * Dropping onto a node is the user naming an upstream, so honour it as the
 * first port's binding rather than spending it on the legacy anchor. Only a
 * node that is actually a legal candidate is used.
 */
export function initialInputBindings(
  inputSpecs: Array<{ name: string }>,
  anchorNodeId: string | null,
  candidates: Array<{ id: string }>,
): Record<string, string> {
  const bindings: Record<string, string> = {};
  for (const spec of inputSpecs) bindings[spec.name] = "";
  const first = inputSpecs[0];
  if (
    first &&
    anchorNodeId &&
    candidates.some((candidate) => candidate.id === anchorNodeId)
  ) {
    bindings[first.name] = anchorNodeId;
  }
  return bindings;
}

/** Drop bindings whose node is no longer a legal candidate.
 *
 * The lane refreshes over the websocket while the dialog is open, so a bound
 * node can be deleted or obsoleted mid-edit. Without this the selector would
 * show a blank row while "create" stayed enabled, and the backend would reject
 * the vanished id. Clearing the port instead states the problem where the user
 * can fix it.
 */
export function pruneStaleBindings(
  bindings: Record<string, string>,
  candidates: Array<{ id: string }>,
): Record<string, string> {
  const live = new Set(candidates.map((candidate) => candidate.id));
  let changed = false;
  const pruned: Record<string, string> = {};
  for (const [port, nodeId] of Object.entries(bindings)) {
    if (nodeId && !live.has(nodeId)) {
      pruned[port] = "";
      changed = true;
    } else {
      pruned[port] = nodeId;
    }
  }
  return changed ? pruned : bindings;
}

/** Nodes in the active lane that may be bound to an input port.
 *
 * Mirrors the backend's binding check, which loads the node and rejects
 * anything outside the active planspace. Obsolete nodes are filtered out
 * because depending on one is never what the user means.
 */
export function inputCandidates(
  nodes: NodeInfo[],
  activePlanspaceId: string | null,
): InputCandidate[] {
  return nodes
    .filter((node) => (node.planspace_id ?? "") === (activePlanspaceId ?? ""))
    .filter((node) => !node.obsolete_reason)
    .map((node) => ({
      id: node.id,
      shortId: node.id.slice(0, 6),
      label: candidateLabel(node),
    }));
}

const CANDIDATE_LABEL_LIMIT = 60;

/** One-line handle for a node in the selector, matching the canvas headline. */
function candidateLabel(node: NodeInfo): string {
  const text = (
    node.summary ||
    node.prompt_draft ||
    node.prompt ||
    ""
  )
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return "(无提示词)";
  return text.length > CANDIDATE_LABEL_LIMIT
    ? `${text.slice(0, CANDIDATE_LABEL_LIMIT)}…`
    : text;
}

/** Build the apply request body from dialog state.
 *
 * Every argument the dialog showed is sent verbatim, including one the user
 * deliberately blanked — the substitution is frozen into `prompt_draft` at
 * stamp time, so what the form displayed is what the nodes should get.
 *
 * `anchor_node_id` is dropped whenever the template declares ports: the stamp
 * already has explicit upstreams, and the backend ignores the anchor in that
 * case anyway.
 */
export function buildInstantiateRequest(
  template: Pick<TemplateSummary, "arguments" | "inputs">,
  values: Record<string, string>,
  bindings: Record<string, string>,
  anchorNodeId: string | null,
): TemplateInstantiateRequest {
  const argumentPayload: Record<string, string> = {};
  for (const spec of template.arguments) {
    argumentPayload[spec.name] = values[spec.name] ?? "";
  }

  const bindingPayload: Record<string, string> = {};
  for (const spec of template.inputs) {
    const nodeId = bindings[spec.name] ?? "";
    if (nodeId) bindingPayload[spec.name] = nodeId;
  }

  return {
    anchor_node_id: template.inputs.length > 0 ? null : anchorNodeId,
    arguments: argumentPayload,
    input_bindings: bindingPayload,
  };
}

/** Human-readable line for a template-authoring warning.
 *
 * These are the template author's problem, not the caller's, so they are shown
 * as advice and never block instantiation. Known codes get a localized line;
 * anything the backend adds later falls back to its own message so a new code
 * still says something useful without a frontend change.
 */
export function warningText(warning: TemplateWarningMeta): string {
  switch (warning.code) {
    case "dangling_argument":
      return `参数 ${warning.name} 已在模板中声明，但没有任何提示词引用它。`;
    case "unreferenced_input":
      return `输入端口 ${warning.name} 没有被任何节点引用。`;
    default:
      if (warning.message) return warning.message;
      return warning.name ? `${warning.code}: ${warning.name}` : warning.code;
  }
}

/** Whether an apply failure is a transient conflict worth retrying as-is.
 *
 * The backend returns 409 while a turn or a context refresh is in flight;
 * nothing about the filled-in form is wrong, so the dialog stays open and
 * invites another attempt.
 */
export function isRetryableApplyStatus(status: number | null): boolean {
  return status === 409;
}
