import { useLayoutEffect } from "react";
import { useStoreApi } from "reactflow";
import { createHandleBoundsSeeder } from "./viewportHandles";

export function ViewportHandleBounds() {
  const store = useStoreApi();
  useLayoutEffect(() => {
    const seed = createHandleBoundsSeeder();
    const update = () => {
      const current = store.getState().nodeInternals;
      const nodeInternals = seed(current);
      if (nodeInternals !== current) store.setState({ nodeInternals });
    };
    const unsubscribe = store.subscribe((state, previous) => {
      if (state.nodeInternals !== previous.nodeInternals) update();
    });
    update();
    return unsubscribe;
  }, [store]);
  return null;
}
