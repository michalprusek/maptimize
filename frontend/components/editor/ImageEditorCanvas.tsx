"use client";

/**
 * ImageEditorCanvas Component
 *
 * Two-layer canvas for rendering FOV image with bbox overlays.
 * Layer 1: Image with brightness/contrast/LUT filters
 * Layer 2: Bboxes, handles, and selection highlights
 */

import { useRef, useEffect, useCallback, useState, type MouseEvent } from "react";
import type { EditorBbox, EditorState, ImageFilters, Rect } from "@/lib/editor/types";
import { getHandlePositions } from "@/lib/editor/geometry";
import {
  COLORS,
  STROKE_WIDTHS,
  HANDLE_SIZE,
  CANVAS_SETTINGS,
} from "@/lib/editor/constants";
import { getDisplayModeFilter } from "@/lib/editor/display";
import type { DisplayMode } from "@/lib/api";

interface ImageEditorCanvasProps {
  imageUrl: string;
  imageWidth: number;
  imageHeight: number;
  bboxes: EditorBbox[];
  editorState: EditorState;
  filters: ImageFilters;
  displayMode: DisplayMode;
  drawingBbox: Rect | null;
  onMouseDown: (e: MouseEvent<HTMLElement>) => void;
  onMouseMove: (e: MouseEvent<HTMLElement>) => void;
  onMouseUp: (e: MouseEvent<HTMLElement>) => void;
  onMouseLeave: () => void;
  onContextMenu: (e: React.MouseEvent<HTMLCanvasElement>) => void;
  cursor: string;
  containerRef: React.RefObject<HTMLDivElement>;
  /** Callback when image canvas is ready for crop extraction */
  onImageCanvasReady?: (canvas: HTMLCanvasElement) => void;
  /** Callback when image is loaded (for direct crop extraction) */
  onImageLoaded?: (image: HTMLImageElement) => void;
  /** Callback when image fails to load */
  onImageError?: (error: string) => void;
}

/**
 * Draw a rounded rectangle path.
 */
function roundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number
) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

