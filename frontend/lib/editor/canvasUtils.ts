/**
 * Canvas utility functions for the image editor.
 * Handles crop extraction and canvas manipulation.
 */

/**
 * Extract a crop region directly from an image element, DE-ROTATED when
 * `bbox.angle` is set, so the cell appears upright exactly as it will once the
 * backend re-cuts it.
 *
 * This avoids CORS/tainted canvas issues by drawing directly from the image.
 *
 * @param image - The source image element
 * @param bbox - Bounding box in image coordinates; `angle` is degrees about its centre
 * @returns Data URL of the cropped region, or null on failure
 */
export function extractCropFromImage(
  image: HTMLImageElement,
  bbox: { x: number; y: number; width: number; height: number; angle?: number }
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
    const angle = bbox.angle ?? 0;
    if (angle) {
      // De-rotate: rotate the source about the box centre so the rotated box
      // becomes upright, then the w×h canvas keeps only that region. The GEOMETRY
      // mirrors the backend (crop_editor_service.extract_crop_from_projection).
      // ⚠️ The PIXELS are not identical: the backend resamples with a cubic spline
      // (order=3, clipped to the source range -- chosen by measurement because
      // bilinear cost ~40% of the crop's Laplacian variance and that loss is a
      // confounder for the DINOv3 embedding and the UMAP built on it), whereas
      // the canvas only has
      // the browser's bilinear-ish smoothing. The backend also fills out-of-frame
      // with black and re-stretches intensity over the crop's own percentiles,
      // whereas the canvas leaves out-of-frame transparent and draws bytes that are
      // already normalised. Expect a visible difference near a FOV edge -- the
      // preview shows the framing, not the tones.
      const cx = bbox.x + bbox.width / 2;
      const cy = bbox.y + bbox.height / 2;
      cropCtx.save();
      cropCtx.translate(cropCanvas.width / 2, cropCanvas.height / 2);
      cropCtx.rotate((-angle * Math.PI) / 180);
      cropCtx.translate(-cx, -cy);
      cropCtx.drawImage(image, 0, 0);
      cropCtx.restore();
    } else {
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
    }

    return cropCanvas.toDataURL("image/png");
  } catch (error) {
    console.error("[canvasUtils] Failed to extract crop from image:", error);
    return null;
  }
}
