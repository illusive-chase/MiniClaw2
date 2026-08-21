import type { SelfUpdateState } from "./types";

export function canApplyUpdate(state: SelfUpdateState | null): boolean {
  return Boolean(
    state?.available &&
      state.fast_forward &&
      !state.dirty &&
      state.ahead === 0 &&
      state.blockers.length === 0,
  );
}
