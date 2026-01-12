"use client";

import { useState, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, Image as ApiImage } from "@/lib/api";
import { formatBytes } from "@/lib/utils";
import {
  ArrowLeft,
  Upload,
  Image as ImageIcon,
  CheckCircle,
  Clock,
  AlertCircle,
  Loader2,
  Trash2,
  FileImage,
  Search,
  ArrowUp,
  ArrowDown,
  Filter,
  X,
} from "lucide-react";

type SortField = "date" | "size" | "name";
type SortOrder = "asc" | "desc";

function DeleteImageDialog({
  image,
  onClose,
  onConfirm,
  isDeleting,
}: {
  image: ApiImage;
  onClose: () => void;
  onConfirm: () => void;
  isDeleting: boolean;
}) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        onClick={(e) => e.stopPropagation()}
        className="glass-card p-6 w-full max-w-md"
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2 bg-accent-red/20 rounded-lg">
            <Trash2 className="w-5 h-5 text-accent-red" />
          </div>
          <h3 className="text-lg font-display font-semibold text-text-primary">
            Delete Image
          </h3>
        </div>

        <p className="text-text-secondary mb-2">
          Are you sure you want to delete this image?
        </p>
        <p className="text-sm text-text-muted mb-6 font-mono bg-bg-secondary px-3 py-2 rounded">
          {image.original_filename}
        </p>

        <div className="flex gap-3 justify-end">
          <button
            onClick={onClose}
            disabled={isDeleting}
            className="px-4 py-2 text-text-secondary hover:text-text-primary transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={isDeleting}
            className="btn-primary bg-accent-red hover:bg-accent-red/80 flex items-center gap-2"
          >
            {isDeleting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Deleting...
              </>
            ) : (
              <>
                <Trash2 className="w-4 h-4" />
                Delete
              </>
            )}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

