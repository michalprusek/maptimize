"use client";

/**
 * ImageEditorCropPreview Component
 *
 * Side panel showing crop previews for all bboxes in the current FOV.
 * Supports clicking to select/focus on a bbox.
 * Shows real-time preview during bbox drag/resize operations.
 */

import { useMemo, useState, useEffect, type RefObject } from "react";
import { useTranslations } from "next-intl";
import { motion } from "framer-motion";
import { Sparkles, Edit2, Hexagon } from "lucide-react";
import type { EditorBbox, Rect, CellPolygon } from "@/lib/editor/types";
import { getDisplayModeFilter } from "@/lib/editor/display";
import { api, type DisplayMode } from "@/lib/api";
import { extractCropFromImage } from "@/lib/editor/canvasUtils";
import { CropPolygonOverlay } from "./SegmentationOverlay";

interface ImageEditorCropPreviewProps {
  bboxes: EditorBbox[];
  selectedBboxId: string | number | null;
  hoveredBboxId: string | number | null;
  onBboxSelect: (id: string | number) => void;
  imageUrl: string;
  /** Reference to the source image for live preview extraction */
  sourceImageRef: RefObject<HTMLImageElement | null>;
  /** ID of bbox being modified (dragged/resized), null if idle */
  modifyingBboxId: string | number | null;
  /** Live bbox rect during modification (updated on every mouse move) */
  liveBboxRect: Rect | null;
  /** Callback when hovering over a preview */
  onBboxHover?: (id: string | number | null) => void;
  /** Current display mode for preview rendering */
  displayMode: DisplayMode;
  /** Saved segmentation polygons for crops */
  savedPolygons?: CellPolygon[];
}

// Thumbnail size for polygon overlay calculation
const THUMBNAIL_SIZE = 256; // w-64 = 16rem = 256px

