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
import { ChevronRight, ChevronLeft, ArrowLeft, AlertCircle, X, ScanSearch, Wand2, Loader2, Type, MousePointer2 } from "lucide-react";
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
  SAMEmbeddingStatus,
  SegmentPromptMode,
  DetectedInstance,
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
import { TextPromptSearch } from "./TextPromptSearch";
import { useBboxInteraction } from "./hooks/useBboxInteraction";
import { useUndoHistory } from "./hooks/useUndoHistory";
import { useSegmentation } from "./hooks/useSegmentation";

// localStorage key for persisting toolbar position
const TOOLBAR_POSITION_KEY = "maptimize:editor:toolbarPosition";
const DEFAULT_TOOLBAR_POSITION: ToolbarPosition = { edge: "bottom", offset: 0.5 };

/** Mouse icon with highlighted button (left or right) */
interface MouseIconProps {
  className?: string;
  button: "left" | "right";
}

function MouseIcon({ className = "", button }: MouseIconProps): React.ReactElement {
  const path = button === "left"
    ? "M1.5 6 Q1.5 1.5 7 1.5 L7 7.5 L1.5 7.5 Z"
    : "M12.5 6 Q12.5 1.5 7 1.5 L7 7.5 L12.5 7.5 Z";

  return (
    <svg width="14" height="18" viewBox="0 0 14 18" fill="none" className={className}>
      <rect x="1" y="1" width="12" height="16" rx="6" stroke="currentColor" strokeWidth="1.5" fill="none" />
      <line x1="7" y1="1" x2="7" y2="8" stroke="currentColor" strokeWidth="1" />
      <path d={path} fill="currentColor" />
    </svg>
  );
}

/** Point prompt help panel showing mouse controls */
function PointPromptHelpPanel(): React.ReactElement {
  const t = useTranslations("editor");

  return (
    <div className="bg-black/60 backdrop-blur-sm rounded-lg px-3 py-2 text-xs text-white/80 space-y-1">
      <div className="flex items-center gap-2">
        <MouseIcon button="left" className="text-emerald-400" />
        <span>{t("addForeground")}</span>
      </div>
      <div className="flex items-center gap-2">
        <MouseIcon button="right" className="text-red-400" />
        <span>{t("addBackground")}</span>
      </div>
      <div className="border-t border-white/20 my-1" />
      <div className="flex items-center gap-2">
        <span className="text-yellow-400 font-medium text-[10px]">Shift+</span>
        <MouseIcon button="left" className="text-yellow-400" />
        <span>{t("panImage")}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-yellow-400 font-medium text-[10px]">Shift+</span>
        <MouseIcon button="right" className="text-yellow-400" />
        <span>{t("undoPoint")}</span>
      </div>
    </div>
  );
}

/** Segmentation panel with mode toggle (point/text) */
interface SegmentationPanelProps {
  promptMode: SegmentPromptMode;
  setPromptMode: (mode: SegmentPromptMode) => void;
  supportsTextPrompts: boolean;
  // Text prompt props
  textPrompt: string;
  setTextPrompt: (prompt: string) => void;
  onTextQuery: () => void;
  isQuerying: boolean;
  detectedInstances: DetectedInstance[];
  selectedInstanceIndex: number | null;
  onSelectInstance: (index: number) => void;
  onSaveInstance: (index: number) => void;
  onClearText: () => void;
  textError: string | null;
}

function SegmentationPanel({
  promptMode,
  setPromptMode,
  supportsTextPrompts,
  textPrompt,
  setTextPrompt,
  onTextQuery,
  isQuerying,
  detectedInstances,
  selectedInstanceIndex,
  onSelectInstance,
  onSaveInstance,
  onClearText,
  textError,
}: SegmentationPanelProps): React.ReactElement {
  const t = useTranslations("editor");

  return (
    <div className="bg-black/60 backdrop-blur-sm rounded-lg overflow-hidden min-w-[220px]">
      {/* Mode toggle - only show if text prompts are supported */}
      {supportsTextPrompts && (
        <div className="flex border-b border-white/10">
          <button
            onClick={() => setPromptMode("point")}
            className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium transition-colors ${
              promptMode === "point"
                ? "bg-primary-500/20 text-primary-400"
                : "text-white/60 hover:text-white/80 hover:bg-white/5"
            }`}
          >
            <MousePointer2 className="w-3.5 h-3.5" />
            <span>{t("pointPrompt")}</span>
          </button>
          <button
            onClick={() => setPromptMode("text")}
            className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium transition-colors ${
              promptMode === "text"
                ? "bg-primary-500/20 text-primary-400"
                : "text-white/60 hover:text-white/80 hover:bg-white/5"
            }`}
          >
            <Type className="w-3.5 h-3.5" />
            <span>{t("textPrompt")}</span>
          </button>
        </div>
      )}

      {/* Content based on mode */}
      <div className="p-2">
        {promptMode === "point" ? (
          <PointPromptHelpPanel />
        ) : (
          <TextPromptSearch
            value={textPrompt}
            onChange={setTextPrompt}
            onSubmit={onTextQuery}
            isLoading={isQuerying}
            detectedInstances={detectedInstances}
            selectedInstanceIndex={selectedInstanceIndex}
            onSelectInstance={onSelectInstance}
            onSaveInstance={onSaveInstance}
            onClear={onClearText}
            error={textError}
          />
        )}
      </div>
    </div>
  );
}

