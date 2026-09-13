import type { ArtifactExtension, ArtifactRef } from "./types";

export function artifactExtension(name: string): ArtifactExtension | null {
  const suffixStart = name.lastIndexOf(".");
  if (suffixStart <= 0) return null;
  const extension = name.slice(suffixStart + 1);
  return extension === "md" || extension === "json" || extension === "html" || extension === "svg"
    ? extension
    : null;
}

export function artifactSelection(nodeId: string, artifact: ArtifactRef | null):
  | { kind: "agent"; nodeId: string }
  | { kind: "artifact"; nodeId: string; name: string; ext: ArtifactExtension } {
  const ext = artifact ? artifactExtension(artifact.name) : null;
  if (!artifact || artifact.status !== "published" || !ext) {
    return { kind: "agent", nodeId };
  }
  return { kind: "artifact", nodeId, name: artifact.name, ext };
}