export default function ExperimentDetailPage() {
  const params = useParams();
  const experimentId = Number(params.id);
  const queryClient = useQueryClient();

  // Filter state
  const [searchQuery, setSearchQuery] = useState("");
  const [sortField, setSortField] = useState<SortField>("date");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [proteinFilter, setProteinFilter] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(false);

  const [imageToDelete, setImageToDelete] = useState<ApiImage | null>(null);

  const { data: experiment, isLoading: expLoading } = useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () => api.getExperiment(experimentId),
  });

  const { data: images, isLoading: imagesLoading } = useQuery({
    queryKey: ["images", experimentId],
    queryFn: () => api.getImages(experimentId),
  });

  const { data: proteins } = useQuery({
    queryKey: ["proteins"],
    queryFn: () => api.getProteins(),
  });

  const deleteMutation = useMutation({
    mutationFn: async (imageId: number) => {
      return api.deleteImage(imageId);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["images", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
      setImageToDelete(null);
    },
  });

  const handleDeleteImage = () => {
    if (imageToDelete) {
      deleteMutation.mutate(imageToDelete.id);
    }
  };

  // Filter and sort images
  const filteredImages = useMemo(() => {
    if (!images) return [];

    let result = [...images];

    // Search filter
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter((img) =>
        img.original_filename.toLowerCase().includes(query)
      );
    }

    // Protein filter
    if (proteinFilter !== null) {
      result = result.filter((img) => img.map_protein?.id === proteinFilter);
    }

    // Status filter
    if (statusFilter !== null) {
      result = result.filter((img) => img.status === statusFilter);
    }

    // Sort
    result.sort((a, b) => {
      let comparison = 0;
      switch (sortField) {
        case "date":
          comparison = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          break;
        case "size":
          comparison = (a.file_size || 0) - (b.file_size || 0);
          break;
        case "name":
          comparison = a.original_filename.localeCompare(b.original_filename);
          break;
      }
      return sortOrder === "asc" ? comparison : -comparison;
    });

    return result;
  }, [images, searchQuery, sortField, sortOrder, proteinFilter, statusFilter]);

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "ready":
        return <CheckCircle className="w-5 h-5 text-primary-400" />;
      case "processing":
      case "detecting":
      case "extracting_features":
        return <Loader2 className="w-5 h-5 text-accent-amber animate-spin" />;
      case "error":
        return <AlertCircle className="w-5 h-5 text-accent-red" />;
      default:
        return <Clock className="w-5 h-5 text-text-muted" />;
    }
  };

  const clearFilters = () => {
    setSearchQuery("");
    setProteinFilter(null);
    setStatusFilter(null);
  };

  const hasActiveFilters = searchQuery || proteinFilter !== null || statusFilter !== null;

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
            <ImageIcon className="w-4 h-4 text-text-muted" />
            <span className="text-sm text-text-secondary">
              {experiment.image_count} images
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
      <div className="glass-card p-4">
        <div className="flex items-center gap-4">
          {/* Search */}
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search images..."
              className="input-field pl-10 w-full"
            />
          </div>

          {/* Sort */}
          <div className="flex items-center gap-2">
            <select
              value={sortField}
              onChange={(e) => setSortField(e.target.value as SortField)}
              className="input-field text-sm"
            >
              <option value="date">Date</option>
              <option value="name">Name</option>
              <option value="size">Size</option>
            </select>
            <button
              onClick={() => setSortOrder(sortOrder === "asc" ? "desc" : "asc")}
              className="p-2 hover:bg-white/5 rounded-lg transition-colors"
              title={sortOrder === "asc" ? "Ascending" : "Descending"}
            >
              {sortOrder === "asc" ? (
                <ArrowUp className="w-4 h-4 text-text-secondary" />
              ) : (
                <ArrowDown className="w-4 h-4 text-text-secondary" />
              )}
            </button>
          </div>

          {/* Filter toggle */}
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`p-2 rounded-lg transition-colors ${
              showFilters || hasActiveFilters
                ? "bg-primary-500/20 text-primary-400"
                : "hover:bg-white/5 text-text-secondary"
            }`}
          >
            <Filter className="w-4 h-4" />
          </button>

          {/* Clear filters */}
          {hasActiveFilters && (
            <button
              onClick={clearFilters}
              className="text-sm text-text-muted hover:text-text-secondary flex items-center gap-1"
            >
              <X className="w-3 h-3" />
              Clear
            </button>
          )}
        </div>

        {/* Expanded filters */}
        <AnimatePresence>
          {showFilters && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className="pt-4 mt-4 border-t border-white/5 flex flex-wrap gap-4">
                {/* Protein filter */}
                <div>
                  <label className="block text-xs text-text-muted mb-2">
                    Protein
                  </label>
                  <div className="flex flex-wrap gap-1">
                    <button
                      onClick={() => setProteinFilter(null)}
                      className={`px-2 py-1 rounded text-xs transition-all ${
                        proteinFilter === null
                          ? "bg-primary-500/20 text-primary-400"
                          : "bg-bg-secondary text-text-secondary hover:bg-bg-hover"
                      }`}
                    >
                      All
                    </button>
                    {proteins?.map((p) => (
                      <button
                        key={p.id}
                        onClick={() => setProteinFilter(p.id)}
                        className={`px-2 py-1 rounded text-xs transition-all ${
                          proteinFilter === p.id
                            ? "bg-primary-500/20 text-primary-400"
                            : "bg-bg-secondary text-text-secondary hover:bg-bg-hover"
                        }`}
                      >
                        {p.name}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Status filter */}
                <div>
                  <label className="block text-xs text-text-muted mb-2">
                    Status
                  </label>
                  <div className="flex flex-wrap gap-1">
                    <button
                      onClick={() => setStatusFilter(null)}
                      className={`px-2 py-1 rounded text-xs transition-all ${
                        statusFilter === null
                          ? "bg-primary-500/20 text-primary-400"
                          : "bg-bg-secondary text-text-secondary hover:bg-bg-hover"
                      }`}
                    >
                      All
                    </button>
                    {["ready", "processing", "error"].map((status) => (
                      <button
                        key={status}
                        onClick={() => setStatusFilter(status)}
                        className={`px-2 py-1 rounded text-xs transition-all capitalize ${
                          statusFilter === status
                            ? "bg-primary-500/20 text-primary-400"
                            : "bg-bg-secondary text-text-secondary hover:bg-bg-hover"
                        }`}
                      >
                        {status}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Images Grid */}
      {imagesLoading ? (
        <div className="glass-card p-8 flex justify-center">
          <Loader2 className="w-8 h-8 text-primary-500 animate-spin" />
        </div>
      ) : filteredImages.length > 0 ? (
        <>
          {/* Results count */}
          {hasActiveFilters && (
            <p className="text-sm text-text-muted">
              Showing {filteredImages.length} of {images?.length} images
            </p>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {filteredImages.map((img, i) => (
              <motion.div
                key={img.id}
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: i * 0.02 }}
                className="glass-card overflow-hidden group"
              >
                {/* Image preview */}
                <div className="aspect-video bg-bg-secondary flex items-center justify-center relative overflow-hidden">
                  {img.status === "ready" ? (
                    <img
                      src={api.getImageUrl(img.id, "thumbnail")}
                      alt={img.original_filename}
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        e.currentTarget.style.display = "none";
                        e.currentTarget.nextElementSibling?.classList.remove("hidden");
                      }}
                    />
                  ) : null}
                  <FileImage className={`w-12 h-12 text-text-muted ${img.status === "ready" ? "hidden" : ""}`} />
                  {/* Status badge */}
                  <div className="absolute top-2 right-2">
                    {getStatusIcon(img.status)}
                  </div>
                  {/* Delete button */}
                  <button
                    onClick={() => setImageToDelete(img)}
                    className="absolute top-2 left-2 p-1.5 bg-black/50 hover:bg-accent-red/80 rounded-lg opacity-0 group-hover:opacity-100 transition-all"
                    title="Delete image"
                  >
                    <Trash2 className="w-4 h-4 text-white" />
                  </button>
                </div>

                {/* Info */}
                <div className="p-3">
                  <p className="font-medium text-text-primary truncate text-sm mb-1">
                    {img.original_filename}
                  </p>
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2 text-text-secondary">
                      {img.file_size && (
                        <span>{formatBytes(img.file_size)}</span>
                      )}
                      {img.z_slices && <span>· {img.z_slices}Z</span>}
                    </div>
                    {img.map_protein && (
                      <span
                        className="px-1.5 py-0.5 rounded text-xs font-medium"
                        style={{
                          backgroundColor: `${img.map_protein.color}20`,
                          color: img.map_protein.color,
                        }}
                      >
                        {img.map_protein.name}
                      </span>
                    )}
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        </>
      ) : images && images.length > 0 ? (
        <div className="glass-card p-12 text-center">
          <Search className="w-12 h-12 text-text-muted mx-auto mb-4" />
          <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
            No images match your filters
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
          <ImageIcon className="w-12 h-12 text-text-muted mx-auto mb-4" />
          <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
            No images yet
          </h3>
          <p className="text-text-secondary mb-4">
            Upload your first microscopy images to get started
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

      {/* Delete confirmation dialog */}
      <AnimatePresence>
        {imageToDelete && (
          <DeleteImageDialog
            image={imageToDelete}
            onClose={() => setImageToDelete(null)}
            onConfirm={handleDeleteImage}
            isDeleting={deleteMutation.isPending}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
