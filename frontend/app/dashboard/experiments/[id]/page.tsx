"use client";

import { useState, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { api, CellCropGallery } from "@/lib/api";
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

  // Filter state
  const [searchQuery, setSearchQuery] = useState("");
  const [sortField, setSortField] = useState<SortField>("date");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [proteinFilter, setProteinFilter] = useState<string | null>(null);

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

      {/* Search and Filters */}
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
                className="glass-card overflow-hidden group"
              >
                {/* Cell crop preview */}
                <div className="aspect-square bg-bg-secondary flex items-center justify-center relative overflow-hidden">
                  <img
                    src={api.getCropImageUrl(crop.id, "mip")}
                    alt={`Cell from ${crop.parent_filename}`}
                    className="w-full h-full object-contain"
                    loading="lazy"
                    onError={(e) => {
                      e.currentTarget.style.display = "none";
                      e.currentTarget.nextElementSibling?.classList.remove("hidden");
                    }}
                  />
                  <Microscope className="w-10 h-10 text-text-muted hidden" />
                </div>

                {/* Info */}
                <div className="p-2 space-y-1">
                  {/* Parent filename */}
                  <p className="text-xs text-text-muted truncate" title={crop.parent_filename}>
                    {crop.parent_filename}
                  </p>

                  {/* Metrics row */}
                  <div className="flex items-center justify-between">
                    {/* Bundleness score */}
                    {crop.bundleness_score !== null && crop.bundleness_score !== undefined && (
                      <span className="text-sm font-mono text-text-secondary">
                        B: {crop.bundleness_score.toFixed(2)}
                      </span>
                    )}

                    {/* MAP protein badge */}
                    {crop.map_protein_name && (
                      <span
                        className="px-1.5 py-0.5 rounded text-xs font-medium"
                        style={{
                          backgroundColor: `${crop.map_protein_color}20`,
                          color: crop.map_protein_color,
                        }}
                      >
                        {crop.map_protein_name}
                      </span>
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
    </div>
  );
}
