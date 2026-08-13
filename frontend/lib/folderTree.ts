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

/**
 * How a folder is placed, for a picker row that has one line to say it.
 *
 * The immediate parent, marked with a leading elision when the trail runs
 * deeper. NOT the full top-down trail: a dropdown row is finite, and a trail
 * truncates from the tail, so it spends the width on the root -- which every
 * sibling shares -- and drops the parent, the cheapest thing that tells them
 * apart. Nine folders named `manip1..3` shipped reading identically for exactly
 * that reason.
 *
 * ⚠️ The elision is lossy, so this is a rendering, not an identity. The parent
 * name is not unique either -- this very tree holds two folders named
 * `détyrosination`, one inside the other -- so two folders under different
 * grandparents can render the same row. Whatever shows this must keep
 * `ancestorPath` reachable, or filing into the wrong branch becomes
 * unrecoverable rather than merely cramped.
 */
export function parentLabel<T extends TreeNode>(
  nodes: T[],
  id: number | null
): string | null {
  const trail = trailTo(nodes, id);
  // Nothing above it, or its parent is outside the visible set -- and a parent
  // the caller was never shown is not a name worth inventing.
  if (trail.length < 2) return null;
  const parent = trail[trail.length - 2];
  return trail.length > 2 ? `… / ${parent.name}` : parent.name;
}

/**
 * Everything above a folder, top down. The unabbreviated form of
 * `parentLabel` -- for a tooltip, a confirmation, anywhere a second line is
 * cheap. Never for a dropdown row: that is the shape that shipped unreadable.
 */
export function ancestorPath<T extends TreeNode>(
  nodes: T[],
  id: number | null
): string | null {
  const trail = trailTo(nodes, id);
  if (trail.length < 2) return null;
  return trail.slice(0, -1).map((node) => node.name).join(" / ");
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
