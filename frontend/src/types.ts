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
  interaction_type: "permission" | "ask_user" | "plan_approval";
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
  final: boolean;
  seq?: number;
};

export type TurnDone = { type: "turn_done"; seq?: number };
export type ErrorEvent = { type: "error"; message: string; seq?: number };
export type NodeStarted = {
  type: "node_started";
  node_id: string;
  parent_node_id?: string | null;
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
  | { type: "user_message"; text: string; resume_from_node_id?: string | null }
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

export type SessionInfo = {
  id: string;
  created_at: number;
  turns: number;
  provider?: string;
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
  state: NodeState;
  parent_node_id?: string | null;
  context_sources: string[];
  provider: string;
  provider_session_id?: string | null;
  provider_turn_id?: string | null;
  sdk_session_id?: string | null;
  commit_before?: string | null;
  commit_after?: string | null;
  prompt: string;
  summary?: string | null;
  error?: string | null;
  system_context_snapshot?: string;
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
