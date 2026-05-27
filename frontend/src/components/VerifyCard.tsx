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
    <div className="border-t border-line bg-surface-sunken px-6 py-4">
      <div className="mb-3 flex items-center gap-3">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-muted">
          Verify
        </div>
        <div className="font-mono text-[10px] text-ink-subtle">
          scenario: {scenarioName}
        </div>
        {scenarioPassed && (
          <span className="rounded-md bg-state-review-soft px-2 py-0.5 text-[10px] uppercase tracking-[0.12em] text-state-review">
            passed
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-2 text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
            Programmatic floor
          </div>
          <button
            type="button"
            onClick={() => void onVerify()}
            disabled={verifying}
            className="rounded-md border border-line bg-surface px-3 py-1 text-xs text-ink transition hover:border-line-strong hover:bg-surface-raised disabled:opacity-40"
          >
            {verifying ? "Running…" : verifyResult ? "Re-run verify.sh" : "Run verify.sh"}
          </button>
          {verifyResult && (
            <div className="mt-3 space-y-2 text-[11px]">
              <div
                className={
                  verifyResult.exit_code === 0
                    ? "text-state-review"
                    : "text-state-error"
                }
              >
                exit {verifyResult.exit_code}
                {verifyResult.timed_out ? " (timed out)" : ""}
              </div>
              {verifyResult.stdout && (
                <details className="rounded-md border border-line bg-surface-raised px-2 py-1">
                  <summary className="cursor-pointer text-ink-muted">stdout</summary>
                  <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[10px] text-ink">
                    {verifyResult.stdout}
                  </pre>
                </details>
              )}
              {verifyResult.stderr && (
                <details className="rounded-md border border-state-error/30 bg-state-error-soft px-2 py-1" open>
                  <summary className="cursor-pointer text-state-error">stderr</summary>
                  <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[10px] text-state-error">
                    {verifyResult.stderr}
                  </pre>
                </details>
              )}
            </div>
          )}
        </div>

        <div>
          <div className="mb-2 text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
            Human acceptance
          </div>
          {items.length === 0 ? (
            <div className="text-[11px] text-ink-muted">
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
                    className="mt-0.5 accent-brand"
                  />
                  <span
                    className={
                      checked[idx]
                        ? "text-ink-muted line-through"
                        : "text-ink-strong"
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
