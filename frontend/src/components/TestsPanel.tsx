import { useEffect, useState } from "react";
import { listScenarios, runScenario } from "../api";
import type { ScenarioSummary, SessionInfo } from "../types";

type Props = {
  onLaunched: (session: SessionInfo, scenarioName: string) => void;
};

export function TestsPanel({ onLaunched }: Props) {
  const [scenarios, setScenarios] = useState<ScenarioSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [launching, setLaunching] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listScenarios()
      .then((next) => {
        if (!cancelled) setScenarios(next);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onRun = async (name: string, provider: "claude" | "codex") => {
    const key = `${name}:${provider}`;
    setLaunching(key);
    setError(null);
    try {
      const session = await runScenario(name, provider);
      onLaunched(session, name);
    } catch (err) {
      setError(String(err));
    } finally {
      setLaunching(null);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-slate-950 px-6 py-6 text-slate-200">
      <div className="mb-6">
        <h1 className="text-base font-semibold text-slate-100">Tests</h1>
        <p className="mt-1 max-w-2xl text-xs text-slate-500">
          Each scenario runs in a fresh temporary git workspace. Click a
          provider button to launch; you'll supervise it in the normal
          project view. After every node reaches a terminal state, a
          Verify card appears at the bottom of the chat — run the
          programmatic floor and tick the human acceptance checklist.
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded border border-rose-700/60 bg-rose-950/40 px-3 py-2 text-xs text-rose-200">
          {error}
        </div>
      )}

      {scenarios === null && !error && (
        <div className="text-xs text-slate-500">Loading…</div>
      )}

      {scenarios && scenarios.length === 0 && (
        <div className="text-xs text-slate-500">No scenarios are bundled.</div>
      )}

      <div className="grid max-w-3xl grid-cols-1 gap-3">
        {scenarios?.map((s) => (
          <div
            key={s.name}
            className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="font-mono text-sm text-slate-100">{s.name}</div>
                <div className="mt-1 text-[11px] text-slate-400">{s.brief}</div>
                <div className="mt-2 text-[10px] uppercase tracking-wide text-slate-600">
                  {s.node_count} node{s.node_count === 1 ? "" : "s"}
                  {s.auto_commit ? " · auto-commit" : ""}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                {s.providers.map((provider) => (
                  <button
                    key={provider}
                    type="button"
                    onClick={() =>
                      void onRun(s.name, provider as "claude" | "codex")
                    }
                    disabled={launching !== null}
                    className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-40"
                  >
                    {launching === `${s.name}:${provider}`
                      ? "Launching…"
                      : `Run · ${provider}`}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