/** Simple help panel for legacy mode (no text support) */
function SegmentationHelpPanel(): React.ReactElement {
  return <PointPromptHelpPanel />;
}

interface SegmentationModeButtonProps {
  isActive: boolean;
  embeddingStatus: SAMEmbeddingStatus;
  onClick: () => void;
  label: string;
  title: string;
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

/**
 * Updates saved polygons list - replaces existing or adds new.
 */
function updateSavedPolygon(
  polygons: CellPolygon[],
  cropId: number,
  points: [number, number][],
  iouScore: number
): CellPolygon[] {
  const existingIndex = polygons.findIndex(p => p.cropId === cropId);
  if (existingIndex >= 0) {
    const updated = [...polygons];
    updated[existingIndex] = { cropId, points, iouScore };
    return updated;
  }
  return [...polygons, { cropId, points, iouScore }];
}

/**
 * Segmentation mode button with status indicators.
 * Extracted to avoid nested ternaries in the main component.
 */
function SegmentationModeButton({
  isActive,
  embeddingStatus,
  onClick,
  label,
  title,
}: SegmentationModeButtonProps): React.ReactElement {
  const isComputing = embeddingStatus === "computing" || embeddingStatus === "pending";
  const isDisabled = isComputing;

  function getButtonClassName(): string {
    const base = "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all relative";
    if (isActive) {
      return `${base} bg-red-500 text-white`;
    }
    if (isComputing) {
      return `${base} text-text-muted cursor-not-allowed`;
    }
    return `${base} text-text-secondary hover:bg-white/10`;
  }

  return (
    <button
      onClick={onClick}
      disabled={isDisabled}
      className={getButtonClassName()}
      title={title}
    >
      {isComputing ? (
        <Loader2 className="w-4 h-4 animate-spin" />
      ) : (
        <Wand2 className="w-4 h-4" />
      )}
      <span className="hidden sm:inline">{label}</span>
      {/* Ready indicator dot */}
      {embeddingStatus === "ready" && !isActive && (
        <span className="absolute -top-0.5 -right-0.5 w-2 h-2 bg-red-400 rounded-full" />
      )}
      {/* Error indicator dot */}
      {embeddingStatus === "error" && (
        <span className="absolute -top-0.5 -right-0.5 w-2 h-2 bg-red-400 rounded-full" />
      )}
    </button>
  );
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
    isShiftPressed: false,
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

  // FOV-level segmentation mask (covers entire image)
  const [fovMaskPolygon, setFovMaskPolygon] = useState<[number, number][] | null>(null);

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
    onFOVMaskSaved: useCallback((_imageId: number, polygon: [number, number][], _iouScore: number) => {
      // Update local FOV mask state and notify parent component
      setFovMaskPolygon(polygon);
      onDataChanged?.();
    }, [onDataChanged]),
  });

  // Load FOV mask on mount
  useEffect(() => {
    const loadFOVMask = async () => {
      try {
        const result = await api.getFOVSegmentationMask(fovImage.id);
        if (result.has_mask && result.polygon && result.polygon.length >= 3) {
          setFovMaskPolygon(result.polygon);
        }
      } catch (err) {
        console.error("[Editor] Failed to load FOV mask:", err);
      }
    };
    loadFOVMask();
  }, [fovImage.id]);

  // Load saved polygons when crops change
  useEffect(() => {
    const loadPolygons = async () => {
      const cropIds = crops.map(c => c.id);
      if (cropIds.length === 0) return;

      try {
        const result = await api.getSegmentationMasksBatch(cropIds);
        if (result.masks && typeof result.masks === 'object') {
          // Backend returns masks as an object keyed by crop_id
          // Validate each entry before adding to state
          const polygons = Object.entries(result.masks)
            .filter(([_, maskData]) =>
              maskData?.polygon &&
              Array.isArray(maskData.polygon) &&
              maskData.polygon.length >= 3
            )
            .map(([cropIdStr, maskData]) => {
              const cropId = parseInt(cropIdStr, 10);
              return {
                cropId,
                points: maskData.polygon as [number, number][],
                iouScore: maskData.iou_score ?? 0,
              };
            })
            .filter(p => !isNaN(p.cropId));
          setSavedPolygons(polygons);
        }
      } catch (err) {
        console.error("[Editor] Failed to load segmentation masks:", err);
        showError(t("loadMasksError"));
      }
    };

    loadPolygons();
  }, [crops, showError, t]);

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

      // Shift = enable pan/undo mode in segmentation
      if (e.key === "Shift" && !editorState.isShiftPressed) {
        setEditorState((prev) => ({
          ...prev,
          isShiftPressed: true,
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
      if (e.key === "Shift") {
        setEditorState((prev) => ({
          ...prev,
          isShiftPressed: false,
        }));
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
  }, [bboxes, editorState.selectedBboxId, editorState.isSpacePressed, editorState.isShiftPressed, editorState.mode, handleBboxDeleteLocal, undoHistory, segmentation]);

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

  // Handle save FOV mask
  const handleSaveMask = useCallback(async () => {
    const result = await segmentation.saveFOVMask();
    if (!result.success && result.error) {
      showError(result.error);
    }
  }, [segmentation, showError]);

  // Save a text-detected instance to FOV
  const handleSaveTextInstance = useCallback(async (instanceIndex: number) => {
    const result = await segmentation.saveTextInstanceToFOV(instanceIndex);
    if (!result.success && result.error) {
      showError(result.error);
    }
  }, [segmentation, showError]);

  // Track panning state for segment mode Shift+drag
  const [segmentPanning, setSegmentPanning] = useState<{ startX: number; startY: number; startPanX: number; startPanY: number } | null>(null);

  // Wrap mouse down handler to support segmentation mode
  const handleMouseDownWithSegmentation = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      // In segment mode, handle segmentation clicks
      if (editorState.mode === "segment") {
        // Shift modifier: left click = pan (bypass bbox detection), right click = undo
        if (e.shiftKey) {
          if (e.button === 0) {
            // Left click with Shift = start panning directly (bypass bbox detection)
            const container = containerRef.current;
            if (container) {
              const rect = container.getBoundingClientRect();
              setSegmentPanning({
                startX: e.clientX - rect.left,
                startY: e.clientY - rect.top,
                startPanX: editorState.panOffset.x,
                startPanY: editorState.panOffset.y,
              });
            }
            return;
          }
          if (e.button === 2) {
            // Right click with Shift = undo last click point
            e.preventDefault();
            segmentation.undoLastClick();
            return;
          }
        }
        // Normal segmentation behavior (no Shift) - only if embedding is ready
        if (segmentation.isReady) {
          // Prevent context menu on right click
          if (e.button === 2) {
            e.preventDefault();
          }
          handleSegmentationClick(e as React.MouseEvent<HTMLCanvasElement | HTMLDivElement>);
          return;
        }
      }
      // Otherwise, delegate to bbox interaction
      handleMouseDown(e);
    },
    [editorState.mode, editorState.panOffset, segmentation.isReady, segmentation.undoLastClick, handleSegmentationClick, handleMouseDown]
  );

  // Handle mouse move for segment mode panning
  const handleMouseMoveWithSegmentation = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      if (segmentPanning) {
        const container = containerRef.current;
        if (container) {
          const rect = container.getBoundingClientRect();
          const currentX = e.clientX - rect.left;
          const currentY = e.clientY - rect.top;
          const dx = currentX - segmentPanning.startX;
          const dy = currentY - segmentPanning.startY;
          setEditorState(prev => ({
            ...prev,
            panOffset: {
              x: segmentPanning.startPanX + dx,
              y: segmentPanning.startPanY + dy,
            },
          }));
        }
        return;
      }
      handleMouseMove(e);
    },
    [segmentPanning, handleMouseMove]
  );

  // Handle mouse up for segment mode panning
  const handleMouseUpWithSegmentation = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      if (segmentPanning) {
        setSegmentPanning(null);
        return;
      }
      handleMouseUp(e);
    },
    [segmentPanning, handleMouseUp]
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
        {/* Back button + Mode switch - top left, moves with sidebar */}
        <div className={`absolute top-4 z-50 flex items-center gap-2 transition-all duration-300 ${
          showNavigation ? "left-[17rem]" : "left-4"
        }`}>
          {/* Back button */}
          <button
            onClick={onClose}
            className="bg-bg-secondary/80 backdrop-blur-sm p-2.5 rounded-xl border border-white/10 hover:bg-white/10 hover:border-white/20 transition-all group"
            title={t("back")}
          >
            <ArrowLeft className="w-5 h-5 text-text-secondary group-hover:text-text-primary transition-colors" />
          </button>

          {/* Detection / Segmentation mode switch */}
          <div className="bg-bg-secondary/80 backdrop-blur-sm rounded-xl border border-white/10 p-1 flex items-center gap-1">
            {/* Detection mode button */}
            <button
              onClick={() => {
                if (editorState.mode === "segment") {
                  setEditorState(prev => ({ ...prev, mode: "view" }));
                  segmentation.clearSegmentation();
                }
              }}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                editorState.mode !== "segment"
                  ? "bg-primary-500 text-white"
                  : "text-text-secondary hover:bg-white/10"
              }`}
              title={t("detectionMode")}
            >
              <ScanSearch className="w-4 h-4" />
              <span className="hidden sm:inline">{t("detection")}</span>
            </button>

            {/* Segmentation mode button */}
            <SegmentationModeButton
              isActive={editorState.mode === "segment"}
              embeddingStatus={segmentation.embeddingStatus}
              onClick={() => {
                if (editorState.mode !== "segment") {
                  setEditorState(prev => ({ ...prev, mode: "segment" }));
                }
              }}
              label={t("segmentation")}
              title={t("segmentMode")}
            />
          </div>
        </div>

        {/* Image navigation - top right, adjusts position based on segment mode */}
        {totalImages > 1 && (
          <div className={`absolute top-4 z-50 flex flex-col items-end gap-2 transition-all duration-300 ${
            editorState.mode === "segment" ? "right-4" : "right-[17rem]"
          }`}>
            <div className="flex items-center gap-2">
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

            {/* Segmentation panel - under navigation */}
            {editorState.mode === "segment" && (
              <SegmentationPanel
                promptMode={segmentation.promptMode}
                setPromptMode={segmentation.setPromptMode}
                supportsTextPrompts={segmentation.supportsTextPrompts}
                textPrompt={segmentation.textState.textPrompt}
                setTextPrompt={segmentation.setTextPrompt}
                onTextQuery={segmentation.queryTextSegmentation}
                isQuerying={segmentation.textState.isQuerying}
                detectedInstances={segmentation.textState.detectedInstances}
                selectedInstanceIndex={segmentation.textState.selectedInstanceIndex}
                onSelectInstance={segmentation.selectInstance}
                onSaveInstance={handleSaveTextInstance}
                onClearText={segmentation.clearTextSegmentation}
                textError={segmentation.textState.error}
              />
            )}
          </div>
        )}

        {/* Segmentation panel when no image navigation - top right */}
        {totalImages <= 1 && editorState.mode === "segment" && (
          <div className="absolute top-4 right-4 z-50">
            <SegmentationPanel
              promptMode={segmentation.promptMode}
              setPromptMode={segmentation.setPromptMode}
              supportsTextPrompts={segmentation.supportsTextPrompts}
              textPrompt={segmentation.textState.textPrompt}
              setTextPrompt={segmentation.setTextPrompt}
              onTextQuery={segmentation.queryTextSegmentation}
              isQuerying={segmentation.textState.isQuerying}
              detectedInstances={segmentation.textState.detectedInstances}
              selectedInstanceIndex={segmentation.textState.selectedInstanceIndex}
              onSelectInstance={segmentation.selectInstance}
              onSaveInstance={handleSaveTextInstance}
              onClearText={segmentation.clearTextSegmentation}
              textError={segmentation.textState.error}
            />
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
            onMouseMove={handleMouseMoveWithSegmentation}
            onMouseUp={handleMouseUpWithSegmentation}
            onMouseLeave={() => { setSegmentPanning(null); handleMouseLeave(); }}
            onContextMenu={handleContextMenuWithSegmentation}
            cursor={editorState.mode === "segment" ? (segmentPanning ? "grabbing" : editorState.isShiftPressed ? "grab" : "crosshair") : cursor}
            containerRef={containerRef}
            onImageCanvasReady={(canvas) => {
              imageCanvasRef.current = canvas;
            }}
            onImageLoaded={(img) => {
              sourceImageRef.current = img;
            }}
            isSegmentMode={editorState.mode === "segment"}
            backgroundColor={editorState.mode === "segment" ? "#1a0a0a" : "#0a1a0a"}
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

        {/* Crop preview panel - hidden in segment mode */}
        {editorState.mode !== "segment" && (
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
            fovMaskPolygon={fovMaskPolygon}
          />
        )}
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
