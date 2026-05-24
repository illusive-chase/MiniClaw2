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
    <div className="rounded-lg border border-sky-500/40 bg-sky-500/5 p-4 space-y-3">
      <div className="text-sm font-medium text-sky-300">Agent is asking:</div>
      {questions.map((q, i) => {
        const key = questionKey(q, i);
        const selected = answers[key] ?? [];
        const options = q.options ?? [];
        return (
        <div key={key} className="space-y-1">
          <div className="text-xs uppercase tracking-wide text-slate-500">
            {q.header || `Question ${i + 1}`}
          </div>
          <div className="text-sm">{q.question}</div>
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
                    "rounded border px-2 py-1 text-xs " +
                    (isSelected
                      ? "border-sky-400 bg-sky-500/20"
                      : "border-slate-700 bg-slate-900 hover:border-slate-500")
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
              className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
            />
          )}
        </div>
      )})}
      <button
        onClick={submit}
        disabled={!allAnswered(questions, answers, other)}
        className="rounded bg-sky-600 px-3 py-1 text-xs font-medium hover:bg-sky-500 disabled:opacity-40"
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