export function ImageEditorCanvas({
  imageUrl,
  imageWidth,
  imageHeight,
  bboxes,
  editorState,
  filters,
  displayMode,
  drawingBbox,
  onMouseDown,
  onMouseMove,
  onMouseUp,
  onMouseLeave,
  onContextMenu,
  cursor,
  containerRef,
  onImageCanvasReady,
  onImageLoaded,
  onImageError,
}: ImageEditorCanvasProps) {
  const imageCanvasRef = useRef<HTMLCanvasElement>(null);
  const overlayCanvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const containerSizeRef = useRef({ width: 0, height: 0 });

  const { zoom, panOffset, selectedBboxId, hoveredBboxId } = editorState;

  // Track if image is loaded or has error
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState<string | null>(null);

  // Store callbacks in refs to avoid re-triggering image load
  const onImageLoadedRef = useRef(onImageLoaded);
  const onImageCanvasReadyRef = useRef(onImageCanvasReady);
  const onImageErrorRef = useRef(onImageError);

  useEffect(() => {
    onImageLoadedRef.current = onImageLoaded;
    onImageCanvasReadyRef.current = onImageCanvasReady;
    onImageErrorRef.current = onImageError;
  }, [onImageLoaded, onImageCanvasReady, onImageError]);

  // Load image
  useEffect(() => {
    setImageLoaded(false);
    setImageError(null);
    const img = new Image();
    // Enable CORS to allow canvas extraction for live preview
    img.crossOrigin = "anonymous";
    img.onload = () => {
      imageRef.current = img;
      setImageLoaded(true);
      setImageError(null);
      // Notify parent that image is ready for crop extraction
      onImageLoadedRef.current?.(img);
    };
    img.onerror = () => {
      console.error("[ImageEditor] Failed to load image:", imageUrl);
      const errorMessage = "Failed to load image";
      setImageError(errorMessage);
      onImageErrorRef.current?.(errorMessage);
    };
    img.src = imageUrl;
  }, [imageUrl]);

  // Render image layer
  const renderImage = useCallback(() => {
    const canvas = imageCanvasRef.current;
    const img = imageRef.current;
    if (!canvas || !img) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Apply transformations
    ctx.save();
    ctx.translate(panOffset.x, panOffset.y);
    ctx.scale(zoom, zoom);

    // Clip to rounded rectangle for image
    const radius = CANVAS_SETTINGS.imageBorderRadius;
    roundedRect(ctx, 0, 0, imageWidth, imageHeight, radius);
    ctx.clip();

    // Apply filters (brightness/contrast + display mode LUT)
    const lutFilter = getDisplayModeFilter(displayMode);
    const brightnessContrast = `brightness(${filters.brightness}%) contrast(${filters.contrast}%)`;
    ctx.filter = lutFilter === "none" ? brightnessContrast : `${brightnessContrast} ${lutFilter}`;

    // Draw image
    ctx.drawImage(img, 0, 0, imageWidth, imageHeight);

    ctx.restore();
  }, [zoom, panOffset, filters, displayMode, imageWidth, imageHeight, imageLoaded]);

  // Render bbox overlays
  const renderBboxes = useCallback(() => {
    const canvas = overlayCanvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Clear overlay
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Apply pan/zoom transform
    ctx.save();
    ctx.translate(panOffset.x, panOffset.y);
    ctx.scale(zoom, zoom);

    // Draw image frame/border
    const frameRadius = CANVAS_SETTINGS.imageBorderRadius;
    roundedRect(ctx, 0, 0, imageWidth, imageHeight, frameRadius);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.15)";
    ctx.lineWidth = 2 / zoom;
    ctx.stroke();

    const bboxRadius = CANVAS_SETTINGS.bboxBorderRadius;

    // Draw each bbox
    bboxes.forEach((bbox) => {
      const isSelected = bbox.id === selectedBboxId;
      const isHovered = bbox.id === hoveredBboxId;

      // Determine colors
      let strokeColor: string = COLORS.bboxDefault;
      let fillColor: string = COLORS.bboxDefaultFill;
      let lineWidth: number = STROKE_WIDTHS.default;

      if (isSelected) {
        strokeColor = COLORS.bboxSelected;
        fillColor = COLORS.bboxSelectedFill;
        lineWidth = STROKE_WIDTHS.selected;
      } else if (isHovered) {
        strokeColor = COLORS.bboxHover;
        fillColor = COLORS.bboxHoverFill;
        lineWidth = STROKE_WIDTHS.hover;
      } else if (bbox.isNew) {
        strokeColor = COLORS.bboxNew;
        fillColor = COLORS.bboxNewFill;
      } else if (bbox.isModified) {
        strokeColor = COLORS.bboxModified;
        fillColor = COLORS.bboxModifiedFill;
      }

      // Draw glow effect for hovered/selected
      if (isHovered || isSelected) {
        ctx.save();
        ctx.shadowColor = isSelected ? COLORS.selectionGlow : COLORS.glowColor;
        ctx.shadowBlur = isSelected ? 15 : 10;
        roundedRect(ctx, bbox.x, bbox.y, bbox.width, bbox.height, bboxRadius);
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = lineWidth / zoom;
        ctx.stroke();
        ctx.restore();
      }

      // Draw filled rounded bbox
      roundedRect(ctx, bbox.x, bbox.y, bbox.width, bbox.height, bboxRadius);
      ctx.fillStyle = fillColor;
      ctx.fill();
      ctx.strokeStyle = strokeColor;
      ctx.lineWidth = lineWidth / zoom;
      ctx.stroke();

      // Draw circular handles for hovered or selected bbox
      if (isHovered || isSelected) {
        drawHandles(ctx, bbox, zoom);
      }
    });

    // Draw drawing preview bbox
    if (drawingBbox) {
      roundedRect(ctx, drawingBbox.x, drawingBbox.y, drawingBbox.width, drawingBbox.height, bboxRadius);
      ctx.fillStyle = COLORS.drawingBbox;
      ctx.fill();
      ctx.strokeStyle = COLORS.drawingBboxStroke;
      ctx.lineWidth = 2 / zoom;
      ctx.setLineDash([5 / zoom, 5 / zoom]);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.restore();
  }, [bboxes, zoom, panOffset, selectedBboxId, hoveredBboxId, drawingBbox, imageWidth, imageHeight]);

  // Draw circular resize handles (corners only)
  function drawHandles(ctx: CanvasRenderingContext2D, bbox: EditorBbox, scale: number): void {
    const handleRadius = (HANDLE_SIZE / 2) / scale;

    const handles = getHandlePositions(bbox, 1);
    // Only corner handles
    const cornerKeys: (keyof typeof handles)[] = ["nw", "ne", "sw", "se"];

    ctx.fillStyle = COLORS.handleFill;
    ctx.strokeStyle = COLORS.handleStroke;
    ctx.lineWidth = 2 / scale;

    cornerKeys.forEach((key) => {
      const pos = handles[key];
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, handleRadius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    });
  };

  // Update canvas size when container resizes (with HiDPI support)
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Set canvas size with device pixel ratio for crisp rendering
    const setCanvasSize = (canvas: HTMLCanvasElement, width: number, height: number) => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.scale(dpr, dpr);
      }
    };

    // Set initial size
    const initSize = () => {
      const rect = container.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        containerSizeRef.current = { width: rect.width, height: rect.height };
        if (imageCanvasRef.current) {
          setCanvasSize(imageCanvasRef.current, rect.width, rect.height);
        }
        if (overlayCanvasRef.current) {
          setCanvasSize(overlayCanvasRef.current, rect.width, rect.height);
        }
        renderImage();
        renderBboxes();
      }
    };

    // Initialize immediately and after a short delay (for layout settling)
    initSize();
    const timer = setTimeout(initSize, 100);

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        const { width, height } = entry.contentRect;
        containerSizeRef.current = { width, height };

        if (imageCanvasRef.current) {
          setCanvasSize(imageCanvasRef.current, width, height);
        }
        if (overlayCanvasRef.current) {
          setCanvasSize(overlayCanvasRef.current, width, height);
        }

        renderImage();
        renderBboxes();
      }
    });

    observer.observe(container);
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [containerRef, renderImage, renderBboxes]);

  // Re-render on state changes
  useEffect(() => {
    renderImage();
  }, [renderImage]);

  useEffect(() => {
    renderBboxes();
  }, [renderBboxes]);

  // Notify parent when image canvas is ready for crop extraction
  useEffect(() => {
    if (imageLoaded && imageCanvasRef.current && onImageCanvasReadyRef.current) {
      onImageCanvasReadyRef.current(imageCanvasRef.current);
    }
  }, [imageLoaded]);

  // Handle wheel event for zoom
  useEffect(() => {
    const canvas = overlayCanvasRef.current;
    if (!canvas) return;

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
    };

    canvas.addEventListener("wheel", handleWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", handleWheel);
  }, []);

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden bg-black"
      style={{ cursor }}
    >
      {/* Image layer */}
      <canvas
        ref={imageCanvasRef}
        className="absolute inset-0"
        style={{ pointerEvents: "none" }}
      />

      {/* Overlay layer (handles mouse events) */}
      <canvas
        ref={overlayCanvasRef}
        className="absolute inset-0"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseLeave}
        onContextMenu={onContextMenu}
      />

      {/* Error overlay */}
      {imageError && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/80">
          <div className="text-center p-6">
            <div className="w-12 h-12 mx-auto mb-4 text-red-400">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
            </div>
            <p className="text-red-400 font-medium">{imageError}</p>
            <p className="text-gray-500 text-sm mt-2 max-w-xs">{imageUrl}</p>
          </div>
        </div>
      )}
    </div>
  );
}
