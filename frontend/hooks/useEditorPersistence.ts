"use client";

/**
 * useEditorPersistence Hook
 *
 * Persists editor mode to localStorage so it survives page refreshes.
 */

import { isEditorMode, type EditorMode } from "@/lib/editor/types";
import { useSyncToLocalStorage } from "./useLocalStorage";

const STORAGE_KEY = "maptimize:editor:mode";
const DEFAULT_MODE: EditorMode = "view";

/**
 * The persisted mode, read synchronously for the editor's initial state.
 *
 * ⚠️ Anything stored that is not a current mode lands on DEFAULT_MODE. That is not
 * hypothetical: browsers that used the removed SAM editor still hold `"segment"`
 * under this key, and letting it through would put the editor in a mode nothing
 * renders for.
 */
export function readPersistedEditorMode(): EditorMode {
  if (typeof window === "undefined") return DEFAULT_MODE;
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    return isEditorMode(parsed) ? parsed : DEFAULT_MODE;
  } catch {
    return DEFAULT_MODE;
  }
}

/**
 * Persists editor mode to localStorage.
 * On mount, restores the saved mode (if valid).
 * On mode change, saves to localStorage.
 */
export function useEditorModePersistence(
  currentMode: EditorMode,
  setMode: (mode: EditorMode) => void
): void {
  useSyncToLocalStorage(STORAGE_KEY, currentMode, setMode, isEditorMode);
}
