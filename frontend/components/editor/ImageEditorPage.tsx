"use client";

/**
 * ImageEditorPage Component
 *
 * Full-page image editor for bbox manipulation.
 * Provides canvas, toolbar, crop preview panel, and navigation sidebar.
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronRight, ChevronLeft, ArrowLeft, AlertCircle, X } from "lucide-react";
import { useSettingsStore } from "@/stores/settingsStore";
import { api, type CellCropGallery, type FOVImage } from "@/lib/api";
import { AppSidebar } from "@/components/layout";
import type {
  EditorBbox,
  EditorState,
  ImageFilters,
  ContextMenuState,
  Rect,
  CellPolygon,
} from "@/lib/editor/types";
import {
  cropToEditorBbox,
  generateTempId,
} from "@/lib/editor/types";
import {
  DEFAULT_FILTERS,
  MIN_ZOOM,
  MAX_ZOOM,
} from "@/lib/editor/constants";
import {
  calculateFitScale,
  calculateCenterOffset,
} from "@/lib/editor/geometry";

import { ImageEditorCanvas } from "./ImageEditorCanvas";
import { ImageEditorToolbar, type ToolbarPosition } from "./ImageEditorToolbar";
import { ImageEditorCropPreview } from "./ImageEditorCropPreview";
import { ImageEditorContextMenu } from "./ImageEditorContextMenu";
import { SegmentationOverlay } from "./SegmentationOverlay";
import { useBboxInteraction } from "./hooks/useBboxInteraction";
import { useUndoHistory } from "./hooks/useUndoHistory";
import { useSegmentation } from "./hooks/useSegmentation";

// localStorage key for persisting toolbar position
const TOOLBAR_POSITION_KEY = "maptimize:editor:toolbarPosition";
const DEFAULT_TOOLBAR_POSITION: ToolbarPosition = { edge: "bottom", offset: 0.5 };

interface ImageEditorPageProps {
  fovImage: FOVImage;
  crops: CellCropGallery[];
  experimentId: number;
  focusCropId?: number;
  onClose: () => void;
  onDataChanged?: () => void;
  // Image navigation props
  currentImageIndex?: number;
  totalImages?: number;
  hasPrevImage?: boolean;
  hasNextImage?: boolean;
  onNavigatePrev?: () => void;
  onNavigateNext?: () => void;
}

export function ImageEditorPage({
  fovImage,
  crops,
  experimentId,
  focusCropId,
  onClose,
  onDataChanged,
  currentImageIndex = 0,
  totalImages = 1,
  hasPrevImage = false,
  hasNextImage = false,
  onNavigatePrev,
  onNavigateNext,
}: ImageEditorPageProps) {
  const t = useTranslations("editor");
  const displayMode = useSettingsStore((state) => state.displayMode);
  const containerRef = useRef<HTMLDivElement>(null);
  const imageCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const sourceImageRef = useRef<HTMLImageElement | null>(null);

  // Navigation sidebar state
  const [showNavigation, setShowNavigation] = useState(false);

  // Convert crops to editor bboxes
  const [bboxes, setBboxes] = useState<EditorBbox[]>(() =>
    crops.map(cropToEditorBbox)
  );

  // Sync bboxes when crops change (e.g., from React Query refetch)
  useEffect(() => {
    console.log("[Editor] Crops received:", crops.length, crops);
    if (crops.length > 0) {
      setBboxes(crops.map(cropToEditorBbox));
    }
  }, [crops]);

  // Editor state
  const [editorState, setEditorState] = useState<EditorState>(() => ({
    mode: "view",
    selectedBboxId: focusCropId ?? null,
    hoveredBboxId: null,
    activeHandle: null,
    isDragging: false,
    dragStart: null,
    isSpacePressed: false,
    zoom: 1,
    panOffset: { x: 0, y: 0 },
  }));

  // Image filters
  const [filters, setFilters] = useState<ImageFilters>(DEFAULT_FILTERS);

  // Context menu state
  const [contextMenu, setContextMenu] = useState<ContextMenuState>({
    isOpen: false,
    position: null,
    targetBbox: null,
  });

  // Error state for user feedback
  const [error, setError] = useState<string | null>(null);

  // Saved polygons for all crops
  const [savedPolygons, setSavedPolygons] = useState<CellPolygon[]>([]);

  // Container dimensions for segmentation overlay
  const [containerDimensions, setContainerDimensions] = useState({ width: 0, height: 0 });

  // Show error toast
  const showError = useCallback((message: string) => {
    setError(message);
    // Auto-dismiss after 5 seconds
    setTimeout(() => setError(null), 5000);
  }, []);

  // Segmentation hook
  const segmentation = useSegmentation({
    imageId: fovImage.id,
    onMaskSaved: useCallback((cropId: number, polygon: [number, number][], iouScore: number) => {
      // Add to saved polygons
      setSavedPolygons(prev => {
        const existing = prev.find(p => p.cropId === cropId);
        if (existing) {
          return prev.map(p => p.cropId === cropId ? { cropId, points: polygon, iouScore } : p);
        }
        return [...prev, { cropId, points: polygon, iouScore }];
      });
      onDataChanged?.();
    }, [onDataChanged]),
  });

  // Load saved polygons when crops change
  useEffect(() => {
    const loadPolygons = async () => {
      const cropIds = crops.map(c => c.id);
      if (cropIds.length === 0) return;

      try {
        const result = await api.getSegmentationMasksBatch(cropIds);
        if (result.masks) {
          setSavedPolygons(
            result.masks.map(m => ({
              cropId: m.crop_id,
              points: m.polygon as [number, number][],
              iouScore: m.iou_score,
            }))
          );
        }
      } catch (err) {
        console.error("[Editor] Failed to load segmentation masks:", err);
      }
    };

    loadPolygons();
  }, [crops]);

  // Update container dimensions for overlay sizing
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const updateDimensions = () => {
      const rect = container.getBoundingClientRect();
      setContainerDimensions({ width: rect.width, height: rect.height });
    };

    updateDimensions();
    window.addEventListener("resize", updateDimensions);
    return () => window.removeEventListener("resize", updateDimensions);
  }, []);

  // Toolbar position state - persisted in localStorage
  const [toolbarPosition, setToolbarPosition] = useState<ToolbarPosition>(() => {
    if (typeof window === "undefined") return DEFAULT_TOOLBAR_POSITION;
    try {
      const stored = localStorage.getItem(TOOLBAR_POSITION_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (parsed.edge && typeof parsed.offset === "number") {
          return parsed as ToolbarPosition;
        }
      }
    } catch (e) {
      console.warn("[ImageEditor] Failed to load toolbar position from localStorage:", e);
    }
    return DEFAULT_TOOLBAR_POSITION;
  });

  // Persist toolbar position to localStorage
  const handleToolbarPositionChange = useCallback((newPosition: ToolbarPosition) => {
    setToolbarPosition(newPosition);
    try {
      localStorage.setItem(TOOLBAR_POSITION_KEY, JSON.stringify(newPosition));
    } catch (e) {
      console.warn("[ImageEditor] Failed to save toolbar position to localStorage:", e);
    }
  }, []);

  // API handlers
  const handleBboxCreate = useCallback(
    async (bbox: Omit<EditorBbox, "id">) => {
      const result = await api.createManualCrop(fovImage.id, {
        x: bbox.x,
        y: bbox.y,
        width: bbox.width,
        height: bbox.height,
      });
      return result.id;
    },
    [fovImage.id]
  );

  const handleBboxUpdate = useCallback(
    async (id: number, bbox: Partial<EditorBbox>) => {
      await api.updateCropBbox(id, {
        x: bbox.x ?? 0,
        y: bbox.y ?? 0,
        width: bbox.width ?? 0,
        height: bbox.height ?? 0,
      });
    },
    []
  );

  const handleBboxDelete = useCallback(async (id: number) => {
    await api.deleteCellCrop(id);
  }, []);

  const handleRegenerateFeatures = useCallback(async (cropId: number) => {
    await api.regenerateCropFeatures(cropId);
  }, []);

  // Undo history
  const undoHistory = useUndoHistory({
    onBboxCreate: async (bbox) => {
      const id = await handleBboxCreate(bbox);
      // Update local state
      setBboxes((prev) => [
        ...prev,
        {
          id,
          cropId: id,
          x: bbox.x ?? 0,
          y: bbox.y ?? 0,
          width: bbox.width ?? 0,
          height: bbox.height ?? 0,
          isNew: false,
          isModified: false,
        },
      ]);
      onDataChanged?.();
      return id;
    },
    onBboxUpdate: async (id, bbox) => {
      await handleBboxUpdate(id, bbox);
      await handleRegenerateFeatures(id);
      // Update local state to match undo position
      setBboxes((prev) =>
        prev.map((b) =>
          b.cropId === id
            ? {
                ...b,
                x: bbox.x ?? b.x,
                y: bbox.y ?? b.y,
                width: bbox.width ?? b.width,
                height: bbox.height ?? b.height,
                isModified: false,
              }
            : b
        )
      );
      onDataChanged?.();
    },
    onBboxDelete: async (id) => {
      await handleBboxDelete(id);
      // Remove from local state
      setBboxes((prev) => prev.filter((b) => b.cropId !== id));
      onDataChanged?.();
    },
    onError: (message) => {
      showError(t("undoError"));
    },
  });

  // Initialize view to fit image
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !fovImage.width || !fovImage.height) return;

    const rect = container.getBoundingClientRect();
    const scale = calculateFitScale(
      fovImage.width,
      fovImage.height,
      rect.width,
      rect.height
    );
    const offset = calculateCenterOffset(
      fovImage.width,
      fovImage.height,
      rect.width,
      rect.height,
      scale
    );

    setEditorState((prev) => ({
      ...prev,
      zoom: scale,
      panOffset: offset,
    }));
  }, [fovImage.width, fovImage.height]);

  // Handle bbox change during drag/resize (local state only, no API call)
  const handleBboxChange = useCallback(
    (id: string | number, changes: Partial<EditorBbox>) => {
      setBboxes((prev) =>
        prev.map((bbox) =>
          bbox.id === id ? { ...bbox, ...changes } : bbox
        )
      );
    },
    []
  );

  // Handle bbox change completion (API save + undo stack)
  const handleBboxChangeComplete = useCallback(
    async (id: string | number, originalBbox: Rect, finalBbox: Rect) => {
      const bbox = bboxes.find((b) => b.id === id);
      if (!bbox?.cropId) return;

      // Store previous state for undo
      const previousState: EditorBbox = {
        ...bbox,
        x: originalBbox.x,
        y: originalBbox.y,
        width: originalBbox.width,
        height: originalBbox.height,
      };

      // Call API to update
      try {
        await handleBboxUpdate(bbox.cropId, finalBbox);

        // Queue feature regeneration
        await handleRegenerateFeatures(bbox.cropId);

        // Push to undo stack (single action for entire drag/resize)
        undoHistory.pushAction({
          type: "update",
          bboxId: id,
          previousState,
          newState: { ...bbox, ...finalBbox, isModified: true },
        });

        onDataChanged?.();
      } catch (error) {
        console.error("Failed to update bbox:", error);
        // Revert local state on error
        setBboxes((prev) =>
          prev.map((b) => (b.id === id ? previousState : b))
        );
        showError(t("updateError"));
      }
    },
    [bboxes, handleBboxUpdate, handleRegenerateFeatures, onDataChanged, undoHistory, showError, t]
  );

  // Handle bbox create
  const handleBboxCreateLocal = useCallback(
    async (rect: Rect) => {
      const tempId = generateTempId();
      const newBbox: EditorBbox = {
        id: tempId,
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        isNew: true,
        isModified: false,
      };

      // Add to local state immediately
      setBboxes((prev) => [...prev, newBbox]);
      setEditorState((prev) => ({
        ...prev,
        selectedBboxId: tempId,
        mode: "view",
      }));

      // Create via API
      try {
        const cropId = await handleBboxCreate(newBbox);

        // Update with real ID
        setBboxes((prev) =>
          prev.map((bbox) =>
            bbox.id === tempId
              ? { ...bbox, id: cropId, cropId, isNew: false }
              : bbox
          )
        );

        setEditorState((prev) => ({
          ...prev,
          selectedBboxId: cropId,
        }));

        // Push to undo stack
        undoHistory.pushAction({
          type: "create",
          bboxId: cropId,
          newState: { ...newBbox, id: cropId, cropId },
        });

        onDataChanged?.();
      } catch (error) {
        console.error("Failed to create bbox:", error);
        // Remove from local state on error
        setBboxes((prev) => prev.filter((b) => b.id !== tempId));
        showError(t("createError"));
      }
    },
    [handleBboxCreate, onDataChanged, undoHistory, showError, t]
  );

  // Handle bbox select
  const handleBboxSelect = useCallback((id: string | number | null) => {
    setEditorState((prev) => ({
      ...prev,
      selectedBboxId: id,
    }));
  }, []);

  // Handle bbox hover (from preview panel)
  const handleBboxHover = useCallback((id: string | number | null) => {
    setEditorState((prev) => ({
      ...prev,
      hoveredBboxId: id,
    }));
  }, []);

  // Handle bbox delete
  const handleBboxDeleteLocal = useCallback(
    async (bbox: EditorBbox) => {
      // Store for undo
      const previousState = { ...bbox };

      // Remove from local state
      setBboxes((prev) => prev.filter((b) => b.id !== bbox.id));
      setEditorState((prev) => ({
        ...prev,
        selectedBboxId: null,
      }));

      // Delete via API
      if (bbox.cropId) {
        try {
          await handleBboxDelete(bbox.cropId);

          // Push to undo stack
          undoHistory.pushAction({
            type: "delete",
            bboxId: bbox.id,
            previousState,
          });

          onDataChanged?.();
        } catch (error) {
          console.error("Failed to delete bbox:", error);
          // Restore on error
          setBboxes((prev) => [...prev, previousState]);
          showError(t("deleteError"));
        }
      }
    },
    [handleBboxDelete, onDataChanged, undoHistory, showError, t]
  );

  // Handle bbox reset (restore original coordinates)
  const handleBboxReset = useCallback(
    async (bbox: EditorBbox) => {
      if (!bbox.original) return;

      const resetBbox: EditorBbox = {
        ...bbox,
        x: bbox.original.x,
        y: bbox.original.y,
        width: bbox.original.width,
        height: bbox.original.height,
        isModified: false,
      };

      await handleBboxChange(bbox.id, resetBbox);
    },
    [handleBboxChange]
  );

  // Handle context menu
  const handleContextMenu = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      e.preventDefault();

      const bbox = bboxes.find((b) => b.id === editorState.selectedBboxId);
      if (bbox) {
        setContextMenu({
          isOpen: true,
          position: { x: e.clientX, y: e.clientY },
          targetBbox: bbox,
        });
      }
    },
    [bboxes, editorState.selectedBboxId]
  );

  // Reset view
  const handleResetView = useCallback(() => {
    const container = containerRef.current;
    if (!container || !fovImage.width || !fovImage.height) return;

    const rect = container.getBoundingClientRect();
    const scale = calculateFitScale(
      fovImage.width,
      fovImage.height,
      rect.width,
      rect.height
    );
    const offset = calculateCenterOffset(
      fovImage.width,
      fovImage.height,
      rect.width,
      rect.height,
      scale
    );

    setEditorState((prev) => ({
      ...prev,
      zoom: scale,
      panOffset: offset,
    }));
  }, [fovImage.width, fovImage.height]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Delete = delete selected bbox
      if ((e.key === "Delete" || e.key === "Backspace") && editorState.selectedBboxId) {
        const bbox = bboxes.find((b) => b.id === editorState.selectedBboxId);
        if (bbox) {
          handleBboxDeleteLocal(bbox);
        }
        return;
      }

      // Ctrl+Z = undo
      if ((e.ctrlKey || e.metaKey) && e.key === "z") {
        e.preventDefault();
        undoHistory.undo();
        return;
      }

      // N = toggle draw mode
      if (e.key === "n" || e.key === "N") {
        setEditorState((prev) => ({
          ...prev,
          mode: prev.mode === "draw" ? "view" : "draw",
        }));
        return;
      }

      // S = toggle segment mode
      if (e.key === "s" || e.key === "S") {
        if (segmentation.isReady || segmentation.embeddingStatus === "not_started") {
          setEditorState((prev) => ({
            ...prev,
            mode: prev.mode === "segment" ? "view" : "segment",
          }));
        }
        return;
      }

      // Escape = clear segmentation points (when in segment mode)
      if (e.key === "Escape" && editorState.mode === "segment") {
        segmentation.clearSegmentation();
        return;
      }

      // Space = enable panning
      if (e.key === " " && !editorState.isSpacePressed) {
        e.preventDefault();
        setEditorState((prev) => ({
          ...prev,
          isSpacePressed: true,
        }));
      }
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      if (e.key === " ") {
        setEditorState((prev) => ({
          ...prev,
          isSpacePressed: false,
        }));
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
  }, [bboxes, editorState.selectedBboxId, editorState.isSpacePressed, editorState.mode, handleBboxDeleteLocal, undoHistory, segmentation]);

  // Bbox interaction hook
  const {
    handleMouseDown,
    handleMouseMove,
    handleMouseUp,
    handleMouseLeave,
    handleWheel,
    cursor,
    drawingBbox,
    modifyingBboxId,
    liveBboxRect,
  } = useBboxInteraction({
    bboxes,
    editorState,
    setEditorState,
    canvasRef: containerRef,
    imageWidth: fovImage.width || 0,
    imageHeight: fovImage.height || 0,
    onBboxChange: handleBboxChange,
    onBboxChangeComplete: handleBboxChangeComplete,
    onBboxCreate: handleBboxCreateLocal,
    onBboxSelect: handleBboxSelect,
  });

  // Handle segmentation clicks (convert canvas coordinates to image coordinates)
  const handleSegmentationClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement | HTMLDivElement>) => {
      if (editorState.mode !== "segment" || !segmentation.isReady) return;

      const container = containerRef.current;
      if (!container) return;

      const rect = container.getBoundingClientRect();
      const canvasX = e.clientX - rect.left;
      const canvasY = e.clientY - rect.top;

      // Convert canvas coords to image coords
      const imageX = (canvasX - editorState.panOffset.x) / editorState.zoom;
      const imageY = (canvasY - editorState.panOffset.y) / editorState.zoom;

      // Check bounds
      if (imageX < 0 || imageY < 0 || imageX > (fovImage.width || 0) || imageY > (fovImage.height || 0)) {
        return;
      }

      // Left click = foreground (1), Right click = background (0)
      const label: 0 | 1 = e.button === 2 ? 0 : 1;
      segmentation.addClickPoint(imageX, imageY, label);
    },
    [editorState.mode, editorState.zoom, editorState.panOffset, segmentation, fovImage.width, fovImage.height]
  );

  // Handle save mask (requires selected crop)
  const handleSaveMask = useCallback(async () => {
    const selectedCropId = typeof editorState.selectedBboxId === "number"
      ? editorState.selectedBboxId
      : null;

    if (!selectedCropId) {
      showError(t("noTargetCrop"));
      return;
    }

    const result = await segmentation.saveMask(selectedCropId);
    if (!result.success && result.error) {
      showError(result.error);
    }
  }, [editorState.selectedBboxId, segmentation, showError, t]);

  // Wrap mouse down handler to support segmentation mode
  const handleMouseDownWithSegmentation = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      // In segment mode, handle segmentation clicks
      if (editorState.mode === "segment" && segmentation.isReady) {
        // Prevent context menu on right click
        if (e.button === 2) {
          e.preventDefault();
        }
        handleSegmentationClick(e);
        return;
      }
      // Otherwise, delegate to bbox interaction
      handleMouseDown(e);
    },
    [editorState.mode, segmentation.isReady, handleSegmentationClick, handleMouseDown]
  );

  // Wrap context menu handler to allow right-click in segment mode
  const handleContextMenuWithSegmentation = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      // In segment mode, right-click adds background point (handled by mouse down)
      if (editorState.mode === "segment" && segmentation.isReady) {
        e.preventDefault();
        return;
      }
      handleContextMenu(e);
    },
    [editorState.mode, segmentation.isReady, handleContextMenu]
  );

  // Handle wheel events
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const wheelHandler = (e: WheelEvent) => {
      e.preventDefault();
      handleWheel(e);
    };

    container.addEventListener("wheel", wheelHandler, { passive: false });
    return () => container.removeEventListener("wheel", wheelHandler);
  }, [handleWheel]);

  // Get image URL
  const imageUrl = api.getImageUrl(fovImage.id, "mip");

  return (
    <div className="h-screen bg-bg-primary flex overflow-hidden">
      {/* Full-screen canvas with crop panel */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Back button - top left, moves with sidebar */}
        <button
          onClick={onClose}
          className={`absolute top-4 z-50 bg-bg-secondary/80 backdrop-blur-sm p-2.5 rounded-xl border border-white/10 hover:bg-white/10 hover:border-white/20 transition-all duration-300 group ${
            showNavigation ? "left-[17rem]" : "left-4"
          }`}
          title={t("back")}
        >
          <ArrowLeft className="w-5 h-5 text-text-secondary group-hover:text-text-primary transition-colors" />
        </button>

        {/* Image navigation - top right */}
        {totalImages > 1 && (
          <div className="absolute top-4 right-[17rem] z-50 flex items-center gap-2">
            {/* Previous button */}
            <button
              onClick={onNavigatePrev}
              disabled={!hasPrevImage}
              className={`bg-bg-secondary/80 backdrop-blur-sm p-2 rounded-lg border border-white/10 transition-all duration-200 ${
                hasPrevImage
                  ? "hover:bg-white/10 hover:border-white/20 cursor-pointer"
                  : "opacity-40 cursor-not-allowed"
              }`}
              title={t("previousImage")}
            >
              <ChevronLeft className="w-4 h-4 text-text-secondary" />
            </button>

            {/* Image info */}
            <div className="bg-bg-secondary/80 backdrop-blur-sm px-3 py-1.5 rounded-lg border border-white/10">
              <div className="text-xs text-text-secondary text-center">
                {currentImageIndex + 1} / {totalImages}
              </div>
              <div className="text-sm text-text-primary truncate max-w-[200px]" title={fovImage.original_filename}>
                {fovImage.original_filename}
              </div>
            </div>

            {/* Next button */}
            <button
              onClick={onNavigateNext}
              disabled={!hasNextImage}
              className={`bg-bg-secondary/80 backdrop-blur-sm p-2 rounded-lg border border-white/10 transition-all duration-200 ${
                hasNextImage
                  ? "hover:bg-white/10 hover:border-white/20 cursor-pointer"
                  : "opacity-40 cursor-not-allowed"
              }`}
              title={t("nextImage")}
            >
              <ChevronRight className="w-4 h-4 text-text-secondary" />
            </button>
          </div>
        )}

        {/* Navigation toggle trigger - moves with sidebar */}
        <button
          onClick={() => setShowNavigation(!showNavigation)}
          className={`absolute top-1/2 -translate-y-1/2 z-50 bg-bg-secondary px-1 py-6 rounded-r-lg border-y border-r border-white/5 hover:bg-white/5 transition-all duration-300 ${
            showNavigation ? "left-64" : "left-0"
          }`}
          title={showNavigation ? "Hide navigation" : "Show navigation"}
        >
          <ChevronRight className={`w-4 h-4 text-text-secondary transition-transform duration-200 ${showNavigation ? "rotate-180" : ""}`} />
        </button>

        {/* Slide-out navigation sidebar - same as dashboard */}
        <AnimatePresence>
          {showNavigation && (
            <AppSidebar
              variant="overlay"
              onClose={() => setShowNavigation(false)}
              activePath={`/dashboard/experiments/${experimentId}`}
            />
          )}
        </AnimatePresence>

        {/* Canvas area - takes full remaining space */}
        <div className="flex-1 relative">
          <ImageEditorCanvas
            imageUrl={imageUrl}
            imageWidth={fovImage.width || 0}
            imageHeight={fovImage.height || 0}
            bboxes={bboxes}
            editorState={editorState}
            filters={filters}
            displayMode={displayMode}
            drawingBbox={drawingBbox}
            onMouseDown={handleMouseDownWithSegmentation}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseLeave}
            onContextMenu={handleContextMenuWithSegmentation}
            cursor={editorState.mode === "segment" ? "crosshair" : cursor}
            containerRef={containerRef}
            onImageCanvasReady={(canvas) => {
              imageCanvasRef.current = canvas;
            }}
            onImageLoaded={(img) => {
              sourceImageRef.current = img;
            }}
          />

          {/* Segmentation overlay - renders click points and polygons */}
          <SegmentationOverlay
            clickPoints={segmentation.state.clickPoints}
            previewPolygon={segmentation.state.previewPolygon}
            savedPolygons={savedPolygons}
            zoom={editorState.zoom}
            panOffset={editorState.panOffset}
            isActive={editorState.mode === "segment"}
            isLoading={segmentation.state.isLoading}
            containerWidth={containerDimensions.width}
            containerHeight={containerDimensions.height}
          />
        </div>

        {/* Crop preview panel */}
        <ImageEditorCropPreview
          bboxes={bboxes}
          selectedBboxId={editorState.selectedBboxId}
          hoveredBboxId={editorState.hoveredBboxId}
          onBboxSelect={handleBboxSelect}
          onBboxHover={handleBboxHover}
          imageUrl={imageUrl}
          sourceImageRef={sourceImageRef}
          modifyingBboxId={modifyingBboxId}
          liveBboxRect={liveBboxRect}
          displayMode={displayMode}
          savedPolygons={savedPolygons}
        />
      </div>

      {/* Toolbar - fixed position for proper centering */}
      <ImageEditorToolbar
        filters={filters}
        onFiltersChange={setFilters}
        displayMode={displayMode}
        onDisplayModeChange={(mode) => useSettingsStore.getState().setDisplayMode(mode)}
        editorMode={editorState.mode}
        onEditorModeChange={(mode) =>
          setEditorState((prev) => ({ ...prev, mode }))
        }
        zoom={editorState.zoom}
        onZoomChange={(zoom) =>
          setEditorState((prev) => ({
            ...prev,
            zoom: Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom)),
          }))
        }
        onResetView={handleResetView}
        canUndo={undoHistory.canUndo}
        onUndo={undoHistory.undo}
        isUndoing={undoHistory.isUndoing}
        position={toolbarPosition}
        onPositionChange={handleToolbarPositionChange}
        sidebarOpen={showNavigation}
        // Segmentation props
        samEmbeddingStatus={segmentation.embeddingStatus}
        onComputeEmbedding={segmentation.computeEmbedding}
        hasClickPoints={segmentation.state.clickPoints.length > 0}
        hasPreviewPolygon={!!segmentation.state.previewPolygon}
        onClearSegmentation={segmentation.clearSegmentation}
        onSaveMask={handleSaveMask}
        onUndoClick={segmentation.undoLastClick}
        isSavingMask={false}
        clickPointCount={segmentation.state.clickPoints.length}
      />

      {/* Context menu */}
      <ImageEditorContextMenu
        isOpen={contextMenu.isOpen}
        position={contextMenu.position}
        targetBbox={contextMenu.targetBbox}
        onDelete={handleBboxDeleteLocal}
        onReset={handleBboxReset}
        onClose={() =>
          setContextMenu({ isOpen: false, position: null, targetBbox: null })
        }
      />

      {/* Error toast */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: 50 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 50 }}
            className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[200] flex items-center gap-3 px-4 py-3 bg-red-500/90 backdrop-blur-sm rounded-xl shadow-xl"
          >
            <AlertCircle className="w-5 h-5 text-white" />
            <span className="text-white text-sm">{error}</span>
            <button
              onClick={() => setError(null)}
              className="p-1 hover:bg-white/20 rounded-lg transition-colors"
            >
              <X className="w-4 h-4 text-white" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
