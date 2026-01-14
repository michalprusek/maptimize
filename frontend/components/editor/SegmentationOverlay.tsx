"use client";

/**
 * SegmentationOverlay Component
 *
 * SVG overlay for the image editor canvas that renders:
 * - Click points (green + for foreground, red - for background)
 * - Preview polygon (dashed green during active segmentation)
 * - Saved polygons for crops (solid blue in segmentation mode)
 */

import { useMemo } from "react";
import type { SegmentClickPoint, CellPolygon } from "@/lib/editor/types";

interface SegmentationOverlayProps {
  /** Current click points for active segmentation */
  clickPoints: SegmentClickPoint[];
  /** Preview polygon from SAM inference */
  previewPolygon: [number, number][] | null;
  /** Saved polygons for all crops (shown in segment mode) */
  savedPolygons: CellPolygon[];
  /** Current zoom level */
  zoom: number;
  /** Pan offset */
  panOffset: { x: number; y: number };
  /** Whether segmentation mode is active */
  isActive: boolean;
  /** Whether API is currently loading */
  isLoading?: boolean;
  /** Container dimensions for SVG sizing */
  containerWidth?: number;
  containerHeight?: number;
}

export function SegmentationOverlay({
  clickPoints,
  previewPolygon,
  savedPolygons,
  zoom,
  panOffset,
  isActive,
  isLoading = false,
  containerWidth = 0,
  containerHeight = 0,
}: SegmentationOverlayProps) {
  // Convert image coordinates to canvas coordinates
  const toCanvas = (x: number, y: number) => ({
    x: x * zoom + panOffset.x,
    y: y * zoom + panOffset.y,
  });

  // Build SVG path from polygon points
  const buildPath = (points: [number, number][]) => {
    if (points.length < 3) return "";
    return (
      points
        .map((p, i) => {
          const { x, y } = toCanvas(p[0], p[1]);
          return `${i === 0 ? "M" : "L"} ${x} ${y}`;
        })
        .join(" ") + " Z"
    );
  };

  // Memoize saved polygon paths
  const savedPolygonPaths = useMemo(() => {
    return savedPolygons.map((poly) => ({
      cropId: poly.cropId,
      path: buildPath(poly.points),
      iouScore: poly.iouScore,
    }));
  }, [savedPolygons, zoom, panOffset.x, panOffset.y]);

  // Build preview polygon path
  const previewPath = useMemo(() => {
    if (!previewPolygon || previewPolygon.length < 3) return null;
    return buildPath(previewPolygon);
  }, [previewPolygon, zoom, panOffset.x, panOffset.y]);

  // Don't render if no content
  if (!isActive && savedPolygons.length === 0) {
    return null;
  }

  return (
    <svg
      className="absolute inset-0 pointer-events-none"
      width={containerWidth || "100%"}
      height={containerHeight || "100%"}
      style={{ overflow: "visible" }}
    >
      <defs>
        {/* Reserved for future patterns/gradients */}
      </defs>

      {/* Saved polygons - only show in segment mode */}
      {isActive &&
        savedPolygonPaths.map(({ cropId, path }) =>
          path ? (
            <path
              key={`saved-${cropId}`}
              d={path}
              fill="rgba(59, 130, 246, 0.15)"
              stroke="rgba(59, 130, 246, 0.6)"
              strokeWidth={1.5}
            />
          ) : null
        )}

      {/* Preview polygon - dashed green */}
      {isActive && previewPath && (
        <g>
          {/* Fill */}
          <path
            d={previewPath}
            fill="rgba(34, 197, 94, 0.25)"
            stroke="none"
          />
          {/* Dashed outline */}
          <path
            d={previewPath}
            fill="none"
            stroke="#22c55e"
            strokeWidth={2}
            strokeDasharray="6 4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* Loading indicator pulse */}
          {isLoading && (
            <path
              d={previewPath}
              fill="none"
              stroke="#22c55e"
              strokeWidth={3}
              strokeOpacity={0.5}
              className="animate-pulse"
            />
          )}
        </g>
      )}

      {/* Click points - scale with zoom (smaller when zoomed out) */}
      {isActive &&
        (() => {
          // Scale markers: smaller when zoomed out, normal at 100%+
          // At 10% zoom: scale ~0.5, at 100%+: scale 1.0
          const markerScale = Math.max(0.5, Math.min(1, Math.sqrt(zoom)));
          const radius = 12 * markerScale;
          const symbolSize = 5 * markerScale;
          const strokeW = Math.max(1.5, 2 * markerScale);
          const fontSize = Math.max(9, 11 * markerScale);
          const labelOffset = 14 * markerScale + 2;

          return clickPoints.map((point, index) => {
            const { x, y } = toCanvas(point.x, point.y);
            const isPositive = point.label === 1;
            const color = isPositive ? "#22c55e" : "#ef4444";

            return (
              <g key={`click-${index}`}>
                {/* Outer ring */}
                <circle
                  cx={x}
                  cy={y}
                  r={radius}
                  fill="rgba(0, 0, 0, 0.6)"
                  stroke={color}
                  strokeWidth={strokeW}
                />
                {/* Plus/Minus symbol */}
                {isPositive ? (
                  // Plus sign
                  <>
                    <line
                      x1={x - symbolSize}
                      y1={y}
                      x2={x + symbolSize}
                      y2={y}
                      stroke="white"
                      strokeWidth={strokeW}
                      strokeLinecap="round"
                    />
                    <line
                      x1={x}
                      y1={y - symbolSize}
                      x2={x}
                      y2={y + symbolSize}
                      stroke="white"
                      strokeWidth={strokeW}
                      strokeLinecap="round"
                    />
                  </>
                ) : (
                  // Minus sign
                  <line
                    x1={x - symbolSize}
                    y1={y}
                    x2={x + symbolSize}
                    y2={y}
                    stroke="white"
                    strokeWidth={strokeW}
                    strokeLinecap="round"
                  />
                )}
                {/* Point number label */}
                <text
                  x={x + labelOffset}
                  y={y + fontSize * 0.35}
                  fontSize={fontSize}
                  fill={color}
                  fontWeight="600"
                  fontFamily="system-ui, sans-serif"
                  style={{ textShadow: "0 1px 2px rgba(0,0,0,0.8)" }}
                >
                  {index + 1}
                </text>
              </g>
            );
          });
        })()}

      {/* Loading spinner in center when loading with no preview yet */}
      {isActive && isLoading && !previewPath && clickPoints.length > 0 && (
        <g>
          {(() => {
            const lastPoint = clickPoints[clickPoints.length - 1];
            const { x, y } = toCanvas(lastPoint.x, lastPoint.y);
            const spinnerScale = Math.max(0.5, Math.min(1, Math.sqrt(zoom)));
            const spinnerR = 20 * spinnerScale;
            return (
              <circle
                cx={x}
                cy={y}
                r={spinnerR}
                fill="none"
                stroke="rgba(34, 197, 94, 0.5)"
                strokeWidth={2 * spinnerScale}
                strokeDasharray={`${10 * spinnerScale} ${10 * spinnerScale}`}
                className="animate-spin"
                style={{ transformOrigin: `${x}px ${y}px` }}
              />
            );
          })()}
        </g>
      )}
    </svg>
  );
}

