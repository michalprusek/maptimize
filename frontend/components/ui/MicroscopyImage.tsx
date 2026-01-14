"use client";

/**
 * MicroscopyImage - A specialized image component for microscopy images.
 *
 * Applies LUT (Lookup Table) display modes based on user settings:
 * - grayscale: Default white-on-black display
 * - inverted: Black-on-white (good for printing)
 * - green: GFP-style green fluorescence
 * - fire: Heat-map style (black -> red -> yellow -> white)
 * - hilo: Highlights under/over-exposed pixels (blue/red)
 *
 * For most modes, CSS filters are used for performance.
 * HiLo mode uses canvas-based rendering for pixel-level analysis.
 */

import { useEffect, useRef, useState, useMemo } from "react";
import { useSettingsStore, DisplayMode } from "@/stores/settingsStore";
import { clsx } from "clsx";

interface MicroscopyImageProps extends React.ImgHTMLAttributes<HTMLImageElement> {
  src: string;
  alt: string;
  /** Threshold below which pixels are considered under-exposed (for HiLo) */
  hiloLowThreshold?: number;
  /** Threshold above which pixels are considered over-exposed (for HiLo) */
  hiloHighThreshold?: number;
}

const lutClasses: Record<DisplayMode, string> = {
  grayscale: "lut-grayscale",
  inverted: "lut-inverted",
  green: "lut-green",
  fire: "lut-fire",
  hilo: "lut-hilo",
};

/**
 * Apply HiLo LUT to an image using canvas.
 * Under-exposed pixels (< lowThreshold) are shown in blue.
 * Over-exposed pixels (> highThreshold) are shown in red.
 * Mid-range pixels are shown in grayscale.
 */
function applyHiLoLUT(
  canvas: HTMLCanvasElement,
  image: HTMLImageElement,
  lowThreshold: number = 10,
  highThreshold: number = 245
): void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  canvas.width = image.naturalWidth || image.width;
  canvas.height = image.naturalHeight || image.height;

  ctx.drawImage(image, 0, 0);

  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const data = imageData.data;

  for (let i = 0; i < data.length; i += 4) {
    // Calculate luminance from RGB
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const luminance = 0.299 * r + 0.587 * g + 0.114 * b;

    if (luminance < lowThreshold) {
      // Under-exposed: show in blue
      data[i] = 0;      // R
      data[i + 1] = 0;  // G
      data[i + 2] = 255; // B
    } else if (luminance > highThreshold) {
      // Over-exposed: show in red
      data[i] = 255;    // R
      data[i + 1] = 0;  // G
      data[i + 2] = 0;  // B
    } else {
      // Mid-range: keep as grayscale
      const gray = Math.round(luminance);
      data[i] = gray;     // R
      data[i + 1] = gray; // G
      data[i + 2] = gray; // B
    }
    // Alpha channel (data[i + 3]) remains unchanged
  }

  ctx.putImageData(imageData, 0, 0);
}

export function MicroscopyImage({
  src,
  alt,
  className,
  hiloLowThreshold = 10,
  hiloHighThreshold = 245,
  ...props
}: MicroscopyImageProps): JSX.Element {
  const displayMode = useSettingsStore((state) => state.displayMode);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [canvasReady, setCanvasReady] = useState(false);

  // Determine if we need canvas-based rendering
  const useCanvas = displayMode === "hilo";

  // Handle image load for HiLo mode
  useEffect(() => {
    if (!useCanvas || !imageLoaded || !canvasRef.current || !imageRef.current) {
      setCanvasReady(false);
      return;
    }

    applyHiLoLUT(
      canvasRef.current,
      imageRef.current,
      hiloLowThreshold,
      hiloHighThreshold
    );
    setCanvasReady(true);
  }, [useCanvas, imageLoaded, src, hiloLowThreshold, hiloHighThreshold]);

  // Reset canvas ready state when switching away from HiLo
  useEffect(() => {
    if (!useCanvas) {
      setCanvasReady(false);
    }
  }, [useCanvas]);

  const handleImageLoad = () => {
    setImageLoaded(true);
  };

  const handleImageError = (e: React.SyntheticEvent<HTMLImageElement>) => {
    setImageLoaded(false);
    setCanvasReady(false);
    // Call original onError if provided
    if (props.onError) {
      props.onError(e);
    }
  };

  // Memoize class computation
  const imageClass = useMemo(() => {
    return clsx(className, !useCanvas && lutClasses[displayMode]);
  }, [className, useCanvas, displayMode]);

  return (
    <>
      {/* Hidden image for HiLo canvas processing */}
      {useCanvas && (
        <img
          ref={imageRef}
          src={src}
          alt={alt}
          onLoad={handleImageLoad}
          onError={handleImageError}
          className="hidden"
          crossOrigin="anonymous"
          {...props}
        />
      )}

      {/* Canvas for HiLo rendering */}
      {useCanvas && (
        <canvas
          ref={canvasRef}
          className={clsx(
            className,
            !canvasReady && "opacity-0"
          )}
          style={{
            display: canvasReady ? "block" : "none",
          }}
        />
      )}

      {/* Regular image with CSS filter (non-HiLo modes or HiLo fallback) */}
      {!useCanvas && (
        <img
          src={src}
          alt={alt}
          data-microscopy-image
          className={imageClass}
          loading="lazy"
          {...props}
        />
      )}

      {/* Loading placeholder for HiLo mode while canvas renders */}
      {useCanvas && !canvasReady && !imageLoaded && (
        <div
          className={clsx(
            className,
            "animate-pulse bg-bg-secondary"
          )}
        />
      )}
    </>
  );
}
