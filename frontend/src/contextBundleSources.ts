import type { ContextBundleSources, NodeInfo } from "./types";

export type ContextBundleSourcesByNodeId = Record<string, ContextBundleSources | null>;

type FetchSources = (
  sessionId: string, nodeIds?: string[], signal?: AbortSignal,
) => Promise<ContextBundleSourcesByNodeId>;

const TERMINAL_STATES = new Set<NodeInfo["state"]>(["done", "error", "cancelled"]);

export class ContextBundleSourcesLoader {
  private desired = new Map<string, string>();
  private loaded = new Map<string, string>();
  private bundles: ContextBundleSourcesByNodeId = {};
  private request: AbortController | null = null;
  private started = false;
  private disposed = false;

  constructor(
    private readonly sessionId: string,
    private readonly fetchSources: FetchSources,
    private readonly publish: (bundles: ContextBundleSourcesByNodeId) => void,
    private readonly onError: (error: unknown) => void,
  ) {}

  update(nodes: NodeInfo[]): void {
    if (this.disposed) return;
    this.desired = new Map(nodes
      .filter((node) => node.kind !== "op" && TERMINAL_STATES.has(node.state)
        && (node.context_bundle_id || node.context_bundle_path))
      .map((node) => [node.id, JSON.stringify([
        node.context_bundle_id, node.context_bundle_path, node.finished_at,
      ])]));
    let changed = false;
    for (const [nodeId, signature] of this.loaded) {
      if (this.desired.get(nodeId) === signature) continue;
      this.loaded.delete(nodeId);
      changed = true;
    }
    if (changed) {
      this.bundles = Object.fromEntries(Object.entries(this.bundles)
        .filter(([nodeId]) => this.loaded.has(nodeId)));
      this.publish(this.bundles);
    }
    void this.loadMissing();
  }

  dispose(): void {
    this.disposed = true;
    this.request?.abort();
  }

  private async loadMissing(): Promise<void> {
    if (this.disposed || this.request) return;
    const missing = new Map([...this.desired]
      .filter(([nodeId, signature]) => this.loaded.get(nodeId) !== signature));
    if (missing.size === 0) return;
    const controller = new AbortController();
    this.request = controller;
    const nodeIds = this.started && missing.size <= 100 ? [...missing.keys()] : undefined;
    this.started = true;
    let succeeded = false;
    try {
      const result = await this.fetchSources(this.sessionId, nodeIds, controller.signal);
      if (this.disposed) return;
      const next = { ...this.bundles };
      let changed = false;
      for (const [nodeId, signature] of missing) {
        if (this.desired.get(nodeId) !== signature) continue;
        next[nodeId] = result[nodeId] ?? null;
        this.loaded.set(nodeId, signature);
        changed = true;
      }
      if (changed) {
        this.bundles = next;
        this.publish(next);
      }
      succeeded = true;
    } catch (error) {
      if (!this.disposed) this.onError(error);
    } finally {
      this.request = null;
      if (succeeded) void this.loadMissing();
    }
  }
}
