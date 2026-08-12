/**
 * Tree arithmetic shared by the two folder trees in this app.
 *
 * The document library and the experiment list both draw a tree the server
 * hands over as a flat list. The walking, the ordering fallback, the breadcrumb
 * and the subtree rollup are identical in both; only what a node *means*
 * differs, so only that stays with each feature.
 *
 * Pure functions, no React: what goes wrong here is a quietly disagreeing UI —
 * a folder that vanishes because its parent is invisible, a parent reading "0"
 * while its children hold everything, a breadcrumb that spins on a cycle — and
 * none of those raise. tsc is happy with all of them, so they are unit-tested.
 */

/** The minimum a node needs for any of this to work. */
export interface TreeNode {
  id: number;
  name: string;
  parent_id: number | null;
}

/** Fallback ordering. Callers with their own ranking pass a comparator. */
function byName(a: TreeNode, b: TreeNode): number {
  return a.name.localeCompare(b.name);
}

/** Direct children of `parentId` (null = top level), in display order. */
export function childrenOf<T extends TreeNode>(
  nodes: T[],
  parentId: number | null,
  order: (a: T, b: T) => number = byName
): T[] {
  return nodes.filter((n) => (n.parent_id ?? null) === parentId).sort(order);
}

/**
 * Top-level rows.
 *
 * A node whose parent is not in the visible set is surfaced here rather than
 * dropped. That happens legitimately — a folder nested under one the caller
 * cannot see is itself visible — and silently hiding a folder that holds work is
 * worse than showing it at the top.
 */
export function rootsOf<T extends TreeNode>(
  nodes: T[],
  order: (a: T, b: T) => number = byName
): T[] {
  const ids = new Set(nodes.map((n) => n.id));
  return nodes
    .filter((n) => n.parent_id == null || !ids.has(n.parent_id))
    .sort(order);
}

/** Path from the top down to (and including) `id`. */
export function trailTo<T extends TreeNode>(
  nodes: T[],
  id: number | null
): T[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const trail: T[] = [];
  const seen = new Set<number>();
  let current = id != null ? byId.get(id) : undefined;
  // `parent_id` has no foreign key server-side, so a cycle is representable; a
  // hung render is worse than a short trail.
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    trail.unshift(current);
    current = current.parent_id != null ? byId.get(current.parent_id) : undefined;
  }
  return trail;
}

/** Every id below `id`. Also what blocks moving a folder into its own subtree. */
export function descendantIdsOf<T extends TreeNode>(
  nodes: T[],
  id: number
): Set<number> {
  const out = new Set<number>();
  const walk = (parent: number) => {
    for (const n of nodes) {
      if (n.parent_id === parent && !out.has(n.id)) {
        out.add(n.id);
        walk(n.id);
      }
    }
  };
  walk(id);
  return out;
}

/**
 * A node's own count plus everything beneath it.
 *
 * The listing endpoints count one folder at a time, so without this a group root
 * reads "0" while holding the entire library.
 */
export function subtreeTotal<T extends TreeNode>(
  nodes: T[],
  id: number,
  countOf: (node: T) => number
): number {
  const counted = descendantIdsOf(nodes, id);
  counted.add(id);
  return nodes.reduce(
    (total, n) => (counted.has(n.id) ? total + countOf(n) : total),
    0
  );
}
