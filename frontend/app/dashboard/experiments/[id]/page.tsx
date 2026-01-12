"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, CellCropGallery } from "@/lib/api";
import { ConfirmModal } from "@/components/ui";
import {
  ImageGalleryFilters,
  SortOrder,
  SortOption,
  ProteinInfo,
} from "@/components/shared";
import {
  ArrowLeft,
  Upload,
  Microscope,
  Loader2,
  Search,
  Trash2,
  Check,
  X,
} from "lucide-react";

type SortField = "date" | "bundleness" | "parent" | "confidence";

const SORT_OPTIONS: SortOption<SortField>[] = [
  { value: "date", label: "Date" },
  { value: "bundleness", label: "Bundleness" },
  { value: "parent", label: "Parent Image" },
  { value: "confidence", label: "Confidence" },
];

export default function ExperimentDetailPage(): JSX.Element {
  const params = useParams();
  const experimentId = Number(params.id);
  const queryClient = useQueryClient();

  // Filter state
  const [searchQuery, setSearchQuery] = useState("");
  const [sortField, setSortField] = useState<SortField>("date");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [proteinFilter, setProteinFilter] = useState<string | null>(null);

  // Delete state
  const [cropToDelete, setCropToDelete] = useState<{ id: number; name: string } | null>(null);

  // Selection state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showBulkDeleteConfirm, setShowBulkDeleteConfirm] = useState(false);
  const [bulkProteinDropdownOpen, setBulkProteinDropdownOpen] = useState(false);

  const { data: experiment, isLoading: expLoading } = useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () => api.getExperiment(experimentId),
  });

  const { data: crops, isLoading: cropsLoading } = useQuery({
    queryKey: ["crops", experimentId],
    queryFn: () => api.getCellCrops(experimentId),
  });

  const { data: proteins } = useQuery({
    queryKey: ["proteins"],
    queryFn: () => api.getProteins(),
  });

  // Delete error state
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const deleteCropMutation = useMutation({
    mutationFn: (cropId: number) => api.deleteCellCrop(cropId),
    onSuccess: () => {
      setCropToDelete(null);
      setDeleteError(null);
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
    },
    onError: (err: Error) => {
      console.error("Failed to delete cell crop:", err);
      setDeleteError(err.message || "Failed to delete cell crop");
    },
  });

  const updateProteinMutation = useMutation({
    mutationFn: ({ cropId, proteinId }: { cropId: number; proteinId: number | null }) =>
      api.updateCellCropProtein(cropId, proteinId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
      setProteinDropdownCropId(null);
    },
  });

  // Bulk delete mutation
  const bulkDeleteMutation = useMutation({
    mutationFn: async (ids: number[]) => {
      await Promise.all(ids.map((id) => api.deleteCellCrop(id)));
    },
    onSuccess: () => {
      setSelectedIds(new Set());
      setShowBulkDeleteConfirm(false);
      queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
    },
  });

  // Bulk update protein mutation
  const bulkUpdateProteinMutation = useMutation({
    mutationFn: async ({ ids, proteinId }: { ids: number[]; proteinId: number | null }) => {
      await Promise.all(ids.map((id) => api.updateCellCropProtein(id, proteinId)));
    },
    onSuccess: () => {
      setBulkProteinDropdownOpen(false);
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
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const selectAll = () => {
    if (filteredCrops.length === selectedIds.size) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredCrops.map((c) => c.id)));
    }
  };

  const clearSelection = () => setSelectedIds(new Set());

  // Get unique proteins from crops with color info
  const availableProteins = useMemo((): ProteinInfo[] => {
    if (!crops) return [];
    const proteinSet = new Set<string>();
    crops.forEach((crop) => {
      if (crop.map_protein_name) {
        proteinSet.add(crop.map_protein_name);
      }
    });
    return Array.from(proteinSet).map((name) => {
      const protein = proteins?.find((p) => p.name === name);
      return { name, color: protein?.color };
    });
  }, [crops, proteins]);

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
      switch (sortField) {
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
  }, [crops, searchQuery, sortField, sortOrder, proteinFilter]);

  const clearFilters = () => {
    setSearchQuery("");
    setProteinFilter(null);
  };

  const hasActiveFilters = searchQuery || proteinFilter !== null;

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
          <div className="flex items-center gap-2 px-3 py-1.5 bg-bg-elevated rounded-lg">
            <Microscope className="w-4 h-4 text-text-muted" />
            <span className="text-sm text-text-secondary">
              {crops?.length || 0} cells
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

      {/* Search and Filters with Select All and Bulk Actions */}
      <ImageGalleryFilters
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="Search by parent image..."
        sortField={sortField}
        onSortFieldChange={setSortField}
        sortOrder={sortOrder}
        onSortOrderChange={setSortOrder}
        sortOptions={SORT_OPTIONS}
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
                filteredCrops.length > 0 && selectedIds.size === filteredCrops.length
                  ? "bg-primary-500 border-primary-500"
                  : selectedIds.size > 0
                  ? "bg-primary-500/50 border-primary-500"
                  : "border-white/20 hover:border-white/40"
              }`}
              title={selectedIds.size === filteredCrops.length ? "Deselect all" : "Select all"}
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

                {/* Bulk Assign MAP */}
                <div className="relative">
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
                  <Microscope className="w-10 h-10 text-text-muted hidden" />

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
          <Microscope className="w-12 h-12 text-text-muted mx-auto mb-4" />
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
        onConfirm={() => bulkDeleteMutation.mutate(Array.from(selectedIds))}
        title="Delete Selected Cells"
        message={`Are you sure you want to delete ${selectedIds.size} selected cell crop${selectedIds.size !== 1 ? "s" : ""}? This action cannot be undone.`}
        confirmLabel="Delete All"
        isLoading={bulkDeleteMutation.isPending}
        variant="danger"
      />
    </div>
  );
}
