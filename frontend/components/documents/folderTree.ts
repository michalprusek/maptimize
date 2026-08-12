/**
 * What a document-library folder *means*, on top of the shared tree arithmetic.
 *
 * The walking, ordering fallback, breadcrumb and rollup live in `lib/folderTree`
 * and are shared with the experiment tree. Only the library's own rules are
 * here: which folders the server refuses to touch, and the ordering that puts
 * shared material above people's own.
 */
import {
  childrenOf,
  descendantIdsOf,
  rootsOf,
  subtreeTotal,
  trailTo,
} from "@/lib/folderTree";
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

/** Shared by every listing, so one level of the tree never sorts unlike another. */
function byDisplayOrder(a: Folder, b: Folder): number {
  return rank(a) - rank(b) || a.name.localeCompare(b.name);
}

/** Direct children of `parentId` (null = top level), in display order. */
export function childFolders(folders: Folder[], parentId: number | null): Folder[] {
  return childrenOf(folders, parentId, byDisplayOrder);
}

/** Top-level rows, in display order. */
export function rootFolders(folders: Folder[]): Folder[] {
  return rootsOf(folders, byDisplayOrder);
}

/** Path from the top down to (and including) `folderId`. */
export function breadcrumbTrail(folders: Folder[], folderId: number | null): Folder[] {
  return trailTo(folders, folderId);
}

/** All descendant ids of `folderId` (used to block moving a folder into itself). */
export function descendantIds(folders: Folder[], folderId: number): Set<number> {
  return descendantIdsOf(folders, folderId);
}

/** Documents in a folder plus everything beneath it. */
export function totalDocumentCount(folders: Folder[], folderId: number): number {
  return subtreeTotal(folders, folderId, (f) => f.document_count);
}
