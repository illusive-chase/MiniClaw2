export type HierarchyNode = {
  segment: string;
  fullPath: string[];
  entry: string | null;
  children: HierarchyNode[];
  leafCount: number;
};

export type SearchHit = {
  entry: string;
  fullPath: string[];
  /** Half-open character range in `entry`, suitable for text highlighting. */
  matchRange: [start: number, end: number];
};

type MutableHierarchyNode = {
  segment: string;
  fullPath: string[];
  entry: string | null;
  children: Map<string, MutableHierarchyNode>;
};

type RankedSearchHit = SearchHit & { rank: number };

function compareStrings(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function foldNode(node: MutableHierarchyNode): HierarchyNode {
  const children = [...node.children.values()]
    .map(foldNode)
    .sort((left, right) => compareStrings(left.segment, right.segment));

  if (node.entry === null && children.length === 1) {
    const child = children[0];
    return {
      segment: `${node.segment}-${child.segment}`,
      fullPath: child.fullPath,
      entry: child.entry,
      children: child.children,
      leafCount: child.leafCount,
    };
  }

  return {
    segment: node.segment,
    fullPath: node.fullPath,
    entry: node.entry,
    children,
    leafCount: (node.entry === null ? 0 : 1)
      + children.reduce((count, child) => count + child.leafCount, 0),
  };
}

export function buildHierarchy(names: string[]): HierarchyNode[] {
  const roots = new Map<string, MutableHierarchyNode>();

  for (const name of names) {
    if (name.length === 0) continue;
    const segments = name.split("-");
    let siblings = roots;
    let node: MutableHierarchyNode | null = null;

    for (let index = 0; index < segments.length; index += 1) {
      const segment = segments[index];
      node = siblings.get(segment) ?? null;
      if (node === null) {
        node = {
          segment,
          fullPath: segments.slice(0, index + 1),
          entry: null,
          children: new Map(),
        };
        siblings.set(segment, node);
      }
      siblings = node.children;
    }

    if (node !== null) node.entry = name;
  }

  return [...roots.values()]
    .map(foldNode)
    .sort((left, right) => compareStrings(left.segment, right.segment));
}

function rankMatch(entry: string, query: string): RankedSearchHit | null {
  const normalizedEntry = entry.toLowerCase();
  const fullPath = entry.split("-");

  if (normalizedEntry.startsWith(query)) {
    return { entry, fullPath, matchRange: [0, query.length], rank: 0 };
  }

  let offset = 0;
  for (const segment of fullPath) {
    if (segment.toLowerCase().startsWith(query)) {
      return {
        entry,
        fullPath,
        matchRange: [offset, offset + query.length],
        rank: 1,
      };
    }
    offset += segment.length + 1;
  }

  const index = normalizedEntry.indexOf(query);
  if (index === -1) return null;
  return {
    entry,
    fullPath,
    matchRange: [index, index + query.length],
    rank: 2,
  };
}

export function searchEntries(names: string[], query: string): SearchHit[] {
  const normalizedQuery = query.trim().toLowerCase();
  if (normalizedQuery.length === 0) return [];

  const hits = [...new Set(names)]
    .map((entry) => rankMatch(entry, normalizedQuery))
    .filter((hit): hit is RankedSearchHit => hit !== null);

  hits.sort((left, right) => left.rank - right.rank || compareStrings(left.entry, right.entry));
  return hits.map(({ rank: _rank, ...hit }) => hit);
}

/** Directory path keys that must be expanded for `entryName` to be visible.
 *
 * Derived from the folded tree rather than from `entryName.split("-")`, because
 * folding merges single-child directories: `lark-workflow-meeting-summary` sits
 * under `lark` › `workflow`, and no directory key `lark-workflow-meeting`
 * exists. Keys match `pathKey` (segments joined with `-`). Returns an empty
 * array for a root-level leaf, or for a name that is not present.
 *
 * A node that is both a directory and an entry (`lark-vc`) includes its own key:
 * its entry row renders inside its expansion, below the caret.
 */
export function ancestorDirectoryPaths(names: string[], entryName: string): string[] {
  const walk = (nodes: HierarchyNode[], trail: string[]): string[] | null => {
    for (const node of nodes) {
      const key = node.fullPath.join("-");
      if (node.entry === entryName) {
        return node.children.length > 0 ? [...trail, key] : trail;
      }
      if (node.children.length === 0) continue;
      const found = walk(node.children, [...trail, key]);
      if (found !== null) return found;
    }
    return null;
  };
  return walk(buildHierarchy(names), []) ?? [];
}
