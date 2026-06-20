import type {
  ContextBundle,
  EventRecord,
  NodeDiff,
  NodeInfo,
  ReviewBrief,
  NodeCategory,
  ReviewSubtype,
  PlanspaceMode,
  TemplateDetail,
  TemplateSummary,
  SessionFile,
  SessionFileRole,
  CanvasViewport,
  SessionInfo,
  SessionContextSpaceInfo,
} from "./types";

export async function createSession(
  body: {
    cwd?: string;
    model?: string;
    model_provider?: string;
    provider?: string;
    preferred_language?: string | null;
    temporary?: boolean;
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

export async function getSession(id: string): Promise<SessionInfo> {
  const res = await fetch(`/sessions/${id}`);
  if (!res.ok) throw new Error(`getSession failed: ${res.status}`);
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
    mode?: PlanspaceMode;
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

export async function updatePlanspaceMode(
  sessionId: string,
  planspaceId: string,
  mode: PlanspaceMode,
): Promise<SessionContextSpaceInfo> {
  const res = await fetch(
    `/sessions/${sessionId}/planspaces/${encodeURIComponent(planspaceId)}/mode`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    },
  );
  if (!res.ok) throw new Error(`updatePlanspaceMode failed: ${res.status}`);
  return res.json();
}

export async function promoteVirtual(
  sessionId: string,
  nodeId: string,
): Promise<{ ok: boolean; node_id: string; node: NodeInfo }> {
  const res = await fetch(
    `/sessions/${sessionId}/virtuals/${encodeURIComponent(nodeId)}/promote`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`promoteVirtual failed: ${res.status}`);
  return res.json();
}

export type UpdateVirtualPayload = {
  prompt_draft?: string;
  category?: NodeCategory;
  subtype?: ReviewSubtype | null;
  brief?: ReviewBrief | null;
  motivation?: string | null;
  scheduled_deps?: string[];
  obsolete_reason?: string | null;
};

export type CreateVirtualPayload = {
  prompt_draft: string;
  category?: NodeCategory;
  subtype?: ReviewSubtype | null;
  brief?: ReviewBrief | null;
  motivation?: string | null;
  scheduled_deps?: string[];
  planspace_id?: string | null;
};

export async function createVirtual(
  sessionId: string,
  body: CreateVirtualPayload,
): Promise<{ ok: boolean; node_id: string; node: NodeInfo }> {
  const res = await fetch(`/sessions/${sessionId}/virtuals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = `${res.status}: ${body.detail}`;
    } catch {
      /* keep status-only detail */
    }
    throw new Error(`createVirtual failed: ${detail}`);
  }
  return res.json();
}

export async function updateVirtual(
  sessionId: string,
  nodeId: string,
  body: UpdateVirtualPayload,
): Promise<{ ok: boolean; node_id: string; node: NodeInfo }> {
  const res = await fetch(
    `/sessions/${sessionId}/virtuals/${encodeURIComponent(nodeId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = `${res.status}: ${body.detail}`;
    } catch {
      /* keep status-only detail */
    }
    throw new Error(`updateVirtual failed: ${detail}`);
  }
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

export async function cancelProjectContext(
  sessionId: string,
): Promise<SessionContextSpaceInfo> {
  const res = await fetch(`/sessions/${sessionId}/context/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(`cancelProjectContext failed: ${res.status}`);
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

export async function updateSessionPreferences(
  id: string,
  body: {
    preferred_language?: string | null;
  },
): Promise<SessionInfo> {
  const res = await fetch(`/sessions/${id}/preferences`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`updateSessionPreferences failed: ${res.status}`);
  return res.json();
}

export async function updateLayoutHints(
  sessionId: string,
  updates: Record<string, { x: number; y: number }> = {},
  remove: string[] = [],
  layoutViewport?: CanvasViewport | null,
): Promise<SessionInfo> {
  const body: {
    updates: Record<string, { x: number; y: number }>;
    remove: string[];
    layout_viewport?: CanvasViewport;
  } = { updates, remove };
  if (layoutViewport) body.layout_viewport = layoutViewport;
  const res = await fetch(`/sessions/${sessionId}/layout-hints`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
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

export async function getNodePreview(
  sessionId: string,
  nodeId: string,
): Promise<{ text: string } | null> {
  const res = await fetch(
    `/sessions/${sessionId}/nodes/${encodeURIComponent(nodeId)}/preview`,
  );
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`getNodePreview failed: ${res.status}`);
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

export async function listTemplates(): Promise<TemplateSummary[]> {
  const res = await fetch("/templates");
  if (!res.ok) throw new Error(`listTemplates failed: ${res.status}`);
  return res.json();
}

export async function getTemplate(name: string): Promise<TemplateDetail> {
  const res = await fetch(`/templates/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`getTemplate failed: ${res.status}`);
  return res.json();
}

export async function runTemplate(
  name: string,
  provider: "claude" | "codex",
): Promise<SessionInfo> {
  const res = await fetch(`/templates/${encodeURIComponent(name)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  });
  if (!res.ok) throw new Error(`runTemplate failed: ${res.status}`);
  return res.json();
}
