// Mirror of backend WebSocket events (backend/miniclaw2/events.py).

export type TextDelta = { type: "text_delta"; text: string; seq?: number };

export type Thinking = { type: "thinking"; text: string; seq?: number };

export type ResultKind = "stdout" | "diff" | "text" | "json";

export type Activity = {
  type: "activity";
  kind: "tool" | "agent";
  status: "start" | "finish" | "failed" | "progress";
  id: string;
  name: string;
  summary: string;
  result?: string | null;
  result_kind?: ResultKind | null;
  seq?: number;
};

export type InteractionRequest = {
  type: "interaction_request";
  id: string;
  interaction_type:
    | "permission"
    | "ask_user"
    | "plan_approval"
    | "checkpoint_review"
    | "human_review_prose";
  tool_name: string;
  tool_input: Record<string, unknown>;
  suggestions: unknown[];
  response_hint?: Record<string, unknown>;
  seq?: number;
};

export type Usage = {
  type: "usage";
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  cumulative_output_tokens?: number | null;
  cumulative_cache_creation_tokens?: number | null;
  final: boolean;
  seq?: number;
};

export type TokenUsage = {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  cumulative_output_tokens?: number | null;
  cumulative_cache_creation_tokens?: number | null;
};

export type TurnDone = { type: "turn_done"; seq?: number };
export type ErrorEvent = { type: "error"; message: string; seq?: number };
export type NodeStarted = {
  type: "node_started";
  node_id: string;
  parent_node_id?: string | null;
  kind?: string;
  prompt?: string;
  seq?: number;
};
export type NodeUpdated = {
  type: "node_updated";
  node: NodeInfo;
  seq?: number;
};

export type ServerEvent =
  | TextDelta
  | Thinking
  | Activity
  | InteractionRequest
  | Usage
  | TurnDone
  | ErrorEvent
  | NodeStarted
  | NodeUpdated;

export type ClientMessage =
  | {
      type: "user_message";
      text: string;
      resume_from_node_id?: string | null;
      needs_review?: boolean | null;
      extra_planspace_loads?: string[] | null;
    }
  | {
      type: "interaction_response";
      id: string;
      allow: boolean;
      decision?: string | Record<string, unknown> | null;
      message?: string;
      updated_input?: Record<string, unknown> | null;
      response?: Record<string, unknown> | null;
      scope?: string | null;
      interrupt?: boolean;
      permission_mode?: string | null;
      clear_context?: boolean;
    }
  | { type: "interrupt" }
  | { type: "replay_request"; node_id: string; since_seq: number };

export type CanvasViewport = {
  x: number;
  y: number;
  zoom: number;
};

export type SessionInfo = {
  id: string;
  created_at: number;
  turns: number;
  provider?: string;
  preferred_language?: string | null;
  temporary?: boolean;
  scenario_name?: string | null;
  name?: string;
  project_context_binding_id?: string | null;
  /** Persisted canvas positions keyed by node id (or synthetic id, e.g.
   * `artifact:<nid>`). Optional for older sessions. */
  layout_hints?: Record<string, { x: number; y: number }>;
  /** Persisted React Flow viewport so pan/zoom survives project reopen. */
  layout_viewport?: CanvasViewport | null;
  planspace_view?: Record<string, { hidden?: boolean }>;
};

export type ScenarioSummary = {
  name: string;
  brief: string;
  providers: string[];
  auto_commit: boolean;
  node_count: number;
};

export type ScenarioDetail = ScenarioSummary & {
  acceptance: string;
};

export type VerifyResponse = {
  exit_code: number;
  stdout: string;
  stderr: string;
  timed_out: boolean;
};

export type NodeKind = "agent" | "gate" | "op";
export type NodeState =
  | "queued"
  | "running"
  | "waiting"
  | "awaiting_human_input"
  | "awaiting_review"
  | "done"
  | "error"
  | "cancelled";

export type AcceptanceState =
  | "not_applicable"
  | "unreviewed"
  | "accepted"
  | "rejected"
  | "blocked";

export type VerdictSource =
  | "none"
  | "human"
  | "deterministic"
  | "cross_provider"
  | "same_provider_advisory";

