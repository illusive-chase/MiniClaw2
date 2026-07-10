import type {
  ContextBundle,
  EventRecord,
  NodeDiff,
  NodeInfo,
  ModelPreset,
  ReviewBrief,
  NodeCategory,
  ReviewSubtype,
  PlanspaceMode,
  TemplateSummary,
  SessionFile,
  SessionFileRole,
  CanvasViewport,
  SessionInfo,
  SessionContextSpaceInfo,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | null;

  constructor(operation: string, status: number, detail: string | null) {
    super(`${operation} failed: ${status}${detail ? `: ${detail}` : ""}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function readErrorDetail(res: Response): Promise<string | null> {
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    try {
      const body: unknown = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        const detail = (body as { detail?: unknown }).detail;
        return typeof detail === "string" ? detail : JSON.stringify(detail);
      }
    } catch {
      return null;
    }
    return null;
  }
  const text = await res.text();
  return text || null;
}

export async function createSession(
  body: {
    cwd?: string;
    model_preset_id?: string;
    preferred_language?: string | null;
    temporary?: boolean;
    name?: string;
    project_context_binding_id?: string | null;
    create_missing_cwd?: boolean;
  } = {},
): Promise<SessionInfo> {
  const res = await fetch("/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError("createSession", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function listModelPresets(): Promise<ModelPreset[]> {
  const res = await fetch("/model-presets");
  if (!res.ok) throw new Error(`listModelPresets failed: ${res.status}`);
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

export async function createPlanspace(
  sessionId: string,
  body: {
    seed: string;
    mode?: PlanspaceMode;
    model_preset_id?: string;
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

export type CreateBlankPlanspacePayload = {
  title?: string;
  seed: string;
  mode: PlanspaceMode;
  model_preset_id?: string;
};

export async function createBlankPlanspace(
  sessionId: string,
  body: CreateBlankPlanspacePayload,
): Promise<{ planspace_id: string; binding_id: string; node_id: string }> {
  const res = await fetch(`/sessions/${sessionId}/planspaces/blank`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`createBlankPlanspace failed: ${res.status}`);
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

export async function rerunNode(
  sessionId: string,
  nodeId: string,
): Promise<{ ok: boolean; node_id: string; node: NodeInfo }> {
  const res = await fetch(
    `/sessions/${sessionId}/nodes/${encodeURIComponent(nodeId)}/rerun`,
    { method: "POST" },
  );
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = `${res.status}: ${body.detail}`;
    } catch {
      /* keep status-only detail */
    }
    throw new Error(`rerunNode failed: ${detail}`);
  }
  return res.json();
}

export type UpdateVirtualPayload = {
  prompt_draft?: string;
  category?: NodeCategory;
  subtype?: ReviewSubtype | null;
  brief?: ReviewBrief | null;
  motivation?: string | null;
  scheduled_deps?: string[];
  pending_extra_skills?: string[];
  model_preset_id?: string;
  obsolete_reason?: string | null;
};

export type CreateVirtualPayload = {
  prompt_draft: string;
  category?: NodeCategory;
  subtype?: ReviewSubtype | null;
  brief?: ReviewBrief | null;
  motivation?: string | null;
  scheduled_deps?: string[];
  pending_extra_skills?: string[];
  agent_op_kind?: string | null;
  model_preset_id?: string;
  planspace_id?: string | null;
  parent_node_id?: string | null;
  resume_from_node_id?: string | null;
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

export class DeleteVirtualConflictError extends Error {
  blockers: string[];

  constructor(blockers: string[]) {
    super(`deleteVirtual blocked by: ${blockers.join(", ")}`);
    this.name = "DeleteVirtualConflictError";
    this.blockers = blockers;
  }
}

export async function deleteVirtual(
  sessionId: string,
  nodeId: string,
): Promise<void> {
  const res = await fetch(
    `/sessions/${sessionId}/virtuals/${encodeURIComponent(nodeId)}`,
    { method: "DELETE" },
  );
  if (res.status === 204) return;
  let detail: unknown = res.status;
  try {
    const body = await res.json();
    detail = body?.detail ?? detail;
    const blockers = (body?.detail as { blockers?: unknown } | undefined)?.blockers;
    if (Array.isArray(blockers)) {
      throw new DeleteVirtualConflictError(
        blockers.filter((value): value is string => typeof value === "string"),
      );
    }
  } catch (err) {
    if (err instanceof DeleteVirtualConflictError) throw err;
  }
  throw new Error(
    `deleteVirtual failed: ${
      typeof detail === "string" ? detail : JSON.stringify(detail)
    }`,
  );
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
    keepalive: true,
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

export async function runTemplate(
  name: string,
  modelPresetId: string,
): Promise<SessionInfo> {
  const res = await fetch(`/templates/${encodeURIComponent(name)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_preset_id: modelPresetId }),
  });
  if (!res.ok) throw new Error(`runTemplate failed: ${res.status}`);
  return res.json();
}

export async function listUserTemplates(): Promise<TemplateSummary[]> {
  const res = await fetch("/user-templates");
  if (!res.ok) throw new Error(`listUserTemplates failed: ${res.status}`);
  return res.json();
}

export type SaveUserTemplatePayload = {
  name: string;
  brief: string;
  node_ids: string[];
};

export type SaveUserTemplateResponse = {
  slug: string;
  name: string;
  brief: string;
  node_count: number;
};

export async function saveUserTemplate(
  sessionId: string,
  payload: SaveUserTemplatePayload,
): Promise<SaveUserTemplateResponse> {
  const res = await fetch(
    `/sessions/${encodeURIComponent(sessionId)}/user-templates`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function applyUserTemplate(
  sessionId: string,
  slug: string,
  anchorNodeId: string | null,
): Promise<{ node_ids: string[] }> {
  const res = await fetch(
    `/sessions/${encodeURIComponent(sessionId)}/user-templates/${encodeURIComponent(slug)}/apply`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ anchor_node_id: anchorNodeId }),
    },
  );
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function deleteUserTemplate(slug: string): Promise<void> {
  const res = await fetch(`/user-templates/${encodeURIComponent(slug)}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    throw new Error(`deleteUserTemplate failed: ${res.status}`);
  }
}

export type SkillSummary = {
  id: string;
  kind: "skill";
  slug: string;
  title: string;
  description: string | null;
  injection: string | Record<string, string> | null;
  max_chars: number | Record<string, number> | null;
  path: string;
  exists: boolean;
};

export async function listSkills(): Promise<SkillSummary[]> {
  const res = await fetch("/skills");
  if (!res.ok) throw new Error(`listSkills failed: ${res.status}`);
  return res.json();
}

export async function deleteSkill(slug: string): Promise<void> {
  const res = await fetch(`/skills/${encodeURIComponent(slug)}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    throw new Error(`deleteSkill failed: ${res.status}`);
  }
}
