"use client";

/**
 * ImageEditorToolbar Component
 *
 * Freely positionable toolbar that snaps to edges.
 * - Horizontal when docked to top/bottom
 * - Vertical when docked to left/right
 * - Draggable along the edge
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { motion } from "framer-motion";
import {
  Sun,
  Contrast,
  Plus,
  Undo2,
  ZoomIn,
  ZoomOut,
  Maximize2,
  GripVertical,
  GripHorizontal,
  Wand2,
  Save,
  Trash2,
  RotateCcw,
  Loader2,
  AlertCircle,
  CheckCircle2,
} from "lucide-react";
import type { ImageFilters, EditorMode, SAMEmbeddingStatus } from "@/lib/editor/types";
import type { DisplayMode } from "@/lib/api";
import { DEFAULT_FILTERS, FILTER_LIMITS, MIN_ZOOM, MAX_ZOOM } from "@/lib/editor/constants";

export interface ToolbarPosition {
  edge: "top" | "bottom" | "left" | "right";
  offset: number; // 0-1, position along the edge (0 = start, 1 = end)
}

interface ImageEditorToolbarProps {
  filters: ImageFilters;
  onFiltersChange: (filters: ImageFilters) => void;
  displayMode: DisplayMode;
  onDisplayModeChange: (mode: DisplayMode) => void;
  editorMode: EditorMode;
  onEditorModeChange: (mode: EditorMode) => void;
  zoom: number;
  onZoomChange: (zoom: number) => void;
  onResetView: () => void;
  canUndo: boolean;
  onUndo: () => void;
  isUndoing: boolean;
  position: ToolbarPosition;
  onPositionChange: (position: ToolbarPosition) => void;
  /** Whether the sidebar is open (affects left edge positioning) */
  sidebarOpen?: boolean;
  // Segmentation props
  /** SAM embedding status for current image */
  samEmbeddingStatus?: SAMEmbeddingStatus;
  /** Trigger SAM embedding computation */
  onComputeEmbedding?: () => void;
  /** Whether there are click points in segmentation */
  hasClickPoints?: boolean;
  /** Whether there's a preview polygon ready to save */
  hasPreviewPolygon?: boolean;
  /** Clear all segmentation click points */
  onClearSegmentation?: () => void;
  /** Save the current segmentation mask */
  onSaveMask?: () => void;
  /** Undo the last click point */
  onUndoClick?: () => void;
  /** Whether mask is currently being saved */
  isSavingMask?: boolean;
  /** Number of click points */
  clickPointCount?: number;
}

const displayModes: { value: DisplayMode; label: string }[] = [
  { value: "grayscale", label: "Gray" },
  { value: "inverted", label: "Inv" },
  { value: "green", label: "GFP" },
  { value: "fire", label: "Fire" },
];

const EDGE_MARGIN = 16; // Distance from edge in pixels
const CROP_PANEL_WIDTH = 256; // w-64 crop preview panel
const SIDEBAR_WIDTH = 256; // w-64 sidebar

// Standalone draggable slider component
interface DraggableSliderProps {
  icon: React.ComponentType<{ className?: string }>;
  value: number;
  onChange: (value: number) => void;
  label: string;
  isVertical?: boolean;
  min?: number;
  max?: number;
}

