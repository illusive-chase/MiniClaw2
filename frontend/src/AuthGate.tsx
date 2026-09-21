import { useCallback, useEffect, useState } from "react";
import type { PropsWithChildren } from "react";
import { RotateCcw } from "lucide-react";
import { AUTH_REQUIRED_EVENT, getAuthState } from "./api";
import type { AuthState } from "./api";
import { PasscodeGate } from "./components/PasscodeGate";

type GateState =
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "login"; locked: boolean }
  | { kind: "error" };

let initialAuthRequest: Promise<AuthState> | null = null;

function loadAuthState(): Promise<AuthState> {
  initialAuthRequest ??= getAuthState();
  return initialAuthRequest;
}

export function AuthGate({ children }: PropsWithChildren) {
  const [state, setState] = useState<GateState>({ kind: "loading" });

  const checkAuth = useCallback(() => {
    setState({ kind: "loading" });
    void loadAuthState()
      .then((auth) => {
        setState(
          !auth.required || auth.authenticated
            ? { kind: "ready" }
            : { kind: "login", locked: auth.locked },
        );
      })
      .catch(() => setState({ kind: "error" }));
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    const requireAuth = () => setState({ kind: "login", locked: false });
    window.addEventListener(AUTH_REQUIRED_EVENT, requireAuth);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, requireAuth);
  }, []);

  if (state.kind === "ready") return <>{children}</>;

  if (state.kind === "login") {
    return (
      <PasscodeGate
        locked={state.locked}
        onAuthenticated={() => setState({ kind: "ready" })}
      />
    );
  }

  if (state.kind === "error") {
    return (
      <main className="flex min-h-full items-center justify-center bg-surface px-6">
        <div className="text-center">
          <p className="text-sm text-ink">无法连接到 MiniClaw2 后端</p>
          <button
            type="button"
            className="mt-4 inline-flex h-9 items-center gap-2 rounded-md border border-line-strong bg-surface-raised px-3 text-sm font-medium text-ink-strong hover:bg-surface-sunken"
            onClick={() => {
              initialAuthRequest = null;
              checkAuth();
            }}
          >
            <RotateCcw size={15} aria-hidden="true" />
            重试
          </button>
        </div>
      </main>
    );
  }

  return (
    <main
      className="flex min-h-full items-center justify-center bg-surface"
      aria-label="正在连接"
    >
      <span className="h-5 w-5 animate-spin rounded-full border-2 border-line-strong border-t-brand" />
    </main>
  );
}
