/* Live-geometry bridge for placing a node the user explicitly asked for.
 *
 * The two entry points that create a node by a direct click — the lane header
 * "+" button and the Git "Review" button — live in App, which holds the node
 * records but not the canvas geometry those records were laid out into. Lane
 * children are positioned relative to their lane and can be dragged freely, so
 * "below everything currently in this lane" is only answerable from the live
 * React Flow nodes. Canvas registers a resolver here so those handlers can ask
 * without React Flow state being lifted out of the canvas.
 *
 * Same module-singleton shape as `hoverStore` and `setPlanspaceLaneContext`.
 */

export type LaneAppendResolver = (
  planspaceId: string,
  anchorNodeIds: readonly string[],
  forNodeId?: string,
) => { x: number; y: number } | null;

let resolver: LaneAppendResolver | null = null;

export function setLaneAppendResolver(next: LaneAppendResolver | null): void {
  resolver = next;
}

/** Lane-relative position for a newly created node, or null when the lane has
 * no children yet (the ordinary first-slot default is already right), the
 * canvas is not mounted, or `forNodeId` is already placed in the lane.
 *
 * Pass `forNodeId` whenever the node may already be on the canvas — a
 * server-created node can arrive over the WebSocket before the request that
 * created it returns, and a node the user can already see must not be moved. */
export function resolveLaneAppendPosition(
  planspaceId: string,
  anchorNodeIds: readonly string[],
  forNodeId?: string,
): { x: number; y: number } | null {
  return resolver?.(planspaceId, anchorNodeIds, forNodeId) ?? null;
}
