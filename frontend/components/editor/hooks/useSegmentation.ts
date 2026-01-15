"use client";

/**
 * useSegmentation Hook
 *
 * Manages interactive SAM segmentation state and API calls.
 * Handles click points, API inference with debouncing, and mask saving.
 *
 * SAM 3 Text Prompting:
 * - When CUDA GPU is available, SAM 3 provides text-based segmentation
 * - Users can type descriptions like "cell" or "nucleus" to find objects
 * - Text queries return all instances directly rendered on canvas
 *
 * Accumulated Polygons:
 * - Both point and text segmentation add to pendingPolygons
 * - User saves all at once via toolbar
 * - Saving merges (union) with existing FOV mask
 */

import { useState, useCallback, useRef, useEffect } from "react";
import { api, type SegmentClickPoint, type SAMEmbeddingStatus, type SegmentationCapabilitiesResponse, type TextSegmentInstance } from "@/lib/api";
import type { SegmentationState, TextSegmentationState, DetectedInstance, SegmentPromptMode, SegmentationCapabilities, PendingPolygon } from "@/lib/editor/types";
import { INITIAL_SEGMENTATION_STATE, INITIAL_TEXT_SEGMENTATION_STATE } from "@/lib/editor/types";

interface UseSegmentationOptions {
  /** Image ID for segmentation */
  imageId: number;
  /** Callback when FOV masks are saved successfully (multiple polygons) */
  onFOVMaskSaved?: (imageId: number, polygons: [number, number][][], iouScore: number) => void;
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
  /** Save all pending polygons to the FOV image (union with existing) */
  saveFOVMask: () => Promise<{ success: boolean; error?: string }>;
  /** Trigger embedding computation */
  computeEmbedding: () => Promise<void>;
  /** Refresh embedding status */
  refreshStatus: () => Promise<void>;
  /** Add current preview polygon to pending list */
  addPreviewToPending: () => void;
  /** Remove a pending polygon by ID */
  removePendingPolygon: (id: string) => void;
  /** Clear all pending polygons */
  clearPendingPolygons: () => void;
  /** Whether there are pending polygons to save */
  hasPendingPolygons: boolean;

  // SAM 3 Text Prompting
  /** Segmentation capabilities (device, text support) */
  capabilities: SegmentationCapabilities | null;
  /** Whether text prompting is available (SAM 3 on CUDA) */
  supportsTextPrompts: boolean;
  /** Current prompt mode */
  promptMode: SegmentPromptMode;
  /** Set prompt mode */
  setPromptMode: (mode: SegmentPromptMode) => void;
  /** Text segmentation state */
  textState: TextSegmentationState;
  /** Set text prompt value */
  setTextPrompt: (prompt: string) => void;
  /** Run text-based segmentation query - adds all instances to pending */
  queryTextSegmentation: (confidenceThreshold?: number) => Promise<void>;
  /** Select a detected instance (for highlighting) */
  selectInstance: (index: number | null) => void;
  /** Clear text segmentation results */
  clearTextSegmentation: () => void;
  /** Save a specific instance from text segmentation to FOV */
  saveTextInstanceToFOV: (instanceIndex: number) => Promise<{ success: boolean; error?: string }>;
}