function DraggableSlider({
  icon: Icon,
  value,
  onChange,
  label,
  isVertical = false,
  min = FILTER_LIMITS.min,
  max = FILTER_LIMITS.max,
}: DraggableSliderProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const isDraggingRef = useRef(false);
  // Use refs to avoid stale closures in event handlers
  const onChangeRef = useRef(onChange);
  const minRef = useRef(min);
  const maxRef = useRef(max);
  onChangeRef.current = onChange;
  minRef.current = min;
  maxRef.current = max;

  const calculateValue = (clientX: number): number => {
    if (!trackRef.current) return FILTER_LIMITS.default;
    const rect = trackRef.current.getBoundingClientRect();
    const percentage = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return Math.round(minRef.current + percentage * (maxRef.current - minRef.current));
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    isDraggingRef.current = true;

    const newValue = calculateValue(e.clientX);
    onChangeRef.current(newValue);

    const handleMouseMove = (moveEvent: MouseEvent) => {
      if (isDraggingRef.current) {
        const newVal = calculateValue(moveEvent.clientX);
        onChangeRef.current(newVal);
      }
    };

    const handleMouseUp = () => {
      isDraggingRef.current = false;
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
  };

  const percentage = ((value - min) / (max - min)) * 100;

  return (
    <div className={`flex ${isVertical ? "flex-col" : "flex-row"} items-center gap-1.5`} title={`${label}: ${value}%`}>
      <Icon className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
      <div
        ref={trackRef}
        onMouseDown={handleMouseDown}
        className={`${isVertical ? "w-16" : "w-24"} h-3 bg-white/10 rounded-full cursor-pointer relative select-none border border-white/10`}
      >
        {/* Filled track */}
        <div
          className="absolute left-0 top-0 h-full bg-primary-500/40 rounded-full pointer-events-none"
          style={{ width: `${percentage}%` }}
        />
        {/* Thumb */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 bg-primary-500 rounded-full shadow-md pointer-events-none"
          style={{ left: `calc(${percentage}% - 7px)` }}
        />
      </div>
      <span className="text-[10px] text-text-muted w-7 flex-shrink-0">{value}%</span>
    </div>
  );
}

// Helper function to get SAM status badge styling
function getSAMStatusStyle(status: SAMEmbeddingStatus): string {
  switch (status) {
    case "ready":
      return "bg-emerald-500/20 text-emerald-400";
    case "computing":
    case "pending":
      return "bg-amber-500/20 text-amber-400";
    case "error":
      return "bg-red-500/20 text-red-400";
    default:
      return "bg-gray-500/20 text-gray-400";
  }
}

// SAM status badge component
interface SAMStatusBadgeProps {
  status: SAMEmbeddingStatus;
  t: ReturnType<typeof useTranslations>;
  onComputeEmbedding?: () => void;
}

function SAMStatusBadge({ status, t, onComputeEmbedding }: SAMStatusBadgeProps) {
  const baseClasses = "flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-medium";

  switch (status) {
    case "ready":
      return (
        <div className={`${baseClasses} ${getSAMStatusStyle(status)}`}>
          <CheckCircle2 className="w-3 h-3" />
          <span>{t("samReady")}</span>
        </div>
      );
    case "computing":
    case "pending":
      return (
        <div className={`${baseClasses} ${getSAMStatusStyle(status)}`}>
          <Loader2 className="w-3 h-3 animate-spin" />
          <span>{t("samComputing")}</span>
        </div>
      );
    case "error":
      return (
        <div className={`${baseClasses} ${getSAMStatusStyle(status)}`}>
          <AlertCircle className="w-3 h-3" />
          <span>{t("samError")}</span>
        </div>
      );
    case "not_started":
      if (!onComputeEmbedding) {
        return (
          <div className={`${baseClasses} ${getSAMStatusStyle(status)}`}>
            <Wand2 className="w-3 h-3" />
            <span>{t("computeSam")}</span>
          </div>
        );
      }
      return (
        <button
          onClick={onComputeEmbedding}
          className={`${baseClasses} ${getSAMStatusStyle(status)} hover:text-emerald-300 transition-colors`}
        >
          <Wand2 className="w-3 h-3" />
          <span>{t("computeSam")}</span>
        </button>
      );
    default:
      return null;
  }
}

// Toolbar icon button with consistent styling
interface ToolbarIconButtonProps {
  onClick?: () => void;
  disabled?: boolean;
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  variant?: "default" | "danger" | "primary" | "active";
  isLoading?: boolean;
}

function ToolbarIconButton({
  onClick,
  disabled = false,
  title,
  icon: Icon,
  variant = "default",
  isLoading = false,
}: ToolbarIconButtonProps) {
  function getButtonStyles(): string {
    if (disabled) {
      return "bg-bg-tertiary/50 text-text-muted cursor-not-allowed";
    }
    switch (variant) {
      case "danger":
        return "bg-bg-tertiary text-red-400 hover:bg-red-500/20";
      case "primary":
        return "bg-emerald-500 text-white hover:bg-emerald-600";
      case "active":
        return "bg-primary-500 text-white";
      default:
        return "bg-bg-tertiary text-text-secondary hover:bg-white/10";
    }
  }

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`p-1.5 rounded transition-colors ${getButtonStyles()}`}
      title={title}
    >
      {isLoading ? (
        <Loader2 className="w-4 h-4 animate-spin" />
      ) : (
        <Icon className="w-4 h-4" />
      )}
    </button>
  );
}

