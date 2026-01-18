/**
 * Shared UMAP chart configuration (SSOT for chart styling)
 *
 * These constants define the visual styling for UMAP scatter plots
 * across all visualization components.
 */

// Default color for points without protein assignment
export const DEFAULT_POINT_COLOR = "#888888";

// Shared axis styling for UMAP charts
export const UMAP_AXIS_STYLE = {
  tick: { fill: "#5a7285", fontSize: 10 },
  axisLine: { stroke: "#2a3a4a" },
  tickLine: { stroke: "#2a3a4a" },
} as const;

// Axis domain with padding
export const UMAP_AXIS_DOMAIN = ["dataMin - 1", "dataMax + 1"] as [string, string];

// Format axis tick values to integers
export function formatAxisTick(value: number): string {
  return Math.round(value).toString();
}

// Tooltip cursor styling
export const UMAP_TOOLTIP_CURSOR = {
  strokeDasharray: "3 3",
  stroke: "#5a7285",
} as const;

// Scatter plot animation settings
export const UMAP_SCATTER_ANIMATION = {
  isAnimationActive: true,
  animationDuration: 300,
  animationEasing: "ease-out",
} as const;
