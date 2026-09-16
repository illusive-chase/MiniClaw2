import type { NodeInfo } from "../types";

export function artifactNodeId(nodeId: string, name: string): string {
  return `artifact:${nodeId}:${encodeURIComponent(name)}`;
}

export function artifactOverflowNodeId(nodeId: string): string {
  return `artifact-overflow:${nodeId}`;
}

export function nodeLayoutOwners(nodes: readonly NodeInfo[]): Map<string, NodeInfo> {
  const owners = new Map(nodes.map((node) => [node.id, node]));
  for (const node of nodes) {
    if (node.kind === "op") continue;
    if (node.state === "error" && node.error) owners.set(`err:${node.id}`, node);
    const published = (node.artifacts ?? []).filter((artifact) => artifact.status === "published");
    for (const artifact of published) {
      owners.set(artifactNodeId(node.id, artifact.name), node);
    }
    if (published.length > 4) owners.set(artifactOverflowNodeId(node.id), node);
  }
  return owners;
}
