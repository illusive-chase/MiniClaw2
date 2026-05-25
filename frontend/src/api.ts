import type { EventRecord, NodeDiff, NodeInfo, SessionInfo } from "./types";

export async function createSession(
  body: { cwd?: string; model?: string; model_provider?: string; provider?: string } = {},
): Promise<SessionInfo> {
  const res = await fetch("/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`createSession failed: ${res.status}`);
  return res.json();
}

export async function listSessions(): Promise<SessionInfo[]> {
  const res = await fetch("/sessions");
  if (!res.ok) throw new Error(`listSessions failed: ${res.status}`);
  return res.json();
}

export async function deleteSession(id: string): Promise<void> {
  const res = await fetch(`/sessions/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`deleteSession failed: ${res.status}`);
}

export async function listNodes(sessionId: string): Promise<NodeInfo[]> {
  const res = await fetch(`/sessions/${sessionId}/nodes`);
  if (!res.ok) throw new Error(`listNodes failed: ${res.status}`);
  return res.json();
}

export async function listNodeEvents(
  sessionId: string,
  nodeId: string,
): Promise<EventRecord[]> {
  const res = await fetch(`/sessions/${sessionId}/nodes/${nodeId}/events`);
  if (!res.ok) throw new Error(`listNodeEvents failed: ${res.status}`);
  return res.json();
}

export async function getNodeDiff(
  sessionId: string,
  nodeId: string,
): Promise<NodeDiff> {
  const res = await fetch(`/sessions/${sessionId}/nodes/${nodeId}/diff`);
  if (!res.ok) throw new Error(`getNodeDiff failed: ${res.status}`);
  return res.json();
}