export function ImageEditorCropPreview({
  bboxes,
  selectedBboxId,
  hoveredBboxId,
  onBboxSelect,
  imageUrl,
  sourceImageRef,
  modifyingBboxId,
  liveBboxRect,
  onBboxHover,
  displayMode,
  savedPolygons = [],
}: ImageEditorCropPreviewProps) {
  const t = useTranslations("editor");

  // Live previews extracted from image during modification
  const [livePreviews, setLivePreviews] = useState<Record<string | number, string>>({});

  // Real-time live preview extraction - directly from source image (no CORS issues)
  useEffect(() => {
    // Stop if not modifying or no image
    if (!modifyingBboxId || !liveBboxRect || !sourceImageRef.current) {
      return;
    }

    // Extract immediately when liveBboxRect changes
    const image = sourceImageRef.current;
    const dataUrl = extractCropFromImage(image, liveBboxRect);
    if (dataUrl) {
      setLivePreviews((prev) => ({ ...prev, [modifyingBboxId]: dataUrl }));
    }
  }, [modifyingBboxId, liveBboxRect, sourceImageRef]);

  // Clear live previews for bboxes that are no longer modified (e.g., after undo)
  useEffect(() => {
    // When not actively modifying, clear previews for unmodified bboxes
    if (!modifyingBboxId) {
      setLivePreviews((prev) => {
        const newPreviews: Record<string | number, string> = {};
        // Only keep previews for bboxes that are still marked as modified
        for (const [id, preview] of Object.entries(prev)) {
          const bbox = bboxes.find((b) => String(b.id) === id || b.id === Number(id));
          if (bbox?.isModified) {
            newPreviews[id] = preview;
          }
        }
        return newPreviews;
      });
    }
  }, [bboxes, modifyingBboxId]);

  // Get preview URL for a bbox (live preview takes priority)
  const getPreviewUrl = (bbox: EditorBbox): string | null => {
    // Priority 1: Live preview during/after modification
    if (livePreviews[bbox.id]) {
      return livePreviews[bbox.id];
    }
    // Priority 2: API image for existing unmodified crops
    if (bbox.cropId && !bbox.isModified) {
      return api.getCropImageUrl(bbox.cropId, "mip");
    }
    return null;
  };

  // Sort bboxes: selected first, then by ID
  const sortedBboxes = useMemo(() => {
    return [...bboxes].sort((a, b) => {
      if (a.id === selectedBboxId) return -1;
      if (b.id === selectedBboxId) return 1;
      return 0;
    });
  }, [bboxes, selectedBboxId]);

  if (bboxes.length === 0) {
    return (
      <aside className="w-64 bg-bg-secondary border-l border-white/5 flex flex-col">
        <div className="p-6 border-b border-white/5">
          <h2 className="text-sm font-medium text-text-primary">{t("cropPreviews")}</h2>
        </div>
        <div className="flex-1 flex items-center justify-center p-4">
          <p className="text-sm text-text-secondary text-center">
            {t("noCells")}
          </p>
        </div>
      </aside>
    );
  }

  return (
    <aside className="w-64 bg-bg-secondary border-l border-white/5 flex flex-col">
      {/* Header */}
      <div className="p-6 border-b border-white/5">
        <h2 className="text-sm font-medium text-text-primary">{t("cropPreviews")}</h2>
        <p className="text-xs text-text-secondary mt-1">
          {t("cellCount", { count: bboxes.length })}
        </p>
      </div>

      {/* Scrollable list */}
      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {sortedBboxes.map((bbox, index) => {
          const isSelected = bbox.id === selectedBboxId;
          const isHovered = bbox.id === hoveredBboxId;
          const isBeingModified = bbox.id === modifyingBboxId;
          const previewUrl = getPreviewUrl(bbox);

          return (
            <motion.button
              key={bbox.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.02 }}
              onClick={() => onBboxSelect(bbox.id)}
              onMouseEnter={() => onBboxHover?.(bbox.id)}
              onMouseLeave={() => onBboxHover?.(null)}
              className={`w-full rounded-xl transition-all duration-200 text-left overflow-hidden ${
                isSelected
                  ? "bg-primary-500/10 ring-1 ring-primary-400"
                  : isHovered
                    ? "bg-yellow-500/10 ring-1 ring-yellow-400"
                    : "hover:bg-white/5"
              } ${isBeingModified ? "ring-2 ring-accent-amber animate-pulse" : ""}`}
            >
              {/* Preview thumbnail */}
              <div className="w-full aspect-square overflow-hidden bg-bg-tertiary relative">
                {previewUrl ? (
                  <img
                    src={previewUrl}
                    alt={`Crop ${bbox.id}`}
                    className="w-full h-full object-cover"
                    style={{
                      filter: getDisplayModeFilter(displayMode),
                    }}
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center">
                    {bbox.isNew ? (
                      <Sparkles className="w-8 h-8 text-accent-amber" />
                    ) : (
                      <Edit2 className="w-8 h-8 text-accent-orange" />
                    )}
                  </div>
                )}
                {/* Polygon overlay for crops with segmentation */}
                {(() => {
                  const polygon = savedPolygons.find(p => p.cropId === bbox.cropId);
                  if (polygon && polygon.points.length >= 3) {
                    return (
                      <CropPolygonOverlay
                        polygon={polygon.points}
                        bbox={{ x: bbox.x, y: bbox.y, width: bbox.width, height: bbox.height }}
                        thumbnailSize={THUMBNAIL_SIZE}
                      />
                    );
                  }
                  return null;
                })()}
              </div>

              {/* Info below thumbnail */}
              <div className="p-2">
                <div className="flex items-center gap-1.5">
                  {bbox.isNew && (
                    <span className="px-1.5 py-0.5 text-[10px] bg-accent-amber/20 text-accent-amber rounded-md font-medium">
                      {t("newCrop")}
                    </span>
                  )}
                  {bbox.isModified && !bbox.isNew && (
                    <span className="px-1.5 py-0.5 text-[10px] bg-accent-orange/20 text-accent-orange rounded-md font-medium">
                      {t("modifiedCrop")}
                    </span>
                  )}
                  {!bbox.isNew && !bbox.isModified && (
                    <span className="text-xs text-text-secondary">
                      {bbox.width}×{bbox.height} px
                    </span>
                  )}
                  {/* Segmentation indicator */}
                  {savedPolygons.some(p => p.cropId === bbox.cropId) && (
                    <span className="flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] bg-emerald-500/20 text-emerald-400 rounded-md font-medium" title="Has segmentation mask">
                      <Hexagon className="w-2.5 h-2.5" />
                    </span>
                  )}
                </div>
              </div>
            </motion.button>
          );
        })}
      </div>
    </aside>
  );
}
