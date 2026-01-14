/**
 * Display Mode Utilities
 *
 * SSOT for display mode filter calculations.
 * Used by ImageEditorCanvas and ImageEditorCropPreview.
 */

import type { DisplayMode } from "@/lib/api";

/**
 * Get CSS filter string for display mode.
 * Maps DisplayMode to corresponding CSS filter values.
 */
export function getDisplayModeFilter(mode: DisplayMode): string {
  switch (mode) {
    case "inverted":
      return "invert(1)";
    case "green":
      return "sepia(1) saturate(5) hue-rotate(70deg) brightness(0.9)";
    case "fire":
      return "sepia(1) saturate(10) hue-rotate(-10deg) brightness(1.1) contrast(1.1)";
    default:
      return "none";
  }
}
