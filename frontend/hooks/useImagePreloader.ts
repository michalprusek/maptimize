import { useEffect, useRef } from "react";
import { api } from "@/lib/api";

interface PreloadableImage {
  id: number;
}

/**
 * Preloads neighboring images for smooth navigation in the editor.
 *
 * Uses browser's Image() constructor to load images into memory cache.
 * When the user navigates, the preloaded images render instantly.
 *
 * @param currentIndex - Current image index in the sorted array
 * @param images - Array of images with id property
 * @param bufferSize - Number of images to preload in each direction (default: 2)
 * @param imageType - Type of image to preload: "mip" for editor, "thumbnail" for gallery
 */
export function useImagePreloader(
  currentIndex: number,
  images: PreloadableImage[],
  bufferSize = 2,
  imageType: "mip" | "thumbnail" = "mip"
): void {
  const preloadedRef = useRef<Set<number>>(new Set());

  useEffect(() => {
    if (!images.length || currentIndex < 0) return;

    const preloadImage = (imageId: number): void => {
      if (preloadedRef.current.has(imageId)) return;

      const img = new Image();
      img.src = api.getImageUrl(imageId, imageType);
      preloadedRef.current.add(imageId);
    };

    // Preload next N images (higher priority - user likely navigating forward)
    for (let i = 1; i <= bufferSize; i++) {
      const nextIndex = currentIndex + i;
      if (nextIndex < images.length) {
        preloadImage(images[nextIndex].id);
      }
    }

    // Preload previous N images
    for (let i = 1; i <= bufferSize; i++) {
      const prevIndex = currentIndex - i;
      if (prevIndex >= 0) {
        preloadImage(images[prevIndex].id);
      }
    }
  }, [currentIndex, images, bufferSize, imageType]);

  // Reset preloaded set when images array changes (e.g., different experiment)
  useEffect(() => {
    const imageIds = new Set(images.map((img) => img.id));

    // Remove stale entries no longer in current image set
    preloadedRef.current.forEach((id) => {
      if (!imageIds.has(id)) {
        preloadedRef.current.delete(id);
      }
    });
  }, [images]);
}
