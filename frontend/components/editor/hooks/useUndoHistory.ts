"use client";

/**
 * useUndoHistory Hook
 *
 * Manages undo stack for bbox editor operations.
 * Since auto-save is enabled, undo works by calling API to revert changes.
 */

import { useState, useCallback, useRef } from "react";
import type { EditorBbox, UndoAction } from "@/lib/editor/types";
import { MAX_UNDO_STACK_SIZE } from "@/lib/editor/constants";

export interface UseUndoHistoryOptions {
  /** Callback to create a bbox via API */
  onBboxCreate?: (bbox: Omit<EditorBbox, "id">) => Promise<number>;
  /** Callback to update a bbox via API */
  onBboxUpdate?: (id: number, bbox: Partial<EditorBbox>) => Promise<void>;
  /** Callback to delete a bbox via API */
  onBboxDelete?: (id: number) => Promise<void>;
  /** Callback when an error occurs during undo */
  onError?: (message: string, error: unknown) => void;
}

export interface UseUndoHistoryReturn {
  /** Push a new action to the undo stack */
  pushAction: (action: UndoAction) => void;
  /** Undo the last action */
  undo: () => Promise<void>;
  /** Check if undo is available */
  canUndo: boolean;
  /** Number of actions in the stack */
  stackSize: number;
  /** Clear the undo stack */
  clearStack: () => void;
  /** Whether an undo operation is in progress */
  isUndoing: boolean;
}

export function useUndoHistory(
  options: UseUndoHistoryOptions
): UseUndoHistoryReturn {
  const [undoStack, setUndoStack] = useState<UndoAction[]>([]);
  const [isUndoing, setIsUndoing] = useState(false);
  const stackRef = useRef<UndoAction[]>([]);

  // Keep ref in sync with state for async operations
  stackRef.current = undoStack;

  const pushAction = useCallback((action: UndoAction) => {
    setUndoStack((prev) => {
      const newStack = [...prev, action];
      // Limit stack size
      if (newStack.length > MAX_UNDO_STACK_SIZE) {
        return newStack.slice(-MAX_UNDO_STACK_SIZE);
      }
      return newStack;
    });
  }, []);

  const undo = useCallback(async () => {
    const stack = stackRef.current;
    if (stack.length === 0 || isUndoing) return;

    const action = stack[stack.length - 1];
    setIsUndoing(true);

    try {
      switch (action.type) {
        case "create":
          // Undo create = delete the bbox
          if (action.newState?.cropId && options.onBboxDelete) {
            await options.onBboxDelete(action.newState.cropId);
          }
          break;

        case "update":
          // Undo update = restore previous state
          if (action.previousState?.cropId && options.onBboxUpdate) {
            await options.onBboxUpdate(action.previousState.cropId, {
              x: action.previousState.x,
              y: action.previousState.y,
              width: action.previousState.width,
              height: action.previousState.height,
            });
          }
          break;

        case "delete":
          // Undo delete = re-create the bbox
          if (action.previousState && options.onBboxCreate) {
            await options.onBboxCreate({
              x: action.previousState.x,
              y: action.previousState.y,
              width: action.previousState.width,
              height: action.previousState.height,
            });
          }
          break;
      }

      // Remove the action from stack
      setUndoStack((prev) => prev.slice(0, -1));
    } catch (error) {
      console.error("Undo failed:", error);
      // Don't remove from stack if undo failed
      const message = error instanceof Error ? error.message : "Undo operation failed";
      options.onError?.(`Failed to undo: ${message}`, error);
    } finally {
      setIsUndoing(false);
    }
  }, [isUndoing, options]);

  const clearStack = useCallback(() => {
    setUndoStack([]);
  }, []);

  return {
    pushAction,
    undo,
    canUndo: undoStack.length > 0 && !isUndoing,
    stackSize: undoStack.length,
    clearStack,
    isUndoing,
  };
}
