"use client";

/**
 * useSegmentation Hook
 *
 * Manages interactive SAM segmentation state and API calls.
 * Handles click points, API inference with debouncing, and mask saving.
 */

import { useState, useCallback, useRef, useEffect } from "react";
import { api, type SegmentClickPoint, type SAMEmbeddingStatus } from "@/lib/api";
import type { SegmentationState, CellPolygon } from "@/lib/editor/types";
import { INITIAL_SEGMENTATION_STATE } from "@/lib/editor/types";

interface UseSegmentationOptions {
  /** Image ID for segmentation */
  imageId: number;
  /** Callback when mask is saved successfully */
  onMaskSaved?: (cropId: number, polygon: [number, number][], iouScore: number) => void;
  /** Debounce delay for API calls (ms) */
  debounceMs?: number;
}

interface UseSegmentationReturn {
  /** Current segmentation state */
  state: SegmentationState;
  /** SAM embedding status for this image */
  embeddingStatus: SAMEmbeddingStatus;
  /** Whether segmentation is available (embedding ready) */
  isReady: boolean;
  /** Add a click point (triggers inference) */
  addClickPoint: (x: number, y: number, label: 0 | 1) => void;
  /** Remove the last click point */
  undoLastClick: () => void;
  /** Clear all click points and preview */
  clearSegmentation: () => void;
  /** Save the current mask to a crop */
  saveMask: (cropId: number) => Promise<{ success: boolean; error?: string }>;
  /** Set target crop ID for saving */
  setTargetCropId: (id: number | null) => void;
  /** Trigger embedding computation */
  computeEmbedding: () => Promise<void>;
  /** Refresh embedding status */
  refreshStatus: () => Promise<void>;
}

export function useSegmentation({
  imageId,
  onMaskSaved,
  debounceMs = 50,
}: UseSegmentationOptions): UseSegmentationReturn {
  const [state, setState] = useState<SegmentationState>(INITIAL_SEGMENTATION_STATE);
  const [embeddingStatus, setEmbeddingStatus] = useState<SAMEmbeddingStatus>("not_started");

  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Check embedding status on mount
  useEffect(() => {
    refreshStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageId]);

  // Poll for status while computing
  useEffect(() => {
    if (embeddingStatus === "computing" || embeddingStatus === "pending") {
      const interval = setInterval(refreshStatus, 2000);
      return () => clearInterval(interval);
    }
  }, [embeddingStatus]);

  const refreshStatus = useCallback(async () => {
    try {
      const response = await api.getSAMEmbeddingStatus(imageId);
      setEmbeddingStatus(response.status);
    } catch (error) {
      console.error("[useSegmentation] Failed to get embedding status:", error);
      // Set error state so UI can show feedback
      setEmbeddingStatus("error");
      setState(prev => ({
        ...prev,
        error: error instanceof Error ? error.message : "Failed to check embedding status",
      }));
    }
  }, [imageId]);

  const computeEmbedding = useCallback(async () => {
    try {
      setEmbeddingStatus("pending");
      await api.computeSAMEmbedding(imageId);
    } catch (error) {
      console.error("[useSegmentation] Failed to trigger embedding computation:", error);
      // Set error state instead of re-throwing
      setEmbeddingStatus("error");
      setState(prev => ({
        ...prev,
        error: error instanceof Error ? error.message : "Failed to start embedding computation",
      }));
    }
  }, [imageId]);

  const runInference = useCallback(
    async (points: SegmentClickPoint[]) => {
      if (points.length === 0) {
        setState((prev) => ({
          ...prev,
          previewPolygon: null,
          previewIoU: null,
          isLoading: false,
        }));
        return;
      }

      // Cancel previous request
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      abortControllerRef.current = new AbortController();

      setState((prev) => ({ ...prev, isLoading: true, error: null }));

      try {
        const result = await api.segmentInteractive(imageId, points);

        if (result.success && result.polygon) {
          setState((prev) => ({
            ...prev,
            previewPolygon: result.polygon!,
            previewIoU: result.iou_score ?? null,
            isLoading: false,
            error: null,
          }));
        } else {
          setState((prev) => ({
            ...prev,
            isLoading: false,
            error: result.error || "Segmentation failed",
          }));
        }
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") {
          return; // Request was cancelled
        }
        setState((prev) => ({
          ...prev,
          isLoading: false,
          error: error instanceof Error ? error.message : "Unknown error",
        }));
      }
    },
    [imageId]
  );

  const addClickPoint = useCallback(
    (x: number, y: number, label: 0 | 1) => {
      const newPoint: SegmentClickPoint = { x, y, label };
      const newPoints = [...state.clickPoints, newPoint];

      setState((prev) => ({
        ...prev,
        clickPoints: newPoints,
        isLoading: true,
      }));

      // Debounce API call
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }

      debounceTimerRef.current = setTimeout(() => {
        runInference(newPoints);
      }, debounceMs);
    },
    [state.clickPoints, debounceMs, runInference]
  );

  const undoLastClick = useCallback(() => {
    if (state.clickPoints.length === 0) return;

    const newPoints = state.clickPoints.slice(0, -1);
    setState((prev) => ({
      ...prev,
      clickPoints: newPoints,
    }));

    // Clear debounce timer
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }

    // Run inference with remaining points
    if (newPoints.length === 0) {
      setState((prev) => ({
        ...prev,
        previewPolygon: null,
        previewIoU: null,
        isLoading: false,
      }));
    } else {
      runInference(newPoints);
    }
  }, [state.clickPoints, runInference]);

  const clearSegmentation = useCallback(() => {
    // Clear debounce timer
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }

    // Cancel any pending request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    setState(INITIAL_SEGMENTATION_STATE);
  }, []);

  const saveMask = useCallback(
    async (cropId: number): Promise<{ success: boolean; error?: string }> => {
      if (!state.previewPolygon || state.previewIoU === null) {
        return { success: false, error: "No mask to save" };
      }

      try {
        const result = await api.saveSegmentationMask({
          crop_id: cropId,
          polygon: state.previewPolygon,
          iou_score: state.previewIoU,
          prompt_count: state.clickPoints.length,
        });

        if (result.success) {
          onMaskSaved?.(cropId, state.previewPolygon, state.previewIoU);
          clearSegmentation();
          return { success: true };
        }

        return { success: false, error: "Failed to save mask" };
      } catch (error) {
        return {
          success: false,
          error: error instanceof Error ? error.message : "Save failed",
        };
      }
    },
    [state, onMaskSaved, clearSegmentation]
  );

  const setTargetCropId = useCallback((id: number | null) => {
    setState((prev) => ({ ...prev, targetCropId: id }));
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  return {
    state,
    embeddingStatus,
    isReady: embeddingStatus === "ready",
    addClickPoint,
    undoLastClick,
    clearSegmentation,
    saveMask,
    setTargetCropId,
    computeEmbedding,
    refreshStatus,
  };
}