export type NodeInfo = {
  id: string;
  project_id: string;
  kind: NodeKind;
  op_kind?: string | null;
  state: NodeState;
  parent_node_id?: string | null;
  planspace_id?: string | null;
  context_sources: string[];
  context_bundle_id?: string | null;
  context_bundle_path?: string | null;
  provider: string;
  provider_session_id?: string | null;
  provider_turn_id?: string | null;
  sdk_session_id?: string | null;
  commit_before?: string | null;
  commit_after?: string | null;
  requires_review?: boolean;
  prompt: string;
  contract?: string;
  summary?: string | null;
  error?: string | null;
  usage?: TokenUsage | null;
  system_context_snapshot?: string;
  settings_snapshot?: Record<string, unknown>;
  scenario_step_id?: string | null;
  review_outcome?: "approved" | "rejected" | null;
  acceptance_state?: AcceptanceState;
  verdict_source?: VerdictSource;
  verdict_artifact_path?: string | null;
  verdict_thread_id?: string | null;
  accepted_at?: number | null;
  rejected_at?: number | null;
  created_at: number;
  started_at?: number | null;
  finished_at?: number | null;
};

export type ContextBundleSource = {
  scope: string;
  kind: string;
  path: string;
  sha256: string;
  chars: number;
  raw_chars?: number;
  injection: string;
  plug_id?: string;
  truncated?: boolean;
};

export type ContextBundlePlugRef = {
  id: string;
  role?: string;
  injection?: string | null;
  enabled?: boolean;
  auto_update?: boolean;
  source?: string;
  color?: string;
  title?: string;
};

export type ContextBundle = {
  bundle_id: string;
  created_at: number;
  project_id?: string;
  node_id?: string;
  project_binding_id?: string | null;
  active_planspace_id?: string | null;
  active_planspace?: ContextBundlePlugRef | null;
  sources: ContextBundleSource[];
  system_text?: string;
  turn_text?: string;
};

export type ContextSpacePlugSummary = {
  id: string;
  kind: string;
  slug: string;
  role?: string;
  injection?: string | null;
  enabled: boolean;
  auto_update: boolean;
  source: string;
  active: boolean;
  hidden?: boolean;
  exists: boolean;
  path?: string | null;
  title: string;
  description?: string | null;
  color?: string | null;
};

export type ContextSpaceBindingSummary = {
  id: string;
  path: string;
  title: string;
  project_name?: string | null;
  local_paths: string[];
  matches_project_path: boolean;
  active_planspace_id?: string | null;
  binding_active_planspace_id?: string | null;
  plugs: ContextSpacePlugSummary[];
};

export type SessionContextSpaceInfo = {
  root: string;
  exists: boolean;
  project_context_binding_id?: string | null;
  project_active_planspace_id?: string | null;
  resolved_binding_id?: string | null;
  active_planspace_id?: string | null;
  planspace_view?: Record<string, { hidden?: boolean }>;
  context_file?: {
    exists: boolean;
  };
  context_refresh?: {
    running: boolean;
    mode?: "init" | "refresh" | string;
    started_at?: number;
  };
  bindings: ContextSpaceBindingSummary[];
  bootstrap?: {
    context_root: string;
    binding_id: string;
    planspace_id: string;
    created: string[];
  };
};

export type EventRecord = {
  seq: number;
  event: ServerEvent;
};

export type NodeDiff = {
  kind: "commit_diff" | "working_tree" | string;
  text: string;
  error?: string | null;
};

export type SessionFileRole = "status" | "plan" | "context";

export type SessionFile = {
  role: SessionFileRole;
  path: string;
  text: string;
  mtime: number;
  last_writer: {
    kind: "node" | "context-refresh" | "hand" | string;
    node_id?: string;
    updated_at?: number;
    source?: string;
    previous?: string;
  };
};

export type StatusDeltaOp = Record<string, unknown> & {
  target?: string;
  operation?: string;
  text?: string;
  summary?: string;
  patch?: string;
};

export type NodeStatusDelta = {
  planspace_id: string;
  before: string;
  after: string;
  ops: StatusDeltaOp[];
  applied_at: number;
};
