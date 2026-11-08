import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { readPersistedEditorMode } from "../../hooks/useEditorPersistence";
import { EDITOR_MODES, isEditorMode } from "../../lib/editor/types";

/**
 * The editor mode is persisted in localStorage, so a mode that is removed from the
 * app keeps living in users' browsers. SAM segmentation was such a mode: every
 * browser that used it still holds `"segment"`, and the editor must open in a mode
 * it can render instead of trusting that value.
 */

const KEY = "maptimize:editor:mode";

function withStorage(stored: Record<string, string>, fn: () => void): void {
  const g = globalThis as Record<string, unknown>;
  g.window = globalThis;
  g.localStorage = { getItem: (k: string) => (k in stored ? stored[k] : null) };
  try {
    fn();
  } finally {
    delete g.window;
    delete g.localStorage;
  }
}

test("only the modes the editor renders are valid", () => {
  for (const mode of EDITOR_MODES) expect(isEditorMode(mode)).toBe(true);
  // "edit" was declared but never set by anything; it is gone too.
  for (const value of ["segment", "edit", "", null, undefined, 0, {}]) {
    expect(isEditorMode(value), String(value)).toBe(false);
  }
});

test("a persisted 'segment' from the removed SAM editor lands on view", () => {
  withStorage({ [KEY]: JSON.stringify("segment") }, () => {
    expect(readPersistedEditorMode()).toBe("view");
  });
});

test("a persisted valid mode is restored", () => {
  withStorage({ [KEY]: JSON.stringify("draw") }, () => {
    expect(readPersistedEditorMode()).toBe("draw");
  });
});

test("nothing stored, or garbage stored, lands on view", () => {
  withStorage({}, () => expect(readPersistedEditorMode()).toBe("view"));
  withStorage({ [KEY]: "{not json" }, () => expect(readPersistedEditorMode()).toBe("view"));
});

test("the editor takes its initial mode from the shared reader", () => {
  // The page used to parse this key itself, against its own hand-written mode list,
  // so dropping a mode from EditorMode left a second list that still admitted it.
  const src = readFileSync(
    join(__dirname, "../../components/editor/ImageEditorPage.tsx"),
    "utf8"
  );
  expect(src).toMatch(/mode:\s*readPersistedEditorMode\(\)/);
  expect(src, "the page must not read the mode key itself").not.toContain(KEY);
});
