import type { NodeDetail } from "./types";

export function nodeDetailKey(sessionId: string, nodeId: string, rev: number): string {
  return JSON.stringify([sessionId, nodeId, rev]);
}

export class NodeDetailCache {
  private readonly entries = new Map<string, NodeDetail>();
  private readonly pending = new Map<string, Promise<NodeDetail>>();

  constructor(
    private readonly fetchDetail: (sessionId: string, nodeId: string) => Promise<NodeDetail>,
    private readonly limit = 32,
  ) {}

  get(sessionId: string, nodeId: string, rev: number): NodeDetail | null {
    const key = nodeDetailKey(sessionId, nodeId, rev);
    const detail = this.entries.get(key);
    if (!detail) return null;
    this.entries.delete(key);
    this.entries.set(key, detail);
    return detail;
  }

  load(sessionId: string, nodeId: string, rev: number): Promise<NodeDetail> {
    const cached = this.get(sessionId, nodeId, rev);
    if (cached) return Promise.resolve(cached);
    const key = nodeDetailKey(sessionId, nodeId, rev);
    const pending = this.pending.get(key);
    if (pending) return pending;
    const request = this.fetchDetail(sessionId, nodeId).then((detail) => {
      if (detail.id !== nodeId || detail.project_id !== sessionId || (detail.rev ?? 0) < rev) {
        throw new Error("节点详情版本已过期，请重试。");
      }
      this.entries.set(nodeDetailKey(sessionId, nodeId, detail.rev ?? 0), detail);
      while (this.entries.size > this.limit) {
        const oldest = this.entries.keys().next().value;
        if (oldest === undefined) break;
        this.entries.delete(oldest);
      }
      return detail;
    }).finally(() => this.pending.delete(key));
    this.pending.set(key, request);
    return request;
  }
}