export function ImageEditorToolbar({
  filters,
  onFiltersChange,
  displayMode,
  onDisplayModeChange,
  editorMode,
  onEditorModeChange,
  zoom,
  onZoomChange,
  onResetView,
  canUndo,
  onUndo,
  isUndoing,
  position,
  onPositionChange,
  sidebarOpen = false,
  // Segmentation props
  samEmbeddingStatus = "not_started",
  onComputeEmbedding,
  hasClickPoints = false,
  hasPreviewPolygon = false,
  onClearSegmentation,
  onSaveMask,
  onUndoClick,
  isSavingMask = false,
  clickPointCount = 0,
}: ImageEditorToolbarProps) {
  const t = useTranslations("editor");
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [dragPosition, setDragPosition] = useState<{ x: number; y: number } | null>(null);
  const [dragOffset, setDragOffset] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  // Use refs to avoid stale closure issues in slider callbacks
  const filtersRef = useRef(filters);
  const onFiltersChangeRef = useRef(onFiltersChange);
  filtersRef.current = filters;
  onFiltersChangeRef.current = onFiltersChange;

  // Stable callbacks that read from refs
  const handleBrightnessChange = useCallback((value: number) => {
    onFiltersChangeRef.current({ ...filtersRef.current, brightness: value });
  }, []);

  const handleContrastChange = useCallback((value: number) => {
    onFiltersChangeRef.current({ ...filtersRef.current, contrast: value });
  }, []);

  const resetFilters = () => {
    onFiltersChange(DEFAULT_FILTERS);
  };

  const isVertical = position.edge === "left" || position.edge === "right";

  // Calculate position from edge and offset (accounting for sidebar only when necessary)
  // offset represents the position of toolbar's left edge (for horizontal) or top edge (for vertical)
  const getToolbarStyle = useCallback((): React.CSSProperties => {
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    // Calculate where toolbar would be without sidebar adjustment
    const fullCanvasWidth = viewportWidth - CROP_PANEL_WIDTH;
    const baseLeft = position.offset * fullCanvasWidth;

    // Only push toolbar if it would overlap with sidebar
    const needsShift = sidebarOpen && baseLeft < SIDEBAR_WIDTH;
    const adjustedLeft = needsShift ? SIDEBAR_WIDTH : baseLeft;

    switch (position.edge) {
      case "top":
        return {
          top: EDGE_MARGIN,
          left: adjustedLeft,
          transition: "left 0.3s ease-in-out",
        };
      case "bottom":
        return {
          bottom: EDGE_MARGIN,
          left: adjustedLeft,
          transition: "left 0.3s ease-in-out",
        };
      case "left":
        // Left edge toolbar always moves with sidebar
        return {
          left: (sidebarOpen ? SIDEBAR_WIDTH : 0) + EDGE_MARGIN,
          top: position.offset * viewportHeight,
          transition: "left 0.3s ease-in-out",
        };
      case "right":
        return {
          right: EDGE_MARGIN + CROP_PANEL_WIDTH,
          top: position.offset * viewportHeight,
        };
    }
  }, [position, sidebarOpen]);

  // Determine closest edge and offset from toolbar's top-left corner position
  // IMPORTANT: Offset calculation must be exact inverse of getToolbarStyle positioning
  // We always use fullCanvasWidth (without sidebar) since that's what getToolbarStyle uses for baseLeft
  const getEdgeAndOffset = useCallback((toolbarLeft: number, toolbarTop: number): ToolbarPosition => {
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const leftOffset = sidebarOpen ? SIDEBAR_WIDTH : 0;
    const fullCanvasWidth = viewportWidth - CROP_PANEL_WIDTH;

    // Calculate distances from edges (relative to visible canvas area)
    const distTop = toolbarTop;
    const distBottom = viewportHeight - toolbarTop;
    const distLeft = toolbarLeft - leftOffset;
    const distRight = (viewportWidth - CROP_PANEL_WIDTH) - toolbarLeft;

    const minDist = Math.min(distTop, distBottom, Math.max(0, distLeft), Math.max(0, distRight));

    // Offset calculation must match getToolbarStyle exactly (inverse formula)
    // getToolbarStyle uses: baseLeft = offset * fullCanvasWidth
    // So: offset = toolbarLeft / fullCanvasWidth
    if (minDist === distTop) {
      const offset = Math.max(0, Math.min(1, toolbarLeft / fullCanvasWidth));
      return { edge: "top", offset };
    }
    if (minDist === distBottom) {
      const offset = Math.max(0, Math.min(1, toolbarLeft / fullCanvasWidth));
      return { edge: "bottom", offset };
    }
    // getToolbarStyle uses: top = offset * viewportHeight
    // So: offset = toolbarTop / viewportHeight
    if (minDist === distLeft || distLeft < 0) {
      const offset = Math.max(0, Math.min(1, toolbarTop / viewportHeight));
      return { edge: "left", offset };
    }
    // distRight
    const offset = Math.max(0, Math.min(1, toolbarTop / viewportHeight));
    return { edge: "right", offset };
  }, [sidebarOpen]);

  // Handle drag start - capture offset from click position to toolbar's top-left corner
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();

    // Get toolbar bounding rect to calculate offset from top-left corner
    const toolbar = toolbarRef.current;
    if (toolbar) {
      const rect = toolbar.getBoundingClientRect();
      setDragOffset({
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
      });
    }

    setIsDragging(true);
    setDragPosition({ x: e.clientX, y: e.clientY });
  }, []);

  // Handle drag move and end
  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      setDragPosition({ x: e.clientX, y: e.clientY });
    };

    const handleMouseUp = (e: MouseEvent) => {
      // Calculate position based on toolbar's top-left corner
      const toolbarLeft = e.clientX - dragOffset.x;
      const toolbarTop = e.clientY - dragOffset.y;
      const newPosition = getEdgeAndOffset(toolbarLeft, toolbarTop);
      onPositionChange(newPosition);
      setIsDragging(false);
      setDragPosition(null);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);

    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging, getEdgeAndOffset, onPositionChange, dragOffset]);

  // Divider element (not a component to avoid remounting)
  const divider = (
    <div className={`${isVertical ? "w-full h-px" : "w-px h-5"} bg-white/10`} />
  );

  // Grip handle element (not a component to avoid remounting)
  const gripHandle = (
    <div
      onMouseDown={handleDragStart}
      className={`cursor-grab active:cursor-grabbing p-1 rounded hover:bg-white/10 transition-colors ${
        isDragging ? "bg-primary-500/20" : ""
      }`}
      title={t("dragToReposition")}
    >
      {isVertical ? (
        <GripVertical className="w-4 h-4 text-text-muted" />
      ) : (
        <GripHorizontal className="w-4 h-4 text-text-muted" />
      )}
    </div>
  );

  // Floating toolbar during drag - show full toolbar
  if (isDragging && dragPosition) {
    // Calculate toolbar's top-left corner position
    const toolbarLeft = dragPosition.x - dragOffset.x;
    const toolbarTop = dragPosition.y - dragOffset.y;
    const previewPos = getEdgeAndOffset(toolbarLeft, toolbarTop);
    const previewIsVertical = previewPos.edge === "left" || previewPos.edge === "right";

    return (
      <div
        className={`fixed z-[61] flex ${previewIsVertical ? "flex-col" : "flex-row"} items-center gap-2 px-3 py-2 bg-bg-secondary/95 backdrop-blur-sm border border-primary-500 rounded-2xl shadow-xl pointer-events-none`}
        style={{
          left: toolbarLeft,
          top: toolbarTop,
        }}
      >
        <div className={`cursor-grabbing p-1 rounded bg-primary-500/20`}>
          {previewIsVertical ? (
            <GripVertical className="w-4 h-4 text-primary-400" />
          ) : (
            <GripHorizontal className="w-4 h-4 text-primary-400" />
          )}
        </div>
        <div className={`${previewIsVertical ? "w-full h-px" : "w-px h-5"} bg-white/10`} />

        {/* Brightness */}
        <div className={`flex ${previewIsVertical ? "flex-col" : "flex-row"} items-center gap-1.5`}>
          <Sun className="w-3.5 h-3.5 text-text-muted" />
          <div className={`${previewIsVertical ? "w-16" : "w-24"} h-3 bg-white/10 rounded-full relative border border-white/10`}>
            <div className="absolute left-0 top-0 h-full bg-primary-500/40 rounded-full" style={{ width: `${((filters.brightness - FILTER_LIMITS.min) / (FILTER_LIMITS.max - FILTER_LIMITS.min)) * 100}%` }} />
            <div className="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 bg-primary-500 rounded-full shadow-md" style={{ left: `calc(${((filters.brightness - FILTER_LIMITS.min) / (FILTER_LIMITS.max - FILTER_LIMITS.min)) * 100}% - 7px)` }} />
          </div>
          <span className="text-[10px] text-text-muted w-8">{filters.brightness}%</span>
        </div>

        {/* Contrast */}
        <div className={`flex ${previewIsVertical ? "flex-col" : "flex-row"} items-center gap-1.5`}>
          <Contrast className="w-3.5 h-3.5 text-text-muted" />
          <div className={`${previewIsVertical ? "w-16" : "w-24"} h-3 bg-white/10 rounded-full relative border border-white/10`}>
            <div className="absolute left-0 top-0 h-full bg-primary-500/40 rounded-full" style={{ width: `${((filters.contrast - FILTER_LIMITS.min) / (FILTER_LIMITS.max - FILTER_LIMITS.min)) * 100}%` }} />
            <div className="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 bg-primary-500 rounded-full shadow-md" style={{ left: `calc(${((filters.contrast - FILTER_LIMITS.min) / (FILTER_LIMITS.max - FILTER_LIMITS.min)) * 100}% - 7px)` }} />
          </div>
          <span className="text-[10px] text-text-muted w-8">{filters.contrast}%</span>
        </div>

        <div className={`${previewIsVertical ? "w-full h-px" : "w-px h-5"} bg-white/10`} />

        {/* LUT */}
        <div className="bg-white/10 text-white text-xs font-medium px-2 py-1.5 rounded-lg border border-white/20">
          {displayModes.find(m => m.value === displayMode)?.label}
        </div>

        <div className={`${previewIsVertical ? "w-full h-px" : "w-px h-5"} bg-white/10`} />

        {/* Zoom */}
        <div className={`flex ${previewIsVertical ? "flex-col" : "flex-row"} items-center gap-0.5`}>
          <ZoomOut className="w-3.5 h-3.5 text-text-secondary" />
          <span className="text-[10px] text-text-muted w-8 text-center">{Math.round(zoom * 100)}%</span>
          <ZoomIn className="w-3.5 h-3.5 text-text-secondary" />
          <Maximize2 className="w-3.5 h-3.5 text-text-secondary" />
        </div>

        <div className={`${previewIsVertical ? "w-full h-px" : "w-px h-5"} bg-white/10`} />

        {/* Actions */}
        <div className={`p-1.5 rounded ${editorMode === "draw" ? "bg-primary-500 text-white" : "bg-bg-tertiary text-text-secondary"}`}>
          <Plus className="w-4 h-4" />
        </div>
        <div className={`p-1.5 rounded ${canUndo ? "bg-bg-tertiary text-text-secondary" : "bg-bg-tertiary/50 text-text-muted"}`}>
          <Undo2 className="w-4 h-4" />
        </div>
      </div>
    );
  }

  return (
    <motion.div
      ref={toolbarRef}
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className={`fixed z-50 flex ${isVertical ? "flex-col" : "flex-row"} items-center gap-2 px-3 py-2 bg-bg-secondary border border-white/5 rounded-2xl shadow-2xl`}
      style={getToolbarStyle()}
    >
      {gripHandle}
      {divider}

      {/* Brightness */}
      <DraggableSlider
        icon={Sun}
        value={filters.brightness}
        onChange={handleBrightnessChange}
        label={t("brightness")}
        isVertical={isVertical}
      />

      {/* Contrast */}
      <DraggableSlider
        icon={Contrast}
        value={filters.contrast}
        onChange={handleContrastChange}
        label={t("contrast")}
        isVertical={isVertical}
      />

      {/* Reset button */}
      {(filters.brightness !== 100 || filters.contrast !== 100) && (
        <button
          onClick={resetFilters}
          className="text-[10px] text-text-muted hover:text-text-secondary transition-colors"
        >
          {t("resetFilters")}
        </button>
      )}

      {divider}

      {/* LUT selector */}
      <select
        value={displayMode}
        onChange={(e) => onDisplayModeChange(e.target.value as DisplayMode)}
        className="bg-white/10 text-white text-xs font-medium px-2 py-1.5 rounded-lg border border-white/20
                   focus:outline-none focus:border-primary-500 hover:bg-white/15 cursor-pointer transition-colors"
      >
        {displayModes.map((mode) => (
          <option key={mode.value} value={mode.value} className="bg-bg-secondary text-white">
            {mode.label}
          </option>
        ))}
      </select>

      {divider}

      {/* Zoom controls */}
      <div className={`flex ${isVertical ? "flex-col" : "flex-row"} items-center gap-0.5`}>
        <button
          onClick={() => onZoomChange(Math.max(MIN_ZOOM, zoom - 0.25))}
          className="p-1 rounded hover:bg-white/10 transition-colors"
          title={t("zoomOut")}
        >
          <ZoomOut className="w-3.5 h-3.5 text-text-secondary" />
        </button>
        <span className="text-[10px] text-text-muted w-8 text-center">{Math.round(zoom * 100)}%</span>
        <button
          onClick={() => onZoomChange(Math.min(MAX_ZOOM, zoom + 0.25))}
          className="p-1 rounded hover:bg-white/10 transition-colors"
          title={t("zoomIn")}
        >
          <ZoomIn className="w-3.5 h-3.5 text-text-secondary" />
        </button>
        <button
          onClick={onResetView}
          className="p-1 rounded hover:bg-white/10 transition-colors"
          title={t("fitToView")}
        >
          <Maximize2 className="w-3.5 h-3.5 text-text-secondary" />
        </button>
      </div>

      {divider}

      {/* Draw mode - only show when not in segment mode */}
      {editorMode !== "segment" && (
        <ToolbarIconButton
          onClick={() => onEditorModeChange(editorMode === "draw" ? "view" : "draw")}
          icon={Plus}
          variant={editorMode === "draw" ? "active" : "default"}
          title={t("addBbox")}
        />
      )}

      {/* Segment mode controls - only show when in segment mode */}
      {editorMode === "segment" && (
        <>
          {divider}

          {/* SAM status badge */}
          <SAMStatusBadge
            status={samEmbeddingStatus}
            t={t}
            onComputeEmbedding={onComputeEmbedding}
          />

          {/* Click point count */}
          {clickPointCount > 0 && (
            <span className="text-xs text-text-muted">
              {t("clickPoints", { count: clickPointCount })}
            </span>
          )}

          {/* Undo last click */}
          <ToolbarIconButton
            onClick={onUndoClick}
            disabled={!hasClickPoints}
            icon={RotateCcw}
            title={t("undoClick")}
          />

          {/* Clear segmentation */}
          <ToolbarIconButton
            onClick={onClearSegmentation}
            disabled={!hasClickPoints}
            icon={Trash2}
            variant="danger"
            title={t("clearSegmentation")}
          />

          {/* Save mask */}
          <ToolbarIconButton
            onClick={onSaveMask}
            disabled={!hasPreviewPolygon || isSavingMask}
            icon={Save}
            variant="primary"
            isLoading={isSavingMask}
            title={t("saveMask")}
          />
        </>
      )}

      {/* Undo (for bbox operations, hidden in segment mode) */}
      {editorMode !== "segment" && (
        <ToolbarIconButton
          onClick={onUndo}
          disabled={!canUndo || isUndoing}
          icon={Undo2}
          isLoading={isUndoing}
          title={t("shortcuts.undo")}
        />
      )}
    </motion.div>
  );
}