export function useSegmentation({
  imageId,
  onFOVMaskSaved,
  debounceMs = 50,
}: UseSegmentationOptions): UseSegmentationReturn {
  const [state, setState] = useState<SegmentationState>(INITIAL_SEGMENTATION_STATE);
  const [embeddingStatus, setEmbeddingStatus] = useState<SAMEmbeddingStatus>("not_started");

  // SAM 3 Text Prompting State
  const [capabilities, setCapabilities] = useState<SegmentationCapabilities | null>(null);
  const [promptMode, setPromptMode] = useState<SegmentPromptMode>("point");
  const [textState, setTextState] = useState<TextSegmentationState>(INITIAL_TEXT_SEGMENTATION_STATE);

  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const pendingIdCounterRef = useRef(0);

  // Generate unique ID for pending polygons
  const generatePendingId = useCallback(() => {
    pendingIdCounterRef.current += 1;
    return `pending-${Date.now()}-${pendingIdCounterRef.current}`;
  }, []);

  // Fetch capabilities on mount
  useEffect(() => {
    const fetchCapabilities = async () => {
      try {
        const caps = await api.getSegmentationCapabilities();
        setCapabilities({
          device: caps.device,
          variant: caps.variant,
          supportsTextPrompts: caps.supports_text_prompts,
          modelName: caps.model_name,
        });
      } catch (error) {
        console.error("[useSegmentation] Failed to get capabilities:", error);
      }
    };
    fetchCapabilities();
  }, []);

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

  // Add current preview polygon to pending list
  const addPreviewToPending = useCallback(() => {
    if (!state.previewPolygon || state.previewIoU === null) return;

    const newPending: PendingPolygon = {
      id: generatePendingId(),
      points: state.previewPolygon,
      score: state.previewIoU,
      source: "point",
      colorIndex: state.pendingPolygons.length,
    };

    setState((prev) => ({
      ...prev,
      pendingPolygons: [...prev.pendingPolygons, newPending],
      // Clear preview and click points for next segmentation
      clickPoints: [],
      previewPolygon: null,
      previewIoU: null,
    }));
  }, [state.previewPolygon, state.previewIoU, state.pendingPolygons.length, generatePendingId]);

  // Remove a pending polygon by ID
  const removePendingPolygon = useCallback((id: string) => {
    setState((prev) => ({
      ...prev,
      pendingPolygons: prev.pendingPolygons.filter((p) => p.id !== id),
    }));
  }, []);

  // Clear all pending polygons
  const clearPendingPolygons = useCallback(() => {
    setState((prev) => ({
      ...prev,
      pendingPolygons: [],
    }));
  }, []);

  const saveFOVMask = useCallback(
    async (): Promise<{ success: boolean; error?: string }> => {
      // Collect all polygons to save: pending + current preview (if any)
      const polygonsToSave: [number, number][][] = [
        ...state.pendingPolygons.map((p) => p.points),
      ];

      // Add current preview if exists
      if (state.previewPolygon && state.previewPolygon.length >= 3) {
        polygonsToSave.push(state.previewPolygon);
      }

      if (polygonsToSave.length === 0) {
        return { success: false, error: "No mask to save" };
      }

      try {
        // Save with union flag to merge with existing mask
        const result = await api.saveFOVSegmentationMaskWithUnion({
          image_id: imageId,
          polygons: polygonsToSave,
          iou_score: state.previewIoU ?? 0.9,
          prompt_count: state.clickPoints.length + state.pendingPolygons.length,
        });

        if (result.success) {
          // Notify parent with all saved polygons (returned from backend)
          const savedPolygons = result.polygons || [polygonsToSave[0]];
          onFOVMaskSaved?.(imageId, savedPolygons, state.previewIoU ?? 0.9);
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
    [state, imageId, onFOVMaskSaved, clearSegmentation]
  );

  // ============================================================================
  // SAM 3 Text Prompting Methods
  // ============================================================================

  const setTextPrompt = useCallback((prompt: string) => {
    setTextState((prev) => ({ ...prev, textPrompt: prompt }));
  }, []);

  const queryTextSegmentation = useCallback(
    async (confidenceThreshold: number = 0.5) => {
      const prompt = textState.textPrompt.trim();
      if (!prompt) {
        setTextState((prev) => ({
          ...prev,
          error: "Please enter a search term",
        }));
        return;
      }

      setTextState((prev) => ({
        ...prev,
        isQuerying: true,
        error: null,
        detectedInstances: [],
        selectedInstanceIndex: null,
      }));

      try {
        const result = await api.segmentWithText(imageId, prompt, confidenceThreshold);

        if (result.success && result.instances && result.instances.length > 0) {
          // Convert API response to DetectedInstance format
          const instances: DetectedInstance[] = result.instances.map((inst) => ({
            index: inst.index,
            polygon: inst.polygon,
            bbox: inst.bbox,
            score: inst.score,
            areaPixels: inst.area_pixels,
          }));

          // Add all instances directly to pending polygons
          const currentPendingCount = state.pendingPolygons.length;
          const newPendingPolygons: PendingPolygon[] = instances.map((inst, idx) => ({
            id: generatePendingId(),
            points: inst.polygon,
            score: inst.score,
            source: "text" as const,
            colorIndex: currentPendingCount + idx,
          }));

          // Update both textState and main state
          setTextState((prev) => ({
            ...prev,
            isQuerying: false,
            detectedInstances: instances, // Keep for reference/highlighting
            error: null,
          }));

          setState((prev) => ({
            ...prev,
            pendingPolygons: [...prev.pendingPolygons, ...newPendingPolygons],
          }));
        } else {
          setTextState((prev) => ({
            ...prev,
            isQuerying: false,
            error: result.error || "No instances found",
          }));
        }
      } catch (error) {
        setTextState((prev) => ({
          ...prev,
          isQuerying: false,
          error: error instanceof Error ? error.message : "Query failed",
        }));
      }
    },
    [imageId, textState.textPrompt, state.pendingPolygons.length, generatePendingId]
  );

  const selectInstance = useCallback((index: number | null) => {
    setTextState((prev) => ({ ...prev, selectedInstanceIndex: index }));

    // When selecting an instance, show its polygon as preview
    if (index !== null && textState.detectedInstances[index]) {
      const instance = textState.detectedInstances[index];
      setState((prev) => ({
        ...prev,
        previewPolygon: instance.polygon,
        previewIoU: instance.score,
      }));
    } else {
      // Clear preview when deselecting
      setState((prev) => ({
        ...prev,
        previewPolygon: null,
        previewIoU: null,
      }));
    }
  }, [textState.detectedInstances]);

  const clearTextSegmentation = useCallback(() => {
    setTextState(INITIAL_TEXT_SEGMENTATION_STATE);
    // Also clear the preview
    setState((prev) => ({
      ...prev,
      previewPolygon: null,
      previewIoU: null,
    }));
  }, []);

  const saveTextInstanceToFOV = useCallback(
    async (instanceIndex: number): Promise<{ success: boolean; error?: string }> => {
      const instance = textState.detectedInstances[instanceIndex];
      if (!instance) {
        return { success: false, error: "Instance not found" };
      }

      try {
        const result = await api.saveFOVSegmentationMask({
          image_id: imageId,
          polygon: instance.polygon,
          iou_score: instance.score,
          prompt_count: 1, // Text prompt counts as 1
        });

        if (result.success) {
          // Wrap single polygon in array for multi-polygon callback signature
          onFOVMaskSaved?.(imageId, [instance.polygon], instance.score);
          clearTextSegmentation();
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
    [textState.detectedInstances, imageId, onFOVMaskSaved, clearTextSegmentation]
  );

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
    saveFOVMask,
    computeEmbedding,
    refreshStatus,
    // Pending polygon management
    addPreviewToPending,
    removePendingPolygon,
    clearPendingPolygons,
    hasPendingPolygons: state.pendingPolygons.length > 0,

    // SAM 3 Text Prompting
    capabilities,
    supportsTextPrompts: capabilities?.supportsTextPrompts ?? false,
    promptMode,
    setPromptMode,
    textState,
    setTextPrompt,
    queryTextSegmentation,
    selectInstance,
    clearTextSegmentation,
    saveTextInstanceToFOV,
  };
}
