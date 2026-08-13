import { expect, test } from "@playwright/test";

import { parentLabel } from "../../lib/folderTree";

/**
 * The label a folder picker shows under each folder name.
 *
 * A dropdown row is one line of finite width, and the thing being picked is the
 * *name*. The path only earns its place by disambiguating names that repeat
 * between branches -- so what it must never do is spend that width on the top of
 * the tree, which every sibling shares, and truncate away the parent, which is
 * the only part that differs. A full top-down trail does exactly that, silently:
 * it renders, it fits, and every row reads the same.
 */

interface Node {
  id: number;
  name: string;
  parent_id: number | null;
}

/** The real production tree (`experiment_folders`), which is what broke. */
const PROD: Node[] = [
  { id: 11, parent_id: null, name: "Airy scan with same parameters per maps" },
  { id: 12, parent_id: null, name: "airy scan (old)" },
  { id: 13, parent_id: null, name: "3D sim" },
  { id: 14, parent_id: 11, name: "unmodified" },
  { id: 15, parent_id: 14, name: "manip 1" },
  { id: 16, parent_id: 14, name: "manip2" },
  { id: 17, parent_id: 14, name: "manip3" },
  { id: 18, parent_id: 11, name: "détyrosination" },
  { id: 19, parent_id: 18, name: "controles" },
  { id: 20, parent_id: 18, name: "détyrosination" },
  { id: 21, parent_id: 19, name: "manip2" },
  { id: 22, parent_id: 19, name: "manip1" },
  { id: 23, parent_id: 19, name: "manip3" },
  { id: 24, parent_id: 20, name: "manip1" },
  { id: 25, parent_id: 20, name: "manip2" },
  { id: 26, parent_id: 20, name: "manip3" },
];

test("a top-level folder has nothing above it to name", () => {
  expect(parentLabel(PROD, 11)).toBeNull();
});

test("a folder one level down names its parent without an elision mark", () => {
  // Nothing was dropped, so claiming otherwise would be a lie about the tree.
  expect(parentLabel(PROD, 14)).toBe("Airy scan with same parameters per maps");
});

test("a deeper folder shows the parent and marks what it left out", () => {
  expect(parentLabel(PROD, 21)).toBe("… / controles");
});

test("the label still tells branches apart after the row truncates it", () => {
  // The property that broke, stated the way it broke. Nine folders are named
  // manip1/manip2/manip3 across three parents, so the label is the whole
  // disambiguator -- and a dropdown row shows only its first characters. A full
  // top-down trail passes a plain distinctness check (the strings do differ)
  // and still collapses to one visible value, which is what shipped.
  const CUTOFF = 24; // roughly what a row of this menu fits
  // Siblings share a parent and are told apart by name, so a row is both.
  const leaves = [15, 16, 17, 21, 22, 23, 24, 25, 26];
  const rows = leaves.map((id) => {
    const name = PROD.find((f) => f.id === id)!.name;
    return `${name} | ${(parentLabel(PROD, id) ?? "").slice(0, CUTOFF)}`;
  });
  expect(new Set(rows).size).toBe(leaves.length);
});

test("an unknown id has no label rather than a broken one", () => {
  expect(parentLabel(PROD, 999)).toBeNull();
  expect(parentLabel(PROD, null)).toBeNull();
});

test("a parent outside the visible set is not invented", () => {
  // Legitimate: a folder nested under one the caller cannot see. `trailTo`
  // stops there, so the folder reads as top-level rather than naming a folder
  // the caller was never shown.
  const hidden: Node[] = [{ id: 5, name: "batch", parent_id: 4 }];
  expect(parentLabel(hidden, 5)).toBeNull();
});

test("a cycle in parent_id does not hang the label", () => {
  const cyclic: Node[] = [
    { id: 1, name: "a", parent_id: 2 },
    { id: 2, name: "b", parent_id: 1 },
  ];
  expect(parentLabel(cyclic, 1)).toBe("b");
});
