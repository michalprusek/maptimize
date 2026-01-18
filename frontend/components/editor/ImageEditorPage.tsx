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
import { ChevronRight, ChevronLeft, ArrowLeft, AlertCircle, X, ScanSearch, Wand2, Loader2, Type, MousePointer2, Keyboard, Trash2 } from "lucide-react";
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
  MaskContextMenuState,
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
  normalizePolygonData,
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
import { KeyboardShortcutsModal } from "./KeyboardShortcutsModal";
import { useEditorModePersistence, useLocalStorage } from "@/hooks";

// localStorage keys for persisting editor settings
const TOOLBAR_POSITION_KEY = "maptimize:editor:toolbarPosition";
const MASK_OPACITY_KEY = "maptimize:editor:maskOpacity";
const FILTERS_KEY = "maptimize:editor:filters";
const DEFAULT_TOOLBAR_POSITION: ToolbarPosition = { edge: "bottom", offset: 0.5 };
const DEFAULT_MASK_OPACITY = 0.3;

/** Type guard for mask opacity validation */
function isValidMaskOpacity(value: unknown): value is number {
  return typeof value === "number" && value >= 0 && value <= 1;
}

/** Type guard for ImageFilters validation */
function isValidFilters(value: unknown): value is ImageFilters {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  return (
    typeof obj.brightness === "number" &&
    obj.brightness >= 0 &&
    obj.brightness <= 400 &&
    typeof obj.contrast === "number" &&
    obj.contrast >= 0 &&
    obj.contrast <= 400
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


/** Segmentation panel with mode toggle (point/text) - clean switch design */
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
  onClearText,
  textError,
}: SegmentationPanelProps): React.ReactElement {
  const t = useTranslations("editor");

  // Don't render anything if text prompts not supported (point mode is default)
  if (!supportsTextPrompts) {
    return <></>;
  }

  return (
    <div className="bg-bg-secondary/80 backdrop-blur-sm rounded-xl border border-white/10 overflow-hidden">
      {/* Clean toggle switch */}
      <div className="flex items-center p-1 gap-1">
        <button
          onClick={() => setPromptMode("point")}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
            promptMode === "point"
              ? "bg-primary-500 text-white"
              : "text-text-secondary hover:bg-white/10"
          }`}
        >
          <MousePointer2 className="w-3.5 h-3.5" />
          <span>{t("pointPrompt")}</span>
        </button>
        <button
          onClick={() => setPromptMode("text")}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
            promptMode === "text"
              ? "bg-primary-500 text-white"
              : "text-text-secondary hover:bg-white/10"
          }`}
        >
          <Type className="w-3.5 h-3.5" />
          <span>{t("textPrompt")}</span>
        </button>
      </div>

      {/* Text search - only show in text mode */}
      {promptMode === "text" && (
        <div className="p-2 pt-0">
          <TextPromptSearch
            value={textPrompt}
            onChange={setTextPrompt}
            onSubmit={onTextQuery}
            isLoading={isQuerying}
            detectedInstances={detectedInstances}
            onClear={onClearText}
            error={textError}
          />
        </div>
      )}
    </div>
  );
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
    isSegmentAddMode: true, // Default to add mode when entering segment mode
  }));

  // Persist editor mode across page refreshes (localStorage)
  const setEditorMode = useCallback((mode: EditorMode) => {
    setEditorState(prev => ({ ...prev, mode }));
  }, []);
  useEditorModePersistence(editorState.mode, setEditorMode);

  // Ref to preserve mode when switching images
  const preservedModeRef = useRef<EditorMode>(editorState.mode);
  useEffect(() => {
    preservedModeRef.current = editorState.mode;
  }, [editorState.mode]);

  // Restore mode when image changes (except on first mount)
  const isFirstMount = useRef(true);
  useEffect(() => {
    if (isFirstMount.current) {
      isFirstMount.current = false;
      return;
    }
    // Restore the preserved mode and reset add mode to ON when navigating to a new image
    setEditorState(prev => ({
      ...prev,
      mode: preservedModeRef.current,
      isSegmentAddMode: true, // Always enable add mode when switching images
    }));
    // Clear selected mask when switching images
    setSelectedMaskIndex(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fovImage.id]);

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

  // Mask context menu state (for right-click on FOV masks)
  const [maskContextMenu, setMaskContextMenu] = useState<MaskContextMenuState>({
    isOpen: false,
    position: null,
    targetMaskIndex: null,
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

  // Saved polygons for all crops
  const [savedPolygons, setSavedPolygons] = useState<CellPolygon[]>([]);

  // FOV-level segmentation masks (multiple polygons covering entire image)
  const [fovMaskPolygons, setFovMaskPolygons] = useState<[number, number][][] | null>(null);

  // FOV mask UI state - opacity persisted to localStorage
  const [maskOpacity, setMaskOpacity] = useLocalStorage<number>(
    MASK_OPACITY_KEY,
    DEFAULT_MASK_OPACITY,
    { validate: isValidMaskOpacity }
  );
  const [hoveredMaskIndex, setHoveredMaskIndex] = useState<number | null>(null);
  const [selectedMaskIndex, setSelectedMaskIndex] = useState<number | null>(null);

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
    onFOVMaskSaved: useCallback((_imageId: number, polygons: [number, number][][], _iouScore: number) => {
      // Update local FOV masks state and notify parent component
      setFovMaskPolygons(polygons);
      onDataChanged?.();
    }, [onDataChanged]),
  });

  // Load FOV mask on mount
  useEffect(() => {
    const loadFOVMask = async () => {
      try {
        const result = await api.getFOVSegmentationMask(fovImage.id);
        if (result.has_mask && result.polygon) {
          const normalized = normalizePolygonData(result.polygon);
          if (normalized) {
            setFovMaskPolygons(normalized);
          } else {
            console.warn("[Editor] FOV mask data could not be normalized");
          }
        }
      } catch (err) {
        console.error("[Editor] Failed to load FOV mask:", err);
        // Show error toast so user knows masks couldn't be loaded
        showError(t("loadMasksError"));
      }
    };
    loadFOVMask();
  }, [fovImage.id, showError, t]);

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

  // Delete a specific FOV mask polygon by index
  const deleteMaskPolygon = useCallback(async (index: number) => {
    if (!fovMaskPolygons) return;

    const newPolygons = fovMaskPolygons.filter((_, idx) => idx !== index);

    // Step 1: Delete entire mask
    try {
      await api.deleteFOVSegmentationMask(fovImage.id);
    } catch (deleteErr) {
      console.error("[Editor] Failed to delete FOV mask:", { imageId: fovImage.id, error: deleteErr });
      showError(t("deleteError"));
      return;
    }

    // Step 2: Re-save remaining polygons if any
    if (newPolygons.length > 0) {
      try {
        await api.saveFOVSegmentationMaskWithUnion({
          image_id: fovImage.id,
          polygons: newPolygons,
          iou_score: 0.9,
          prompt_count: 0,
        });
      } catch (saveErr) {
        console.error("[Editor] Delete succeeded but re-save failed:", { imageId: fovImage.id, remainingCount: newPolygons.length, error: saveErr });
        // Mask is deleted on server - update local state to match
        setFovMaskPolygons(null);
        setSelectedMaskIndex(null);
        setHoveredMaskIndex(null);
        showError(t("partialDeleteError"));
        onDataChanged?.();
        return;
      }
    }

    // Success
    setFovMaskPolygons(newPolygons.length > 0 ? newPolygons : null);
    setSelectedMaskIndex(null);
    setHoveredMaskIndex(null);
    onDataChanged?.();
  }, [fovMaskPolygons, fovImage.id, onDataChanged, showError, t]);

  // Handle FOV mask polygon click - right-click shows context menu
  const handleMaskClick = useCallback((index: number, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();

    // Right-click = show context menu (always allowed for mask deletion)
    if (e.button === 2) {
      setMaskContextMenu({
        isOpen: true,
        position: { x: e.clientX, y: e.clientY },
        targetMaskIndex: index,
      });
      return;
    }

    // Left-click = select/deselect for keyboard deletion (only when not in add mode)
    if (!editorState.isSegmentAddMode) {
      setSelectedMaskIndex(prev => prev === index ? null : index);
    }
  }, [editorState.isSegmentAddMode]);

  // Handle FOV mask polygon hover
  const handleMaskHover = useCallback((index: number | null) => {
    setHoveredMaskIndex(index);
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

      // Escape = close modal or clear segmentation points
      if (e.key === "Escape") {
        if (showShortcutsModal) {
          setShowShortcutsModal(false);
          return;
        }
        if (editorState.mode === "segment") {
          segmentation.clearSegmentation();
          return;
        }
      }

      // Don't process other shortcuts when modal is open
      if (showShortcutsModal) {
        return;
      }

      // Delete / Backspace = delete hovered FOV mask polygon (in segment mode, not in add mode) or bbox
      if (e.key === "Delete" || e.key === "Backspace") {
        // In segment mode with hovered mask, delete the mask polygon (only when not in add mode)
        if (editorState.mode === "segment" && !editorState.isSegmentAddMode && hoveredMaskIndex !== null && fovMaskPolygons) {
          e.preventDefault();
          deleteMaskPolygon(hoveredMaskIndex);
          return;
        }
        // Otherwise, delete bbox
        const targetId = editorState.hoveredBboxId ?? editorState.selectedBboxId;
        if (targetId) {
          const bbox = bboxes.find((b) => b.id === targetId);
          if (bbox) {
            handleBboxDeleteLocal(bbox);
          }
        }
        return;
      }

      // D = delete hovered or selected bbox (not in segment mode)
      if (e.key === "d" || e.key === "D") {
        // D key only works without modifiers and not in segment mode
        if ((e.ctrlKey || e.metaKey || e.altKey) || editorState.mode === "segment") {
          return;
        }
        // Use hovered bbox if available, otherwise use selected
        const targetId = editorState.hoveredBboxId ?? editorState.selectedBboxId;
        if (targetId) {
          const bbox = bboxes.find((b) => b.id === targetId);
          if (bbox) {
            handleBboxDeleteLocal(bbox);
          }
        }
        return;
      }

      // Z = undo (with or without Ctrl)
      if (e.key === "z" || e.key === "Z") {
        e.preventDefault();
        // In segment mode: undo last segmentation click
        if (editorState.mode === "segment") {
          segmentation.undoLastClick();
        } else {
          // In other modes: undo bbox changes
          undoHistory.undo();
        }
        return;
      }

      // A = toggle add points mode (in segment mode) or toggle draw mode (otherwise)
      if (e.key === "a" || e.key === "A") {
        if (editorState.mode === "segment") {
          // In segment mode: toggle add points mode
          setEditorState((prev) => ({
            ...prev,
            isSegmentAddMode: !prev.isSegmentAddMode,
          }));
        } else {
          // Not in segment mode: toggle draw mode
          setEditorState((prev) => ({
            ...prev,
            mode: prev.mode === "draw" ? "view" : "draw",
          }));
        }
        return;
      }

      // N = toggle draw mode (only when not in segment mode)
      if (e.key === "n" || e.key === "N") {
        if (editorState.mode === "segment") return;
        setEditorState((prev) => ({
          ...prev,
          mode: prev.mode === "draw" ? "view" : "draw",
        }));
        return;
      }

      // S = toggle segment mode (only when not in draw mode)
      if (e.key === "s" || e.key === "S") {
        // Don't switch modes when in draw mode - let user stay in their chosen mode
        if (editorState.mode === "draw") return;
        if (segmentation.isReady || segmentation.embeddingStatus === "not_started") {
          setEditorState((prev) => ({
            ...prev,
            mode: prev.mode === "segment" ? "view" : "segment",
          }));
        }
        return;
      }

      // Enter = save mask (in segment mode with pending polygons or preview)
      if (e.key === "Enter" && editorState.mode === "segment") {
        if (segmentation.hasPendingPolygons || segmentation.state.previewPolygon) {
          e.preventDefault();
          segmentation.saveFOVMask()
            .then((result) => {
              if (!result.success && result.error) {
                // Show error to user if the hook didn't handle it
                showError(result.error);
              }
            })
            .catch((err) => {
              console.error("[Editor] Unexpected error saving mask on Enter:", err);
              showError(t("saveError"));
            });
        }
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
  }, [bboxes, editorState.selectedBboxId, editorState.hoveredBboxId, editorState.isSpacePressed, editorState.isShiftPressed, editorState.mode, editorState.isSegmentAddMode, handleBboxDeleteLocal, undoHistory, segmentation, showShortcutsModal, handleResetView, hasPrevImage, hasNextImage, onNavigatePrev, onNavigateNext, hoveredMaskIndex, fovMaskPolygons, fovImage.id, onDataChanged, showError, t, deleteMaskPolygon]);

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
      // Only add points when in segment mode with add mode enabled
      if (editorState.mode !== "segment" || !segmentation.isReady || !editorState.isSegmentAddMode) return;

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
    [editorState.mode, editorState.zoom, editorState.panOffset, editorState.isSegmentAddMode, segmentation, fovImage.width, fovImage.height]
  );

  // Handle save FOV mask
  const handleSaveMask = useCallback(async () => {
    const result = await segmentation.saveFOVMask();
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
        // Shift modifier OR add mode OFF: left click = pan (bypass bbox detection), right click = undo
        if (e.shiftKey || !editorState.isSegmentAddMode) {
          if (e.button === 0) {
            // Left click with Shift (or add mode off) = start panning directly (bypass bbox detection)
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
        // Normal segmentation behavior (no Shift, add mode ON) - only if embedding is ready
        if (segmentation.isReady && editorState.isSegmentAddMode) {
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
    [editorState.mode, editorState.panOffset, editorState.isSegmentAddMode, segmentation.isReady, segmentation.undoLastClick, handleSegmentationClick, handleMouseDown]
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

            {/* Keyboard shortcuts button */}
            <button
              onClick={() => setShowShortcutsModal(true)}
              className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-white/10 transition-all"
              title={t("keyboardShortcuts")}
            >
              <Keyboard className="w-4 h-4" />
            </button>
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
            cursor={editorState.mode === "segment" ? (segmentPanning ? "grabbing" : (!editorState.isSegmentAddMode || editorState.isShiftPressed) ? "grab" : "crosshair") : cursor}
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
            pendingPolygons={segmentation.state.pendingPolygons}
            fovMaskPolygons={fovMaskPolygons}
            savedPolygons={savedPolygons}
            zoom={editorState.zoom}
            panOffset={editorState.panOffset}
            isActive={editorState.mode === "segment"}
            isAddMode={editorState.isSegmentAddMode}
            isLoading={segmentation.state.isLoading}
            containerWidth={containerDimensions.width}
            containerHeight={containerDimensions.height}
            maskOpacity={maskOpacity}
            selectedMaskIndex={selectedMaskIndex}
            hoveredMaskIndex={hoveredMaskIndex}
            onMaskClick={handleMaskClick}
            onMaskHover={handleMaskHover}
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
            fovMaskPolygons={fovMaskPolygons}
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
        onPositionChange={setToolbarPosition}
        sidebarOpen={showNavigation}
        // Segmentation props
        samEmbeddingStatus={segmentation.embeddingStatus}
        onComputeEmbedding={segmentation.computeEmbedding}
        hasClickPoints={segmentation.state.clickPoints.length > 0}
        hasPreviewPolygon={!!segmentation.state.previewPolygon || segmentation.hasPendingPolygons}
        onClearSegmentation={segmentation.clearSegmentation}
        onSaveMask={handleSaveMask}
        onUndoClick={segmentation.undoLastClick}
        isSavingMask={false}
        clickPointCount={segmentation.state.clickPoints.length}
        // Pending polygon props
        pendingPolygonCount={segmentation.state.pendingPolygons.length}
        onAddToPending={segmentation.addPreviewToPending}
        canAddToPending={!!segmentation.state.previewPolygon}
        // Mask opacity props
        maskOpacity={maskOpacity}
        onMaskOpacityChange={setMaskOpacity}
        // Segment add mode props
        isSegmentAddMode={editorState.isSegmentAddMode}
        onToggleSegmentAddMode={() => setEditorState(prev => ({ ...prev, isSegmentAddMode: !prev.isSegmentAddMode }))}
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

      {/* Mask context menu with click-outside backdrop */}
      <AnimatePresence>
        {maskContextMenu.isOpen && maskContextMenu.position && maskContextMenu.targetMaskIndex !== null && (
          <>
            {/* Invisible backdrop to catch outside clicks */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-[99]"
              onClick={() => setMaskContextMenu({ isOpen: false, position: null, targetMaskIndex: null })}
            />
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              transition={{ duration: 0.1 }}
              style={{
                position: "fixed",
                left: maskContextMenu.position.x,
                top: maskContextMenu.position.y,
              }}
              className="z-[100] bg-bg-elevated border border-white/10 rounded-lg shadow-xl py-1 min-w-[140px]"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                onClick={() => {
                  if (maskContextMenu.targetMaskIndex !== null) {
                    deleteMaskPolygon(maskContextMenu.targetMaskIndex);
                  }
                  setMaskContextMenu({ isOpen: false, position: null, targetMaskIndex: null });
                }}
                className="w-full px-3 py-2 text-left text-sm text-accent-red hover:bg-accent-red/10 flex items-center gap-2 transition-colors"
              >
                <Trash2 className="w-4 h-4" />
                {t("deleteMask")}
              </button>
            </motion.div>
          </>
        )}
      </AnimatePresence>

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
