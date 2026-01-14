"use client";

/**
 * MicroscopyImage - A specialized image component for microscopy images.
 *
 * Applies LUT (Lookup Table) display modes based on user settings:
 * - grayscale: Default white-on-black display
 * - inverted: Black-on-white (good for printing)
 * - green: GFP-style green fluorescence
 * - fire: Heat-map style (black -> red -> yellow -> white)
 *
 * All modes use CSS filters for performance.
 */

import { useMemo } from "react";
import { useSettingsStore, DisplayMode } from "@/stores/settingsStore";
import { clsx } from "clsx";

interface MicroscopyImageProps extends React.ImgHTMLAttributes<HTMLImageElement> {
  src: string;
  alt: string;
}

const lutClasses: Record<DisplayMode, string> = {
  grayscale: "lut-grayscale",
  inverted: "lut-inverted",
  green: "lut-green",
  fire: "lut-fire",
};

export function MicroscopyImage({
  src,
  alt,
  className,
  ...props
}: MicroscopyImageProps): JSX.Element {
  const displayMode = useSettingsStore((state) => state.displayMode);

  // Memoize class computation
  const imageClass = useMemo(() => {
    return clsx(className, lutClasses[displayMode]);
  }, [className, displayMode]);

  return (
    <img
      src={src}
      alt={alt}
      data-microscopy-image
      className={imageClass}
      loading="lazy"
      {...props}
    />
  );
}
