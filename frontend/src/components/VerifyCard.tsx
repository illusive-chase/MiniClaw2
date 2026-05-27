import { useEffect, useMemo, useState } from "react";
import { getScenario, verifySession } from "../api";
import type { ScenarioDetail, VerifyResponse } from "../types";

type Props = {
  sessionId: string;
  scenarioName: string;
};

export function VerifyCard({ sessionId, scenarioName }: Props) {
  const [detail, setDetail] = useState<ScenarioDetail | null>(null);
  const [verifyResult, setVerifyResult] = useState<VerifyResponse | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [checked, setChecked] = useState<Record<number, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    getScenario(scenarioName)
      .then((next) => {
        if (!cancelled) setDetail(next);
      })
      .catch((err) => console.error("getScenario failed:", err));
    return () => {
      cancelled = true;
    };
  }, [scenarioName]);

  const items = useMemo(() => parseChecklist(detail?.acceptance ?? ""), [detail]);

  const onVerify = async () => {
    setVerifying(true);
    try {
      const next = await verifySession(sessionId);
      setVerifyResult(next);
    } catch (err) {
      setVerifyResult({
        exit_code: -1,
        stdout: "",
        stderr: String(err),
        timed_out: false,
      });
    } finally {
      setVerifying(false);
    }
  };

  const allChecked = items.length > 0 && items.every((_, i) => checked[i]);
  const programmaticPassed = verifyResult?.exit_code === 0;
  const scenarioPassed = programmaticPassed && allChecked;

  return (
    <div className="border-t border-slate-800 bg-slate-900/30 px-6 py-4">
      <div className="mb-3 flex items-center gap-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Verify
        </div>
        <div className="font-mono text-[11px] text-slate-500">
          scenario: {scenarioName}
        </div>
        {scenarioPassed && (
          <span className="rounded bg-emerald-900/60 px-2 py-0.5 text-[10px] uppercase tracking-wide text-emerald-200">
            passed
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-2 text-[11px] uppercase tracking-wide text-slate-500">
            Programmatic floor
          </div>
          <button
            type="button"
            onClick={() => void onVerify()}
            disabled={verifying}
            className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-40"
          >
            {verifying ? "Running…" : verifyResult ? "Re-run verify.sh" : "Run verify.sh"}
          </button>
          {verifyResult && (
            <div className="mt-3 space-y-2 text-[11px]">
              <div
                className={
                  verifyResult.exit_code === 0
                    ? "text-emerald-300"
                    : "text-rose-300"
                }
              >
                exit {verifyResult.exit_code}
                {verifyResult.timed_out ? " (timed out)" : ""}
              </div>
              {verifyResult.stdout && (
                <details className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1">
                  <summary className="cursor-pointer text-slate-400">stdout</summary>
                  <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[10px] text-slate-300">
                    {verifyResult.stdout}
                  </pre>
                </details>
              )}
              {verifyResult.stderr && (
                <details className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1" open>
                  <summary className="cursor-pointer text-slate-400">stderr</summary>
                  <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[10px] text-rose-200">
                    {verifyResult.stderr}
                  </pre>
                </details>
              )}
            </div>
          )}
        </div>

        <div>
          <div className="mb-2 text-[11px] uppercase tracking-wide text-slate-500">
            Human acceptance
          </div>
          {items.length === 0 ? (
            <div className="text-[11px] text-slate-500">
              {detail ? "No acceptance items defined." : "Loading…"}
            </div>
          ) : (
            <ul className="space-y-1.5">
              {items.map((text, idx) => (
                <li key={idx} className="flex items-start gap-2 text-[12px]">
                  <input
                    type="checkbox"
                    checked={!!checked[idx]}
                    onChange={(e) =>
                      setChecked((prev) => ({ ...prev, [idx]: e.target.checked }))
                    }
                    className="mt-0.5"
                  />
                  <span
                    className={
                      checked[idx] ? "text-slate-400 line-through" : "text-slate-200"
                    }
                  >
                    {text}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function parseChecklist(markdown: string): string[] {
  const lines = markdown.split(/\r?\n/);
  const out: string[] = [];
  for (const raw of lines) {
    const m = raw.match(/^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.+?)\s*$/);
    if (m) out.push(m[1]);
  }
  return out;
}
