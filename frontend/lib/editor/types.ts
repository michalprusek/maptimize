/**
 * Image Editor Types
 *
 * Type definitions for the bbox editor component and related functionality.
 */

import type { CellCropGallery, FOVImage } from "@/lib/api";

/**
 * Bounding box representation in the editor.
 */
export interface EditorBbox {
  /** Unique ID - number for existing crops, string (temp-id) for new */
  id: number | string;
  /** Bbox X coordinate (left) */
  x: number;
  /** Bbox Y coordinate (top) */
  y: number;
  /** Bbox width */
  width: number;
  /** Bbox height */
  height: number;
  /** Reference to CellCrop.id if existing */
  cropId?: number;
  /** True for newly created bboxes (not yet saved) */
  isNew?: boolean;
  /** True if bbox has been modified from original */
  isModified?: boolean;
  /** Original bbox coordinates (for undo) */
  original?: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
}

/**
 * Handle positions for bbox resize handles.
 * 8 handles: 4 corners + 4 midpoints
 */
export type HandlePosition =
  | "nw"
  | "n"
  | "ne"
  | "w"
  | "e"
  | "sw"
  | "s"
  | "se";

/**
 * Editor interaction modes.
 */
export type EditorMode = "view" | "draw" | "edit";

/**
 * Editor state for tracking interactions.
 */
export interface EditorState {
  /** Current interaction mode */
  mode: EditorMode;
  /** ID of the currently selected bbox */
  selectedBboxId: string | number | null;
  /** ID of the currently hovered bbox */
  hoveredBboxId: string | number | null;
  /** Active resize handle (if resizing) */
  activeHandle: HandlePosition | null;
  /** Whether user is currently dragging */
  isDragging: boolean;
  /** Starting position of drag operation */
  dragStart: { x: number; y: number } | null;
  /** Whether Space key is pressed (for panning) */
  isSpacePressed: boolean;
  /** Current zoom level (1 = 100%) */
  zoom: number;
  /** Pan offset */
  panOffset: { x: number; y: number };
}

/**
 * Image filter settings.
 */
export interface ImageFilters {
  /** Brightness (0-400, default 100) */
  brightness: number;
  /** Contrast (0-400, default 100) */
  contrast: number;
}

/**
 * Undo action types for the editor.
 */
export type UndoActionType = "create" | "update" | "delete";

/**
 * Single undo action.
 */
export interface UndoAction {
  type: UndoActionType;
  bboxId: string | number;
  /** Previous state (for update/delete) */
  previousState?: EditorBbox;
  /** New state (for create/update) */
  newState?: EditorBbox;
}

/**
 * Props for the main ImageEditor component.
 */
export interface ImageEditorProps {
  /** FOV image being edited */
  fovImage: FOVImage;
  /** Existing crops for this FOV */
  crops: CellCropGallery[];
  /** Close callback */
  onClose: () => void;
  /** Optional: Focus on specific crop (when opened from crop view) */
  focusCropId?: number;
  /** Callback when bbox is created */
  onBboxCreate?: (bbox: Omit<EditorBbox, "id">) => Promise<number>;
  /** Callback when bbox is updated */
  onBboxUpdate?: (id: number, bbox: Partial<EditorBbox>) => Promise<void>;
  /** Callback when bbox is deleted */
  onBboxDelete?: (id: number) => Promise<void>;
  /** Callback when features should be regenerated */
  onRegenerateFeatures?: (cropId: number) => Promise<void>;
}

/**
 * Point in 2D space.
 */
export interface Point {
  x: number;
  y: number;
}

/**
 * Rectangle bounds.
 */
export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Context menu position and target.
 */
export interface ContextMenuState {
  isOpen: boolean;
  position: Point | null;
  targetBbox: EditorBbox | null;
}

/**
 * Convert CellCropGallery to EditorBbox.
 */
export function cropToEditorBbox(crop: CellCropGallery): EditorBbox {
  return {
    id: crop.id,
    x: crop.bbox_x,
    y: crop.bbox_y,
    width: crop.bbox_w,
    height: crop.bbox_h,
    cropId: crop.id,
    isNew: false,
    isModified: false,
    original: {
      x: crop.bbox_x,
      y: crop.bbox_y,
      width: crop.bbox_w,
      height: crop.bbox_h,
    },
  };
}

/**
 * Generate a temporary ID for new bboxes.
 */
export function generateTempId(): string {
  return `temp-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
}
