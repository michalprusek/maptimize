"use client";

/**
 * Image Editor Page
 *
 * Full-screen editor for viewing and editing FOV images with bbox manipulation.
 * Located outside /dashboard to avoid inheriting the dashboard layout.
 */

import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useCallback } from "react";
import { useTranslations } from "next-intl";
import { AlertCircle } from "lucide-react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { ImageEditorPage } from "@/components/editor/ImageEditorPage";
import { useImagePreloader } from "@/hooks";

interface EditorPageProps {
  params: {
    experimentId: string;
    imageId: string;
  };
}

export default function EditorPage({ params }: EditorPageProps) {
  const { experimentId: expId, imageId } = params;
  const router = useRouter();
  const t = useTranslations("editor");
  const experimentId = parseInt(expId, 10);
  const fovId = parseInt(imageId, 10);

  // Auth check
  const { isAuthenticated, isLoading: authLoading, checkAuth } = useAuthStore();

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    if (!authLoading && !isAuthenticated) {
      router.push("/auth");
    }
  }, [authLoading, isAuthenticated, router]);

  // Fetch all FOV images for the experiment (for navigation)
  const { data: allFovImages = [], isLoading: isLoadingAllFov } = useQuery({
    queryKey: ["experiment-fov-images", experimentId],
    queryFn: () => api.getFOVs(experimentId),
    enabled: !isNaN(experimentId) && isAuthenticated,
  });

  // Fetch FOV image data
  const { data: fovImage, isLoading: isLoadingFov, error: fovError } = useQuery({
    queryKey: ["fov-image", fovId],
    queryFn: () => api.getFovImage(fovId),
    enabled: !isNaN(fovId) && isAuthenticated,
  });

  // Fetch crops for this FOV
  const { data: crops = [], isLoading: isLoadingCrops, refetch: refetchCrops, error: cropsError } = useQuery({
    queryKey: ["fov-crops", fovId],
    queryFn: () => api.getFovCrops(fovId),
    enabled: !isNaN(fovId) && isAuthenticated,
  });

  // Calculate current image index and navigation info
  const imageNavigation = useMemo(() => {
    const sortedImages = [...allFovImages].sort((a, b) => a.id - b.id);
    const currentIndex = sortedImages.findIndex((img) => img.id === fovId);
    return {
      images: sortedImages,
      currentIndex,
      total: sortedImages.length,
      hasPrev: currentIndex > 0,
      hasNext: currentIndex < sortedImages.length - 1,
      prevId: currentIndex > 0 ? sortedImages[currentIndex - 1]?.id : null,
      nextId: currentIndex < sortedImages.length - 1 ? sortedImages[currentIndex + 1]?.id : null,
    };
  }, [allFovImages, fovId]);

  // Preload neighboring images for instant navigation
  // Buffer 2 images in each direction (total 4 preloaded at any time)
  useImagePreloader(imageNavigation.currentIndex, imageNavigation.images, 2, "mip");

  // Navigation handlers
  const handleNavigatePrev = useCallback(() => {
    if (imageNavigation.prevId) {
      router.push(`/editor/${experimentId}/${imageNavigation.prevId}`);
    }
  }, [router, experimentId, imageNavigation.prevId]);

  const handleNavigateNext = useCallback(() => {
    if (imageNavigation.nextId) {
      router.push(`/editor/${experimentId}/${imageNavigation.nextId}`);
    }
  }, [router, experimentId, imageNavigation.nextId]);

  const handleClose = () => {
    router.push(`/dashboard/experiments/${experimentId}`);
  };

  const handleDataChanged = () => {
    refetchCrops();
  };

  if (authLoading || isLoadingFov || isLoadingCrops || isLoadingAllFov) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-primary">
        <div className="w-12 h-12 border-4 border-primary-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  // Handle API errors
  if (fovError || cropsError) {
    const errorMessage = fovError instanceof Error ? fovError.message : cropsError instanceof Error ? cropsError.message : t("loadError");
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-primary">
        <div className="text-center">
          <AlertCircle className="w-12 h-12 text-red-400 mx-auto mb-4" />
          <p className="text-red-400 mb-2">{t("loadError")}</p>
          <p className="text-text-secondary text-sm mb-4">{errorMessage}</p>
          <button
            onClick={() => router.back()}
            className="px-4 py-2 bg-bg-secondary border border-white/10 rounded-lg text-text-primary hover:bg-white/10 transition-colors"
          >
            {t("back")}
          </button>
        </div>
      </div>
    );
  }

  if (!fovImage) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-primary">
        <p className="text-text-secondary">{t("imageNotFound")}</p>
      </div>
    );
  }

  return (
    <ImageEditorPage
      fovImage={fovImage}
      crops={crops}
      experimentId={experimentId}
      onClose={handleClose}
      onDataChanged={handleDataChanged}
      // Image navigation props
      currentImageIndex={imageNavigation.currentIndex}
      totalImages={imageNavigation.total}
      hasPrevImage={imageNavigation.hasPrev}
      hasNextImage={imageNavigation.hasNext}
      onNavigatePrev={handleNavigatePrev}
      onNavigateNext={handleNavigateNext}
    />
  );
}