/**
 * Small polygon overlay for crop preview thumbnails.
 * Shows the segmentation polygon scaled to thumbnail size.
 */
interface CropPolygonOverlayProps {
  /** Polygon points in original image coordinates */
  polygon: [number, number][];
  /** Crop bbox in original image coordinates */
  bbox: { x: number; y: number; width: number; height: number };
  /** Thumbnail size */
  thumbnailSize: number;
}

export function CropPolygonOverlay({
  polygon,
  bbox,
  thumbnailSize,
}: CropPolygonOverlayProps) {
  // Scale factor from original to thumbnail
  const scale = thumbnailSize / Math.max(bbox.width, bbox.height);

  // Offset for centering (if bbox is not square)
  const offsetX = (thumbnailSize - bbox.width * scale) / 2;
  const offsetY = (thumbnailSize - bbox.height * scale) / 2;

  // Convert polygon points to thumbnail coordinates
  const path = polygon
    .map((p, i) => {
      // Transform from image coords to bbox-relative coords, then scale
      const x = (p[0] - bbox.x) * scale + offsetX;
      const y = (p[1] - bbox.y) * scale + offsetY;
      return `${i === 0 ? "M" : "L"} ${x} ${y}`;
    })
    .join(" ") + " Z";

  return (
    <svg
      className="absolute inset-0 pointer-events-none"
      width={thumbnailSize}
      height={thumbnailSize}
    >
      <path
        d={path}
        fill="rgba(59, 130, 246, 0.2)"
        stroke="rgba(59, 130, 246, 0.8)"
        strokeWidth={1}
      />
    </svg>
  );
}
