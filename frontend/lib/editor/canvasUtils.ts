/**
 * Canvas utility functions for the image editor.
 * Handles crop extraction and canvas manipulation.
 */

/**
 * Extract a crop region directly from an image element.
 * This avoids CORS/tainted canvas issues by drawing directly from the image.
 *
 * @param image - The source image element
 * @param bbox - Bounding box in image coordinates
 * @returns Data URL of the cropped region, or null on failure
 */
export function extractCropFromImage(
  image: HTMLImageElement,
  bbox: { x: number; y: number; width: number; height: number }
): string | null {
  // Validate bbox dimensions
  if (bbox.width <= 0 || bbox.height <= 0) return null;

  // Create offscreen canvas for the crop
  const cropCanvas = document.createElement("canvas");
  cropCanvas.width = Math.max(1, Math.round(bbox.width));
  cropCanvas.height = Math.max(1, Math.round(bbox.height));
  const cropCtx = cropCanvas.getContext("2d");
  if (!cropCtx) return null;

  // Draw the crop region directly from the image
  try {
    cropCtx.drawImage(
      image,
      bbox.x,
      bbox.y,
      bbox.width,
      bbox.height, // Source (image coords)
      0,
      0,
      cropCanvas.width,
      cropCanvas.height // Dest
    );

    return cropCanvas.toDataURL("image/png");
  } catch (error) {
    console.error("[canvasUtils] Failed to extract crop from image:", error);
    return null;
  }
}

/**
 * Extract a crop region from canvas as data URL.
 * Works with original image coordinates (not screen coordinates).
 * Note: This may fail if canvas is tainted (CORS). Use extractCropFromImage instead.
 *
 * @param canvas - The source canvas element
 * @param bbox - Bounding box in image coordinates
 * @param zoom - Current zoom level
 * @param panOffset - Current pan offset
 * @returns Data URL of the cropped region, or null on failure
 */
export function extractCropFromCanvas(
  canvas: HTMLCanvasElement,
  bbox: { x: number; y: number; width: number; height: number },
  zoom: number,
  panOffset: { x: number; y: number }
): string | null {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  // Validate bbox dimensions
  if (bbox.width <= 0 || bbox.height <= 0) return null;

  // Create offscreen canvas for the crop (at original resolution)
  const cropCanvas = document.createElement("canvas");
  cropCanvas.width = Math.max(1, Math.round(bbox.width));
  cropCanvas.height = Math.max(1, Math.round(bbox.height));
  const cropCtx = cropCanvas.getContext("2d");
  if (!cropCtx) return null;

  // Calculate screen coordinates of bbox
  const screenX = bbox.x * zoom + panOffset.x;
  const screenY = bbox.y * zoom + panOffset.y;
  const screenWidth = bbox.width * zoom;
  const screenHeight = bbox.height * zoom;

  // Extract region and scale back to original size
  try {
    cropCtx.drawImage(
      canvas,
      screenX,
      screenY,
      screenWidth,
      screenHeight, // Source (screen coords)
      0,
      0,
      cropCanvas.width,
      cropCanvas.height // Dest (original size)
    );

    return cropCanvas.toDataURL("image/png");
  } catch (error) {
    // Canvas might be tainted (CORS) or other error
    console.error("[canvasUtils] Failed to extract crop from canvas:", error);
    return null;
  }
}
