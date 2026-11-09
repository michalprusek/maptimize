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
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, ChevronLeft, ArrowLeft, AlertCircle, X, Keyboard } from "lucide-react";
import { useSettingsStore } from "@/stores/settingsStore";
import { ConfirmModal } from "@/components/ui";
import { api, type CellCropGallery, type FOVImage } from "@/lib/api";
import { AppSidebar } from "@/components/layout";
import type {
  EditorBbox,
  EditorMode,
  EditorState,
  ImageFilters,
  ContextMenuState,
  Rect,
} from "@/lib/editor/types";
import {
  cropToEditorBbox,
  generateTempId,
  withGeometryFrom,
} from "@/lib/editor/types";
import {
  DEFAULT_FILTERS,
  FILTER_LIMITS,
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
import { useBboxInteraction } from "./hooks/useBboxInteraction";
import { useUndoHistory } from "./hooks/useUndoHistory";
import { KeyboardShortcutsModal } from "./KeyboardShortcutsModal";
import { readPersistedEditorMode, useEditorModePersistence, useLocalStorage } from "@/hooks";

// localStorage keys for persisting editor settings
const TOOLBAR_POSITION_KEY = "maptimize:editor:toolbarPosition";
const FILTERS_KEY = "maptimize:editor:filters";
const DEFAULT_TOOLBAR_POSITION: ToolbarPosition = { edge: "bottom", offset: 0.5 };

/** Type guard for ImageFilters validation (validates brightness & contrast ranges) */
function isValidFilters(value: unknown): value is ImageFilters {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  return (
    typeof obj.brightness === "number" &&
    obj.brightness >= FILTER_LIMITS.min &&
    obj.brightness <= FILTER_LIMITS.max &&
    typeof obj.contrast === "number" &&
    obj.contrast >= FILTER_LIMITS.min &&
    obj.contrast <= FILTER_LIMITS.max
  );
}

/** Type guard for ToolbarPosition validation */
function isValidToolbarPosition(value: unknown): value is ToolbarPosition {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  return (
    typeof obj.edge === "string" &&
    ["top", "bottom", "left", "right"].includes(obj.edge) &&
    typeof obj.offset === "number" &&
    obj.offset >= 0 &&
    obj.offset <= 1
  );
}

/**
 * Returns class names for navigation buttons (prev/next image).
 */
function getNavButtonClassName(isEnabled: boolean): string {
  const base = "bg-bg-secondary/80 backdrop-blur-sm p-2 rounded-lg border border-white/10 transition-all duration-200";
  if (isEnabled) {
    return `${base} hover:bg-white/10 hover:border-white/20 cursor-pointer`;
  }
  return `${base} opacity-40 cursor-not-allowed`;
}

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
  const sourceImageRef = useRef<HTMLImageElement | null>(null);

  // Navigation sidebar state
  const [showNavigation, setShowNavigation] = useState(false);

  // Keyboard shortcuts modal state
  const [showShortcutsModal, setShowShortcutsModal] = useState(false);

  // Convert crops to editor bboxes
  const [bboxes, setBboxes] = useState<EditorBbox[]>(() =>
    crops.map(cropToEditorBbox)
  );

  // Sync bboxes when crops change (e.g., from React Query refetch)
  useEffect(() => {
    if (crops.length > 0) {
      setBboxes(crops.map(cropToEditorBbox));
    }
  }, [crops]);

  // Editor state - mode comes from localStorage so it survives image navigation
  const [editorState, setEditorState] = useState<EditorState>(() => ({
    mode: readPersistedEditorMode(),
    selectedBboxId: focusCropId ?? null,
    hoveredBboxId: null,
    activeHandle: null,
    isDragging: false,
    dragStart: null,
    isSpacePressed: false,
    zoom: 1,
    panOffset: { x: 0, y: 0 },
  }));

  // Persist editor mode across page refreshes (localStorage)
  const setEditorMode = useCallback((mode: EditorMode) => {
    setEditorState(prev => ({ ...prev, mode }));
  }, []);
  useEditorModePersistence(editorState.mode, setEditorMode);

  // Image filters - persisted to localStorage
  const [filters, setFilters] = useLocalStorage<ImageFilters>(
    FILTERS_KEY,
    DEFAULT_FILTERS,
    { validate: isValidFilters }
  );

  // Context menu state
  const [contextMenu, setContextMenu] = useState<ContextMenuState>({
    isOpen: false,
    position: null,
    targetBbox: null,
  });

  // Error state for user feedback
  const [error, setError] = useState<string | null>(null);

  // Re-detect state
  const queryClient = useQueryClient();
  const [showRedetectConfirm, setShowRedetectConfirm] = useState(false);

  const redetectMutation = useMutation({
    mutationFn: () => api.reprocessImage(fovImage.id, true),
    onSuccess: () => {
      setShowRedetectConfirm(false);
      // Invalidate queries to refresh data
      queryClient.invalidateQueries({ queryKey: ["fovs", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["fov-crops", fovImage.id] });
      // Notify parent component to refresh data
      onDataChanged?.();
    },
    onError: (err: Error) => {
      console.error("[Editor] Re-detect failed:", err);
      setError(t("redetectError"));
    },
  });

  // Show error toast
  const showError = useCallback((message: string) => {
    setError(message);
    // Auto-dismiss after 5 seconds
    setTimeout(() => setError(null), 5000);
  }, []);

  // Toolbar position state - persisted in localStorage via useLocalStorage hook
  const [toolbarPosition, setToolbarPosition] = useLocalStorage<ToolbarPosition>(
    TOOLBAR_POSITION_KEY,
    DEFAULT_TOOLBAR_POSITION,
    { validate: isValidToolbarPosition }
  );

  // API handlers
  const handleBboxCreate = useCallback(
    async (bbox: Omit<EditorBbox, "id">) => {
      const result = await api.createManualCrop(fovImage.id, {
        x: bbox.x,
        y: bbox.y,
        width: bbox.width,
        height: bbox.height,
        angle: bbox.angle ?? 0,
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
        angle: bbox.angle ?? 0,
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
          angle: bbox.angle ?? 0,
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
                angle: bbox.angle ?? b.angle,
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
    onError: () => {
      showError(t("undoError"));
    },
  });

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

      // Store previous state for undo (every geometry field from originalBbox --
      // `bbox` already carries the new values by the time the drag completes)
      const previousState = withGeometryFrom(bbox, originalBbox);

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
        // Show what the server actually said. The validator explains which corner
        // left the image and by how much; collapsing that to "Failed to update
        // cell" left the user with no idea that the fix is to move the box inward.
        showError(error instanceof Error && error.message ? error.message : t("updateError"));
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
        angle: rect.angle ?? 0,
        isNew: true,
        isModified: false,
      };

      // Add to local state immediately
      setBboxes((prev) => [...prev, newBbox]);
      setEditorState((prev) => ({
        ...prev,
        selectedBboxId: tempId,
        // Don't reset mode - keep draw mode active for continuous adding
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

      // Geometry (including angle) from `original`; see withGeometryFrom.
      const resetBbox: EditorBbox = {
        ...withGeometryFrom(bbox, bbox.original),
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

      // First try to use hoveredBboxId (bbox under cursor)
      // Fall back to selectedBboxId if no hovered bbox
      const targetId = editorState.hoveredBboxId ?? editorState.selectedBboxId;
      const bbox = bboxes.find((b) => b.id === targetId);

      if (bbox) {
        // Also select the bbox when showing context menu
        setEditorState((prev) => ({
          ...prev,
          selectedBboxId: bbox.id,
        }));
        setContextMenu({
          isOpen: true,
          position: { x: e.clientX, y: e.clientY },
          targetBbox: bbox,
        });
      }
    },
    [bboxes, editorState.hoveredBboxId, editorState.selectedBboxId]
  );

  // Fit the image to the view
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

  // Fit on open, and again whenever the image size changes
  useEffect(() => {
    handleResetView();
  }, [handleResetView]);

  // Keyboard shortcuts
  useEffect(() => {
    function deleteHoveredOrSelectedBbox(): void {
      const targetId = editorState.hoveredBboxId ?? editorState.selectedBboxId;
      if (!targetId) return;
      const bbox = bboxes.find((b) => b.id === targetId);
      if (bbox) {
        handleBboxDeleteLocal(bbox);
      }
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't trigger shortcuts when typing in input fields
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return;
      }

      // ? = open keyboard shortcuts modal
      if (e.key === "?") {
        e.preventDefault();
        setShowShortcutsModal(true);
        return;
      }

      // Escape = close the shortcuts modal
      if (e.key === "Escape" && showShortcutsModal) {
        setShowShortcutsModal(false);
        return;
      }

      // Don't process other shortcuts when modal is open
      if (showShortcutsModal) {
        return;
      }

      // Delete / Backspace = delete hovered or selected bbox
      if (e.key === "Delete" || e.key === "Backspace") {
        deleteHoveredOrSelectedBbox();
        return;
      }

      // D = the same, but only without modifiers
      if (e.key === "d" || e.key === "D") {
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          deleteHoveredOrSelectedBbox();
        }
        return;
      }

      // Z = undo (with or without Ctrl)
      if (e.key === "z" || e.key === "Z") {
        e.preventDefault();
        undoHistory.undo();
        return;
      }

      // A / N = toggle draw mode
      if (e.key === "a" || e.key === "A" || e.key === "n" || e.key === "N") {
        setEditorState((prev) => ({
          ...prev,
          mode: prev.mode === "draw" ? "view" : "draw",
        }));
        return;
      }

      // F = fit to view
      if (e.key === "f" || e.key === "F") {
        handleResetView();
        return;
      }

      // Arrow left = previous image
      if (e.key === "ArrowLeft" && hasPrevImage && onNavigatePrev) {
        e.preventDefault();
        onNavigatePrev();
        return;
      }

      // Arrow right = next image
      if (e.key === "ArrowRight" && hasNextImage && onNavigateNext) {
        e.preventDefault();
        onNavigateNext();
        return;
      }

      // Arrow up = zoom in
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setEditorState((prev) => ({
          ...prev,
          zoom: Math.min(MAX_ZOOM, prev.zoom * 1.2),
        }));
        return;
      }

      // Arrow down = zoom out
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setEditorState((prev) => ({
          ...prev,
          zoom: Math.max(MIN_ZOOM, prev.zoom / 1.2),
        }));
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
  }, [bboxes, editorState.selectedBboxId, editorState.hoveredBboxId, editorState.isSpacePressed, handleBboxDeleteLocal, undoHistory, showShortcutsModal, handleResetView, hasPrevImage, hasNextImage, onNavigatePrev, onNavigateNext]);

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
        {/* Back + keyboard shortcuts - top left, moves with sidebar */}
        <div className={`absolute top-4 z-50 flex items-center gap-2 transition-all duration-300 ${
          showNavigation ? "left-[17rem]" : "left-4"
        }`}>
          <button
            onClick={onClose}
            className="bg-bg-secondary/80 backdrop-blur-sm p-2.5 rounded-xl border border-white/10 hover:bg-white/10 hover:border-white/20 transition-all group"
            title={t("back")}
          >
            <ArrowLeft className="w-5 h-5 text-text-secondary group-hover:text-text-primary transition-colors" />
          </button>

          <button
            onClick={() => setShowShortcutsModal(true)}
            className="bg-bg-secondary/80 backdrop-blur-sm p-2.5 rounded-xl border border-white/10 hover:bg-white/10 hover:border-white/20 transition-all group"
            title={t("keyboardShortcuts")}
          >
            <Keyboard className="w-5 h-5 text-text-secondary group-hover:text-text-primary transition-colors" />
          </button>
        </div>

        {/* Image navigation - top right, left of the crop preview panel */}
        {totalImages > 1 && (
          <div className="absolute top-4 right-[17rem] z-50 flex items-center gap-2">
            {/* Previous button */}
            <button
              onClick={onNavigatePrev}
              disabled={!hasPrevImage}
              className={getNavButtonClassName(hasPrevImage)}
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
              className={getNavButtonClassName(hasNextImage)}
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
          title={showNavigation ? t("hideNavigation") : t("showNavigation")}
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
        <div className="flex-1">
          <ImageEditorCanvas
            imageUrl={imageUrl}
            imageWidth={fovImage.width || 0}
            imageHeight={fovImage.height || 0}
            bboxes={bboxes}
            editorState={editorState}
            filters={filters}
            displayMode={displayMode}
            drawingBbox={drawingBbox}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseLeave}
            onContextMenu={handleContextMenu}
            cursor={cursor}
            containerRef={containerRef}
            onImageLoaded={(img) => {
              sourceImageRef.current = img;
            }}
          />
        </div>

        {/* Crop preview panel */}
        <ImageEditorCropPreview
          bboxes={bboxes}
          selectedBboxId={editorState.selectedBboxId}
          hoveredBboxId={editorState.hoveredBboxId}
          onBboxSelect={handleBboxSelect}
          onBboxHover={handleBboxHover}
          sourceImageRef={sourceImageRef}
          modifyingBboxId={modifyingBboxId}
          liveBboxRect={liveBboxRect}
          displayMode={displayMode}
        />
      </div>

      {/* Toolbar - fixed position for proper centering */}
      <ImageEditorToolbar
        filters={filters}
        onFiltersChange={setFilters}
        displayMode={displayMode}
        onDisplayModeChange={(mode) => useSettingsStore.getState().setDisplayMode(mode)}
        editorMode={editorState.mode}
        onEditorModeChange={setEditorMode}
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
        onPositionChange={setToolbarPosition}
        sidebarOpen={showNavigation}
        // Re-detect props
        onRedetect={() => setShowRedetectConfirm(true)}
        isRedetecting={redetectMutation.isPending}
      />

      {/* Bbox context menu */}
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

      {/* Re-detect confirmation modal */}
      <ConfirmModal
        isOpen={showRedetectConfirm}
        onClose={() => setShowRedetectConfirm(false)}
        onConfirm={() => redetectMutation.mutate()}
        title={t("redetectConfirmTitle")}
        message={t("redetectConfirmMessage")}
        confirmLabel={t("redetect")}
        isLoading={redetectMutation.isPending}
        variant="warning"
      />

      {/* Keyboard shortcuts modal */}
      <KeyboardShortcutsModal
        isOpen={showShortcutsModal}
        onClose={() => setShowShortcutsModal(false)}
      />
    </div>
  );
}
