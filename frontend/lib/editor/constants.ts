/**
 * Image Editor Constants
 *
 * SSOT for editor visual constants and configuration.
 */

/**
 * Resize handle size in pixels.
 */
export const HANDLE_SIZE = 10;

/**
 * Hit area padding for handles (makes them easier to click).
 */
export const HANDLE_HIT_PADDING = 4;

/**
 * Minimum bbox dimension in pixels.
 */
export const MIN_BBOX_SIZE = 10;

/**
 * Maximum bbox dimension in pixels.
 */
export const MAX_BBOX_SIZE = 2048;

/**
 * Zoom limits.
 */
export const MIN_ZOOM = 0.1;
export const MAX_ZOOM = 10; // 1000%
export const ZOOM_STEP = 0.1;

/**
 * Colors for bbox rendering.
 */
export const COLORS = {
  /** Default bbox stroke color - red */
  bboxDefault: "#ef4444",
  /** Default bbox fill color - transparent red */
  bboxDefaultFill: "rgba(239, 68, 68, 0.15)",
  /** Hovered bbox stroke color - yellow */
  bboxHover: "#facc15",
  /** Hovered bbox fill color - transparent yellow */
  bboxHoverFill: "rgba(250, 204, 21, 0.2)",
  /** Selected bbox stroke color - bright yellow */
  bboxSelected: "#fbbf24",
  /** Selected bbox fill color - transparent yellow */
  bboxSelectedFill: "rgba(251, 191, 36, 0.25)",
  /** New bbox stroke color - amber */
  bboxNew: "#f59e0b",
  /** New bbox fill color */
  bboxNewFill: "rgba(245, 158, 11, 0.2)",
  /** Modified bbox stroke color - orange */
  bboxModified: "#f97316",
  /** Modified bbox fill color */
  bboxModifiedFill: "rgba(249, 115, 22, 0.2)",
  /** Handle fill color */
  handleFill: "#ffffff",
  /** Handle stroke color */
  handleStroke: "#fbbf24",
  /** Glow color for hover effect */
  glowColor: "rgba(250, 204, 21, 0.5)",
  /** Selection glow color */
  selectionGlow: "rgba(251, 191, 36, 0.6)",
  /** Drawing bbox color */
  drawingBbox: "rgba(250, 204, 21, 0.4)",
  /** Drawing bbox stroke */
  drawingBboxStroke: "#facc15",
} as const;

/**
 * Stroke widths.
 */
export const STROKE_WIDTHS = {
  default: 2,
  hover: 3,
  selected: 3,
} as const;

/**
 * Animation durations in milliseconds.
 */
export const ANIMATION_DURATIONS = {
  hover: 150,
  select: 100,
  fade: 200,
} as const;

/**
 * Undo stack size limit.
 */
export const MAX_UNDO_STACK_SIZE = 50;

/**
 * Default image filter values.
 */
export const DEFAULT_FILTERS = {
  brightness: 100,
  contrast: 100,
} as const;

/**
 * Filter limits.
 */
export const FILTER_LIMITS = {
  /** Minimum filter value (0%) */
  min: 0,
  /** Maximum filter value (400%) */
  max: 400,
  /** Default value (100%) */
  default: 100,
} as const;

/**
 * Keyboard shortcuts.
 */
export const KEYBOARD_SHORTCUTS = {
  close: "Escape",
  delete: ["Delete", "Backspace"],
  undo: "z", // With Ctrl/Cmd
  toggleDrawMode: "n",
  zoomIn: "=",
  zoomOut: "-",
  resetZoom: "0",
} as const;

/**
 * Canvas rendering settings.
 */
export const CANVAS_SETTINGS = {
  /** Target FPS for rendering */
  targetFps: 60,
  /** Debounce time for filter changes (ms) */
  filterDebounce: 50,
  /** Border radius for image corners */
  imageBorderRadius: 16,
  /** Border radius for bbox corners */
  bboxBorderRadius: 8,
} as const;
