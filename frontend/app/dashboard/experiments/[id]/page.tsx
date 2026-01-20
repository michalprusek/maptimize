"use client";

import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, CellCropGallery, FOVImage } from "@/lib/api";
import {
  staggerContainerVariants,
  staggerItemVariants,
  cardHoverProps,
} from "@/lib/animations";
import { ConfirmModal, MicroscopyImage, Pagination, ImagePreviewModal, type PreviewImage } from "@/components/ui";
import { FOVGallery } from "@/components/experiment";
import {
  ImageGalleryFilters,
  SortOrder,
  SortOption,
  ProteinInfo,
  SelectionCheckbox,
  DeleteOverlayButton,
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
  Pencil,
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
  const router = useRouter();
  const experimentId = Number(params.id);
  const queryClient = useQueryClient();
  const t = useTranslations("experiments");
  const tCommon = useTranslations("common");
  const tImages = useTranslations("images");

  // View mode state - will be set based on data availability
  const [viewMode, setViewMode] = useState<ViewMode | null>(null);

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
  const [experimentProteinDropdownOpen, setExperimentProteinDropdownOpen] = useState(false);

  // Pagination state for crops
  const [cropPage, setCropPage] = useState(1);
  const [cropsPerPage, setCropsPerPage] = useState(48);

  // Image preview modal state
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewIndex, setPreviewIndex] = useState(0);

  // Inline editing state
  const [isEditingName, setIsEditingName] = useState(false);
  const [isEditingDescription, setIsEditingDescription] = useState(false);
  const [editedName, setEditedName] = useState("");
  const [editedDescription, setEditedDescription] = useState("");
  const nameInputRef = useRef<HTMLInputElement>(null);
  const descInputRef = useRef<HTMLTextAreaElement>(null);

  // Current view's selected IDs (derived) - default to crops if viewMode not set yet
  const effectiveViewMode = viewMode ?? "crops";
  const selectedIds = effectiveViewMode === "fovs" ? selectedFovIds : selectedCropIds;
  const setSelectedIds = effectiveViewMode === "fovs" ? setSelectedFovIds : setSelectedCropIds;

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

  // Auto-select view mode based on available data
  useEffect(() => {
    if (viewMode === null && experiment) {
      // If no crops exist, default to FOVs view
      if (experiment.cell_count === 0) {
        setViewMode("fovs");
      } else {
        setViewMode("crops");
      }
    }
  }, [experiment, viewMode]);

  // Mutation error state for user feedback
  const [mutationError, setMutationError] = useState<string | null>(null);

  // Helper to invalidate experiment-related queries (DRY)
  const invalidateExperimentQueries = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["fovs", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
  }, [queryClient, experimentId]);

  const deleteCropMutation = useMutation({
    mutationFn: (cropId: number) => api.deleteCellCrop(cropId),
    onSuccess: () => {
      setCropToDelete(null);
      setMutationError(null);
      invalidateExperimentQueries();
    },
    onError: (err: Error) => {
      console.error("Failed to delete cell crop:", err);
      setMutationError(err.message || "Failed to delete cell crop");
    },
  });

  // Update experiment protein mutation (cascades to all images and crops)
  const updateExperimentProteinMutation = useMutation({
    mutationFn: ({ proteinId }: { proteinId: number | null }) =>
      api.updateExperimentProtein(experimentId, proteinId),
    onSuccess: () => {
      setMutationError(null);
      invalidateExperimentQueries();
    },
    onError: (err: Error) => {
      console.error("Failed to update experiment protein:", err);
      setMutationError(err.message || "Failed to update protein assignment");
    },
  });

  // Update experiment name/description mutation
  const updateExperimentMutation = useMutation({
    mutationFn: (data: { name?: string; description?: string }) =>
      api.updateExperiment(experimentId, data),
    onSuccess: () => {
      setMutationError(null);
      setIsEditingName(false);
      setIsEditingDescription(false);
      invalidateExperimentQueries();
    },
    onError: (err: Error) => {
      console.error("Failed to update experiment:", err);
      setMutationError(err.message || "Failed to update experiment");
    },
  });

  // Inline editing handlers
  const startEditingName = () => {
    setEditedName(experiment?.name || "");
    setIsEditingName(true);
    setTimeout(() => nameInputRef.current?.focus(), 0);
  };

  const startEditingDescription = () => {
    setEditedDescription(experiment?.description || "");
    setIsEditingDescription(true);
    setTimeout(() => descInputRef.current?.focus(), 0);
  };

  const saveName = () => {
    if (editedName.trim() && editedName !== experiment?.name) {
      updateExperimentMutation.mutate({ name: editedName.trim() });
    } else {
      setIsEditingName(false);
    }
  };

  const saveDescription = () => {
    const newDesc = editedDescription.trim() || undefined;
    if (newDesc !== (experiment?.description || undefined)) {
      updateExperimentMutation.mutate({ description: newDesc || "" });
    } else {
      setIsEditingDescription(false);
    }
  };

  const cancelEditingName = () => {
    setIsEditingName(false);
    setEditedName(experiment?.name || "");
  };

  const cancelEditingDescription = () => {
    setIsEditingDescription(false);
    setEditedDescription(experiment?.description || "");
  };

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
      invalidateExperimentQueries();
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
      invalidateExperimentQueries();
    },
    onError: (err: Error) => {
      console.error("Bulk delete FOVs failed:", err);
      setMutationError(err.message);
      queryClient.invalidateQueries({ queryKey: ["fovs", experimentId] });
    },
  });

  // Navigation handlers for editor page
  const handleOpenEditorFromFov = useCallback((fov: FOVImage) => {
    router.push(`/editor/${experimentId}/${fov.id}`);
  }, [router, experimentId]);

  const handleOpenEditorFromCrop = useCallback((crop: CellCropGallery) => {
    // Validate that crop has parent image before navigation
    if (!crop.image_id) {
      console.error("Cannot open editor: crop has no parent image_id", { cropId: crop.id });
      return;
    }
    // Navigate to editor with the parent FOV image
    router.push(`/editor/${experimentId}/${crop.image_id}`);
  }, [router, experimentId]);

  // Preview modal handlers
  const handleOpenCropPreview = useCallback((cropIndex: number) => {
    setPreviewIndex(cropIndex);
    setPreviewOpen(true);
  }, []);

  const handleClosePreview = useCallback(() => {
    setPreviewOpen(false);
  }, []);

  const handlePreviewNavigate = useCallback((index: number) => {
    setPreviewIndex(index);
  }, []);

  const handleOpenInEditorFromPreview = useCallback((image: PreviewImage) => {
    // Find the crop by id to get its image_id
    const crop = crops?.find(c => c.id === image.id);
    if (crop) {
      router.push(`/editor/${experimentId}/${crop.image_id}`);
    }
  }, [router, experimentId, crops]);

  // State for experiment protein dropdown
  const experimentProteinRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (experimentProteinRef.current && !experimentProteinRef.current.contains(event.target as Node)) {
        setExperimentProteinDropdownOpen(false);
      }
    };

    if (experimentProteinDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [experimentProteinDropdownOpen]);

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

    if (effectiveViewMode === "crops" && crops) {
      crops.forEach((crop) => {
        if (crop.map_protein_name) {
          proteinSet.add(crop.map_protein_name);
        }
      });
    } else if (effectiveViewMode === "fovs" && fovs) {
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
  }, [effectiveViewMode, crops, fovs, proteins]);

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

  // Pagination for crops
  const cropTotalPages = Math.ceil(filteredCrops.length / cropsPerPage);
  const cropStartIndex = (cropPage - 1) * cropsPerPage;
  const paginatedCrops = filteredCrops.slice(cropStartIndex, cropStartIndex + cropsPerPage);

  // Create preview images array for the modal
  const previewImages: PreviewImage[] = useMemo(() => {
    return paginatedCrops.map((crop) => ({
      id: crop.id,
      src: api.getCropImageUrl(crop.id, "mip"),
      alt: `${crop.parent_filename} - Cell ${crop.id}`,
    }));
  }, [paginatedCrops]);

  // Reset crop page when filters change
  useEffect(() => {
    setCropPage(1);
  }, [filteredCrops.length, cropSortField, sortOrder, proteinFilter, searchQuery]);

  const handleCropPageChange = (page: number) => {
    setCropPage(page);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleCropsPerPageChange = (size: number) => {
    setCropsPerPage(size);
    setCropPage(1);
  };

  // Get current view's filtered items (must be after filteredFovs and filteredCrops)
  const currentFilteredItems = effectiveViewMode === "fovs" ? filteredFovs : filteredCrops;

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
        <div className="flex-1 min-w-0">
          {/* Editable Name */}
          {isEditingName ? (
            <div className="flex items-center gap-2">
              <input
                ref={nameInputRef}
                type="text"
                value={editedName}
                onChange={(e) => setEditedName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveName();
                  if (e.key === "Escape") cancelEditingName();
                }}
                className="text-3xl font-display font-bold text-text-primary bg-transparent border-b-2 border-primary-500 outline-none w-full"
              />
              <button
                onClick={saveName}
                className="p-1.5 text-accent-green hover:bg-accent-green/20 rounded-lg transition-colors"
                disabled={updateExperimentMutation.isPending}
              >
                {updateExperimentMutation.isPending ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <Check className="w-5 h-5" />
                )}
              </button>
              <button
                onClick={cancelEditingName}
                className="p-1.5 text-text-muted hover:bg-white/10 rounded-lg transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          ) : (
            <div className="group flex items-center gap-2">
              <h1 className="text-3xl font-display font-bold text-text-primary truncate">
                {experiment.name}
              </h1>
              <button
                onClick={startEditingName}
                className="p-1.5 text-text-muted hover:text-primary-400 hover:bg-white/10 rounded-lg transition-all opacity-0 group-hover:opacity-100"
                title="Edit name"
              >
                <Pencil className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* Editable Description */}
          {isEditingDescription ? (
            <div className="flex items-start gap-2 mt-1">
              <textarea
                ref={descInputRef}
                value={editedDescription}
                onChange={(e) => setEditedDescription(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    saveDescription();
                  }
                  if (e.key === "Escape") cancelEditingDescription();
                }}
                className="text-text-secondary bg-transparent border-b-2 border-primary-500 outline-none w-full resize-none"
                rows={2}
                placeholder="Add description..."
              />
              <button
                onClick={saveDescription}
                className="p-1.5 text-accent-green hover:bg-accent-green/20 rounded-lg transition-colors"
                disabled={updateExperimentMutation.isPending}
              >
                {updateExperimentMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Check className="w-4 h-4" />
                )}
              </button>
              <button
                onClick={cancelEditingDescription}
                className="p-1.5 text-text-muted hover:bg-white/10 rounded-lg transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          ) : (
            <div className="group flex items-center gap-2 mt-1">
              {experiment.description ? (
                <p className="text-text-secondary">{experiment.description}</p>
              ) : (
                <p className="text-text-muted italic">No description</p>
              )}
              <button
                onClick={startEditingDescription}
                className="p-1 text-text-muted hover:text-primary-400 hover:bg-white/10 rounded-lg transition-all opacity-0 group-hover:opacity-100"
                title="Edit description"
              >
                <Pencil className="w-3 h-3" />
              </button>
            </div>
          )}
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-4 px-3 py-1.5 bg-bg-elevated rounded-lg">
            <span className="text-sm text-text-secondary">
              {experiment.image_count} {t("fovs")}
            </span>
            <span className="text-text-muted">·</span>
            <span className="text-sm text-text-secondary">
              {experiment.cell_count} {t("crops")}
            </span>
          </div>
          <Link
            href={`/dashboard/experiments/${experimentId}/upload`}
            className="btn-primary flex items-center gap-2"
          >
            <Upload className="w-4 h-4" />
            {t("uploadImages")}
          </Link>
        </div>
      </div>

      {/* View Mode Toggle and Experiment Protein Selector */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center bg-bg-secondary rounded-lg p-1">
          <button
            onClick={() => setViewMode("fovs")}
            className={`flex items-center gap-2 px-4 py-2 rounded-md transition-all ${
              effectiveViewMode === "fovs"
                ? "bg-primary-500 text-white"
                : "text-text-secondary hover:text-text-primary hover:bg-white/5"
            }`}
          >
            <ImageIcon className="w-4 h-4" />
            <span className="font-medium">FOVs</span>
            <span className={`text-xs ${effectiveViewMode === "fovs" ? "text-white/70" : "text-text-muted"}`}>
              ({experiment.image_count})
            </span>
          </button>
          <button
            onClick={() => setViewMode("crops")}
            className={`flex items-center gap-2 px-4 py-2 rounded-md transition-all ${
              effectiveViewMode === "crops"
                ? "bg-primary-500 text-white"
                : "text-text-secondary hover:text-text-primary hover:bg-white/5"
            }`}
          >
            <Layers className="w-4 h-4" />
            <span className="font-medium">Crops</span>
            <span className={`text-xs ${effectiveViewMode === "crops" ? "text-white/70" : "text-text-muted"}`}>
              ({experiment.cell_count})
            </span>
          </button>
        </div>

        {/* Experiment Protein Selector */}
        <div className="relative" ref={experimentProteinRef}>
          <button
            onClick={() => setExperimentProteinDropdownOpen(!experimentProteinDropdownOpen)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all ${
              experiment.map_protein
                ? "border border-white/10 hover:border-white/20"
                : "bg-bg-secondary hover:bg-bg-hover"
            }`}
            style={experiment.map_protein ? {
              backgroundColor: `${experiment.map_protein.color}15`,
              borderColor: `${experiment.map_protein.color}40`,
            } : undefined}
          >
            {experiment.map_protein ? (
              <>
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: experiment.map_protein.color }}
                />
                <span
                  className="font-medium"
                  style={{ color: experiment.map_protein.color }}
                >
                  {experiment.map_protein.name}
                </span>
              </>
            ) : (
              <span className="text-text-muted">{t("assignProtein")}</span>
            )}
            <svg
              className={`w-4 h-4 text-text-muted transition-transform ${experimentProteinDropdownOpen ? "rotate-180" : ""}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {experimentProteinDropdownOpen && (
            <div className="absolute top-full right-0 mt-1 w-56 bg-bg-elevated border border-white/10 rounded-lg shadow-xl z-50">
              <div className="px-3 py-2 text-xs text-text-muted border-b border-white/10">
                {t("experimentProteinHint")}
              </div>
              <button
                onClick={() => {
                  updateExperimentProteinMutation.mutate({ proteinId: null });
                  setExperimentProteinDropdownOpen(false);
                }}
                className={`w-full px-3 py-2 text-left text-sm hover:bg-white/5 ${
                  !experiment.map_protein ? "text-primary-400" : "text-text-muted"
                }`}
              >
                {tCommon("none")}
              </button>
              {proteins?.map((p) => (
                <button
                  key={p.id}
                  onClick={() => {
                    updateExperimentProteinMutation.mutate({ proteinId: p.id });
                    setExperimentProteinDropdownOpen(false);
                  }}
                  className={`w-full px-3 py-2 text-left text-sm hover:bg-white/5 flex items-center gap-2 ${
                    experiment.map_protein?.id === p.id ? "bg-white/5" : ""
                  }`}
                  style={{ color: p.color }}
                >
                  <span className="w-3 h-3 rounded-full" style={{ backgroundColor: p.color }} />
                  {p.name}
                  {experiment.map_protein?.id === p.id && (
                    <Check className="w-4 h-4 ml-auto" />
                  )}
                </button>
              ))}
            </div>
          )}
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
        searchPlaceholder={effectiveViewMode === "fovs" ? "Search by filename..." : "Search by parent image..."}
        sortField={effectiveViewMode === "fovs" ? fovSortField : cropSortField}
        onSortFieldChange={effectiveViewMode === "fovs"
          ? (v) => setFovSortField(v as FOVSortField)
          : (v) => setCropSortField(v as CropSortField)
        }
        sortOrder={sortOrder}
        onSortOrderChange={setSortOrder}
        sortOptions={effectiveViewMode === "fovs" ? FOV_SORT_OPTIONS : CROP_SORT_OPTIONS}
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
      {effectiveViewMode === "fovs" ? (
        <FOVGallery
          experimentId={experimentId}
          filteredFovs={filteredFovs}
          fovs={fovs}
          isLoading={fovsLoading}
          onClearFilters={clearFilters}
          selectedIds={selectedFovIds}
          onToggleSelect={toggleSelect}
          onFovClick={handleOpenEditorFromFov}
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

              <motion.div
                className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-4"
                variants={staggerContainerVariants}
                initial="hidden"
                animate="visible"
                key={cropPage}
              >
                <AnimatePresence mode="popLayout">
                {paginatedCrops.map((crop) => (
                  <motion.div
                    key={crop.id}
                    variants={staggerItemVariants}
                    exit={{ opacity: 0, scale: 0.9, transition: { duration: 0.2 } }}
                    layout
                    className="glass-card group"
                    {...cardHoverProps}
                  >
                    {/* Cell crop - click opens editor */}
                    <div
                      className="aspect-square bg-bg-secondary flex items-center justify-center relative overflow-hidden rounded-t-xl cursor-pointer"
                      onClick={() => handleOpenEditorFromCrop(crop)}
                    >
                      <MicroscopyImage
                        src={api.getCropImageUrl(crop.id, "mip")}
                        alt={`Cell from ${crop.parent_filename}`}
                        className="w-full h-full object-cover"
                        onError={(e) => {
                          console.error(`Failed to load crop image ${crop.id}:`, e.type);
                          e.currentTarget.style.display = "none";
                          e.currentTarget.nextElementSibling?.classList.remove("hidden");
                        }}
                      />
                      <Layers className="w-10 h-10 text-text-muted hidden" />

                      <SelectionCheckbox
                        isSelected={selectedIds.has(crop.id)}
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleSelect(crop.id);
                        }}
                      />
                      <DeleteOverlayButton
                        onClick={(e) => {
                          e.stopPropagation();
                          setCropToDelete({ id: crop.id, name: crop.parent_filename });
                        }}
                        title="Delete cell crop"
                      />
                    </div>

                    {/* Info */}
                    <div className="p-2 space-y-1">
                      {/* Parent filename */}
                      <p className="text-xs text-text-muted truncate" title={crop.parent_filename}>
                        {crop.parent_filename}
                      </p>

                      {/* MAP protein badge (inherited from experiment) */}
                      {crop.map_protein_name && (
                        <div
                          className="px-2 py-1 rounded text-xs font-medium"
                          style={{
                            backgroundColor: `${crop.map_protein_color}20`,
                            color: crop.map_protein_color,
                          }}
                        >
                          {crop.map_protein_name}
                        </div>
                      )}

                      {/* Size info */}
                      <div className="text-xs text-text-muted">
                        {crop.bbox_w}×{crop.bbox_h}
                      </div>
                    </div>
                  </motion.div>
                ))}
                </AnimatePresence>
              </motion.div>

              {/* Pagination */}
              <Pagination
                currentPage={cropPage}
                totalPages={cropTotalPages}
                onPageChange={handleCropPageChange}
                totalItems={filteredCrops.length}
                itemsPerPage={cropsPerPage}
                showPageSizeSelector
                onPageSizeChange={handleCropsPerPageChange}
              />
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
          if (effectiveViewMode === "fovs") {
            bulkDeleteFovsMutation.mutate(Array.from(selectedFovIds));
          } else {
            bulkDeleteCropsMutation.mutate(Array.from(selectedCropIds));
          }
        }}
        title={effectiveViewMode === "fovs" ? "Delete Selected FOVs" : "Delete Selected Cells"}
        message={
          effectiveViewMode === "fovs"
            ? `Are you sure you want to delete ${selectedFovIds.size} selected FOV image${selectedFovIds.size !== 1 ? "s" : ""}? This will also delete all detected cells from these images. This action cannot be undone.`
            : `Are you sure you want to delete ${selectedCropIds.size} selected cell crop${selectedCropIds.size !== 1 ? "s" : ""}? This action cannot be undone.`
        }
        confirmLabel="Delete All"
        isLoading={effectiveViewMode === "fovs" ? bulkDeleteFovsMutation.isPending : bulkDeleteCropsMutation.isPending}
        variant="danger"
      />

      {/* Image preview modal with arrow navigation */}
      <ImagePreviewModal
        images={previewImages}
        currentIndex={previewIndex}
        isOpen={previewOpen}
        onClose={handleClosePreview}
        onNavigate={handlePreviewNavigate}
        onOpenInEditor={handleOpenInEditorFromPreview}
      />

    </div>
  );
}
