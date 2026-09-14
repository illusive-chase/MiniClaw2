import type { BuildGraphArgs } from "../../src/canvas/layout";
import type { ContextBundle, NodeDetail } from "../../src/types";

export function largeGraph(copies = 5, nodesPerCopy = 411) {
  const nodes: NodeDetail[] = [];
  const contextBundlesByNodeId: Record<string, ContextBundle> = {};
  const knownPlanspaceIds: string[] = [];
  for (let copy = 0; copy < copies; copy += 1) {
    const lane = `scale-${copy}`;
    knownPlanspaceIds.push(lane);
    for (let index = 0; index < nodesPerCopy; index += 1) {
      const id = `${lane}-${index}`;
      const parent = index > 0 ? `${lane}-${index - 1}` : null;
      const kind = index % 17 === 16 ? "op" : "agent";
      nodes.push({
        id, project_id: "scale", rev: 1, kind, provider: null,
        state: index % 13 === 12 && kind === "agent" ? "virtual" : "done",
        op_kind: kind === "op" ? "commit" : null,
        prompt: "规模回归节点".repeat(30) + " {{late_argument}}",
        system_context_snapshot: "系统快照".repeat(500),
        launch_instructions_snapshot: "启动规则".repeat(500),
        created_at: copy * nodesPerCopy + index + 1,
        planspace_id: lane, parent_node_id: parent,
        scheduled_deps: parent ? [parent] : [],
        context_bundle_id: kind === "agent" ? `bundle-${id}` : null,
        settings_snapshot: { active_planspace_id: lane },
        artifacts: index % 19 === 0 && kind === "agent" ? [{
          name: "result.md", bytes: 100, mtime: 1, sha256: "fixture", status: "published",
        }] : [],
      });
      if (kind === "agent") contextBundlesByNodeId[id] = {
        bundle_id: `bundle-${id}`, created_at: 1,
        active_planspace: { id: lane, color: "rose" },
        sources: [{
          scope: "contextspace", kind: "principle", path: `principles/${lane}.md`,
          sha256: "fixture", chars: 100, injection: "system", plug_id: `principles.${lane}`,
        }],
        system_text: "系统正文".repeat(500), turn_text: "轮次正文".repeat(500),
      };
    }
  }
  const args: BuildGraphArgs = {
    nodes, contextBundlesByNodeId, knownPlanspaceIds, activeNodeIds: [],
    nodePositions: {}, hiddenPlanspaceIds: [], autoPlanspaceIds: [],
    focusedPlanspaceId: knownPlanspaceIds[0], canCreateVirtual: false,
  };
  return { ...args, nodes, contextBundlesByNodeId };
}
