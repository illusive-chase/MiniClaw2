import type {
  ActiveNodesResponse,
  ContextBundle,
  EventRecord,
  NodeDiff,
  NodeInfo,
  ModelPreset,
  GlobalDefaults,
  CodeReviewSettings,
  GlobalState,
  ToolRequestSettings,
  SelfUpdateApplyResult,
  SelfUpdateState,
  ReviewBrief,
  NodeCategory,
  ReviewSubtype,
  PlanspaceMode,
  TemplateSummary,
  TemplateDetail,
  TemplateInstanceRecord,
  SessionFile,
  SessionFileRole,
  CanvasViewport,
  SessionInfo,
  SessionContextSpaceInfo,
  ArtifactFile,
  ArtifactMode,
  GitState,
  GitStatus,
  SkillSelection,
  SkillSummary,
  SkillDetail,
  Tag,
} from "./types";
import type { TemplateRewritePayload } from "./templateEditor";

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
        if (typeof detail === "string") return detail;
        if (detail && typeof detail === "object" && "message" in detail) {
          const message = (detail as { message?: unknown }).message;
          if (typeof message === "string") return message;
        }
        return JSON.stringify(detail);
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
    auto_commit?: boolean;
    preferred_language?: string | null;
    concurrency?: number;
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

export async function getGlobalState(): Promise<GlobalState> {
  const res = await fetch("/global-state");
  if (!res.ok) throw new ApiError("getGlobalState", res.status, await readErrorDetail(res));
  return res.json();
}

