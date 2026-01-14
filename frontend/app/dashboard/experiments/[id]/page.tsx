"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, CellCropGallery, FOVImage } from "@/lib/api";
import { ConfirmModal } from "@/components/ui";
import { FOVGallery } from "@/components/experiment";
import {
  ImageGalleryFilters,
  SortOrder,
  SortOption,
  ProteinInfo,
} from "@/components/shared";
import {
  ArrowLeft,
  Upload,
  Loader2,
  Search,
  Trash2,
  Check,
  X,
  AlertCircle,
  Layers,
  ImageIcon,
} from "lucide-react";

type ViewMode = "fovs" | "crops";
type CropSortField = "date" | "bundleness" | "parent" | "confidence";
type FOVSortField = "date" | "filename" | "cells";

const CROP_SORT_OPTIONS: SortOption<CropSortField>[] = [
  { value: "date", label: "Date" },
  { value: "bundleness", label: "Bundleness" },
  { value: "parent", label: "Parent Image" },
  { value: "confidence", label: "Confidence" },
];

const FOV_SORT_OPTIONS: SortOption<FOVSortField>[] = [
  { value: "date", label: "Date" },
  { value: "filename", label: "Filename" },
  { value: "cells", label: "Cell count" },
];

export default function ExperimentDetailPage(): JSX.Element {
  const params = useParams();
  const experimentId = Number(params.id);
  const queryClient = useQueryClient();

  // View mode state
  const [viewMode, setViewMode] = useState<ViewMode>("crops");

  // Shared filter state
  const [searchQuery, setSearchQuery] = useState("");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [proteinFilter, setProteinFilter] = useState<string | null>(null);

  // Separate sort fields for each view (different options available)
  const [cropSortField, setCropSortField] = useState<CropSortField>("date");
  const [fovSortField, setFovSortField] = useState<FOVSortField>("date");

  // Delete state
  const [cropToDelete, setCropToDelete] = useState<{ id: number; name: string } | null>(null);
  // Note: FOV deletion is handled within the FOVGallery component

  // Selection state (separate for each view)
  const [selectedCropIds, setSelectedCropIds] = useState<Set<number>>(new Set());
  const [selectedFovIds, setSelectedFovIds] = useState<Set<number>>(new Set());
  const [showBulkDeleteConfirm, setShowBulkDeleteConfirm] = useState(false);
  const [bulkProteinDropdownOpen, setBulkProteinDropdownOpen] = useState(false);

  // Current view's selected IDs (derived)
  const selectedIds = viewMode === "fovs" ? selectedFovIds : selectedCropIds;
  const setSelectedIds = viewMode === "fovs" ? setSelectedFovIds : setSelectedCropIds;

  const { data: experiment, isLoading: expLoading } = useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () => api.getExperiment(experimentId),
  });

  const { data: crops, isLoading: cropsLoading } = useQuery({
    queryKey: ["crops", experimentId],
    queryFn: () => api.getCellCrops(experimentId),
  });

  const { data: fovs, isLoading: fovsLoading } = useQuery({
    queryKey: ["fovs", experimentId],
    queryFn: () => api.getFOVs(experimentId),
  });

  const { data: proteins } = useQuery({
    queryKey: ["proteins"],
    queryFn: () => api.getProteins(),
  });

  // Mutation error state for user feedback
  const [mutationError, setMutationError] = useState<string | null>(null);

  const deleteCropMutation = useMutation({
    mutationFn: (cropId: number) => api.deleteCellCrop(cropId),
    onSuccess: () => {
      setCropToDelete(null);
      setMutationError(null);
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
    },
    onError: (err: Error) => {
      console.error("Failed to delete cell crop:", err);
      setMutationError(err.message || "Failed to delete cell crop");
    },
  });

  const updateProteinMutation = useMutation({
    mutationFn: ({ cropId, proteinId }: { cropId: number; proteinId: number | null }) =>
      api.updateCellCropProtein(cropId, proteinId),
    onSuccess: () => {
      setMutationError(null);
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
      setProteinDropdownCropId(null);
    },
    onError: (err: Error) => {
      console.error("Failed to update protein:", err);
      setMutationError(err.message || "Failed to update protein assignment");
    },
  });

  // Bulk delete crops mutation
  const bulkDeleteCropsMutation = useMutation({
    mutationFn: async (ids: number[]) => {
      const results = await Promise.allSettled(ids.map((id) => api.deleteCellCrop(id)));
      const failures = results.filter((r) => r.status === "rejected");
      if (failures.length > 0) {
        const successCount = results.length - failures.length;
        throw new Error(`Deleted ${successCount} of ${ids.length} items. ${failures.length} failed.`);
      }
    },
    onSuccess: () => {
      setSelectedCropIds(new Set());
      setShowBulkDeleteConfirm(false);
      setMutationError(null);
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
    },
    onError: (err: Error) => {
      console.error("Bulk delete crops failed:", err);
      setMutationError(err.message);
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
    },
  });

  // Bulk delete FOVs mutation
  const bulkDeleteFovsMutation = useMutation({
    mutationFn: async (ids: number[]) => {
      const results = await Promise.allSettled(ids.map((id) => api.deleteImage(id)));
      const failures = results.filter((r) => r.status === "rejected");
      if (failures.length > 0) {
        const successCount = results.length - failures.length;
        throw new Error(`Deleted ${successCount} of ${ids.length} images. ${failures.length} failed.`);
      }
    },
    onSuccess: () => {
      setSelectedFovIds(new Set());
      setShowBulkDeleteConfirm(false);
      setMutationError(null);
      queryClient.invalidateQueries({ queryKey: ["fovs", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
    },
    onError: (err: Error) => {
      console.error("Bulk delete FOVs failed:", err);
      setMutationError(err.message);
      queryClient.invalidateQueries({ queryKey: ["fovs", experimentId] });
    },
  });

  // Bulk update protein mutation with partial failure handling
  const bulkUpdateProteinMutation = useMutation({
    mutationFn: async ({ ids, proteinId }: { ids: number[]; proteinId: number | null }) => {
      const results = await Promise.allSettled(ids.map((id) => api.updateCellCropProtein(id, proteinId)));
      const failures = results.filter((r) => r.status === "rejected");
      if (failures.length > 0) {
        const successCount = results.length - failures.length;
        throw new Error(`Updated ${successCount} of ${ids.length} items. ${failures.length} failed.`);
      }
    },
    onSuccess: () => {
      setBulkProteinDropdownOpen(false);
      setMutationError(null);
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
    },
    onError: (err: Error) => {
      console.error("Bulk protein update failed:", err);
      setMutationError(err.message);
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
    },
  });

  // State for protein dropdown
  const [proteinDropdownCropId, setProteinDropdownCropId] = useState<number | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setProteinDropdownCropId(null);
        setBulkProteinDropdownOpen(false);
      }
    };

    if (proteinDropdownCropId !== null || bulkProteinDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [proteinDropdownCropId, bulkProteinDropdownOpen]);

  // Selection helpers
  const toggleSelect = (id: number) => {
    setSelectedIds((prev: Set<number>) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const clearSelection = () => setSelectedIds(new Set());

  // Get unique proteins from current view data
  const availableProteins = useMemo((): ProteinInfo[] => {
    const proteinSet = new Set<string>();

    if (viewMode === "crops" && crops) {
      crops.forEach((crop) => {
        if (crop.map_protein_name) {
          proteinSet.add(crop.map_protein_name);
        }
      });
    } else if (viewMode === "fovs" && fovs) {
      fovs.forEach((fov) => {
        if (fov.map_protein?.name) {
          proteinSet.add(fov.map_protein.name);
        }
      });
    }

    return Array.from(proteinSet).map((name) => {
      const protein = proteins?.find((p) => p.name === name);
      return { name, color: protein?.color };
    });
  }, [viewMode, crops, fovs, proteins]);

  // Filter and sort crops
  const filteredCrops = useMemo(() => {
    if (!crops) return [];

    let result = [...crops];

    // Search filter (by parent filename)
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter((crop) =>
        crop.parent_filename.toLowerCase().includes(query)
      );
    }

    // Protein filter
    if (proteinFilter !== null) {
      result = result.filter((crop) => crop.map_protein_name === proteinFilter);
    }

    // Sort
    result.sort((a, b) => {
      let comparison = 0;
      switch (cropSortField) {
        case "date":
          comparison = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          break;
        case "bundleness":
          comparison = (a.bundleness_score ?? 0) - (b.bundleness_score ?? 0);
          break;
        case "parent":
          comparison = a.parent_filename.localeCompare(b.parent_filename);
          break;
        case "confidence":
          comparison = (a.detection_confidence ?? 0) - (b.detection_confidence ?? 0);
          break;
      }
      return sortOrder === "asc" ? comparison : -comparison;
    });

    return result;
  }, [crops, searchQuery, cropSortField, sortOrder, proteinFilter]);

  // Filter and sort FOVs
  const filteredFovs = useMemo(() => {
    if (!fovs) return [];

    let result = [...fovs];

    // Search filter (by filename)
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter((fov) =>
        fov.original_filename.toLowerCase().includes(query)
      );
    }

    // Protein filter
    if (proteinFilter !== null) {
      result = result.filter((fov) => fov.map_protein?.name === proteinFilter);
    }

    // Sort
    result.sort((a, b) => {
      let comparison = 0;
      switch (fovSortField) {
        case "date":
          comparison = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          break;
        case "filename":
          comparison = a.original_filename.localeCompare(b.original_filename);
          break;
        case "cells":
          comparison = (a.cell_count || 0) - (b.cell_count || 0);
          break;
      }
      return sortOrder === "asc" ? comparison : -comparison;
    });

    return result;
  }, [fovs, searchQuery, fovSortField, sortOrder, proteinFilter]);

  // Get current view's filtered items (must be after filteredFovs and filteredCrops)
  const currentFilteredItems = viewMode === "fovs" ? filteredFovs : filteredCrops;

  const selectAll = () => {
    if (currentFilteredItems.length === selectedIds.size) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(currentFilteredItems.map((item) => item.id)));
    }
  };

  const clearFilters = () => {
    setSearchQuery("");
    setProteinFilter(null);
  };

  const hasActiveFilters = Boolean(searchQuery) || proteinFilter !== null;

  if (expLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-10 h-10 text-primary-500 animate-spin" />
      </div>
    );
  }

  if (!experiment) {
    return (
      <div className="text-center py-12">
        <p className="text-text-secondary">Experiment not found</p>
        <Link href="/dashboard/experiments" className="btn-primary mt-4">
          Back to Experiments
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link
          href="/dashboard/experiments"
          className="p-2 hover:bg-white/5 rounded-lg transition-colors"
        >
          <ArrowLeft className="w-5 h-5 text-text-secondary" />
        </Link>
        <div className="flex-1">
          <h1 className="text-3xl font-display font-bold text-text-primary">
            {experiment.name}
          </h1>
          {experiment.description && (
            <p className="text-text-secondary mt-1">{experiment.description}</p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-4 px-3 py-1.5 bg-bg-elevated rounded-lg">
            <span className="text-sm text-text-secondary">
              {experiment.image_count} FOVs
            </span>
            <span className="text-text-muted">·</span>
            <span className="text-sm text-text-secondary">
              {experiment.cell_count} crops
            </span>
          </div>
          <Link
            href={`/dashboard/experiments/${experimentId}/upload`}
            className="btn-primary flex items-center gap-2"
          >
            <Upload className="w-4 h-4" />
            Upload Images
          </Link>
        </div>
      </div>

      {/* View Mode Toggle */}
      <div className="flex items-center gap-2">
        <div className="flex items-center bg-bg-secondary rounded-lg p-1">
          <button
            onClick={() => setViewMode("fovs")}
            className={`flex items-center gap-2 px-4 py-2 rounded-md transition-all ${
              viewMode === "fovs"
                ? "bg-primary-500 text-white"
                : "text-text-secondary hover:text-text-primary hover:bg-white/5"
            }`}
          >
            <ImageIcon className="w-4 h-4" />
            <span className="font-medium">FOVs</span>
            <span className={`text-xs ${viewMode === "fovs" ? "text-white/70" : "text-text-muted"}`}>
              ({experiment.image_count})
            </span>
          </button>
          <button
            onClick={() => setViewMode("crops")}
            className={`flex items-center gap-2 px-4 py-2 rounded-md transition-all ${
              viewMode === "crops"
                ? "bg-primary-500 text-white"
                : "text-text-secondary hover:text-text-primary hover:bg-white/5"
            }`}
          >
            <Layers className="w-4 h-4" />
            <span className="font-medium">Crops</span>
            <span className={`text-xs ${viewMode === "crops" ? "text-white/70" : "text-text-muted"}`}>
              ({experiment.cell_count})
            </span>
          </button>
        </div>
      </div>

      {/* Error notification */}
      {mutationError && (
        <div className="p-4 bg-accent-red/10 border border-accent-red/20 rounded-lg flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-accent-red flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-accent-red font-medium">Operation failed</p>
            <p className="text-sm text-text-secondary">{mutationError}</p>
          </div>
          <button
            onClick={() => setMutationError(null)}
            className="text-text-muted hover:text-text-primary"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Unified Search and Filters */}
      <ImageGalleryFilters
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={viewMode === "fovs" ? "Search by filename..." : "Search by parent image..."}
        sortField={viewMode === "fovs" ? fovSortField : cropSortField}
        onSortFieldChange={viewMode === "fovs"
          ? (v) => setFovSortField(v as FOVSortField)
          : (v) => setCropSortField(v as CropSortField)
        }
        sortOrder={sortOrder}
        onSortOrderChange={setSortOrder}
        sortOptions={viewMode === "fovs" ? FOV_SORT_OPTIONS : CROP_SORT_OPTIONS}
        proteinFilter={proteinFilter}
        onProteinFilterChange={setProteinFilter}
        availableProteins={availableProteins}
        onClearFilters={clearFilters}
        hasActiveFilters={hasActiveFilters}
        leftSlot={
          <div className="flex items-center gap-3 flex-shrink-0">
            {/* Select All Checkbox */}
            <button
              onClick={selectAll}
              className={`flex items-center justify-center w-6 h-6 rounded border-2 transition-all ${
                currentFilteredItems.length > 0 && selectedIds.size === currentFilteredItems.length
                  ? "bg-primary-500 border-primary-500"
                  : selectedIds.size > 0
                  ? "bg-primary-500/50 border-primary-500"
                  : "border-white/20 hover:border-white/40"
              }`}
              title={selectedIds.size === currentFilteredItems.length ? "Deselect all" : "Select all"}
            >
              {selectedIds.size > 0 && (
                <Check className="w-4 h-4 text-white" />
              )}
            </button>

            {/* Bulk Actions - show when items are selected */}
            {selectedIds.size > 0 && (
              <>
                <span className="text-sm text-primary-400 font-medium whitespace-nowrap">
                  {selectedIds.size} selected
                </span>

                {/* Bulk Assign MAP - only for crops */}
                {viewMode === "crops" && (
                  <div className="relative" ref={dropdownRef}>
                    <button
                      onClick={() => setBulkProteinDropdownOpen(!bulkProteinDropdownOpen)}
                      className="btn-secondary text-sm py-1.5"
                    >
                      Assign MAP
                    </button>
                    {bulkProteinDropdownOpen && (
                      <div className="absolute top-full left-0 mt-1 w-48 bg-bg-elevated border border-white/10 rounded-lg shadow-xl z-50">
                        <button
                          onClick={() => bulkUpdateProteinMutation.mutate({ ids: Array.from(selectedIds), proteinId: null })}
                          className="w-full px-3 py-2 text-left text-sm text-text-muted hover:bg-white/5"
                        >
                          None
                        </button>
                        {proteins?.map((p) => (
                          <button
                            key={p.id}
                            onClick={() => bulkUpdateProteinMutation.mutate({ ids: Array.from(selectedIds), proteinId: p.id })}
                            className="w-full px-3 py-2 text-left text-sm hover:bg-white/5 flex items-center gap-2"
                            style={{ color: p.color }}
                          >
                            <span className="w-2 h-2 rounded-full" style={{ backgroundColor: p.color }} />
                            {p.name}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Bulk Delete */}
                <button
                  onClick={() => setShowBulkDeleteConfirm(true)}
                  className="btn-secondary text-sm py-1.5 text-accent-red hover:bg-accent-red/10"
                >
                  <Trash2 className="w-4 h-4 mr-1" />
                  Delete
                </button>

                {/* Clear Selection */}
                <button
                  onClick={clearSelection}
                  className="p-1.5 hover:bg-white/5 rounded-lg transition-colors"
                  title="Clear selection"
                >
                  <X className="w-4 h-4 text-text-muted" />
                </button>
              </>
            )}
          </div>
        }
      />

      {/* Conditional View */}
      {viewMode === "fovs" ? (
        <FOVGallery
          experimentId={experimentId}
          filteredFovs={filteredFovs}
          fovs={fovs}
          isLoading={fovsLoading}
          onClearFilters={clearFilters}
          selectedIds={selectedFovIds}
          onToggleSelect={toggleSelect}
        />
      ) : (
        <>

          {/* Cell Crops Grid */}
          {cropsLoading ? (
            <div className="glass-card p-8 flex justify-center">
              <Loader2 className="w-8 h-8 text-primary-500 animate-spin" />
            </div>
          ) : filteredCrops.length > 0 ? (
            <>
              {/* Results count */}
              {hasActiveFilters && (
                <p className="text-sm text-text-muted">
                  Showing {filteredCrops.length} of {crops?.length} cells
                </p>
              )}

              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-4">
                {filteredCrops.map((crop, i) => (
                  <motion.div
                    key={crop.id}
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ delay: i * 0.01 }}
                    className="glass-card group"
                  >
                    {/* Cell crop preview */}
                    <div className="aspect-square bg-bg-secondary flex items-center justify-center relative overflow-hidden rounded-t-xl">
                      <img
                        src={api.getCropImageUrl(crop.id, "mip")}
                        alt={`Cell from ${crop.parent_filename}`}
                        className="w-full h-full object-cover"
                        loading="lazy"
                        onError={(e) => {
                          console.error(`Failed to load crop image ${crop.id}:`, e.type);
                          e.currentTarget.style.display = "none";
                          e.currentTarget.nextElementSibling?.classList.remove("hidden");
                        }}
                      />
                      <Layers className="w-10 h-10 text-text-muted hidden" />

                      {/* Selection checkbox */}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleSelect(crop.id);
                        }}
                        className={`absolute top-2 left-2 w-5 h-5 rounded border-2 flex items-center justify-center transition-all ${
                          selectedIds.has(crop.id)
                            ? "bg-primary-500 border-primary-500"
                            : "border-white/40 bg-black/30 opacity-0 group-hover:opacity-100"
                        }`}
                      >
                        {selectedIds.has(crop.id) && (
                          <Check className="w-3 h-3 text-white" />
                        )}
                      </button>

                      {/* Delete button overlay */}
                      <button
                        onClick={() => setCropToDelete({ id: crop.id, name: crop.parent_filename })}
                        className="absolute top-2 right-2 p-1.5 bg-bg-primary/80 hover:bg-accent-red/20 text-text-muted hover:text-accent-red rounded-lg opacity-0 group-hover:opacity-100 transition-all duration-200"
                        title="Delete cell crop"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    {/* Info */}
                    <div className="p-2 space-y-1">
                      {/* Parent filename */}
                      <p className="text-xs text-text-muted truncate" title={crop.parent_filename}>
                        {crop.parent_filename}
                      </p>

                      {/* MAP protein selector */}
                      <div className="relative">
                        <button
                          onClick={() => setProteinDropdownCropId(proteinDropdownCropId === crop.id ? null : crop.id)}
                          className={`px-2 py-1 rounded text-xs font-medium transition-all w-full text-left ${
                            crop.map_protein_name
                              ? ""
                              : "bg-bg-secondary text-text-muted hover:bg-bg-hover"
                          }`}
                          style={crop.map_protein_name ? {
                            backgroundColor: `${crop.map_protein_color}20`,
                            color: crop.map_protein_color,
                          } : undefined}
                        >
                          {crop.map_protein_name || "+ Assign MAP"}
                        </button>

                        {/* Dropdown - opens upward to avoid overflow clipping */}
                        {proteinDropdownCropId === crop.id && (
                          <div className="absolute bottom-full left-0 right-0 mb-1 bg-bg-elevated border border-white/10 rounded-lg shadow-xl z-50">
                            <button
                              onClick={() => updateProteinMutation.mutate({ cropId: crop.id, proteinId: null })}
                              className="w-full px-3 py-2 text-left text-xs text-text-muted hover:bg-white/5"
                            >
                              None
                            </button>
                            {proteins?.map((p) => (
                              <button
                                key={p.id}
                                onClick={() => updateProteinMutation.mutate({ cropId: crop.id, proteinId: p.id })}
                                className="w-full px-3 py-2 text-left text-xs hover:bg-white/5 flex items-center gap-2"
                                style={{ color: p.color }}
                              >
                                <span
                                  className="w-2 h-2 rounded-full"
                                  style={{ backgroundColor: p.color }}
                                />
                                {p.name}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>

                      {/* Size info */}
                      <div className="text-xs text-text-muted">
                        {crop.bbox_w}×{crop.bbox_h}
                      </div>
                    </div>
                  </motion.div>
                ))}
              </div>
            </>
          ) : crops && crops.length > 0 ? (
            <div className="glass-card p-12 text-center">
              <Search className="w-12 h-12 text-text-muted mx-auto mb-4" />
              <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
                No cells match your filters
              </h3>
              <p className="text-text-secondary mb-4">
                Try adjusting your search or filter criteria
              </p>
              <button
                onClick={clearFilters}
                className="btn-primary"
              >
                Clear Filters
              </button>
            </div>
          ) : (
            <div className="glass-card p-12 text-center">
              <Layers className="w-12 h-12 text-text-muted mx-auto mb-4" />
              <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
                No cell crops yet
              </h3>
              <p className="text-text-secondary mb-4">
                Upload microscopy images with &quot;Detect and crop cells&quot; enabled
              </p>
              <Link
                href={`/dashboard/experiments/${experimentId}/upload`}
                className="btn-primary inline-flex items-center gap-2"
              >
                <Upload className="w-4 h-4" />
                Upload Images
              </Link>
            </div>
          )}
        </>
      )}

      {/* Delete confirmation modal */}
      <ConfirmModal
        isOpen={!!cropToDelete}
        onClose={() => setCropToDelete(null)}
        onConfirm={() => cropToDelete && deleteCropMutation.mutate(cropToDelete.id)}
        title="Delete Cell Crop"
        message={`Are you sure you want to delete this cell crop from "${cropToDelete?.name}"? This action cannot be undone.`}
        confirmLabel="Delete"
        isLoading={deleteCropMutation.isPending}
        variant="danger"
      />

      {/* Bulk delete confirmation modal */}
      <ConfirmModal
        isOpen={showBulkDeleteConfirm}
        onClose={() => setShowBulkDeleteConfirm(false)}
        onConfirm={() => {
          if (viewMode === "fovs") {
            bulkDeleteFovsMutation.mutate(Array.from(selectedFovIds));
          } else {
            bulkDeleteCropsMutation.mutate(Array.from(selectedCropIds));
          }
        }}
        title={viewMode === "fovs" ? "Delete Selected FOVs" : "Delete Selected Cells"}
        message={
          viewMode === "fovs"
            ? `Are you sure you want to delete ${selectedFovIds.size} selected FOV image${selectedFovIds.size !== 1 ? "s" : ""}? This will also delete all detected cells from these images. This action cannot be undone.`
            : `Are you sure you want to delete ${selectedCropIds.size} selected cell crop${selectedCropIds.size !== 1 ? "s" : ""}? This action cannot be undone.`
        }
        confirmLabel="Delete All"
        isLoading={viewMode === "fovs" ? bulkDeleteFovsMutation.isPending : bulkDeleteCropsMutation.isPending}
        variant="danger"
      />
    </div>
  );
}
