import type {
  ContextBundle,
  EventRecord,
  NodeDiff,
  NodeInfo,
  NodeStatusDelta,
  ScenarioDetail,
  ScenarioSummary,
  SessionFile,
  SessionFileRole,
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

export async function createPlanspace(
  sessionId: string,
  body: {
    user_seed: string;
    needs_review?: boolean;
  },
): Promise<{ planspace_id: string; binding_id: string; node_id: string }> {
  const res = await fetch(`/sessions/${sessionId}/planspaces`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`createPlanspace failed: ${res.status}`);
  return res.json();
}

export async function updatePlanspaceView(
  sessionId: string,
  planspaces: Record<string, { hidden: boolean }>,
): Promise<SessionContextSpaceInfo> {
  const res = await fetch(`/sessions/${sessionId}/planspace-view`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ planspaces }),
  });
  if (!res.ok) throw new Error(`updatePlanspaceView failed: ${res.status}`);
  return res.json();
}

export async function initProjectContext(
  sessionId: string,
): Promise<SessionContextSpaceInfo> {
  const res = await fetch(`/sessions/${sessionId}/context/init`, { method: "POST" });
  if (!res.ok) throw new Error(`initProjectContext failed: ${res.status}`);
  return res.json();
}

export async function refreshProjectContext(
  sessionId: string,
): Promise<SessionContextSpaceInfo> {
  const res = await fetch(`/sessions/${sessionId}/context/refresh`, { method: "POST" });
  if (!res.ok) throw new Error(`refreshProjectContext failed: ${res.status}`);
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

export async function updateLayoutHints(
  sessionId: string,
  updates: Record<string, { x: number; y: number }>,
  remove: string[] = [],
): Promise<SessionInfo> {
  const res = await fetch(`/sessions/${sessionId}/layout-hints`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ updates, remove }),
  });
  if (!res.ok) throw new Error(`updateLayoutHints failed: ${res.status}`);
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

export async function getNodeContextBundle(
  sessionId: string,
  nodeId: string,
): Promise<ContextBundle | null> {
  const res = await fetch(`/sessions/${sessionId}/nodes/${nodeId}/context-bundle`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`getNodeContextBundle failed: ${res.status}`);
  return res.json();
}

export async function getSessionFile(
  sessionId: string,
  role: SessionFileRole,
  planspaceId?: string | null,
): Promise<SessionFile> {
  const params = new URLSearchParams({ role });
  if (planspaceId) params.set("planspace_id", planspaceId);
  const res = await fetch(`/sessions/${sessionId}/files?${params.toString()}`);
  if (!res.ok) throw new Error(`getSessionFile failed: ${res.status}`);
  return res.json();
}

export async function getNodeStatusDelta(
  sessionId: string,
  nodeId: string,
): Promise<NodeStatusDelta | null> {
  const res = await fetch(
    `/sessions/${sessionId}/nodes/${encodeURIComponent(nodeId)}/status-delta`,
  );
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`getNodeStatusDelta failed: ${res.status}`);
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

export type PlanspaceStatusEntry = {
  id: string;
  summary: string;
  raised_at?: string;
  decided_at?: string;
  raised_by?: string;
  decided_by?: string;
};

export type PlanspaceStatusView = {
  planspace_id: string;
  title: string;
  color: string | null;
  status: {
    goal: string;
    current_state: string;
    open_questions: PlanspaceStatusEntry[];
    decisions: PlanspaceStatusEntry[];
    out_of_scope: string[];
    body: string;
  };
  applied?: Array<Record<string, unknown>>;
};

export type PlanspaceStatusOp =
  | { operation: "rewrite_current_state"; text: string }
  | { operation: "add_open_question"; summary: string }
  | { operation: "add_decision"; summary: string }
  | { operation: "add_out_of_scope"; summary: string }
  | { operation: "remove_open_question"; id: string }
  | { operation: "remove_decision"; id: string }
  | { operation: "remove_out_of_scope"; index: number };

export async function getPlanspaceStatus(
  sessionId: string,
  planspaceId: string,
): Promise<PlanspaceStatusView> {
  const res = await fetch(
    `/sessions/${sessionId}/planspaces/${encodeURIComponent(planspaceId)}/status`,
  );
  if (!res.ok) throw new Error(`getPlanspaceStatus failed: ${res.status}`);
  return res.json();
}

export async function patchPlanspaceStatus(
  sessionId: string,
  planspaceId: string,
  operations: PlanspaceStatusOp[],
): Promise<PlanspaceStatusView> {
  const res = await fetch(
    `/sessions/${sessionId}/planspaces/${encodeURIComponent(planspaceId)}/status`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operations }),
    },
  );
  if (!res.ok) throw new Error(`patchPlanspaceStatus failed: ${res.status}`);
  return res.json();
}

export async function verifySession(sessionId: string): Promise<VerifyResponse> {
  const res = await fetch(`/sessions/${sessionId}/verify`, { method: "POST" });
  if (!res.ok) throw new Error(`verifySession failed: ${res.status}`);
  return res.json();
}
