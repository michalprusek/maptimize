"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, FOVImage } from "@/lib/api";
import {
  staggerContainerVariants,
  staggerItemVariants,
  cardHoverProps,
} from "@/lib/animations";
import { ConfirmModal, MicroscopyImage, Pagination } from "@/components/ui";
import { SelectionCheckbox, DeleteOverlayButton } from "@/components/shared";
import {
  Loader2,
  Search,
  ImageIcon,
  Layers,
  AlertCircle,
  RefreshCw,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

interface FOVGalleryProps {
  experimentId: number;
  /** Filtered FOVs to display (after search/filter). Should be subset of `fovs`. */
  filteredFovs: FOVImage[] | undefined;
  /** All FOVs before filtering. Used to show "X of Y" count. */
  fovs: FOVImage[] | undefined;
  isLoading: boolean;
  onClearFilters: () => void;
  selectedIds: Set<number>;
  onToggleSelect: (id: number) => void;
  /** Callback when clicking on FOV image to open editor */
  onFovClick?: (fov: FOVImage) => void;
}

export function FOVGallery({
  experimentId,
  filteredFovs,
  fovs,
  isLoading,
  onClearFilters,
  selectedIds,
  onToggleSelect,
  onFovClick,
}: FOVGalleryProps): JSX.Element {
  const t = useTranslations("images");

  // Derive hasActiveFilters from props for consistency
  const hasActiveFilters = fovs !== undefined &&
    filteredFovs !== undefined &&
    filteredFovs.length !== fovs.length;

  // Safe access to filteredFovs with fallback
  const allFilteredFovs = filteredFovs ?? [];

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage, setItemsPerPage] = useState(48);

  // Calculate paginated items
  const totalPages = Math.ceil(allFilteredFovs.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const displayFovs = allFilteredFovs.slice(startIndex, startIndex + itemsPerPage);

  // Reset to first page when filters change
  const handlePageChange = (page: number) => {
    setCurrentPage(page);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handlePageSizeChange = (size: number) => {
    setItemsPerPage(size);
    setCurrentPage(1);
  };

  // Reset to page 1 when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [filteredFovs?.length]);

  const queryClient = useQueryClient();

  // Helper to invalidate experiment-related queries (DRY)
  const invalidateExperimentQueries = () => {
    queryClient.invalidateQueries({ queryKey: ["fovs", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
  };

  // Delete state
  const [fovToDelete, setFovToDelete] = useState<{ id: number; name: string } | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Re-detect state
  const [showRedetectConfirm, setShowRedetectConfirm] = useState(false);

  const batchRedetectMutation = useMutation({
    mutationFn: (imageIds: number[]) => api.batchRedetect(imageIds),
    onSuccess: () => {
      setShowRedetectConfirm(false);
      invalidateExperimentQueries();
    },
    onError: (err: Error) => {
      console.error("Failed to re-detect:", err);
    },
  });

  const deleteFovMutation = useMutation({
    mutationFn: (imageId: number) => api.deleteImage(imageId),
    onSuccess: () => {
      setFovToDelete(null);
      setDeleteError(null);
      invalidateExperimentQueries();
    },
    onError: (err: Error) => {
      console.error("Failed to delete FOV:", err);
      setDeleteError(err.message || "Failed to delete FOV. Please try again.");
    },
  });

  if (isLoading) {
    return (
      <div className="glass-card p-8 flex justify-center">
        <Loader2 className="w-8 h-8 text-primary-500 animate-spin" />
      </div>
    );
  }

  if (!fovs || fovs.length === 0) {
    return (
      <div className="glass-card p-12 text-center">
        <ImageIcon className="w-12 h-12 text-text-muted mx-auto mb-4" />
        <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
          {t("noImages")}
        </h3>
        <p className="text-text-secondary mb-6">
          {t("uploadFirst")}
        </p>
        <Link href={`/dashboard/experiments/${experimentId}/upload`}>
          <button className="btn-primary inline-flex items-center gap-2">
            <Upload className="w-4 h-4" />
            {t("uploadImages")}
          </button>
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Batch actions bar when items are selected */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-4 p-3 bg-primary-500/10 border border-primary-500/20 rounded-lg">
          <span className="text-sm text-primary-400">
            {selectedIds.size} selected
          </span>

          {/* Re-detect button */}
          <button
            onClick={() => setShowRedetectConfirm(true)}
            disabled={batchRedetectMutation.isPending}
            className="flex items-center gap-2 px-3 py-1.5 bg-bg-secondary border border-white/10 rounded-lg hover:bg-bg-hover transition-colors text-sm disabled:opacity-50"
            title={t("redetect")}
          >
            {batchRedetectMutation.isPending ? (
              <Loader2 className="w-4 h-4 text-text-muted animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4 text-text-muted" />
            )}
            <span className="text-text-secondary">{t("redetect")}</span>
          </button>
        </div>
      )}

      {/* Results count */}
      {hasActiveFilters && fovs && (
        <p className="text-sm text-text-muted">
          Showing {displayFovs.length} of {fovs.length} images
        </p>
      )}

      {/* FOV Grid */}
      {displayFovs.length > 0 ? (
        <>
        <motion.div
          className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-4"
          variants={staggerContainerVariants}
          initial="hidden"
          animate="visible"
          key={currentPage} // Re-trigger animation on page change
        >
          <AnimatePresence mode="popLayout">
          {displayFovs.map((fov) => (
            <motion.div
              key={fov.id}
              variants={staggerItemVariants}
              exit={{ opacity: 0, scale: 0.9, transition: { duration: 0.2 } }}
              layout
              className="glass-card group"
              {...cardHoverProps}
            >
              {/* Image preview */}
              <div
                className={`aspect-square bg-bg-secondary flex items-center justify-center relative overflow-hidden rounded-t-xl ${
                  onFovClick ? "cursor-pointer" : ""
                }`}
                onClick={() => onFovClick?.(fov)}
              >
                {fov.thumbnail_url ? (
                  <MicroscopyImage
                    src={api.getImageUrl(fov.id, "thumbnail")}
                    alt={fov.original_filename}
                    className="w-full h-full object-cover"
                    onError={(e) => {
                      console.warn(`[FOVGallery] Thumbnail load failed for image: ${fov.original_filename}`, e.type);
                      e.currentTarget.style.display = "none";
                      e.currentTarget.nextElementSibling?.classList.remove("hidden");
                    }}
                  />
                ) : (
                  <ImageIcon className="w-10 h-10 text-text-muted" />
                )}
                <ImageIcon className="w-10 h-10 text-text-muted hidden" />

                <SelectionCheckbox
                  isSelected={selectedIds.has(fov.id)}
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleSelect(fov.id);
                  }}
                />
                <DeleteOverlayButton
                  onClick={(e) => {
                    e.stopPropagation();
                    setFovToDelete({ id: fov.id, name: fov.original_filename });
                  }}
                  title="Delete FOV"
                />

                {/* Cell count badge */}
                {fov.cell_count > 0 && (
                  <div className="absolute bottom-2 left-2 flex items-center gap-1 px-2 py-1 bg-bg-primary/80 rounded-md">
                    <Layers className="w-3 h-3 text-primary-400" />
                    <span className="text-xs text-text-primary font-medium">
                      {fov.cell_count}
                    </span>
                  </div>
                )}
              </div>

              {/* Info */}
              <div className="p-3 space-y-2">
                {/* Filename */}
                <p
                  className="text-sm text-text-primary truncate font-medium"
                  title={fov.original_filename}
                >
                  {fov.original_filename}
                </p>

                {/* Dimensions */}
                {fov.width && fov.height && (
                  <div className="text-xs text-text-muted">
                    {fov.width}×{fov.height}
                  </div>
                )}


                {/* MAP Protein badge (inherited from experiment) */}
                {fov.map_protein && (
                  <div
                    className="flex items-center gap-1.5 text-xs px-2 py-1 rounded w-fit"
                    style={{
                      backgroundColor: `${fov.map_protein.color}20`,
                      color: fov.map_protein.color,
                    }}
                  >
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: fov.map_protein.color }} />
                    {fov.map_protein.name}
                  </div>
                )}
              </div>
            </motion.div>
          ))}
          </AnimatePresence>
        </motion.div>

        {/* Pagination */}
        <Pagination
          currentPage={currentPage}
          totalPages={totalPages}
          onPageChange={handlePageChange}
          totalItems={allFilteredFovs.length}
          itemsPerPage={itemsPerPage}
          showPageSizeSelector
          onPageSizeChange={handlePageSizeChange}
        />
        </>
      ) : hasActiveFilters ? (
        <div className="glass-card p-12 text-center">
          <Search className="w-12 h-12 text-text-muted mx-auto mb-4" />
          <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
            No images match your filters
          </h3>
          <p className="text-text-secondary mb-4">
            Try adjusting your search or filter criteria
          </p>
          <button
            onClick={onClearFilters}
            className="btn-primary"
          >
            Clear Filters
          </button>
        </div>
      ) : null}

      {/* Delete error message */}
      {deleteError && (
        <div className="fixed bottom-4 right-4 z-50 p-4 bg-accent-red/10 border border-accent-red/20 rounded-lg shadow-lg max-w-md">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-accent-red flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-accent-red text-sm font-medium">Delete failed</p>
              <p className="text-accent-red/80 text-sm mt-1">{deleteError}</p>
            </div>
            <button
              onClick={() => setDeleteError(null)}
              className="text-accent-red/60 hover:text-accent-red ml-auto"
            >
              ×
            </button>
          </div>
        </div>
      )}

      {/* Delete confirmation modal */}
      <ConfirmModal
        isOpen={!!fovToDelete}
        onClose={() => {
          setFovToDelete(null);
          setDeleteError(null);
        }}
        onConfirm={() => fovToDelete && deleteFovMutation.mutate(fovToDelete.id)}
        title="Delete FOV Image"
        message={`Are you sure you want to delete "${fovToDelete?.name}"? This will also delete all detected cells from this image. This action cannot be undone.`}
        confirmLabel="Delete"
        isLoading={deleteFovMutation.isPending}
        variant="danger"
      />

      {/* Re-detect confirmation modal */}
      <ConfirmModal
        isOpen={showRedetectConfirm}
        onClose={() => setShowRedetectConfirm(false)}
        onConfirm={() => batchRedetectMutation.mutate(Array.from(selectedIds))}
        title={t("redetectConfirmTitle")}
        message={t("redetectConfirmMessage", { count: selectedIds.size })}
        confirmLabel={t("redetect")}
        isLoading={batchRedetectMutation.isPending}
        variant="warning"
      />
    </div>
  );
}
