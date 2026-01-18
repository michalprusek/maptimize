"use client";

/**
 * SegmentationOverlay Component
 *
 * SVG overlay for the image editor canvas that renders:
 * - Click points (green + for foreground, red - for background)
 * - Preview polygon (dashed green during active segmentation)
 * - Pending polygons (accumulated before save, various colors)
 * - Existing FOV mask (semi-transparent blue)
 * - Saved polygons for crops (solid blue in segmentation mode)
 *
 * Supports polygons with holes using SVG fill-rule="evenodd" for ring-shaped structures.
 */

import { useMemo, useCallback } from "react";
import type {
  SegmentClickPoint,
  CellPolygon,
  PendingPolygon,
  PolygonWithHoles,
} from "@/lib/editor/types";
import {
  calculateObjectCoverTransform,
  buildPolygonSvgPath,
} from "@/lib/editor/geometry";

// Colors for pending polygons
const PENDING_COLORS = [
  { fill: "rgba(34, 197, 94, 0.25)", stroke: "#22c55e" },   // Green
  { fill: "rgba(59, 130, 246, 0.25)", stroke: "#3b82f6" },  // Blue
  { fill: "rgba(168, 85, 247, 0.25)", stroke: "#a855f7" },  // Purple
  { fill: "rgba(249, 115, 22, 0.25)", stroke: "#f97316" },  // Orange
  { fill: "rgba(236, 72, 153, 0.25)", stroke: "#ec4899" },  // Pink
  { fill: "rgba(6, 182, 212, 0.25)", stroke: "#06b6d4" },   // Cyan
];

interface SegmentationOverlayProps {
  /** Current click points for active segmentation */
  clickPoints: SegmentClickPoint[];
  /** Preview polygon from SAM inference (outer boundary only - legacy) */
  previewPolygon: [number, number][] | null;
  /** Preview polygon with holes support (new format) */
  previewPolygonWithHoles?: PolygonWithHoles | null;
  /** Pending polygons accumulated before save */
  pendingPolygons?: PendingPolygon[];
  /** Existing FOV mask polygons (multiple separate instances) */
  fovMaskPolygons?: [number, number][][] | null;
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
  /** FOV mask opacity (0-1, default 0.3) */
  maskOpacity?: number;
  /** Selected FOV mask polygon index for deletion */
  selectedMaskIndex?: number | null;
  /** Hovered FOV mask polygon index for highlight */
  hoveredMaskIndex?: number | null;
  /** Callback when FOV mask polygon is clicked */
  onMaskClick?: (index: number, e: React.MouseEvent) => void;
  /** Callback when FOV mask polygon is hovered */
  onMaskHover?: (index: number | null) => void;
}

