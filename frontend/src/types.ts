// Mirror of backend WebSocket events (backend/miniclaw2/events.py).

export type TextDelta = { type: "text_delta"; text: string };

export type Activity = {
  type: "activity";
  kind: "tool" | "agent";
  status: "start" | "finish" | "failed" | "progress";
  id: string;
  name: string;
  summary: string;
};

export type InteractionRequest = {
  type: "interaction_request";
  id: string;
  interaction_type: "permission" | "ask_user" | "plan_approval";
  tool_name: string;
  tool_input: Record<string, unknown>;
  suggestions: unknown[];
};

export type Usage = {
  type: "usage";
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  final: boolean;
};

export type TurnDone = { type: "turn_done" };
export type ErrorEvent = { type: "error"; message: string };

export type ServerEvent =
  | TextDelta
  | Activity
  | InteractionRequest
  | Usage
  | TurnDone
  | ErrorEvent;

export type ClientMessage =
  | { type: "user_message"; text: string }
  | {
      type: "interaction_response";
      id: string;
      allow: boolean;
      message?: string;
      updated_input?: Record<string, unknown> | null;
      permission_mode?: string | null;
      clear_context?: boolean;
    }
  | { type: "interrupt" };

export type SessionInfo = { id: string; created_at: number; turns: number };
