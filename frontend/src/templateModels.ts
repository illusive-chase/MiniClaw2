import type { TemplateNodeSpec } from "./types";

/** Resolve the model a template node will actually use when stamped.
 *
 * Resume nodes inherit their source session's model, so their own authored
 * value is stale metadata at best. A missing source or a cycle is invalid
 * template data; returning null keeps previews from asserting a false model.
 */
export function resolvedTemplateNodeModelPresetId(
  nodes: TemplateNodeSpec[],
  node: TemplateNodeSpec,
): string | null {
  const nodesById = new Map(nodes.map((item) => [item.id, item]));
  const visited = new Set<string>();
  let current = node;

  while (current.resume_from) {
    if (visited.has(current.id)) return null;
    visited.add(current.id);
    const source = nodesById.get(current.resume_from);
    if (!source) return null;
    current = source;
  }

  return current.model_preset_id ?? null;
}