export function SegmentationOverlay({
  clickPoints,
  previewPolygon,
  previewPolygonWithHoles,
  pendingPolygons = [],
  fovMaskPolygons,
  savedPolygons,
  zoom,
  panOffset,
  isActive,
  isLoading = false,
  containerWidth = 0,
  containerHeight = 0,
  maskOpacity = 0.3,
  selectedMaskIndex = null,
  hoveredMaskIndex = null,
  onMaskClick,
  onMaskHover,
}: SegmentationOverlayProps) {
  // Convert image coordinates to canvas coordinates
  const toCanvas = useCallback((x: number, y: number) => ({
    x: x * zoom + panOffset.x,
    y: y * zoom + panOffset.y,
  }), [zoom, panOffset.x, panOffset.y]);

  // Build SVG path from polygon points - memoized for proper dependency tracking
  const buildPath = useCallback((points: [number, number][]) => {
    if (points.length < 3) return "";
    return (
      points
        .map((p, i) => {
          const { x, y } = toCanvas(p[0], p[1]);
          return `${i === 0 ? "M" : "L"} ${x} ${y}`;
        })
        .join(" ") + " Z"
    );
  }, [toCanvas]);

  // Build SVG path from polygon with holes - uses evenodd fill rule
  const buildPathWithHoles = useCallback((polygon: PolygonWithHoles) => {
    const paths: string[] = [];

    // Outer boundary
    if (polygon.outer.length >= 3) {
      paths.push(buildPath(polygon.outer));
    }

    // Holes (each hole creates a cutout with evenodd fill rule)
    for (const hole of polygon.holes) {
      if (hole.length >= 3) {
        paths.push(buildPath(hole));
      }
    }

    return paths.join(" ");
  }, [buildPath]);

  // DRY helper: Build path from polygon data, supporting both formats
  const buildPolygonPathFromData = useCallback(
    (points: [number, number][], polygonWithHoles?: PolygonWithHoles) => {
      if (polygonWithHoles && polygonWithHoles.holes.length > 0) {
        return { path: buildPathWithHoles(polygonWithHoles), hasHoles: true };
      }
      return { path: buildPath(points), hasHoles: false };
    },
    [buildPath, buildPathWithHoles]
  );

  // Memoize saved polygon paths - supports both legacy and hole formats
  const savedPolygonPaths = useMemo(() => {
    return savedPolygons.map((poly) => {
      const { path, hasHoles } = buildPolygonPathFromData(poly.points, poly.polygonWithHoles);
      return {
        cropId: poly.cropId,
        path,
        iouScore: poly.iouScore,
        hasHoles,
      };
    });
  }, [savedPolygons, buildPolygonPathFromData]);

  // Memoize pending polygon paths - supports both legacy and hole formats
  const pendingPolygonPaths = useMemo(() => {
    return pendingPolygons.map((poly) => {
      const { path, hasHoles } = buildPolygonPathFromData(poly.points, poly.polygonWithHoles);
      return {
        id: poly.id,
        path,
        colorIndex: poly.colorIndex,
        source: poly.source,
        hasHoles,
      };
    });
  }, [pendingPolygons, buildPolygonPathFromData]);

  // Memoize FOV mask paths (multiple polygons)
  const fovMaskPaths = useMemo(() => {
    if (!fovMaskPolygons || fovMaskPolygons.length === 0) return [];
    return fovMaskPolygons
      .filter(poly => poly && poly.length >= 3)
      .map(poly => buildPath(poly));
  }, [fovMaskPolygons, buildPath]);

  // Build preview polygon path - supports holes
  const { previewPath, previewHasHoles } = useMemo(() => {
    // Prefer new holes format if available
    if (previewPolygonWithHoles && previewPolygonWithHoles.outer.length >= 3) {
      return {
        previewPath: buildPathWithHoles(previewPolygonWithHoles),
        previewHasHoles: previewPolygonWithHoles.holes.length > 0,
      };
    }

    // Fall back to legacy format
    if (!previewPolygon || previewPolygon.length < 3) {
      return { previewPath: null, previewHasHoles: false };
    }
    return { previewPath: buildPath(previewPolygon), previewHasHoles: false };
  }, [previewPolygon, previewPolygonWithHoles, buildPath, buildPathWithHoles]);

  // Check if we have any FOV masks to show
  const hasFovMasks = fovMaskPolygons && fovMaskPolygons.length > 0;

  // Don't render if no content at all
  if (!isActive && savedPolygons.length === 0 && pendingPolygons.length === 0 && !hasFovMasks) {
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

      {/* Existing FOV masks - always visible, clickable for deletion */}
      {fovMaskPaths.map((path, index) => {
        if (!path) return null;
        const isSelected = selectedMaskIndex === index;
        const isHovered = hoveredMaskIndex === index;
        // Determine stroke color: red for selected/hovered (deletion preview), blue for default
        const strokeColor = isSelected || isHovered ? "#ef4444" : "rgba(59, 130, 246, 0.6)";
        const strokeW = isSelected ? 2.5 : isHovered ? 2 : 1.5;
        return (
          <path
            key={`fov-mask-${index}`}
            d={path}
            fill={`rgba(59, 130, 246, ${maskOpacity})`}
            stroke={strokeColor}
            strokeWidth={strokeW}
            strokeDasharray={isActive ? undefined : "8 4"}
            className={`${onMaskClick ? "cursor-pointer" : ""} transition-colors`}
            style={{ pointerEvents: isActive && onMaskClick ? "auto" : "none" }}
            onMouseEnter={() => onMaskHover?.(index)}
            onMouseLeave={() => onMaskHover?.(null)}
            onClick={(e) => onMaskClick?.(index, e)}
            onContextMenu={(e) => {
              e.preventDefault();
              onMaskClick?.(index, e);
            }}
          />
        );
      })}

      {/* Saved polygons - only show in segment mode */}
      {isActive &&
        savedPolygonPaths.map(({ cropId, path, hasHoles }) =>
          path ? (
            <path
              key={`saved-${cropId}`}
              d={path}
              fill="rgba(59, 130, 246, 0.15)"
              stroke="rgba(59, 130, 246, 0.6)"
              strokeWidth={1.5}
              fillRule={hasHoles ? "evenodd" : undefined}
            />
          ) : null
        )}

      {/* Pending polygons - accumulated before save */}
      {isActive &&
        pendingPolygonPaths.map(({ id, path, colorIndex, hasHoles }) => {
          if (!path) return null;
          const colors = PENDING_COLORS[colorIndex % PENDING_COLORS.length];
          return (
            <path
              key={`pending-${id}`}
              d={path}
              fill={colors.fill}
              stroke={colors.stroke}
              strokeWidth={2}
              fillRule={hasHoles ? "evenodd" : undefined}
            />
          );
        })}

      {/* Preview polygon - dashed green */}
      {isActive && previewPath && (
        <g>
          {/* Fill - use evenodd for holes */}
          <path
            d={previewPath}
            fill="rgba(34, 197, 94, 0.25)"
            stroke="none"
            fillRule={previewHasHoles ? "evenodd" : undefined}
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
  const transform = calculateObjectCoverTransform(bbox.width, bbox.height, thumbnailSize);
  const scaledPath = buildPolygonSvgPath(
    polygon,
    transform,
    thumbnailSize,
    { x: bbox.x, y: bbox.y }
  );

  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none overflow-hidden"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
    >
      <path
        d={scaledPath}
        fill="rgba(59, 130, 246, 0.2)"
        stroke="rgba(59, 130, 246, 0.8)"
        strokeWidth={0.5}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
