import type {
  EventRecord,
  NodeDiff,
  NodeInfo,
  ScenarioDetail,
  ScenarioSummary,
  SessionInfo,
  VerifyResponse,
} from "./types";

export async function createSession(
  body: {
    cwd?: string;
    model?: string;
    model_provider?: string;
    provider?: string;
    temporary?: boolean;
    scenario_name?: string | null;
  } = {},
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

export async function listScenarios(): Promise<ScenarioSummary[]> {
  const res = await fetch("/scenarios");
  if (!res.ok) throw new Error(`listScenarios failed: ${res.status}`);
  return res.json();
}

export async function getScenario(name: string): Promise<ScenarioDetail> {
  const res = await fetch(`/scenarios/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`getScenario failed: ${res.status}`);
  return res.json();
}

export async function runScenario(
  name: string,
  provider: "claude" | "codex",
): Promise<SessionInfo> {
  const res = await fetch(`/scenarios/${encodeURIComponent(name)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  });
  if (!res.ok) throw new Error(`runScenario failed: ${res.status}`);
  return res.json();
}

export async function verifySession(sessionId: string): Promise<VerifyResponse> {
  const res = await fetch(`/sessions/${sessionId}/verify`, { method: "POST" });
  if (!res.ok) throw new Error(`verifySession failed: ${res.status}`);
  return res.json();
}
