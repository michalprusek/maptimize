"use client";

/**
 * ImagePreviewModal Component
 *
 * Full-screen modal for viewing chat images with navigation.
 * Supports:
 * - Keyboard navigation (Esc, ←, →)
 * - Thumbnail strip for multiple images
 * - Download button (works with all image types including cross-origin)
 * - Click outside to close
 * - LUT display mode for microscopy images
 */

import { useEffect, useCallback, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { X, ChevronLeft, ChevronRight, Download, Loader2, Check } from "lucide-react";
import { clsx } from "clsx";
import type { ChatImage } from "@/stores/chatStore";
import { useSettingsStore, LUT_CLASSES } from "@/stores/settingsStore";
import { processImageUrl } from "@/lib/utils";

interface ImagePreviewModalProps {
  images: ChatImage[];
  currentIndex: number;
  isOpen: boolean;
  onClose: () => void;
  onNavigate: (index: number) => void;
}

export function ImagePreviewModal({
  images,
  currentIndex,
  isOpen,
  onClose,
  onNavigate,
}: ImagePreviewModalProps) {
  const t = useTranslations("chat");
  const displayMode = useSettingsStore((state) => state.displayMode);
  const currentImage = images[currentIndex];
  const hasMultiple = images.length > 1;

  // Download state: idle | downloading | done | error
  const [downloadState, setDownloadState] = useState<"idle" | "downloading" | "done" | "error">("idle");

  // Process current image URL
  const processedCurrent = useMemo(() => {
    if (!currentImage) return null;
    return processImageUrl(currentImage.src);
  }, [currentImage]);

  // Process all image URLs for thumbnails
  const processedImages = useMemo(() => {
    return images.map((img) => ({
      ...img,
      processed: processImageUrl(img.src),
    }));
  }, [images]);

  // Navigate to previous image
  const goToPrevious = useCallback(() => {
    if (currentIndex > 0) {
      onNavigate(currentIndex - 1);
    }
  }, [currentIndex, onNavigate]);

  // Navigate to next image
  const goToNext = useCallback(() => {
    if (currentIndex < images.length - 1) {
      onNavigate(currentIndex + 1);
    }
  }, [currentIndex, images.length, onNavigate]);

  // Keyboard navigation
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      switch (e.key) {
        case "Escape":
          onClose();
          break;
        case "ArrowLeft":
          goToPrevious();
          break;
        case "ArrowRight":
          goToNext();
          break;
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose, goToPrevious, goToNext]);

  // Prevent body scroll when modal is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  // Reset download state when image changes
  useEffect(() => {
    setDownloadState("idle");
  }, [currentIndex]);

  // Handle download with cross-origin support
  const handleDownload = async () => {
    if (!processedCurrent || downloadState === "downloading") return;

    setDownloadState("downloading");

    try {
      // Fetch the image as a blob to handle cross-origin images
      const response = await fetch(processedCurrent.url, {
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.status}`);
      }

      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);

      // Determine file extension from content type or URL
      const contentType = response.headers.get("content-type") || "";
      let extension = "png";
      if (contentType.includes("jpeg") || contentType.includes("jpg")) {
        extension = "jpg";
      } else if (contentType.includes("gif")) {
        extension = "gif";
      } else if (contentType.includes("webp")) {
        extension = "webp";
      } else if (contentType.includes("svg")) {
        extension = "svg";
      }

      // Create download link
      const link = document.createElement("a");
      link.href = blobUrl;
      const filename = currentImage?.alt?.replace(/[^a-zA-Z0-9_-]/g, "_") || `image_${currentIndex + 1}`;
      link.download = `${filename}.${extension}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      // Clean up blob URL
      URL.revokeObjectURL(blobUrl);

      setDownloadState("done");

      // Reset to idle after 2 seconds
      setTimeout(() => setDownloadState("idle"), 2000);
    } catch (error) {
      console.error("Download failed:", error);
      setDownloadState("error");

      // Reset to idle after 2 seconds
      setTimeout(() => setDownloadState("idle"), 2000);
    }
  };

  if (!currentImage || !processedCurrent) return null;

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="fixed inset-0 z-[100] flex flex-col bg-black/95 backdrop-blur-md"
          onClick={onClose}
        >
          {/* Header with close and download buttons */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
            <div className="text-sm text-text-secondary">
              {hasMultiple && (
                <span>
                  {currentIndex + 1} / {images.length}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleDownload();
                }}
                disabled={downloadState === "downloading"}
                className={clsx(
                  "p-2 rounded-lg transition-colors",
                  downloadState === "done"
                    ? "bg-green-500/20 text-green-400"
                    : downloadState === "error"
                      ? "bg-red-500/20 text-red-400"
                      : downloadState === "downloading"
                        ? "bg-white/5 text-text-secondary cursor-wait"
                        : "hover:bg-white/10 text-text-secondary hover:text-text-primary"
                )}
                title={t("downloadImage")}
              >
                {downloadState === "downloading" ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : downloadState === "done" ? (
                  <Check className="w-5 h-5" />
                ) : (
                  <Download className="w-5 h-5" />
                )}
              </button>
              <button
                onClick={onClose}
                className="p-2 rounded-lg hover:bg-white/10 text-text-secondary hover:text-text-primary transition-colors"
                title={t("close")}
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* Main image area */}
          <div
            className="flex-1 flex items-center justify-center relative px-12"
            onClick={onClose}
          >
            {/* Previous button */}
            {hasMultiple && currentIndex > 0 && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  goToPrevious();
                }}
                className={clsx(
                  "absolute left-4 p-3 rounded-full transition-all",
                  "bg-white/10 hover:bg-white/20 text-white",
                  "focus:outline-none focus:ring-2 focus:ring-primary-400"
                )}
                title={t("previousImage")}
              >
                <ChevronLeft className="w-6 h-6" />
              </button>
            )}

            {/* Image */}
            <motion.img
              key={processedCurrent.url}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              transition={{ duration: 0.2 }}
              src={processedCurrent.url}
              alt={currentImage.alt || t("preview")}
              className={clsx(
                "max-h-[calc(100vh-200px)] max-w-full object-contain rounded-lg",
                // Apply LUT only to microscopy images
                processedCurrent.isMicroscopy && LUT_CLASSES[displayMode]
              )}
              onClick={(e) => e.stopPropagation()}
            />

            {/* Next button */}
            {hasMultiple && currentIndex < images.length - 1 && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  goToNext();
                }}
                className={clsx(
                  "absolute right-4 p-3 rounded-full transition-all",
                  "bg-white/10 hover:bg-white/20 text-white",
                  "focus:outline-none focus:ring-2 focus:ring-primary-400"
                )}
                title={t("nextImage")}
              >
                <ChevronRight className="w-6 h-6" />
              </button>
            )}
          </div>

          {/* Caption */}
          {currentImage.alt && (
            <div className="text-center py-2 text-sm text-text-secondary">
              {currentImage.alt}
            </div>
          )}

          {/* Thumbnail strip */}
          {hasMultiple && (
            <div
              className="flex items-center justify-center gap-2 px-4 py-3 border-t border-white/10 overflow-x-auto"
              onClick={(e) => e.stopPropagation()}
            >
              {processedImages.map((image, idx) => (
                <button
                  key={`${image.src}-${idx}`}
                  onClick={() => onNavigate(idx)}
                  className={clsx(
                    "flex-shrink-0 w-16 h-16 rounded-lg overflow-hidden border-2 transition-all",
                    idx === currentIndex
                      ? "border-primary-400 ring-2 ring-primary-400/30"
                      : "border-transparent hover:border-white/30"
                  )}
                >
                  <img
                    src={image.processed.url}
                    alt={image.alt || `Thumbnail ${idx + 1}`}
                    className={clsx(
                      "w-full h-full object-cover",
                      // Apply LUT only to microscopy images
                      image.processed.isMicroscopy && LUT_CLASSES[displayMode]
                    )}
                  />
                </button>
              ))}
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
