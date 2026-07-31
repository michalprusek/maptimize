import { expect, test } from "@playwright/test";

import {
  breadcrumbTrail,
  childFolders,
  descendantIds,
  isStructural,
  rootFolders,
  totalDocumentCount,
} from "../../components/documents/folderTree";
import type { Folder } from "../../lib/api";

/**
 * The tree is derived from API data, and the way that goes wrong is a quietly
 * disagreeing UI rather than a crash: a group's `common` buried under someone's
 * scratch folder, a private folder shown without its badge, a root reading "0
 * documents" while holding the whole library. tsc is happy with all of those.
 */

function folder(over: Partial<Folder> & { id: number }): Folder {
  return {
    name: `f${over.id}`,
    parent_id: null,
    created_at: "2026-07-31T00:00:00Z",
    group_id: 2,
    group_name: "Dr. Janke Lab",
    visibility: "group",
    kind: "custom",
    owner_user_id: 7,
    path: `f${over.id}`,
    document_count: 0,
    ...over,
  };
}

test("shared folders sort ahead of personal ones", () => {
  const folders = [
    folder({ id: 4, name: "zebra", kind: "custom", parent_id: 1 }),
    folder({ id: 3, name: "Theo", kind: "user", parent_id: 1 }),
    folder({ id: 2, name: "common", kind: "common", parent_id: 1 }),
    folder({ id: 5, name: "alpha", kind: "custom", parent_id: 1 }),
  ];
  expect(childFolders(folders, 1).map((f) => f.name)).toEqual([
    "common",
    "Theo",
    "alpha",
    "zebra",
  ]);
});

test("group roots come first at the top level", () => {
  const folders = [
    folder({ id: 9, name: "loose notes", kind: "custom", group_id: null }),
    folder({ id: 1, name: "Dr. Janke Lab", kind: "root" }),
  ];
  expect(rootFolders(folders).map((f) => f.name)).toEqual([
    "Dr. Janke Lab",
    "loose notes",
  ]);
});

test("a folder whose parent is invisible surfaces at the root instead of vanishing", () => {
  // Legitimate: a subfolder can be visible while the private folder holding it
  // is not. Dropping it would hide documents with no way to reach them.
  const orphan = folder({ id: 7, name: "drafts", parent_id: 999 });
  expect(rootFolders([orphan]).map((f) => f.id)).toEqual([7]);
});

test("structural folders are the ones the server refuses to change", () => {
  expect(isStructural(folder({ id: 1, kind: "root" }))).toBe(true);
  expect(isStructural(folder({ id: 2, kind: "common" }))).toBe(true);
  expect(isStructural(folder({ id: 3, kind: "user" }))).toBe(true);
  expect(isStructural(folder({ id: 4, kind: "custom" }))).toBe(false);
});

test("counts aggregate up the tree", () => {
  const folders = [
    folder({ id: 1, kind: "root", document_count: 1 }),
    folder({ id: 2, kind: "common", parent_id: 1, document_count: 10 }),
    folder({ id: 3, parent_id: 2, document_count: 5 }),
  ];
  expect(totalDocumentCount(folders, 1)).toBe(16);
  expect(totalDocumentCount(folders, 2)).toBe(15);
  expect(totalDocumentCount(folders, 3)).toBe(5);
});

test("the breadcrumb runs from the root down to the folder", () => {
  const folders = [
    folder({ id: 1, name: "Lab", kind: "root" }),
    folder({ id: 2, name: "common", kind: "common", parent_id: 1 }),
    folder({ id: 3, name: "papers", parent_id: 2 }),
  ];
  expect(breadcrumbTrail(folders, 3).map((f) => f.name)).toEqual([
    "Lab",
    "common",
    "papers",
  ]);
});

test("a cycle in parent_id does not hang the breadcrumb", () => {
  // parent_id carries no FK server-side, so a bad row can point back up. A hung
  // render is worse than a short trail.
  const folders = [
    folder({ id: 1, name: "a", parent_id: 2 }),
    folder({ id: 2, name: "b", parent_id: 1 }),
  ];
  expect(breadcrumbTrail(folders, 1).length).toBeLessThanOrEqual(2);
});

test("descendants cover the whole subtree", () => {
  const folders = [
    folder({ id: 1 }),
    folder({ id: 2, parent_id: 1 }),
    folder({ id: 3, parent_id: 2 }),
    folder({ id: 4 }),
  ];
  expect(Array.from(descendantIds(folders, 1)).sort()).toEqual([2, 3]);
});
