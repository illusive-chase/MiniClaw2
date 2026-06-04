import type {
  ContextBundle,
  EventRecord,
  NodeArtifact,
  NodeDiff,
  NodeInfo,
  ScenarioDetail,
  ScenarioSummary,
  SessionInfo,
  SessionContextSpaceInfo,
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
    name?: string;
    project_context_binding_id?: string | null;
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

export async function getSessionContextSpace(
  sessionId: string,
): Promise<SessionContextSpaceInfo> {
  const res = await fetch(`/sessions/${sessionId}/contextspace`);
  if (!res.ok) throw new Error(`getSessionContextSpace failed: ${res.status}`);
  return res.json();
}

export async function updateSessionContextSpace(
  sessionId: string,
  body: {
    project_context_binding_id?: string | null;
    active_planspace_id?: string | null;
  },
): Promise<SessionContextSpaceInfo> {
  const res = await fetch(`/sessions/${sessionId}/contextspace`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`updateSessionContextSpace failed: ${res.status}`);
  return res.json();
}

export async function bootstrapSessionContextSpace(
  sessionId: string,
  body: {
    title?: string;
    planspace_slug?: string;
    binding_slug?: string;
  } = {},
): Promise<SessionContextSpaceInfo> {
  const res = await fetch(`/sessions/${sessionId}/contextspace/bootstrap`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`bootstrapSessionContextSpace failed: ${res.status}`);
  return res.json();
}

export async function renameSession(id: string, name: string): Promise<SessionInfo> {
  const res = await fetch(`/sessions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`renameSession failed: ${res.status}`);
  return res.json();
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

export async function getNodeArtifact(
  sessionId: string,
  nodeId: string,
): Promise<NodeArtifact> {
  const res = await fetch(`/sessions/${sessionId}/nodes/${nodeId}/artifact`);
  if (!res.ok) throw new Error(`getNodeArtifact failed: ${res.status}`);
  return res.json();
}

export async function getNodeContextBundle(
  sessionId: string,
  nodeId: string,
): Promise<ContextBundle | null> {
  const res = await fetch(`/sessions/${sessionId}/nodes/${nodeId}/context-bundle`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`getNodeContextBundle failed: ${res.status}`);
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
