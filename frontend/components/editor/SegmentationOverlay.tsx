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
        {/* Dashed stroke pattern for preview */}
        <pattern
          id="dash-pattern"
          patternUnits="userSpaceOnUse"
          width={8 / zoom}
          height={8 / zoom}
        >
          <line
            x1="0"
            y1="0"
            x2={4 / zoom}
            y2="0"
            stroke="#22c55e"
            strokeWidth={2 / zoom}
          />
        </pattern>
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
              strokeWidth={1.5 / zoom}
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
            strokeWidth={2 / zoom}
            strokeDasharray={`${6 / zoom} ${4 / zoom}`}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* Loading indicator pulse */}
          {isLoading && (
            <path
              d={previewPath}
              fill="none"
              stroke="#22c55e"
              strokeWidth={3 / zoom}
              strokeOpacity={0.5}
              className="animate-pulse"
            />
          )}
        </g>
      )}

      {/* Click points */}
      {isActive &&
        clickPoints.map((point, index) => {
          const { x, y } = toCanvas(point.x, point.y);
          const isPositive = point.label === 1;
          const color = isPositive ? "#22c55e" : "#ef4444";

          return (
            <g key={`click-${index}`}>
              {/* Outer ring */}
              <circle
                cx={x}
                cy={y}
                r={10 / zoom}
                fill="rgba(0, 0, 0, 0.5)"
                stroke={color}
                strokeWidth={2 / zoom}
              />
              {/* Inner dot */}
              <circle
                cx={x}
                cy={y}
                r={4 / zoom}
                fill={color}
              />
              {/* Plus/Minus symbol */}
              {isPositive ? (
                // Plus sign
                <>
                  <line
                    x1={x - 5 / zoom}
                    y1={y}
                    x2={x + 5 / zoom}
                    y2={y}
                    stroke="white"
                    strokeWidth={2 / zoom}
                    strokeLinecap="round"
                  />
                  <line
                    x1={x}
                    y1={y - 5 / zoom}
                    x2={x}
                    y2={y + 5 / zoom}
                    stroke="white"
                    strokeWidth={2 / zoom}
                    strokeLinecap="round"
                  />
                </>
              ) : (
                // Minus sign
                <line
                  x1={x - 5 / zoom}
                  y1={y}
                  x2={x + 5 / zoom}
                  y2={y}
                  stroke="white"
                  strokeWidth={2 / zoom}
                  strokeLinecap="round"
                />
              )}
              {/* Point number label */}
              <text
                x={x + 14 / zoom}
                y={y + 4 / zoom}
                fontSize={11 / zoom}
                fill={color}
                fontWeight="600"
                fontFamily="system-ui, sans-serif"
              >
                {index + 1}
              </text>
            </g>
          );
        })}

      {/* Loading spinner in center when loading with no preview yet */}
      {isActive && isLoading && !previewPath && clickPoints.length > 0 && (
        <g>
          {(() => {
            const lastPoint = clickPoints[clickPoints.length - 1];
            const { x, y } = toCanvas(lastPoint.x, lastPoint.y);
            return (
              <circle
                cx={x}
                cy={y}
                r={20 / zoom}
                fill="none"
                stroke="rgba(34, 197, 94, 0.5)"
                strokeWidth={2 / zoom}
                strokeDasharray={`${10 / zoom} ${10 / zoom}`}
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
