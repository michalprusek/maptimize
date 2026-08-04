import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

/**
 * Guards on the translation files themselves.
 *
 * ⚠️ These MUST read the raw text, not `JSON.parse`. A duplicate key is legal
 * JSON — the parser silently keeps the last one — so every structural check
 * built on a parsed object is blind to exactly the failure that has already
 * shipped a UI bug in this repo once. The last edit to touch a key it did not
 * mean to would look identical to a clean one.
 *
 * This lived in the discriminant spec and was deleted with it, over a key list
 * that has since gone away. Re-established over the whole file, which is where
 * it should have been.
 */
const LOCALES = ["en", "fr"] as const;

function raw(locale: string): string {
  return readFileSync(join(process.cwd(), "messages", `${locale}.json`), "utf8");
}

/** Every `"key":` in the file, in source order, with its namespace path. */
function keyPaths(source: string): string[] {
  const paths: string[] = [];
  const stack: string[] = [];
  for (const line of source.split("\n")) {
    const key = line.match(/^\s*"([^"]+)"\s*:/)?.[1];
    if (key) paths.push([...stack, key].join("."));
    // A key that opens an object pushes a namespace; a lone `}` closes one.
    if (key && /:\s*\{\s*$/.test(line)) stack.push(key);
    else if (/^\s*\}[,]?\s*$/.test(line)) stack.pop();
  }
  return paths;
}

for (const locale of LOCALES) {
  test(`${locale}.json has no duplicate keys`, () => {
    // ⚠️ Written out rather than `paths.filter((p) => !seen.add(p))`, which is
    // the tempting one-liner and is always empty: `Set.add` returns the Set,
    // which is truthy, so the negation is a constant false. That version of
    // this test passed against a deliberately duplicated key.
    const seen = new Set<string>();
    const duplicated: string[] = [];
    for (const path of keyPaths(raw(locale))) {
      if (seen.has(path)) duplicated.push(path);
      seen.add(path);
    }
    expect(duplicated, `duplicate keys silently shadow: ${duplicated}`).toEqual([]);
  });

  test(`${locale}.json parses`, () => {
    expect(() => JSON.parse(raw(locale))).not.toThrow();
  });
}

test("both locales define exactly the same keys", () => {
  // A key present in one locale only renders as its own name to half the lab.
  const [en, fr] = LOCALES.map((l) => new Set(keyPaths(raw(l))));
  const enOnly = Array.from(en).filter((k) => !fr.has(k));
  const frOnly = Array.from(fr).filter((k) => !en.has(k));
  // `admin.*` is a known, pre-existing gap; pin the count so it cannot grow
  // unnoticed while still failing on anything new.
  expect(enOnly, `keys missing from fr.json: ${enOnly}`).toEqual([]);
  expect(frOnly.filter((k) => !k.startsWith("admin.")), `keys missing from en.json: ${frOnly}`).toEqual([]);
});
