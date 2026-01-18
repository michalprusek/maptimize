"use client";

/**
 * useEditorPersistence Hook
 *
 * Persists editor mode to localStorage so it survives page refreshes.
 */

import type { EditorMode } from "@/lib/editor/types";
import { useSyncToLocalStorage } from "./useLocalStorage";

const STORAGE_KEY = "maptimize:editor:mode";
const VALID_MODES: EditorMode[] = ["view", "draw", "edit", "segment"];

function isValidEditorMode(value: unknown): value is EditorMode {
  return typeof value === "string" && VALID_MODES.includes(value as EditorMode);
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
  useSyncToLocalStorage(STORAGE_KEY, currentMode, setMode, isValidEditorMode);
}
