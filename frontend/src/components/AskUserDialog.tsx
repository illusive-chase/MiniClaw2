import { useState } from "react";
import type { InteractionRequest } from "../types";

type AskQuestion = {
  id?: string;
  question: string;
  header?: string;
  isOther?: boolean;
  isSecret?: boolean;
  multiSelect?: boolean;
  options: { label: string; description?: string }[] | null;
};

type Props = {
  request: InteractionRequest;
  onRespond: (answers: Record<string, { answers: string[] }>) => void;
};

export function AskUserDialog({ request, onRespond }: Props) {
  const questions = (request.tool_input.questions as AskQuestion[]) || [];
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [other, setOther] = useState<Record<string, string>>({});

  const submit = () => {
    const normalized: Record<string, { answers: string[] }> = {};
    questions.forEach((q, i) => {
      const key = questionKey(q, i);
      const selected = answers[key] ?? [];
      const extra = other[key]?.trim();
      normalized[key] = { answers: extra ? [...selected, extra] : selected };
    });
    onRespond(normalized);
  };

  return (
    <div className="space-y-3 rounded-lg border border-brand/30 bg-brand-soft p-4">
      <div className="text-sm font-medium text-brand-ink dark:text-brand">
        Agent is asking:
      </div>
      {questions.map((q, i) => {
        const key = questionKey(q, i);
        const selected = answers[key] ?? [];
        const options = q.options ?? [];
        return (
          <div key={key} className="space-y-1">
            <div className="text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
              {q.header || `Question ${i + 1}`}
            </div>
            <div className="text-sm text-ink-strong">{q.question}</div>
            <div className="flex flex-wrap gap-1">
              {options.map((opt) => {
                const isSelected = selected.includes(opt.label);
                return (
                  <button
                    key={opt.label}
                    onClick={() =>
                      setAnswers({
                        ...answers,
                        [key]: q.multiSelect
                          ? toggle(selected, opt.label)
                          : [opt.label],
                      })
                    }
                    className={
                      "rounded-md border px-2 py-1 text-xs transition " +
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
            {q.isOther && (
              <input
                value={other[key] ?? ""}
                type={q.isSecret ? "password" : "text"}
                onChange={(e) => setOther({ ...other, [key]: e.target.value })}
                placeholder="Other"
                className="mt-1 w-full rounded-md border border-line bg-surface-raised px-2 py-1 text-xs text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
              />
            )}
          </div>
        );
      })}
      <button
        onClick={submit}
        disabled={!allAnswered(questions, answers, other)}
        className="rounded-md bg-brand px-3 py-1 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
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
