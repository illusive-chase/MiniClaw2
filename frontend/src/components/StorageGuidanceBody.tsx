import { guidanceNotes, type StorageGuidance } from "../storageMaintenance";

/* The steps, shutdown warning and commands for one storage `state`. Both callers
   render this: the maintenance page, which replaces the project list while
   business data is unloaded, and the settings panel, whose sync failures raise
   the same `state` vocabulary while the app stays live. Keeping one component
   means a state can never gain guidance on one surface and stay a bare error
   string on the other. */
export function StorageGuidanceBody({
  guidance,
  compact = false,
}: {
  guidance: StorageGuidance;
  compact?: boolean;
}) {
  const noteClass = compact ? "text-[11px] text-ink-muted" : "text-[12px] text-ink-muted";
  return (
    <>
      <ol className={`list-decimal space-y-1 pl-5 ${compact ? "text-[11px]" : ""}`}>
        {guidance.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      {guidance.commands.length > 0 && (
        <>
          {guidance.requiresShutdown && (
            <p className={`text-state-error ${compact ? "text-[11px]" : ""}`}>
              以下命令需要独占存储：请先在可中断的时间窗口停止本机后端，再执行。
            </p>
          )}
          <pre
            className={`overflow-auto rounded border border-line p-3 ${
              compact ? "text-[11px]" : ""
            }`}
          >
            {guidance.commands.join("\n")}
          </pre>
        </>
      )}
      {guidanceNotes(guidance).map((note) => (
        <p key={note} className={noteClass}>
          {note}
        </p>
      ))}
    </>
  );
}
