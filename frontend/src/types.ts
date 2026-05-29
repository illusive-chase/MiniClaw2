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
    | "checkpoint_review";
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
      output_kind?: "freeform" | "summary" | "interface" | "review_brief" | null;
      output_path?: string | null;
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
  | { type: "replay_request"; node_id: string; since_seq: number }
  | { type: "start_gate_node"; brief: string };

export type SessionInfo = {
  id: string;
  created_at: number;
  turns: number;
  provider?: string;
  temporary?: boolean;
  scenario_name?: string | null;
  name?: string;
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
  | "awaiting_review"
  | "done"
  | "error"
  | "cancelled";

export type NodeInfo = {
  id: string;
  project_id: string;
  kind: NodeKind;
  op_kind?: string | null;
  state: NodeState;
  parent_node_id?: string | null;
  context_sources: string[];
  provider: string;
  provider_session_id?: string | null;
  provider_turn_id?: string | null;
  sdk_session_id?: string | null;
  commit_before?: string | null;
  commit_after?: string | null;
  output_kind?: "freeform" | "summary" | "interface" | "review_brief";
  output_path?: string | null;
  output_contract_snapshot?: string;
  prompt: string;
  contract?: string;
  summary?: string | null;
  error?: string | null;
  usage?: TokenUsage | null;
  system_context_snapshot?: string;
  settings_snapshot?: Record<string, unknown>;
  scenario_step_id?: string | null;
  created_at: number;
  started_at?: number | null;
  finished_at?: number | null;
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

export type NodeArtifact = {
  kind: "freeform" | "summary" | "interface" | "review_brief";
  path?: string | null;
  exists: boolean;
  content?: string | null;
  data?: unknown;
  error?: string | null;
};
