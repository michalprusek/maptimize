/**
 * Ordering and labelling rules for the document-library tree.
 *
 * Pure functions, no React: the tree's shape is derived from API data, and the
 * way that goes wrong is a quietly disagreeing UI -- a private folder rendered
 * without its badge, or a group's `common` buried below someone's scratch
 * folder -- rather than an exception. Types and tsc are happy either way, so the
 * rules live here and are unit-tested.
 */
import type { Folder, FolderKind } from "@/lib/api";

/** Folders the server creates and refuses to rename, move or delete. */
const STRUCTURAL_KINDS: readonly FolderKind[] = ["root", "common", "user"];

export function isStructural(folder: Folder): boolean {
  return STRUCTURAL_KINDS.includes(folder.kind);
}

/**
 * Rank inside one level of the tree. Group roots and the shared `common` come
 * before people's own folders, which come before ad-hoc ones -- so the shared
 * material is what you see first, not whatever happens to sort early.
 */
function rank(folder: Folder): number {
  switch (folder.kind) {
    case "root":
      return 0;
    case "common":
      return 1;
    case "user":
      return 2;
    default:
      return 3;
  }
}

/** Direct children of `parentId` (null = top level), in display order. */
export function childFolders(folders: Folder[], parentId: number | null): Folder[] {
  return folders
    .filter((f) => (f.parent_id ?? null) === parentId)
    .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

/**
 * Top-level rows. A folder whose parent is not in the visible set is surfaced
 * here rather than dropped: that happens legitimately (a folder nested under
 * someone else's private folder is invisible while its own child is not), and
 * silently hiding a folder that holds documents is worse than showing it at the
 * root.
 */
export function rootFolders(folders: Folder[]): Folder[] {
  const ids = new Set(folders.map((f) => f.id));
  return folders
    .filter((f) => f.parent_id == null || !ids.has(f.parent_id))
    .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

/** Path from the top down to (and including) `folderId`. */
export function breadcrumbTrail(folders: Folder[], folderId: number | null): Folder[] {
  const byId = new Map(folders.map((f) => [f.id, f]));
  const trail: Folder[] = [];
  const seen = new Set<number>();
  let current = folderId != null ? byId.get(folderId) : undefined;
  // parent_id has no FK server-side, so a cycle is possible; a hung render is
  // worse than a short trail.
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    trail.unshift(current);
    current = current.parent_id != null ? byId.get(current.parent_id) : undefined;
  }
  return trail;
}

/** All descendant ids of `folderId` (used to block moving a folder into itself). */
export function descendantIds(folders: Folder[], folderId: number): Set<number> {
  const out = new Set<number>();
  const walk = (id: number) => {
    for (const f of folders) {
      if (f.parent_id === id && !out.has(f.id)) {
        out.add(f.id);
        walk(f.id);
      }
    }
  };
  walk(folderId);
  return out;
}

/**
 * Documents in a folder plus everything beneath it. The listing endpoint counts
 * one folder at a time, so a group root would otherwise read "0 documents" while
 * holding a full library.
 */
export function totalDocumentCount(folders: Folder[], folderId: number): number {
  const subtree = descendantIds(folders, folderId);
  const self = folders.find((f) => f.id === folderId);
  let total = self?.document_count ?? 0;
  for (const f of folders) {
    if (subtree.has(f.id)) total += f.document_count;
  }
  return total;
}
