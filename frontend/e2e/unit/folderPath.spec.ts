import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { fullLabel } from "../../components/ui/ColorTagSelect";
import { ancestorPath, parentLabel } from "../../lib/folderTree";

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
  // Every row the picker offers, not just the leaves: ids 18 and 20 are BOTH
  // named `détyrosination` (20 nested inside 18) and collided too.
  // Siblings share a parent and are told apart by name, so a row is both.
  const rows = PROD.map(
    (f) => `${f.name} | ${(parentLabel(PROD, f.id) ?? "").slice(0, CUTOFF)}`
  );
  expect(new Set(rows).size).toBe(PROD.length);
});

test("the whole trail survives for the tooltip, because the elision is lossy", () => {
  // Two folders under different grandparents whose parents share a name render
  // the SAME row -- this tree is one folder away from it, since 18 and 20 are
  // both `détyrosination` and `manip1` already exists under 20. Eliding is a
  // rendering choice; it must not be the only copy, or the picker that files
  // experiments offers two identical rows that file into different branches.
  const withSibling = [...PROD, { id: 99, parent_id: 18, name: "manip1" }];
  expect(parentLabel(withSibling, 24)).toBe(parentLabel(withSibling, 99));
  expect(ancestorPath(withSibling, 24)).not.toBe(ancestorPath(withSibling, 99));
});

test("ancestorPath keeps the top-down trail and the same null contract", () => {
  expect(ancestorPath(PROD, 21)).toBe(
    "Airy scan with same parameters per maps / détyrosination / controles"
  );
  expect(ancestorPath(PROD, 11)).toBeNull();
  expect(ancestorPath(PROD, 999)).toBeNull();
  expect(ancestorPath(PROD, null)).toBeNull();
});

test("the tooltip prefers the unabbreviated form over the rendered one", () => {
  // The elision is only safe because this prefers `secondaryFull`. Reading
  // `secondary` here would put the lossy string in both places and leave the
  // collision above with nowhere to be resolved.
  expect(
    fullLabel({ id: 1, name: "manip1", secondary: "… / dét", secondaryFull: "a / b / dét" })
  ).toBe("manip1 — a / b / dét");
  // Falls back for the pickers that have nothing to abbreviate.
  expect(fullLabel({ id: 1, name: "CLIP170", secondary: "Zeiss" })).toBe("CLIP170 — Zeiss");
  expect(fullLabel({ id: 1, name: "3D sim" })).toBe("3D sim");
});

test("the experiments page derives folder rows from the helpers, not by hand", () => {
  // A pure test of parentLabel cannot catch a call site that stops calling it,
  // and inline trail-building in this very file is how the bug shipped. Same
  // guard, and for the same reason, as `withGeometryFrom.spec.ts`.
  const source = readFileSync(
    join(__dirname, "../../app/dashboard/experiments/page.tsx"),
    "utf8"
  );
  expect(source).toContain("parentLabel(folders,");
  expect(source).toContain("ancestorPath(folders,");
  // The hand-rolled top-down join that used to live here, in any spelling.
  expect(source).not.toMatch(/trailTo\([^)]*\)[\s\S]{0,40}\.slice\(0,\s*-1\)/);
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

test("a cycle in parent_id yields a short label rather than hanging", () => {
  // The parent named is still genuinely the parent -- only the depth is
  // understated, because `trailTo` stops at the repeat. A short label beats a
  // hung render, and the API rejects cycles anyway.
  const cyclic: Node[] = [
    { id: 1, name: "a", parent_id: 2 },
    { id: 2, name: "b", parent_id: 1 },
  ];
  expect(parentLabel(cyclic, 1)).toBe("b");
  expect(ancestorPath(cyclic, 1)).toBe("b");
});
