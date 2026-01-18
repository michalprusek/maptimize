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
 * Editor interaction modes.
 */
export type EditorMode = "view" | "draw" | "edit" | "segment";

// ============================================================================
// Segmentation Types
// ============================================================================

/**
 * Polygon with internal holes.
 *
 * Used for ring-shaped structures (e.g., cell membranes with hollow centers).
 * The outer boundary defines the external edge, and holes define internal
 * regions to be excluded from the mask.
 */
export interface PolygonWithHoles {
  /** Outer boundary points [[x, y], ...] - external edge */
  outer: [number, number][];
  /** List of hole boundaries - each hole is a list of [[x, y], ...] points */
  holes: [number, number][][];
}

/**
 * Type guard to check if polygon data is in the new holes format.
 */
export function isPolygonWithHoles(data: unknown): data is PolygonWithHoles {
  return (
    typeof data === "object" &&
    data !== null &&
    "outer" in data &&
    Array.isArray((data as PolygonWithHoles).outer)
  );
}

/**
 * Normalize any polygon format to the new holes format.
 *
 * Handles:
 * - New format: {outer: [...], holes: [...]} - returned as-is
 * - Legacy format: [[x,y], ...] - converted to {outer: [...], holes: []}
 */
export function normalizePolygonFormat(
  data: [number, number][] | PolygonWithHoles | null | undefined
): PolygonWithHoles {
  if (!data) {
    return { outer: [], holes: [] };
  }

  if (isPolygonWithHoles(data)) {
    return data;
  }

  // Legacy format - simple array of points
  return { outer: data, holes: [] };
}

/**
 * Click point for SAM segmentation.
 * Left click = positive (foreground), Right click = negative (background)
 */
export interface SegmentClickPoint {
  /** X coordinate in image pixels */
  x: number;
  /** Y coordinate in image pixels */
  y: number;
  /** 1 = foreground (include), 0 = background (exclude) */
  label: 1 | 0;
}

/**
 * A pending polygon waiting to be saved.
 * Accumulated from point or text segmentation before committing.
 */
export interface PendingPolygon {
  /** Unique ID for this pending polygon */
  id: string;
  /** Polygon points [[x, y], ...] - outer boundary (legacy format) */
  points: [number, number][];
  /** Full polygon with holes support (new format) */
  polygonWithHoles?: PolygonWithHoles;
  /** Confidence/IoU score */
  score: number;
  /** Source of this polygon */
  source: "point" | "text";
  /** Color index for rendering */
  colorIndex: number;
}

/**
 * Segmentation state for the editor.
 */
export interface SegmentationState {
  /** Current click points for active segmentation */
  clickPoints: SegmentClickPoint[];
  /** Preview polygon from SAM inference (live update) - outer boundary only */
  previewPolygon: [number, number][] | null;
  /** Preview polygon with holes support (new format) */
  previewPolygonWithHoles: PolygonWithHoles | null;
  /** IoU score of current preview (confidence) */
  previewIoU: number | null;
  /** Whether segmentation API is loading */
  isLoading: boolean;
  /** Error message if segmentation failed */
  error: string | null;
  /** Target crop ID for saving the mask */
  targetCropId: number | null;
  /** Accumulated pending polygons before save */
  pendingPolygons: PendingPolygon[];
}

/**
 * Saved polygon for a cell crop.
 */
export interface CellPolygon {
  /** Cell crop database ID */
  cropId: number;
  /** Polygon points [[x, y], ...] - outer boundary (legacy format) */
  points: [number, number][];
  /** Full polygon with holes support (new format) */
  polygonWithHoles?: PolygonWithHoles;
  /** SAM IoU prediction score */
  iouScore: number;
}

/**
 * SAM embedding status for an image.
 */
export type SAMEmbeddingStatus =
  | "not_started"
  | "pending"
  | "computing"
  | "ready"
  | "error";

/**
 * Initial segmentation state.
 */
export const INITIAL_SEGMENTATION_STATE: SegmentationState = {
  clickPoints: [],
  previewPolygon: null,
  previewPolygonWithHoles: null,
  previewIoU: null,
  isLoading: false,
  error: null,
  targetCropId: null,
  pendingPolygons: [],
};

// ============================================================================
// SAM 3 Text Segmentation Types
// ============================================================================

/**
 * Segmentation prompt mode - Point or Text.
 * Text mode requires SAM 3 (CUDA GPU).
 */
export type SegmentPromptMode = "point" | "text";

/**
 * A single detected instance from text-based segmentation.
 */
export interface DetectedInstance {
  /** Instance index (0-based) */
  index: number;
  /** Polygon points [[x, y], ...] */
  polygon: [number, number][];
  /** Bounding box [x1, y1, x2, y2] */
  bbox: [number, number, number, number];
  /** Confidence score (0-1) */
  score: number;
  /** Area in pixels */
  areaPixels: number;
}

/**
 * Text segmentation state for SAM 3.
 */
export interface TextSegmentationState {
  /** Current text prompt */
  textPrompt: string;
  /** Detected instances from text query */
  detectedInstances: DetectedInstance[];
  /** Selected instance index for refinement */
  selectedInstanceIndex: number | null;
  /** Whether text query is loading */
  isQuerying: boolean;
  /** Error message from text query */
  error: string | null;
}

/**
 * Initial text segmentation state.
 */
export const INITIAL_TEXT_SEGMENTATION_STATE: TextSegmentationState = {
  textPrompt: "",
  detectedInstances: [],
  selectedInstanceIndex: null,
  isQuerying: false,
  error: null,
};

/**
 * SAM capabilities returned by the backend.
 */
export interface SegmentationCapabilities {
  /** Current compute device */
  device: "cuda" | "mps" | "cpu" | "unknown";
  /** SAM variant in use */
  variant: "mobilesam" | "sam3" | "unknown";
  /** Whether text prompting is available */
  supportsTextPrompts: boolean;
  /** Human-readable model name */
  modelName: string;
  /** Error message if capabilities check failed */
  loadError?: string;
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
  /** Whether Shift key is pressed (for pan/undo in segment mode) */
  isShiftPressed: boolean;
  /** Current zoom level (1 = 100%) */
  zoom: number;
  /** Pan offset */
  panOffset: { x: number; y: number };
  /** Whether in "add mask" mode within segment mode (clicking adds points) */
  isSegmentAddMode: boolean;
}

/**
 * Context menu state for mask operations.
 */
export interface MaskContextMenuState {
  isOpen: boolean;
  position: { x: number; y: number } | null;
  targetMaskIndex: number | null;
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
