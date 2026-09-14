import { internalsSymbol, Position, type HandleElement, type Node } from "reactflow";

type HandleBounds = NonNullable<NonNullable<Node[typeof internalsSymbol]>["handleBounds"]>;

export function layoutHandleBounds(node: Node): HandleBounds | null {
  const { width, height } = node;
  if (!width || !height) return null;
  const border = node.type === "commit" || node.type === "templateInstanceBox" || node.type === "templatePort"
    ? 2 : node.type === "context" || node.type === "artifact" ? 0 : 1;
  const size = node.type === "commit" ? 6 : 12;
  const anchor = (position: Position, id: string | null = null, fraction = 0.5): HandleElement => {
    const horizontal = position === Position.Top || position === Position.Bottom;
    return {
      id, position, width: size, height: size,
      x: horizontal ? border + (width - border * 2) * fraction - size / 2
        : position === Position.Left ? border - 4 : width - border + 4 - size,
      y: !horizontal ? height / 2 - size / 2
        : position === Position.Top ? border - 4 : height - border + 4 - size,
    };
  };
  switch (node.type) {
    case "agent":
      return {
        source: [anchor(Position.Right), anchor(Position.Bottom, "produces"), anchor(Position.Bottom, "epochOut", 0.22)],
        target: [anchor(Position.Left), anchor(Position.Top, "loads"), anchor(Position.Top, "epochIn", 0.22)],
      };
    case "op":
    case "templateInstanceBox":
      return { source: [anchor(Position.Right)], target: [anchor(Position.Left)] };
    case "context":
      return { source: [anchor(Position.Left, "loads")], target: null };
    case "artifact":
      return { source: null, target: [anchor(Position.Top, "produces")] };
    case "commit":
      return { source: [anchor(Position.Bottom)], target: [anchor(Position.Top)] };
    case "templatePort":
      return { source: [anchor(Position.Right)], target: null };
    case "errorTerminal":
      return { source: null, target: [anchor(Position.Left)] };
    default:
      return null;
  }
}

export function createHandleBoundsSeeder() {
  const seeded = new WeakMap<HandleBounds, { width: Node["width"]; height: Node["height"] }>();
  return (nodes: Map<string, Node>): Map<string, Node> => {
    let next = nodes;
    for (const [id, node] of nodes) {
      const current = node[internalsSymbol]?.handleBounds;
      const dimensions = current && seeded.get(current);
      if (current && (!dimensions || (dimensions.width === node.width && dimensions.height === node.height))) continue;
      const handleBounds = layoutHandleBounds(node);
      if (!handleBounds) continue;
      seeded.set(handleBounds, { width: node.width, height: node.height });
      if (next === nodes) next = new Map(nodes);
      next.set(id, { ...node, [internalsSymbol]: { ...node[internalsSymbol], handleBounds } });
    }
    return next;
  };
}
