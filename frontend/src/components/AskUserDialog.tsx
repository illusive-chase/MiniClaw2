import { useState } from "react";
import type { InteractionRequest } from "../types";

type AskQuestion = {
  question: string;
  header?: string;
  multiSelect?: boolean;
  options: { label: string; description?: string }[];
};

type Props = {
  request: InteractionRequest;
  onRespond: (answers: Record<string, string>) => void;
};

export function AskUserDialog({ request, onRespond }: Props) {
  const questions = (request.tool_input.questions as AskQuestion[]) || [];
  const [answers, setAnswers] = useState<Record<string, string>>({});

  const submit = () => onRespond(answers);

  return (
    <div className="rounded-lg border border-sky-500/40 bg-sky-500/5 p-4 space-y-3">
      <div className="text-sm font-medium text-sky-300">Claude is asking:</div>
      {questions.map((q, i) => (
        <div key={i} className="space-y-1">
          <div className="text-sm">{q.question}</div>
          <div className="flex flex-wrap gap-1">
            {q.options.map((opt) => {
              const selected = answers[q.question] === opt.label;
              return (
                <button
                  key={opt.label}
                  onClick={() => setAnswers({ ...answers, [q.question]: opt.label })}
                  className={
                    "rounded border px-2 py-1 text-xs " +
                    (selected
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
        </div>
      ))}
      <button
        onClick={submit}
        disabled={Object.keys(answers).length < questions.length}
        className="rounded bg-sky-600 px-3 py-1 text-xs font-medium hover:bg-sky-500 disabled:opacity-40"
      >
        Send
      </button>
    </div>
  );
}
