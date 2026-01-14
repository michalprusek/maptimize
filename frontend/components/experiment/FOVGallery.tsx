"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, FOVImage } from "@/lib/api";
import { ConfirmModal, MicroscopyImage } from "@/components/ui";
import {
  Loader2,
  Trash2,
  Search,
  ImageIcon,
  Layers,
  Check,
  AlertCircle,
} from "lucide-react";

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
}

export function FOVGallery({
  experimentId,
  filteredFovs,
  fovs,
  isLoading,
  onClearFilters,
  selectedIds,
  onToggleSelect,
}: FOVGalleryProps): JSX.Element {
  // Derive hasActiveFilters from props for consistency
  const hasActiveFilters = fovs !== undefined &&
    filteredFovs !== undefined &&
    filteredFovs.length !== fovs.length;

  // Safe access to filteredFovs with fallback
  const displayFovs = filteredFovs ?? [];
  const queryClient = useQueryClient();

  // Delete state
  const [fovToDelete, setFovToDelete] = useState<{ id: number; name: string } | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const deleteFovMutation = useMutation({
    mutationFn: (imageId: number) => api.deleteImage(imageId),
    onSuccess: () => {
      setFovToDelete(null);
      setDeleteError(null);
      queryClient.invalidateQueries({ queryKey: ["fovs", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
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
          No FOV images yet
        </h3>
        <p className="text-text-secondary">
          Upload microscopy images to see them here
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Results count */}
      {hasActiveFilters && fovs && (
        <p className="text-sm text-text-muted">
          Showing {displayFovs.length} of {fovs.length} images
        </p>
      )}

      {/* FOV Grid */}
      {displayFovs.length > 0 ? (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-4">
          {displayFovs.map((fov, i) => (
            <motion.div
              key={fov.id}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: i * 0.01 }}
              className="glass-card group"
            >
              {/* Image preview */}
              <div className="aspect-square bg-bg-secondary flex items-center justify-center relative overflow-hidden rounded-t-xl">
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

                {/* Selection checkbox */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleSelect(fov.id);
                  }}
                  className={`absolute top-2 left-2 w-5 h-5 rounded border-2 flex items-center justify-center transition-all ${
                    selectedIds.has(fov.id)
                      ? "bg-primary-500 border-primary-500"
                      : "border-white/40 bg-black/30 opacity-0 group-hover:opacity-100"
                  }`}
                >
                  {selectedIds.has(fov.id) && (
                    <Check className="w-3 h-3 text-white" />
                  )}
                </button>

                {/* Delete button overlay */}
                <button
                  onClick={() => setFovToDelete({ id: fov.id, name: fov.original_filename })}
                  className="absolute top-2 right-2 p-1.5 bg-bg-primary/80 hover:bg-accent-red/20 text-text-muted hover:text-accent-red rounded-lg opacity-0 group-hover:opacity-100 transition-all duration-200"
                  title="Delete FOV"
                >
                  <Trash2 className="w-4 h-4" />
                </button>

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


                {/* MAP Protein */}
                {fov.map_protein && (
                  <div
                    className="text-xs px-2 py-1 rounded w-fit"
                    style={{
                      backgroundColor: `${fov.map_protein.color}20`,
                      color: fov.map_protein.color,
                    }}
                  >
                    {fov.map_protein.name}
                  </div>
                )}
              </div>
            </motion.div>
          ))}
        </div>
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
    </div>
  );
}
