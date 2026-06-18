import { useState } from "react";
import type { InteractionRequest } from "../types";

type PermissionSuggestion = {
  label?: string;
  title?: string;
  description?: string;
  message?: string;
  updated_input?: Record<string, unknown>;
  updatedInput?: Record<string, unknown>;
  input?: Record<string, unknown>;
  decision?: string;
  scope?: string;
};

type Props = {
  request: InteractionRequest;
  onRespond: (args: {
    allow: boolean;
    message?: string;
    decision?: string;
    scope?: string;
    interrupt?: boolean;
    updatedInput?: Record<string, unknown> | null;
  }) => void;
  variant?: "panel" | "compact";
};

export function PermissionDialog({ request, onRespond, variant = "panel" }: Props) {
  const [reason, setReason] = useState("");
  const [inputText, setInputText] = useState(() =>
    JSON.stringify(request.tool_input, null, 2),
  );
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [selectedSuggestion, setSelectedSuggestion] =
    useState<PermissionSuggestion | null>(null);
  const compact = variant === "compact";
  const suggestions = normalizeSuggestions(request.suggestions);
  const parsedInput = (): Record<string, unknown> | null => {
    try {
      const parsed = JSON.parse(inputText);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setJsonError("Tool input must be a JSON object.");
        return null;
      }
      setJsonError(null);
      return parsed as Record<string, unknown>;
    } catch (err) {
      setJsonError(err instanceof Error ? err.message : String(err));
      return null;
    }
  };
  const respondAllow = (extra: {
    decision?: string;
    scope?: string;
    message?: string;
  } = {}) => {
    const updatedInput = parsedInput();
    if (!updatedInput) return;
    onRespond({
      allow: true,
      updatedInput,
      decision: selectedSuggestion?.decision,
      scope: selectedSuggestion?.scope,
      message: selectedSuggestion?.message,
      ...extra,
    });
  };
  const applySuggestion = (suggestion: PermissionSuggestion) => {
    setSelectedSuggestion(suggestion);
    const nextInput = suggestion.updated_input || suggestion.updatedInput || suggestion.input;
    if (nextInput) {
      setInputText(JSON.stringify(nextInput, null, 2));
      setJsonError(null);
    }
    if (suggestion.message) setReason(suggestion.message);
  };
  return (
    <div
      className={
        "border border-state-waiting/30 bg-state-waiting-soft " +
        (compact ? "rounded-md p-2" : "rounded-lg p-4")
      }
    >
      <div className={(compact ? "mb-1 text-[11px]" : "mb-2 text-sm") + " font-medium text-state-waiting"}>
        Allow tool: <span className="font-mono">{request.tool_name}</span>?
      </div>
      {suggestions.length > 0 && (
        <div className="mb-3 space-y-1.5">
          <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-ink-subtle">
            Suggestions
          </div>
          <div className="flex flex-wrap gap-1.5">
            {suggestions.map((suggestion, index) => (
              <button
                key={`${suggestionLabel(suggestion)}-${index}`}
                type="button"
                onClick={() => applySuggestion(suggestion)}
                className={
                  "rounded-md border border-state-waiting/35 bg-surface-raised text-left text-state-waiting transition hover:border-state-waiting/70 " +
                  (compact ? "px-2 py-0.5 text-[11px]" : "px-2 py-1 text-xs")
                }
                title={suggestion.description}
              >
                {suggestionLabel(suggestion)}
              </button>
            ))}
          </div>
        </div>
      )}
      <label className="mb-3 block">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-ink-subtle">
          Tool input
        </div>
        <textarea
          value={inputText}
          onChange={(e) => {
            setInputText(e.target.value);
            if (jsonError) setJsonError(null);
            if (selectedSuggestion) setSelectedSuggestion(null);
          }}
          rows={compact ? 5 : 8}
          className={
            "w-full resize-y rounded border border-line bg-surface-raised p-2 font-mono text-ink focus:border-brand focus:outline-none " +
            (compact ? "text-[11px]" : "text-xs")
          }
        />
      </label>
      {jsonError && (
        <div className="mb-2 rounded-md border border-state-error/30 bg-state-error-soft px-2 py-1 text-[11px] text-state-error">
          {jsonError}
        </div>
      )}
      <input
        value={reason}
        onChange={(e) => {
          setReason(e.target.value);
          if (selectedSuggestion?.message) setSelectedSuggestion(null);
        }}
        placeholder="Optional message"
        className={
          "mb-2 w-full rounded-md border border-line bg-surface-raised px-2 py-1 text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none " +
          (compact ? "text-[11px]" : "text-xs")
        }
      />
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => respondAllow({ message: reason || undefined })}
          className={(compact ? "px-2 py-0.5 text-[11px]" : "px-3 py-1 text-xs") + " rounded-md bg-state-review font-medium text-white transition hover:brightness-[0.92]"}
        >
          Allow
        </button>
        <button
          onClick={() =>
            respondAllow({
              decision: "acceptForSession",
              scope: "session",
              message: reason || undefined,
            })
          }
          className={(compact ? "px-2 py-0.5 text-[11px]" : "px-3 py-1 text-xs") + " rounded-md border border-state-review/40 bg-state-review-soft font-medium text-state-review transition hover:border-state-review/70"}
        >
          Allow for session
        </button>
        <button
          onClick={() => onRespond({ allow: false, message: reason })}
          className={(compact ? "px-2 py-0.5 text-[11px]" : "px-3 py-1 text-xs") + " rounded-md bg-state-error font-medium text-white transition hover:brightness-[0.92]"}
        >
          Deny
        </button>
        <button
          onClick={() =>
            onRespond({
              allow: false,
              decision: "cancel",
              interrupt: true,
              message: reason || "Cancelled by user",
            })
          }
          className={(compact ? "px-2 py-0.5 text-[11px]" : "px-3 py-1 text-xs") + " rounded-md border border-state-error/40 font-medium text-state-error transition hover:bg-state-error-soft"}
        >
          Cancel turn
        </button>
      </div>
    </div>
  );
}

function normalizeSuggestions(values: unknown[]): PermissionSuggestion[] {
  return values
    .map((value) =>
      value && typeof value === "object"
        ? (value as PermissionSuggestion)
        : null,
    )
    .filter((value): value is PermissionSuggestion => value !== null);
}

function suggestionLabel(suggestion: PermissionSuggestion): string {
  return (
    suggestion.label ||
    suggestion.title ||
    suggestion.decision ||
    suggestion.description ||
    "Apply suggestion"
  );
}
