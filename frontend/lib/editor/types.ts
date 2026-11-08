/**
 * Image Editor Types
 *
 * Type definitions for the bbox editor component and related functionality.
 */

import type { CellCropGallery } from "@/lib/api";

/**
 * Bounding box representation in the editor.
 */
export interface EditorBbox {
  /** Unique ID - number for existing crops, string (temp-id) for new */
  id: number | string;
  /** Bbox X coordinate (left) of the axis-aligned rectangle (before rotation) */
  x: number;
  /** Bbox Y coordinate (top) of the axis-aligned rectangle (before rotation) */
  y: number;
  /** Bbox width */
  width: number;
  /** Bbox height */
  height: number;
  /** Rotation in degrees about the box centre (0 = axis-aligned) */
  angle: number;
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
    angle: number;
  };
}

/**
 * Handle positions for bbox resize handles.
 * Type defines 8 positions but only 4 corner handles (nw, ne, sw, se) are currently rendered.
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
 * Editor interaction modes. The type is derived from this list so the two cannot
 * drift: a mode persisted to localStorage is validated against it (isEditorMode),
 * and a value from a mode that no longer exists falls back instead of reaching state.
 * There is no separate edit mode: "view" selects, moves, resizes and rotates boxes,
 * and "draw" adds new ones.
 */
export const EDITOR_MODES = ["view", "draw"] as const;
export type EditorMode = (typeof EDITOR_MODES)[number];

export function isEditorMode(value: unknown): value is EditorMode {
  return typeof value === "string" && (EDITOR_MODES as readonly string[]).includes(value);
}

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
 * Single undo action using discriminated union.
 * Each action type has specific required fields.
 */
export type UndoAction =
  | { type: "create"; bboxId: string | number; newState: EditorBbox }
  | { type: "update"; bboxId: string | number; previousState: EditorBbox; newState: EditorBbox }
  | { type: "delete"; bboxId: string | number; previousState: EditorBbox };

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
  /**
   * Rotation in degrees about the rect centre (0 = axis-aligned).
   *
   * ⚠️ REQUIRED, deliberately. While it was optional, TypeScript's spread typing
   * computed `{...bbox, ...rect}.angle` as `number` even when the Rect genuinely
   * omitted it, so a producer that forgot the field wrote `undefined` into a
   * required slot with no error — and two hand-rolled geometry restores silently
   * kept a stale angle instead. Making it required turns that whole class into a
   * compile error. Use `?? 0` only at the API boundary, where NULL legitimately
   * arrives from the nullable column.
   */
  angle: number;
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
  const angle = crop.bbox_angle ?? 0;
  return {
    id: crop.id,
    x: crop.bbox_x,
    y: crop.bbox_y,
    width: crop.bbox_w,
    height: crop.bbox_h,
    angle,
    cropId: crop.id,
    isNew: false,
    isModified: false,
    original: {
      x: crop.bbox_x,
      y: crop.bbox_y,
      width: crop.bbox_w,
      height: crop.bbox_h,
      angle,
    },
  };
}

/**
 * `bbox` with its geometry taken from `from` — used to build an undo snapshot and
 * to reset a box to its original coordinates.
 *
 * ⚠️ EVERY geometry field must come from `from`, never from `bbox`, and that is
 * why this exists as one function instead of a spread at each call site. The
 * interaction hooks write each intermediate value onto the live bbox on every
 * mousemove, so by the time a drag completes `bbox` already holds the NEW
 * geometry; spreading it and overriding only some fields silently keeps the new
 * value for the rest. `angle` was missed in both places that did this by hand:
 * undoing a rotation sent the new angle back and re-cut the crop for nothing, and
 * "reset to original" restored position and size but left the box rotated. `tsc`
 * cannot catch either, because `angle` is required on EditorBbox and the spread
 * satisfies the type whether or not it is overridden.
 */
export function withGeometryFrom(bbox: EditorBbox, from: Rect): EditorBbox {
  return {
    ...bbox,
    x: from.x,
    y: from.y,
    width: from.width,
    height: from.height,
    angle: from.angle ?? 0,
  };
}

/**
 * Generate a temporary ID for new bboxes.
 */
export function generateTempId(): string {
  return `temp-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
}
