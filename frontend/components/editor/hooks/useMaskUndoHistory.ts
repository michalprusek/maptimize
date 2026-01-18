"use client";

/**
 * useMaskUndoHistory Hook
 *
 * Manages undo stack for FOV mask operations in segmentation mode.
 * Stores previous mask state before each save/delete operation,
 * allowing users to revert changes.
 */

import { useState, useCallback, useRef } from "react";
import type { MaskUndoAction, FOVMaskState } from "@/lib/editor/types";
import { MAX_UNDO_STACK_SIZE } from "@/lib/editor/constants";
import { api } from "@/lib/api";

export interface UseMaskUndoHistoryOptions {
  /** Callback when mask is restored (for UI refresh) */
  onMaskRestored?: (imageId: number) => void;
  /** Callback when an error occurs during undo */
  onError?: (message: string, error: unknown) => void;
}

export interface UseMaskUndoHistoryReturn {
  /** Capture current mask state before an operation */
  captureState: (imageId: number) => Promise<FOVMaskState>;
  /** Push a save action to the undo stack (call after saving) */
  pushSaveAction: (imageId: number, previousState: FOVMaskState) => void;
  /** Push a delete action to the undo stack (call after deleting) */
  pushDeleteAction: (imageId: number, previousState: FOVMaskState) => void;
  /** Undo the last mask operation */
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

export function useMaskUndoHistory(
  options: UseMaskUndoHistoryOptions = {}
): UseMaskUndoHistoryReturn {
  const [undoStack, setUndoStack] = useState<MaskUndoAction[]>([]);
  const [isUndoing, setIsUndoing] = useState(false);
  const stackRef = useRef<MaskUndoAction[]>([]);

  // Keep ref in sync with state for async operations
  stackRef.current = undoStack;

  /**
   * Capture the current mask state for an image.
   * Call this BEFORE performing a save/delete operation.
   */
  const captureState = useCallback(async (imageId: number): Promise<FOVMaskState> => {
    try {
      const response = await api.getFOVSegmentationMask(imageId);
      return {
        hasMask: response.has_mask,
        polygon: response.polygon,
        iouScore: response.iou_score,
        areaPixels: response.area_pixels,
      };
    } catch {
      // If we can't get the state, assume no mask exists
      return { hasMask: false };
    }
  }, []);

  const pushSaveAction = useCallback((imageId: number, previousState: FOVMaskState) => {
    setUndoStack((prev) => {
      const action: MaskUndoAction = { type: "save", imageId, previousState };
      const newStack = [...prev, action];
      if (newStack.length > MAX_UNDO_STACK_SIZE) {
        return newStack.slice(-MAX_UNDO_STACK_SIZE);
      }
      return newStack;
    });
  }, []);

  const pushDeleteAction = useCallback((imageId: number, previousState: FOVMaskState) => {
    setUndoStack((prev) => {
      const action: MaskUndoAction = { type: "delete", imageId, previousState };
      const newStack = [...prev, action];
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
      const { imageId, previousState, type } = action;

      if (type === "save") {
        // Undo save: restore previous state
        if (previousState.hasMask && previousState.polygon) {
          // Restore the previous mask by deleting current and re-saving
          await api.deleteFOVSegmentationMask(imageId);

          // Normalize polygon to array format for save
          const polygons = Array.isArray(previousState.polygon[0]?.[0])
            ? (previousState.polygon as [number, number][][])
            : [previousState.polygon as [number, number][]];

          await api.saveFOVSegmentationMaskWithUnion({
            image_id: imageId,
            polygons,
            iou_score: previousState.iouScore ?? 0.9,
            prompt_count: 1,
          });
        } else {
          // Previous state had no mask - delete current mask
          await api.deleteFOVSegmentationMask(imageId);
        }
      } else if (type === "delete") {
        // Undo delete: restore the deleted mask
        if (previousState.hasMask && previousState.polygon) {
          const polygons = Array.isArray(previousState.polygon[0]?.[0])
            ? (previousState.polygon as [number, number][][])
            : [previousState.polygon as [number, number][]];

          await api.saveFOVSegmentationMaskWithUnion({
            image_id: imageId,
            polygons,
            iou_score: previousState.iouScore ?? 0.9,
            prompt_count: 1,
          });
        }
      }

      // Remove the action from stack
      setUndoStack((prev) => prev.slice(0, -1));

      // Notify parent to refresh UI
      options.onMaskRestored?.(imageId);
    } catch (error) {
      console.error("[useMaskUndoHistory] Undo failed:", error);
      const message = error instanceof Error ? error.message : "Undo operation failed";
      options.onError?.(`Failed to undo mask: ${message}`, error);
    } finally {
      setIsUndoing(false);
    }
  }, [isUndoing, options]);

  const clearStack = useCallback(() => {
    setUndoStack([]);
  }, []);

  return {
    captureState,
    pushSaveAction,
    pushDeleteAction,
    undo,
    canUndo: undoStack.length > 0 && !isUndoing,
    stackSize: undoStack.length,
    clearStack,
    isUndoing,
  };
}