export async function setupSync(remoteUrl: string): Promise<GlobalState> {
  const res = await fetch("/global-state/sync/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      remote_url: remoteUrl,
      privacy_acknowledged: true,
    }),
  });
  if (!res.ok) {
    throw new ApiError("setupSync", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function syncNow(): Promise<GlobalState> {
  const res = await fetch("/global-state/sync", { method: "POST" });
  if (!res.ok) {
    throw new ApiError("syncNow", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function checkSyncRemote(): Promise<GlobalState> {
  const res = await fetch("/global-state/sync/check", { method: "POST" });
  if (!res.ok) {
    throw new ApiError("checkSyncRemote", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function updateGlobalDefaults(
  body: Partial<GlobalDefaults>,
): Promise<GlobalState> {
  const res = await fetch("/global-state/defaults", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError("updateGlobalDefaults", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function updateCodeReviewSettings(
  body: CodeReviewSettings,
): Promise<GlobalState> {
  const res = await fetch("/global-state/code-review", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(
      "updateCodeReviewSettings",
      res.status,
      await readErrorDetail(res),
    );
  }
  return res.json();
}

export async function updateToolRequestSettings(
  body: Partial<ToolRequestSettings>,
): Promise<GlobalState> {
  const res = await fetch("/global-state/tool-requests", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(
      "updateToolRequestSettings",
      res.status,
      await readErrorDetail(res),
    );
  }
  return res.json();
}

export async function getSelfUpdate(): Promise<SelfUpdateState> {
  const res = await fetch("/self-update");
  if (!res.ok) throw new ApiError("getSelfUpdate", res.status, await readErrorDetail(res));
  return res.json();
}

export async function checkSelfUpdate(): Promise<SelfUpdateState> {
  const res = await fetch("/self-update/check", { method: "POST" });
  if (!res.ok) throw new ApiError("checkSelfUpdate", res.status, await readErrorDetail(res));
  return res.json();
}

export async function applySelfUpdate(): Promise<SelfUpdateApplyResult> {
  const res = await fetch("/self-update/apply", { method: "POST" });
  if (!res.ok) throw new ApiError("applySelfUpdate", res.status, await readErrorDetail(res));
  return res.json();
}

export async function createModelPreset(preset: ModelPreset): Promise<GlobalState> {
  const res = await fetch("/global-state/model-presets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(preset),
  });
  if (!res.ok) {
    throw new ApiError("createModelPreset", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function replaceModelPreset(preset: ModelPreset): Promise<GlobalState> {
  const res = await fetch(`/global-state/model-presets/${encodeURIComponent(preset.id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(preset),
  });
  if (!res.ok) {
    throw new ApiError("replaceModelPreset", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function deleteModelPreset(presetId: string): Promise<void> {
  const res = await fetch(
    `/global-state/model-presets/${encodeURIComponent(presetId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw new ApiError("deleteModelPreset", res.status, await readErrorDetail(res));
  }
}

export async function listTags(): Promise<Tag[]> {
  const res = await fetch("/tags");
  if (!res.ok) throw new ApiError("listTags", res.status, await readErrorDetail(res));
  return res.json();
}

export async function createTag(name: string, color?: string): Promise<Tag> {
  const res = await fetch("/tags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(color === undefined ? { name } : { name, color }),
  });
  if (!res.ok) throw new ApiError("createTag", res.status, await readErrorDetail(res));
  return res.json();
}

/* Omitted fields are left alone server-side, so a rename never resets the
 * color and a recolor never touches the name. */
export async function updateTag(
  tagId: string,
  patch: { name?: string; color?: string },
): Promise<Tag> {
  const res = await fetch(`/tags/${tagId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new ApiError("updateTag", res.status, await readErrorDetail(res));
  return res.json();
}

/** Also strips the id from every project that referenced it. */
export async function deleteTag(tagId: string): Promise<void> {
  const res = await fetch(`/tags/${tagId}`, { method: "DELETE" });
  if (!res.ok) throw new ApiError("deleteTag", res.status, await readErrorDetail(res));
}

export async function updateSessionTags(
  id: string,
  tagIds: string[],
): Promise<SessionInfo> {
  const res = await fetch(`/sessions/${id}/tags`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tag_ids: tagIds }),
  });
  if (!res.ok) {
    throw new ApiError("updateSessionTags", res.status, await readErrorDetail(res));
  }
  return res.json();
}

let listSessionsInFlight: Promise<SessionInfo[]> | null = null;

export function listSessions(): Promise<SessionInfo[]> {
  if (listSessionsInFlight) return listSessionsInFlight;

  const request = (async () => {
    const res = await fetch("/sessions");
    if (!res.ok) throw new Error(`listSessions failed: ${res.status}`);
    return res.json() as Promise<SessionInfo[]>;
  })();
  listSessionsInFlight = request;
  const clear = () => {
    if (listSessionsInFlight === request) listSessionsInFlight = null;
  };
  void request.then(clear, clear);
  return request;
}

export async function listActiveNodes(): Promise<ActiveNodesResponse> {
  const res = await fetch("/active-nodes");
  if (!res.ok) {
    throw new ApiError("listActiveNodes", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function getSession(id: string): Promise<SessionInfo> {
  const res = await fetch(`/sessions/${id}`);
  if (!res.ok) throw new Error(`getSession failed: ${res.status}`);
  return res.json();
}

export async function getGitState(sessionId: string): Promise<GitState> {
  const res = await fetch(`/sessions/${sessionId}/git`);
  if (!res.ok) throw new ApiError("getGitState", res.status, await readErrorDetail(res));
  return res.json();
}

export async function gitCommit(
  sessionId: string,
  message: string,
): Promise<{ node: NodeInfo }> {
  const res = await fetch(`/sessions/${sessionId}/git/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new ApiError("gitCommit", res.status, await readErrorDetail(res));
  return res.json();
}

export async function gitReview(sessionId: string): Promise<{ node: NodeInfo }> {
  const res = await fetch(`/sessions/${sessionId}/git/review`, { method: "POST" });
  if (!res.ok) throw new ApiError("gitReview", res.status, await readErrorDetail(res));
  return res.json();
}

export async function gitPull(sessionId: string): Promise<{ node: NodeInfo }> {
  const res = await fetch(`/sessions/${sessionId}/git/pull`, { method: "POST" });
  if (!res.ok) throw new ApiError("gitPull", res.status, await readErrorDetail(res));
  return res.json();
}

export async function gitPush(sessionId: string): Promise<{ status: GitStatus }> {
  const res = await fetch(`/sessions/${sessionId}/git/push`, { method: "POST" });
  if (!res.ok) throw new ApiError("gitPush", res.status, await readErrorDetail(res));
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
): Promise<{
  planspace_id: string;
  binding_id: string;
  node_id: string;
  activated: boolean;
}> {
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
): Promise<{
  planspace_id: string;
  binding_id: string;
  node_id: string;
  activated: boolean;
}> {
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

/** Raised when the lane still holds queued or running nodes. */
export class DeletePlanspaceBusyError extends Error {
  busy: string[];

  constructor(busy: string[]) {
    super(`deletePlanspace blocked by: ${busy.join(", ")}`);
    this.name = "DeletePlanspaceBusyError";
    this.busy = busy;
  }
}

export async function deletePlanspace(
  sessionId: string,
  planspaceId: string,
): Promise<SessionContextSpaceInfo> {
  const res = await fetch(
    `/sessions/${sessionId}/planspaces/${encodeURIComponent(planspaceId)}`,
    { method: "DELETE" },
  );
  if (res.ok) return res.json();
  let detail: string | null = null;
  try {
    const body: unknown = await res.json();
    const raw = (body as { detail?: unknown } | null)?.detail;
    const busy = (raw as { busy?: unknown } | undefined)?.busy;
    if (Array.isArray(busy)) {
      throw new DeletePlanspaceBusyError(
        busy.filter((value): value is string => typeof value === "string"),
      );
    }
    detail = typeof raw === "string" ? raw : raw ? JSON.stringify(raw) : null;
  } catch (err) {
    if (err instanceof DeletePlanspaceBusyError) throw err;
  }
  throw new ApiError("deletePlanspace", res.status, detail);
}

export async function promoteVirtual(
  sessionId: string,
  nodeId: string,
): Promise<{
  ok: boolean;
  node_id: string;
  node: NodeInfo;
  already_promoted?: boolean;
}> {
  const res = await fetch(
    `/sessions/${sessionId}/virtuals/${encodeURIComponent(nodeId)}/promote`,
    { method: "POST" },
  );
  if (!res.ok) {
    throw new ApiError("promoteVirtual", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function dequeueNode(
  sessionId: string,
  nodeId: string,
): Promise<{ ok: boolean; node_id: string; node: NodeInfo }> {
  const res = await fetch(
    `/sessions/${sessionId}/nodes/${encodeURIComponent(nodeId)}/dequeue`,
    { method: "POST" },
  );
  if (!res.ok) {
    throw new ApiError("dequeueNode", res.status, await readErrorDetail(res));
  }
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
  review_target?: { type: "uncommitted" } | null;
  motivation?: string | null;
  scheduled_deps?: string[];
  pending_extra_principles?: string[];
  pending_extra_skills?: SkillSelection[];
  qa_mode?: boolean;
  artifact_mode?: ArtifactMode;
  artifact_spec?: string;
  agent_op_kind?: string | null;
  model_preset_id?: string;
  obsolete_reason?: string | null;
};

export type CreateVirtualPayload = {
  prompt_draft: string;
  category?: NodeCategory;
  subtype?: ReviewSubtype | null;
  brief?: ReviewBrief | null;
  review_target?: { type: "uncommitted" } | null;
  motivation?: string | null;
  scheduled_deps?: string[];
  pending_extra_principles?: string[];
  pending_extra_skills?: SkillSelection[];
  qa_mode?: boolean;
  artifact_mode?: ArtifactMode;
  artifact_spec?: string;
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

export async function bindProjectHere(
  id: string,
  rootPath: string,
  options: {
    unverifiedAcknowledged?: boolean;
  } = {},
): Promise<SessionInfo> {
  const res = await fetch(`/sessions/${id}/hosts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      root_path: rootPath,
      unverified_acknowledged: options.unverifiedAcknowledged ?? false,
    }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null) as { detail?: string } | null;
    throw new Error(detail?.detail || `bindProjectHere failed: ${res.status}`);
  }
  return res.json();
}

export async function unbindProjectHere(
  id: string,
  machineId: string,
): Promise<SessionInfo> {
  const res = await fetch(
    `/sessions/${id}/hosts/${encodeURIComponent(machineId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const detail = await res.json().catch(() => null) as { detail?: string } | null;
    throw new Error(detail?.detail || `unbindProjectHere failed: ${res.status}`);
  }
  return res.json();
}

/** Ask the backend to open the project directory in the host file manager.
 * Only the backend can do this — the browser cannot reveal a local path. */
export async function revealProjectRoot(id: string): Promise<{ root_path: string }> {
  const res = await fetch(`/sessions/${id}/reveal`, { method: "POST" });
  if (!res.ok) {
    throw new ApiError("revealProjectRoot", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function updateSessionPreferences(
  id: string,
  body: {
    preferred_language?: string | null;
    concurrency?: number;
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

export async function getReviewedDiff(
  sessionId: string,
  nodeId: string,
): Promise<NodeDiff> {
  const res = await fetch(`/sessions/${sessionId}/nodes/${nodeId}/reviewed-diff`);
  if (!res.ok) throw new Error(`getReviewedDiff failed: ${res.status}`);
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

export function artifactRawUrl(
  sessionId: string,
  nodeId: string,
  name: string,
): string {
  return `/sessions/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(nodeId)}/artifacts/${encodeURIComponent(name)}?raw=1`;
}

export async function getNodeArtifact(
  sessionId: string,
  nodeId: string,
  name: string,
): Promise<ArtifactFile> {
  const res = await fetch(
    `/sessions/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(nodeId)}/artifacts/${encodeURIComponent(name)}`,
  );
  if (!res.ok) {
    throw new ApiError("getNodeArtifact", res.status, await readErrorDetail(res));
  }
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

/** Read one template with full prompt source for each node.
 *
 * The list endpoints carry only `prompt_preview`, a 160-character truncation.
 * The editor must load through here — editing a preview would silently discard
 * the tail of every long prompt on the next save.
 */
export async function getUserTemplate(slug: string): Promise<TemplateDetail> {
  const res = await fetch(`/user-templates/${encodeURIComponent(slug)}`);
  if (!res.ok) {
    throw new ApiError("getUserTemplate", res.status, await readErrorDetail(res));
  }
  return res.json();
}

/** Replace a template from the editor's complete state.
 *
 * The backend writes a candidate directory, loads it back through the same
 * loader path the runtime uses, and only then swaps it in — so a 400 here means
 * the old template is still intact on disk and the editor may keep its state.
 */
export async function rewriteUserTemplate(
  slug: string,
  payload: TemplateRewritePayload,
): Promise<TemplateDetail> {
  const res = await fetch(`/user-templates/${encodeURIComponent(slug)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new ApiError(
      "rewriteUserTemplate",
      res.status,
      await readErrorDetail(res),
    );
  }
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

export type ApplyUserTemplatePayload = {
  anchor_node_id: string | null;
  arguments: Record<string, string>;
  input_bindings: Record<string, string>;
};

export type ApplyUserTemplateResponse = {
  node_ids: string[];
  /** Groups the stamped nodes; the collapse state is keyed by it. */
  instance_id: string;
};

export async function applyUserTemplate(
  sessionId: string,
  slug: string,
  payload: ApplyUserTemplatePayload,
): Promise<ApplyUserTemplateResponse> {
  const res = await fetch(
    `/sessions/${encodeURIComponent(sessionId)}/user-templates/${encodeURIComponent(slug)}/apply`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!res.ok) {
    throw new ApiError("applyUserTemplate", res.status, await readErrorDetail(res));
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

/** Open (or re-attach to) a template's embedded editing session.
 *
 * The session is an ordinary temporary project whose lane holds the template's
 * own nodes with `{{placeholder}}` text intact, so the shared project canvas can
 * edit it. Calling this twice returns the same session rather than stamping a
 * second copy — otherwise unsaved edits in the first would become unreachable. */
export async function openUserTemplateSession(
  slug: string,
): Promise<SessionInfo> {
  const res = await fetch(
    `/user-templates/${encodeURIComponent(slug)}/session`,
    { method: "POST" },
  );
  if (!res.ok) {
    throw new ApiError(
      "openUserTemplateSession",
      res.status,
      await readErrorDetail(res),
    );
  }
  return res.json();
}

/** Write an embedded session's graph back onto its template. Explicit: editing
 * the session never saves on its own. */
export async function commitUserTemplateSession(
  slug: string,
): Promise<TemplateDetail> {
  const res = await fetch(
    `/user-templates/${encodeURIComponent(slug)}/session/commit`,
    { method: "POST" },
  );
  if (!res.ok) {
    throw new ApiError(
      "commitUserTemplateSession",
      res.status,
      await readErrorDetail(res),
    );
  }
  return res.json();
}

/** Discard an embedded session. Uncommitted graph edits go with it. */
export async function discardUserTemplateSession(slug: string): Promise<void> {
  const res = await fetch(
    `/user-templates/${encodeURIComponent(slug)}/session`,
    { method: "DELETE" },
  );
  if (!res.ok && res.status !== 204) {
    throw new ApiError(
      "discardUserTemplateSession",
      res.status,
      await readErrorDetail(res),
    );
  }
}

/** Instance records for one planspace, newest last. Nodes carry only the
 * instance id, so the group header reads its template and arguments here. */
export async function listTemplateInstances(
  sessionId: string,
  planspaceId: string,
): Promise<TemplateInstanceRecord[]> {
  const res = await fetch(
    `/sessions/${encodeURIComponent(sessionId)}/planspaces/${encodeURIComponent(planspaceId)}/template-instances`,
  );
  if (!res.ok) {
    throw new ApiError("listTemplateInstances", res.status, await readErrorDetail(res));
  }
  return res.json();
}

export async function deleteTemplateInstance(
  sessionId: string,
  planspaceId: string,
  instanceId: string,
): Promise<{ ok: boolean; removed_node_ids: string[] }> {
  const res = await fetch(
    `/sessions/${encodeURIComponent(sessionId)}/planspaces/${encodeURIComponent(planspaceId)}/template-instances/${encodeURIComponent(instanceId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw new ApiError(
      "deleteTemplateInstance",
      res.status,
      await readErrorDetail(res),
    );
  }
  return res.json();
}

export type PrincipleSummary = {
  id: string;
  kind: "principle";
  slug: string;
  title: string;
  description: string | null;
  injection: string | Record<string, string> | null;
  max_chars: number | Record<string, number> | null;
  path: string;
  exists: boolean;
};

/** A principle plus its CONTEXT.md text. `body` is null when the plug exists
 * but has no CONTEXT.md yet — an authoring state, not a failure. */
export type PrincipleDetail = PrincipleSummary & {
  body: string | null;
  body_path: string;
};

export async function listPrinciples(): Promise<PrincipleSummary[]> {
  const res = await fetch("/principles");
  if (!res.ok) throw new Error(`listPrinciples failed: ${res.status}`);
  return res.json();
}

const principleDetailRequests = new Map<string, Promise<PrincipleDetail>>();

/** Read one principle with its CONTEXT.md body. Concurrent calls for the same
 * slug share a request, so StrictMode's double effect fetches once. */
export function getPrinciple(slug: string): Promise<PrincipleDetail> {
  const pending = principleDetailRequests.get(slug);
  if (pending) return pending;
  const request = (async () => {
    const res = await fetch(`/principles/${encodeURIComponent(slug)}`);
    if (!res.ok) {
      throw new ApiError("getPrinciple", res.status, await readErrorDetail(res));
    }
    return res.json() as Promise<PrincipleDetail>;
  })();
  principleDetailRequests.set(slug, request);
  void request.then(
    () => principleDetailRequests.delete(slug),
    () => principleDetailRequests.delete(slug),
  );
  return request;
}

export async function deletePrinciple(slug: string): Promise<void> {
  const res = await fetch(`/principles/${encodeURIComponent(slug)}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    throw new Error(`deletePrinciple failed: ${res.status}`);
  }
}

export type { SkillDetail, SkillSummary } from "./types";

export type SkillPackImport = {
  kind: "skill-pack";
  package_id: string;
  source: string;
  count: number;
  skills: SkillSummary[];
};

export async function listSkills(): Promise<SkillSummary[]> {
  const res = await fetch("/skills");
  if (!res.ok) throw new Error(`listSkills failed: ${res.status}`);
  return res.json();
}

const skillDetailRequests = new Map<string, Promise<SkillDetail>>();

/** Read one skill with its complete SKILL.md body. */
export function getSkill(slug: string): Promise<SkillDetail> {
  const pending = skillDetailRequests.get(slug);
  if (pending) return pending;
  const request = (async () => {
    const res = await fetch(`/skills/${encodeURIComponent(slug)}`);
    if (!res.ok) {
      throw new ApiError("getSkill", res.status, await readErrorDetail(res));
    }
    return res.json() as Promise<SkillDetail>;
  })();
  skillDetailRequests.set(slug, request);
  void request.then(
    () => skillDetailRequests.delete(slug),
    () => skillDetailRequests.delete(slug),
  );
  return request;
}

export async function importSkill(
  source: string,
  slug?: string,
): Promise<SkillSummary | SkillPackImport> {
  const res = await fetch("/skills/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, slug: slug || null }),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(payload?.detail || `importSkill failed: ${res.status}`);
  }
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
