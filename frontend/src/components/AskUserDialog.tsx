import { useState } from "react";
import type { InteractionRequest } from "../types";

type AskQuestion = {
  id?: string;
  question: string;
  header?: string;
  /* Codex marks credential prompts with this; the answer must never be
   * rendered in plain text. */
  isSecret?: boolean;
  multiSelect?: boolean;
  options: { label: string; description?: string }[] | null;
};

type Props = {
  request: InteractionRequest;
  onRespond: (answers: Record<string, { answers: string[] }>) => void;
  variant?: "panel" | "compact";
};

export function AskUserDialog({ request, onRespond, variant = "panel" }: Props) {
  const questions = (request.tool_input.questions as AskQuestion[]) || [];
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [other, setOther] = useState<Record<string, string>>({});
  const compact = variant === "compact";

  const submit = () => {
    const normalized: Record<string, { answers: string[] }> = {};
    questions.forEach((q, i) => {
      const key = questionKey(q, i);
      const extra = other[key]?.trim();
      normalized[key] = { answers: extra ? [extra] : (answers[key] ?? []) };
    });
    onRespond(normalized);
  };

  return (
    <div
      className={
        "rounded-lg border border-brand/30 bg-brand-soft " +
        (compact ? "space-y-2 p-2" : "space-y-3 p-4")
      }
    >
      <div className={(compact ? "text-[11px]" : "text-sm") + " font-medium text-brand-ink dark:text-brand"}>
        Agent is asking:
      </div>
      {questions.map((q, i) => {
        const key = questionKey(q, i);
        const selected = answers[key] ?? [];
        const custom = other[key] ?? "";
        const hasCustom = Boolean(custom.trim());
        const options = q.options ?? [];
        return (
          <div key={key} className="space-y-1">
            <div className="text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
              {q.header || `Question ${i + 1}`}
            </div>
            <div className={(compact ? "text-[11px]" : "text-sm") + " text-ink-strong"}>
              {q.question}
            </div>
            <div className="flex flex-wrap gap-1">
              {options.map((opt) => {
                const isSelected = !hasCustom && selected.includes(opt.label);
                return (
                  <button
                    key={opt.label}
                    onClick={() => {
                      setAnswers({
                        ...answers,
                        [key]: q.multiSelect
                          ? toggle(hasCustom ? [] : selected, opt.label)
                          : [opt.label],
                      });
                      setOther({ ...other, [key]: "" });
                    }}
                    className={
                      "rounded-md border transition " +
                      (compact ? "px-1.5 py-0.5 text-[11px] " : "px-2 py-1 text-xs ") +
                      (isSelected
                        ? "border-brand bg-brand/15 text-brand-ink dark:text-brand"
                        : "border-line bg-surface-raised text-ink hover:border-brand/40")
                    }
                    title={opt.description}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
            <input
              value={custom}
              type={q.isSecret ? "password" : "text"}
              autoComplete={q.isSecret ? "off" : undefined}
              onChange={(e) => setOther({ ...other, [key]: e.target.value })}
              placeholder={
                q.isSecret
                  ? "输入你的回答（不会显示）"
                  : options.length
                    ? "其他（填写后将覆盖已选选项）"
                    : "输入你的回答"
              }
              className={(compact ? "text-[11px]" : "text-xs") + " mt-1 w-full rounded-md border border-line bg-surface-raised px-2 py-1 text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"}
            />
          </div>
        );
      })}
      <button
        onClick={submit}
        disabled={!allAnswered(questions, answers, other)}
        className={(compact ? "px-2 py-0.5 text-[11px]" : "px-3 py-1 text-xs") + " rounded-md bg-brand font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"}
      >
        Send
      </button>
    </div>
  );
}

function questionKey(q: AskQuestion, index: number) {
  return q.id || q.question || `q${index}`;
}

function toggle(values: string[], value: string) {
  return values.includes(value)
    ? values.filter((v) => v !== value)
    : [...values, value];
}

function allAnswered(
  questions: AskQuestion[],
  answers: Record<string, string[]>,
  other: Record<string, string>,
) {
  return questions.every((q, i) => {
    const key = questionKey(q, i);
    return (answers[key]?.length ?? 0) > 0 || Boolean(other[key]?.trim());
  });
}
