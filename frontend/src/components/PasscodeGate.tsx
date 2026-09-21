import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { LoaderCircle, LockKeyhole, LogIn, ShieldAlert } from "lucide-react";
import { ApiError, loginWithPasscode } from "../api";

type PasscodeGateProps = {
  locked: boolean;
  onAuthenticated: () => void;
};

export function PasscodeGate({
  locked: initiallyLocked,
  onAuthenticated,
}: PasscodeGateProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const submittingRef = useRef(false);
  const [passcode, setPasscode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [locked, setLocked] = useState(initiallyLocked);
  const [error, setError] = useState<string | null>(null);
  const [shake, setShake] = useState(false);

  useEffect(() => {
    if (!locked) inputRef.current?.focus();
  }, [locked]);

  const submit = async (candidate: string) => {
    if (candidate.length !== 4 || submittingRef.current || locked) return;
    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    try {
      await loginWithPasscode(candidate);
      onAuthenticated();
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.state === "auth_locked" || caught.status === 423) {
          setLocked(true);
          setError(null);
          return;
        }
        setError(caught.detail ?? "密码验证失败");
      } else {
        setError("无法连接到 MiniClaw2 后端");
      }
      setPasscode("");
      setShake(false);
      window.requestAnimationFrame(() => setShake(true));
      window.setTimeout(() => inputRef.current?.focus(), 0);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submit(passcode);
  };

  return (
    <main className="flex min-h-full items-center justify-center bg-surface px-6 py-12">
      <section className="w-full max-w-sm" aria-labelledby="passcode-title">
        <div className="mb-8 flex items-center gap-3 border-b border-line pb-5">
          <span
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-md ${locked ? "bg-state-error-soft text-state-error" : "bg-brand-soft text-brand"}`}
          >
            {locked ? (
              <ShieldAlert size={20} aria-hidden="true" />
            ) : (
              <LockKeyhole size={20} aria-hidden="true" />
            )}
          </span>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-ink-strong">MiniClaw2</div>
            <h1 id="passcode-title" className="text-xl font-semibold text-ink-strong">
              {locked ? "登录已锁定" : "访问受保护"}
            </h1>
          </div>
        </div>

        {locked ? (
          <div role="alert">
            <p className="text-sm leading-6 text-ink">
              错误尝试次数已达上限。请重启 MiniClaw2 后端以重新开放登录。
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit}>
            <label
              htmlFor="miniclaw-passcode"
              className="mb-2 block text-sm font-medium text-ink-strong"
            >
              四位访问密码
            </label>
            <input
              ref={inputRef}
              id="miniclaw-passcode"
              className={`h-12 w-full rounded-md border bg-surface-raised px-4 text-center font-mono text-xl text-ink-strong outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20 ${error ? "border-state-error" : "border-line-strong"} ${shake ? "passcode-shake" : ""}`}
              type="password"
              inputMode="numeric"
              autoComplete="current-password"
              maxLength={4}
              pattern="[0-9]{4}"
              value={passcode}
              disabled={submitting}
              aria-describedby={error ? "passcode-error" : undefined}
              aria-invalid={error ? "true" : undefined}
              onAnimationEnd={() => setShake(false)}
              onChange={(event) => {
                const next = event.target.value.replace(/\D/g, "").slice(0, 4);
                setPasscode(next);
                setError(null);
                if (next.length === 4) void submit(next);
              }}
            />
            <div className="mt-2 min-h-6" aria-live="polite">
              {error && (
                <p id="passcode-error" className="text-sm text-state-error">
                  {error}
                </p>
              )}
            </div>
            <button
              type="submit"
              disabled={submitting || passcode.length !== 4}
              className="mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-md bg-brand px-4 text-sm font-semibold text-white transition hover:bg-brand/90 focus:outline-none focus:ring-2 focus:ring-brand/30 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? (
                <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />
              ) : (
                <LogIn size={16} aria-hidden="true" />
              )}
              {submitting ? "验证中" : "解锁"}
            </button>
          </form>
        )}
      </section>
    </main>
  );
}
