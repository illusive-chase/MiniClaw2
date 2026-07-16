// Mirror of backend WebSocket events (backend/miniclaw2/events.py).

export type TextDelta = { type: "text_delta"; text: string; node_id: string; seq?: number };

export type Thinking = { type: "thinking"; text: string; node_id: string; seq?: number };

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
  node_id: string;
  seq?: number;
};

export type InteractionRequest = {
  type: "interaction_request";
  id: string;
  interaction_type:
    | "permission"
    | "ask_user"
    | "human_review_prose";
  tool_name: string;
  tool_input: Record<string, unknown>;
  suggestions: unknown[];
  response_hint?: Record<string, unknown>;
  node_id: string;
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
  node_id: string;
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

export type TurnDone = { type: "turn_done"; node_id: string; seq?: number };
export type ErrorEvent = { type: "error"; message: string; node_id: string; seq?: number };
export type NodeStarted = {
  type: "node_started";
  node_id: string;
  parent_node_id?: string | null;
  kind?: string;
  provider?: AgentProvider | null;
  model_preset_id?: string | null;
  category?: NodeCategory | null;
  subtype?: ReviewSubtype | null;
  prompt?: string;
  seq?: number;
};
export type NodeUpdated = {
  type: "node_updated";
  node_id: string;
  node: NodeInfo;
  seq?: number;
};
export type NodeRemoved = {
  type: "node_removed";
  id: string;
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
  | NodeUpdated
  | NodeRemoved;

export type ClientMessage =
    | {
      type: "user_message";
      text: string;
      resume_from_node_id?: string | null;
      extra_skills?: string[] | null;
      agent_op_kind?: string | null;
      model_preset_id?: string | null;
    }
  | {
      type: "interaction_response";
      id: string;
      node_id?: string | null;
      allow: boolean;
      message?: string;
      updated_input?: Record<string, unknown> | null;
      response?: Record<string, unknown> | null;
      scope?: string | null;
      interrupt?: boolean;
      permission_mode?: string | null;
      clear_context?: boolean;
    }
  | { type: "interrupt"; node_id: string }
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
  model_preset_id: string;
  provider?: AgentProvider;
  concurrency: number;
  active_count: number;
  queued_count: number;
  preferred_language?: string | null;
  temporary?: boolean;
  template_id?: string | null;
  name?: string;
  machine_id: string;
  native_machine_label: string;
  is_native: boolean;
  read_only: boolean;
  last_sync_at?: number | null;
  project_context_binding_id?: string | null;
  /** Persisted canvas positions keyed by node id or synthetic graph id. */
  layout_hints?: Record<string, { x: number; y: number }>;
  /** Persisted React Flow viewport so pan/zoom survives project reopen. */
  layout_viewport?: CanvasViewport | null;
};

export type TemplateSummary = {
  name: string;
  brief: string;
  allowed_model_preset_ids: string[];
  auto_commit: boolean;
  node_count: number;
  nodes?: TemplateNodeSpec[];
};

export type TemplateNodeSpec = {
  id: string;
  kind: NodeKind;
  category: NodeCategory;
  subtype?: ReviewSubtype | null;
  scheduled_deps?: string[];
  resume_from?: string | null;
  prompt_preview: string;
  brief?: ReviewBrief | null;
};

export type NodeKind = "agent" | "op" | "verifier";
export type AgentProvider = "claude" | "codex";
export type ModelPreset = {
  id: string;
  label: string;
  provider: AgentProvider;
  model: string;
  model_provider?: string | null;
  service_tier?: string | null;
  reasoning_effort?: string | null;
  description?: string;
  is_default?: boolean;
  status: "active" | "compatibility";
};
export type GlobalDefaults = {
  default_model_preset_id: string;
  auto_commit: boolean;
  preferred_language?: string | null;
  concurrency: number;
};
export type GlobalState = {
  config_path: string;
  defaults: GlobalDefaults;
  model_presets: ModelPreset[];
  sync: {
    configured: boolean;
    remote_url?: string | null;
    status: "up-to-date" | "changed";
    changed: boolean;
    last_sync_at?: number | null;
    machine_id: string;
    machine_label: string;
    hostname_mismatch: boolean;
    privacy_notice: string;
  };
};
export type NodeCategory = "planning" | "regular" | "review";
export type ReviewSubtype =
  | "agentic_review"
  | "human_interact_review"
  | "programmatic_review";
export type PlanspaceMode = "auto" | "manual";
export type ReviewBrief = {
  check_what: string;
  expected: string;
  abnormal: string;
};
export type NodeState =
  | "virtual"
  | "queued"
  | "running"
  | "waiting"
  | "awaiting_human_input"
  | "done"
  | "error"
  | "cancelled";

export type NodeInfo = {
  id: string;
  project_id: string;
  kind: NodeKind;
  op_kind?: string | null;
  agent_op_kind?: string | null;
  state: NodeState;
  parent_node_id?: string | null;
  planspace_id?: string | null;
  context_bundle_id?: string | null;
  context_bundle_path?: string | null;
  model_preset_id?: string | null;
  provider: AgentProvider | null;
  provider_session_id?: string | null;
  provider_turn_id?: string | null;
  commit_before?: string | null;
  commit_after?: string | null;
  prompt: string;
  category?: NodeCategory | null;
  subtype?: ReviewSubtype | null;
  brief?: ReviewBrief | null;
  prompt_draft?: string | null;
  scheduled_deps?: string[];
  pending_extra_skills?: string[];
  resume_from_node_id?: string | null;
  verify_script_ref?: string | null;
  proposed_by?: string | null;
  obsolete_reason?: string | null;
  summary?: string | null;
  error?: string | null;
  usage?: TokenUsage | null;
  system_context_snapshot?: string;
  settings_snapshot?: Record<string, unknown>;
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
  mode?: PlanspaceMode;
};

export type ContextSpaceBindingSummary = {
  id: string;
  path: string;
  title: string;
  project_name?: string | null;
  local_paths: string[];
  matches_project_path: boolean;
  active_planspace_id?: string | null;
  plugs: ContextSpacePlugSummary[];
};

export type SessionContextSpaceInfo = {
  root: string;
  exists: boolean;
  project_context_binding_id?: string | null;
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
  selectable_bindings?: ContextSpaceBindingSummary[];
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

export type SessionFileRole = "context";

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
