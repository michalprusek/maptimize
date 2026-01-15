"use client";

import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";
import { api, FOVImage, MapProtein } from "@/lib/api";
import { ConfirmModal, MicroscopyImage } from "@/components/ui";
import { SelectionCheckbox, DeleteOverlayButton } from "@/components/shared";
import {
  Loader2,
  Search,
  ImageIcon,
  Layers,
  AlertCircle,
  Dna,
  ChevronDown,
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
  /** Callback when clicking on FOV image to open editor */
  onFovClick?: (fov: FOVImage) => void;
  /** Available proteins for selection */
  proteins?: MapProtein[];
  /** Callback when protein is changed for a single FOV */
  onProteinChange?: (imageId: number, proteinId: number | null) => void;
  /** Callback when protein is changed for multiple FOVs */
  onBatchProteinChange?: (imageIds: number[], proteinId: number | null) => void;
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
  proteins,
  onProteinChange,
  onBatchProteinChange,
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

  // Protein dropdown state
  const [openProteinDropdown, setOpenProteinDropdown] = useState<number | null>(null);
  const [batchProteinDropdownOpen, setBatchProteinDropdownOpen] = useState(false);

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
      {/* Batch actions bar when items are selected */}
      {selectedIds.size > 0 && proteins && onBatchProteinChange && (
        <div className="flex items-center gap-4 p-3 bg-primary-500/10 border border-primary-500/20 rounded-lg">
          <span className="text-sm text-primary-400">
            {selectedIds.size} selected
          </span>
          <div className="relative">
            <button
              onClick={() => setBatchProteinDropdownOpen(!batchProteinDropdownOpen)}
              className="flex items-center gap-2 px-3 py-1.5 bg-bg-secondary border border-white/10 rounded-lg hover:bg-bg-hover transition-colors text-sm"
            >
              <Dna className="w-4 h-4 text-text-muted" />
              <span className="text-text-secondary">Set protein</span>
              <ChevronDown className={`w-3 h-3 text-text-muted transition-transform ${batchProteinDropdownOpen ? "rotate-180" : ""}`} />
            </button>
            {batchProteinDropdownOpen && (
              <div className="absolute top-full mt-1 left-0 z-50 min-w-[180px] bg-bg-elevated border border-white/10 rounded-lg shadow-xl overflow-hidden">
                <button
                  onClick={() => {
                    onBatchProteinChange(Array.from(selectedIds), null);
                    setBatchProteinDropdownOpen(false);
                  }}
                  className="w-full px-3 py-2 text-left hover:bg-white/5 transition-colors text-sm text-text-muted"
                >
                  No protein
                </button>
                {proteins.map((protein) => (
                  <button
                    key={protein.id}
                    onClick={() => {
                      onBatchProteinChange(Array.from(selectedIds), protein.id);
                      setBatchProteinDropdownOpen(false);
                    }}
                    className="w-full px-3 py-2 text-left hover:bg-white/5 transition-colors flex items-center gap-2 text-sm"
                  >
                    <span
                      className="w-3 h-3 rounded-full flex-shrink-0"
                      style={{ backgroundColor: protein.color || "#888" }}
                    />
                    <span className="text-text-primary">{protein.name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
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


                {/* MAP Protein Selector */}
                {proteins && onProteinChange ? (
                  <div className="relative">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpenProteinDropdown(openProteinDropdown === fov.id ? null : fov.id);
                      }}
                      className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded transition-colors ${
                        fov.map_protein
                          ? ""
                          : "bg-white/5 hover:bg-white/10 text-text-muted"
                      }`}
                      style={fov.map_protein ? {
                        backgroundColor: `${fov.map_protein.color}20`,
                        color: fov.map_protein.color,
                      } : undefined}
                    >
                      {fov.map_protein ? (
                        <>
                          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: fov.map_protein.color }} />
                          {fov.map_protein.name}
                        </>
                      ) : (
                        <>
                          <Dna className="w-3 h-3" />
                          <span>Set protein</span>
                        </>
                      )}
                      <ChevronDown className="w-3 h-3 ml-0.5" />
                    </button>
                    {openProteinDropdown === fov.id && (
                      <div className="absolute top-full mt-1 left-0 z-50 min-w-[150px] bg-bg-elevated border border-white/10 rounded-lg shadow-xl overflow-hidden">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onProteinChange(fov.id, null);
                            setOpenProteinDropdown(null);
                          }}
                          className={`w-full px-3 py-1.5 text-left hover:bg-white/5 transition-colors text-xs ${
                            !fov.map_protein ? "bg-primary-500/10" : ""
                          }`}
                        >
                          <span className="text-text-muted">No protein</span>
                        </button>
                        {proteins.map((protein) => (
                          <button
                            key={protein.id}
                            onClick={(e) => {
                              e.stopPropagation();
                              onProteinChange(fov.id, protein.id);
                              setOpenProteinDropdown(null);
                            }}
                            className={`w-full px-3 py-1.5 text-left hover:bg-white/5 transition-colors flex items-center gap-2 text-xs ${
                              fov.map_protein?.id === protein.id ? "bg-primary-500/10" : ""
                            }`}
                          >
                            <span
                              className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                              style={{ backgroundColor: protein.color || "#888" }}
                            />
                            <span className="text-text-primary">{protein.name}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ) : fov.map_protein ? (
                  <div
                    className="text-xs px-2 py-1 rounded w-fit"
                    style={{
                      backgroundColor: `${fov.map_protein.color}20`,
                      color: fov.map_protein.color,
                    }}
                  >
                    {fov.map_protein.name}
                  </div>
                ) : null}
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
