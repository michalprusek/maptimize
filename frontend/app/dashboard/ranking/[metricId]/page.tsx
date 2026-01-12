"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, API_URL, MetricImage, MetricImageForRanking } from "@/lib/api";
import { ImportDialog } from "@/components/metric/ImportDialog";
import { ConfirmModal } from "@/components/ui";
import {
  ImageGalleryFilters,
  SortOrder,
  SortOption,
  ProteinInfo,
} from "@/components/shared";
import {
  ArrowLeft,
  Scale,
  Trophy,
  Image as ImageIcon,
  Loader2,
  Trash2,
  Check,
  Keyboard,
  RotateCcw,
  TrendingUp,
  Target,
  AlertCircle,
  Download,
  SkipForward,
  X,
  ZoomIn,
} from "lucide-react";

type Tab = "images" | "ranking" | "leaderboard";
type ImageSortField = "date" | "score" | "filename" | "comparisons";

const IMAGE_SORT_OPTIONS: SortOption<ImageSortField>[] = [
  { value: "date", label: "Date Added" },
  { value: "score", label: "Score" },
  { value: "filename", label: "Filename" },
  { value: "comparisons", label: "Comparisons" },
];

export default function MetricDetailPage(): JSX.Element {
  const params = useParams();
  const metricId = Number(params.metricId);
  const queryClient = useQueryClient();

  const [activeTab, setActiveTab] = useState<Tab>("images");
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [imageToDelete, setImageToDelete] = useState<{ id: number; name: string } | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [lightboxImage, setLightboxImage] = useState<{ url: string; name: string } | null>(null);

  // Ranking state
  const [startTime, setStartTime] = useState<number>(0);
  const [selectedWinner, setSelectedWinner] = useState<number | null>(null);
  const [showKeyboardHint, setShowKeyboardHint] = useState(true);

  // Image gallery filter state
  const [searchQuery, setSearchQuery] = useState("");
  const [sortField, setSortField] = useState<ImageSortField>("date");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");

  const { data: metric, isLoading: metricLoading } = useQuery({
    queryKey: ["metric", metricId],
    queryFn: () => api.getMetric(metricId),
  });

  const { data: images, isLoading: imagesLoading } = useQuery({
    queryKey: ["metric-images", metricId],
    queryFn: () => api.getMetricImages(metricId),
  });

  const { data: progress } = useQuery({
    queryKey: ["metric-progress", metricId],
    queryFn: () => api.getMetricProgress(metricId),
    refetchInterval: activeTab === "ranking" ? 5000 : false,
  });

  const { data: pair, isLoading: pairLoading, error: pairError, refetch: refetchPair } = useQuery({
    queryKey: ["metric-pair", metricId],
    queryFn: () => api.getMetricPair(metricId),
    enabled: activeTab === "ranking",
    retry: false,
  });

  const { data: leaderboard, isLoading: leaderboardLoading } = useQuery({
    queryKey: ["metric-leaderboard", metricId],
    queryFn: () => api.getMetricLeaderboard(metricId),
    enabled: activeTab === "leaderboard",
  });

  // Filter and sort images
  const filteredImages = useMemo(() => {
    if (!images) return [];

    let result = [...images];

    // Search filter (by filename)
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter((img) =>
        img.original_filename?.toLowerCase().includes(query)
      );
    }

    // Sort
    result.sort((a, b) => {
      let comparison = 0;
      switch (sortField) {
        case "date":
          comparison = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          break;
        case "score":
          comparison = (a.ordinal_score ?? -Infinity) - (b.ordinal_score ?? -Infinity);
          break;
        case "filename":
          comparison = (a.original_filename ?? "").localeCompare(b.original_filename ?? "");
          break;
        case "comparisons":
          comparison = a.comparison_count - b.comparison_count;
          break;
      }
      return sortOrder === "asc" ? comparison : -comparison;
    });

    return result;
  }, [images, searchQuery, sortField, sortOrder]);

  const clearFilters = () => {
    setSearchQuery("");
  };

  const hasActiveFilters = !!searchQuery;

  const deleteImageMutation = useMutation({
    mutationFn: (imageId: number) => api.deleteMetricImage(metricId, imageId),
    onSuccess: () => {
      setImageToDelete(null);
      setMutationError(null);
      queryClient.invalidateQueries({ queryKey: ["metric-images", metricId] });
      queryClient.invalidateQueries({ queryKey: ["metric", metricId] });
      queryClient.invalidateQueries({ queryKey: ["metric-pair", metricId] });
      queryClient.invalidateQueries({ queryKey: ["metric-leaderboard", metricId] });
    },
    onError: (error: Error) => {
      console.error("Failed to delete image:", error);
      setMutationError(error.message || "Failed to delete image. Please try again.");
    },
  });


  const handleDeleteClick = (id: number, name: string) => {
    setImageToDelete({ id, name });
  };

  const handleConfirmDelete = () => {
    if (imageToDelete) {
      deleteImageMutation.mutate(imageToDelete.id);
    }
  };

  const compareMutation = useMutation({
    mutationFn: async (winnerId: number) => {
      if (!pair) return;
      const responseTime = Date.now() - startTime;
      return api.submitMetricComparison(metricId, {
        image_a_id: pair.image_a.id,
        image_b_id: pair.image_b.id,
        winner_id: winnerId,
        response_time_ms: responseTime,
      });
    },
    onSuccess: () => {
      setSelectedWinner(null);
      setMutationError(null);
      queryClient.invalidateQueries({ queryKey: ["metric-progress", metricId] });
      setTimeout(() => refetchPair(), 300);
    },
    onError: (err: Error) => {
      console.error("Failed to submit comparison:", err);
      setMutationError(err.message || "Failed to submit comparison. Please try again.");
      setSelectedWinner(null);
    },
  });

  const undoMutation = useMutation({
    mutationFn: () => api.undoMetricComparison(metricId),
    onSuccess: () => {
      setMutationError(null);
      queryClient.invalidateQueries({ queryKey: ["metric-progress", metricId] });
      refetchPair();
    },
    onError: (err: Error) => {
      console.error("Failed to undo comparison:", err);
      setMutationError(err.message || "Failed to undo last comparison. Please try again.");
    },
  });

  // Reset start time when new pair loads
  useEffect(() => {
    if (pair) {
      setStartTime(Date.now());
    }
  }, [pair]);

  // Skip to next pair without voting
  const handleSkip = useCallback(() => {
    if (compareMutation.isPending) return;
    refetchPair();
  }, [compareMutation.isPending, refetchPair]);

  // Keyboard shortcuts for ranking
  const handleKeyPress = useCallback(
    (e: KeyboardEvent) => {
      if (activeTab !== "ranking" || !pair || compareMutation.isPending) return;

      if (e.key === "ArrowLeft") {
        setSelectedWinner(pair.image_a.id);
        setTimeout(() => compareMutation.mutate(pair.image_a.id), 200);
      } else if (e.key === "ArrowRight") {
        setSelectedWinner(pair.image_b.id);
        setTimeout(() => compareMutation.mutate(pair.image_b.id), 200);
      } else if (e.key === " " || e.key === "Spacebar") {
        e.preventDefault(); // Prevent page scroll
        handleSkip();
      } else if (e.key === "z" && (e.ctrlKey || e.metaKey)) {
        undoMutation.mutate();
      }
    },
    [activeTab, pair, compareMutation, undoMutation, handleSkip]
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyPress);
    return () => window.removeEventListener("keydown", handleKeyPress);
  }, [handleKeyPress]);

  const handleSelect = (winnerId: number) => {
    if (compareMutation.isPending) return;
    setSelectedWinner(winnerId);
    setTimeout(() => compareMutation.mutate(winnerId), 200);
  };

  const getImageUrl = (img: MetricImage) => {
    if (img.cell_crop_id) {
      return api.getCropImageUrl(img.cell_crop_id);
    }
    return api.getMetricImageUrl(metricId, img.id);
  };

  // Helper to build authenticated image URL from API-provided relative path
  const getAuthImageUrl = (imageUrl: string | undefined) => {
    if (!imageUrl) return null;
    const token = api.getToken();
    const separator = imageUrl.includes("?") ? "&" : "?";
    return `${API_URL}${imageUrl}${separator}token=${token}`;
  };

  if (metricLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-10 h-10 text-primary-500 animate-spin" />
      </div>
    );
  }

  if (!metric) {
    return (
      <div className="text-center py-12">
        <p className="text-text-secondary">Metric not found</p>
        <Link href="/dashboard/ranking" className="btn-primary mt-4">
          Back to Metrics
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link
          href="/dashboard/ranking"
          className="p-2 hover:bg-white/5 rounded-lg transition-colors"
        >
          <ArrowLeft className="w-5 h-5 text-text-secondary" />
        </Link>
        <div className="flex-1">
          <h1 className="text-3xl font-display font-bold text-text-primary">
            {metric.name}
          </h1>
          {metric.description && (
            <p className="text-text-secondary mt-1">{metric.description}</p>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center justify-between border-b border-white/5 pb-2">
        <div className="flex items-center gap-2">
          {[
            { id: "images" as Tab, label: "Images", icon: ImageIcon },
            { id: "ranking" as Tab, label: "Metrics", icon: Scale },
            { id: "leaderboard" as Tab, label: "Leaderboard", icon: Trophy },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all ${
                activeTab === tab.id
                  ? "bg-primary-500/20 text-primary-400"
                  : "text-text-secondary hover:bg-white/5 hover:text-text-primary"
              }`}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          ))}
        </div>
        <button
          onClick={() => setShowImportDialog(true)}
          className="btn-primary flex items-center gap-2"
        >
          <Download className="w-4 h-4" />
          Import
        </button>
      </div>

      {/* Error notification */}
      {mutationError && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="p-4 bg-accent-red/10 border border-accent-red/20 rounded-lg flex items-start gap-3"
        >
          <AlertCircle className="w-5 h-5 text-accent-red flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-accent-red font-medium">Operation failed</p>
            <p className="text-sm text-text-secondary">{mutationError}</p>
          </div>
          <button
            onClick={() => setMutationError(null)}
            className="text-text-muted hover:text-text-primary"
          >
            ×
          </button>
        </motion.div>
      )}

      {/* Tab Content */}
      {activeTab === "images" && (
        <div className="space-y-6">
          {/* Search and Filters */}
          <ImageGalleryFilters
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            searchPlaceholder="Search by filename..."
            sortField={sortField}
            onSortFieldChange={setSortField}
            sortOrder={sortOrder}
            onSortOrderChange={setSortOrder}
            sortOptions={IMAGE_SORT_OPTIONS}
            onClearFilters={clearFilters}
            hasActiveFilters={hasActiveFilters}
          />

          {/* Images grid */}
          {imagesLoading ? (
            <div className="flex justify-center py-8">
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

              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {filteredImages.map((img) => (
                  <motion.div
                    key={img.id}
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    className="aspect-square rounded-2xl overflow-hidden relative group bg-bg-secondary"
                  >
                    <img
                      src={getImageUrl(img)}
                      alt={`Image ${img.id}`}
                      className="w-full h-full object-cover"
                    />
                    <button
                      onClick={() => handleDeleteClick(img.id, `Image #${img.id}`)}
                      className="absolute top-2 right-2 p-1.5 bg-black/50 hover:bg-accent-red/80 rounded-lg opacity-0 group-hover:opacity-100 transition-all"
                      title="Remove from metric"
                    >
                      <Trash2 className="w-4 h-4 text-white" />
                    </button>
                  </motion.div>
                ))}
              </div>
            </>
          ) : images && images.length > 0 ? (
            <div className="glass-card p-12 text-center">
              <ImageIcon className="w-12 h-12 text-text-muted mx-auto mb-4" />
              <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
                No images match your filters
              </h3>
              <p className="text-text-secondary mb-4">
                Try adjusting your search criteria
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
                Import images from experiments to start ranking
              </p>
              <button
                onClick={() => setShowImportDialog(true)}
                className="btn-primary inline-flex items-center gap-2"
              >
                <Download className="w-4 h-4" />
                Import from Experiments
              </button>
            </div>
          )}
        </div>
      )}

      {activeTab === "ranking" && (
        <div className="space-y-6">
          {/* Progress */}
          {progress && (
            <div className="glass-card p-6">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-primary-500/20 rounded-lg">
                    <TrendingUp className="w-5 h-5 text-primary-400" />
                  </div>
                  <div>
                    <p className="font-medium text-text-primary">Convergence Progress</p>
                    <p className="text-sm text-text-secondary">
                      {progress.total_comparisons} comparisons · {progress.image_count} images · {progress.phase} phase
                    </p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-2xl font-display font-bold text-primary-400">
                    {progress.convergence_percent.toFixed(1)}%
                  </p>
                  <p className="text-sm text-text-muted">
                    ~{progress.estimated_remaining} remaining
                  </p>
                </div>
              </div>
              <div className="h-3 bg-bg-secondary rounded-full overflow-hidden">
                <motion.div
                  className="h-full bg-gradient-to-r from-primary-600 to-primary-400 rounded-full"
                  initial={{ width: 0 }}
                  animate={{ width: `${progress.convergence_percent}%` }}
                  transition={{ duration: 0.5, ease: "easeOut" }}
                />
              </div>
            </div>
          )}

          {/* Comparison Area */}
          {pairLoading ? (
            <div className="glass-card p-12 flex justify-center">
              <div className="text-center">
                <Loader2 className="w-10 h-10 text-primary-500 animate-spin mx-auto mb-4" />
                <p className="text-text-secondary">Loading next pair...</p>
              </div>
            </div>
          ) : pairError ? (
            <div className="glass-card p-12 text-center">
              <AlertCircle className="w-12 h-12 text-accent-amber mx-auto mb-4" />
              <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
                Not enough images
              </h3>
              <p className="text-text-secondary mb-4">
                Add at least 2 images to start ranking
              </p>
              <button
                onClick={() => setActiveTab("images")}
                className="btn-primary"
              >
                Add Images
              </button>
            </div>
          ) : pair ? (
            <>
              {/* Keyboard hint */}
              <AnimatePresence>
                {showKeyboardHint && (
                  <motion.div
                    initial={{ opacity: 0, y: -10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className="glass-card p-4 flex items-center justify-between"
                  >
                    <div className="flex items-center gap-3">
                      <Keyboard className="w-5 h-5 text-primary-400" />
                      <p className="text-text-secondary">
                        <kbd className="px-2 py-1 bg-bg-secondary rounded text-text-primary font-mono text-sm">←</kbd> left,{" "}
                        <kbd className="px-2 py-1 bg-bg-secondary rounded text-text-primary font-mono text-sm">→</kbd> right,{" "}
                        <kbd className="px-2 py-1 bg-bg-secondary rounded text-text-primary font-mono text-sm">Space</kbd> skip
                      </p>
                    </div>
                    <button
                      onClick={() => setShowKeyboardHint(false)}
                      className="text-text-muted hover:text-text-primary"
                    >
                      Got it
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Comparison cards with Skip in the middle */}
              <div className="flex items-stretch gap-4">
                {[pair.image_a, pair.image_b].map((img, i) => {
                  const isSelected = selectedWinner === img.id;
                  const isOther = selectedWinner !== null && !isSelected;

                  return (
                    <div key={img.id} className="flex-1 flex flex-col">
                      <motion.div
                        layout
                        whileHover={{ scale: compareMutation.isPending ? 1 : 1.01 }}
                        whileTap={{ scale: compareMutation.isPending ? 1 : 0.99 }}
                        animate={{
                          opacity: isOther ? 0.5 : 1,
                          scale: isSelected ? 1.01 : 1,
                        }}
                        className={`glass-card overflow-hidden cursor-pointer transition-all duration-200 flex-1 ${
                          isSelected
                            ? "ring-2 ring-primary-500 border-primary-500/50"
                            : "hover:border-primary-500/30"
                        } ${compareMutation.isPending ? "pointer-events-none" : ""}`}
                        onClick={() => handleSelect(img.id)}
                      >
                        {/* Canvas container - natural image size, no scaling */}
                        <div className="bg-bg-secondary relative group flex items-center justify-center p-4">
                          {getAuthImageUrl(img.image_url) ? (
                            <img
                              src={getAuthImageUrl(img.image_url)!}
                              alt={`Image ${img.id}`}
                            />
                          ) : (
                            <div className="flex items-center justify-center p-12">
                              <Target className="w-16 h-16 text-text-muted" />
                            </div>
                          )}

                          {/* Exclude button */}
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteClick(img.id, img.original_filename || `Image #${img.id}`);
                            }}
                            className="absolute top-2 right-2 p-2 bg-black/50 hover:bg-accent-red/80 rounded-lg opacity-0 group-hover:opacity-100 transition-all z-10"
                            title="Remove from metric"
                          >
                            <Trash2 className="w-4 h-4 text-white" />
                          </button>

                          <AnimatePresence>
                            {isSelected && (
                              <motion.div
                                initial={{ opacity: 0 }}
                                animate={{ opacity: 1 }}
                                exit={{ opacity: 0 }}
                                className="absolute inset-0 bg-primary-500/20 flex items-center justify-center"
                              >
                                <div className="p-4 bg-primary-500 rounded-full">
                                  <Check className="w-8 h-8 text-white" />
                                </div>
                              </motion.div>
                            )}
                          </AnimatePresence>

                          <div className="absolute bottom-4 left-4">
                            <span className="px-3 py-1.5 bg-black/50 backdrop-blur-sm rounded-lg text-white font-mono text-lg">
                              {i === 0 ? "←" : "→"}
                            </span>
                          </div>
                        </div>

                        <div className="p-3">
                          <p className="text-sm text-text-muted">
                            Image #{img.id}
                            {img.original_filename && ` · ${img.original_filename}`}
                          </p>
                        </div>
                      </motion.div>
                    </div>
                  );
                })}
              </div>

              {/* Skip, Undo buttons and comparison counter */}
              <div className="flex items-center justify-center gap-4">
                <button
                  onClick={() => undoMutation.mutate()}
                  disabled={undoMutation.isPending || !progress?.total_comparisons}
                  className="btn-secondary flex items-center gap-2"
                  title="Ctrl+Z"
                >
                  <RotateCcw className="w-4 h-4" />
                  Undo
                </button>
                <button
                  onClick={handleSkip}
                  disabled={compareMutation.isPending}
                  className="btn-secondary flex items-center gap-2"
                >
                  <SkipForward className="w-4 h-4" />
                  Skip
                </button>
                <span className="text-text-muted">
                  #{pair.comparison_number}
                </span>
              </div>
            </>
          ) : null}
        </div>
      )}

      {activeTab === "leaderboard" && (
        <div>
          {leaderboardLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-8 h-8 text-primary-500 animate-spin" />
            </div>
          ) : leaderboard && leaderboard.items.length > 0 ? (
            <div className="glass-card overflow-hidden">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-white/5">
                    <th className="px-4 py-3 text-left text-sm font-medium text-text-secondary">Rank</th>
                    <th className="px-4 py-3 text-left text-sm font-medium text-text-secondary">Image</th>
                    <th className="px-4 py-3 text-right text-sm font-medium text-text-secondary">Score</th>
                    <th className="px-4 py-3 text-right text-sm font-medium text-text-secondary">Comparisons</th>
                    <th className="px-4 py-3 text-right text-sm font-medium text-text-secondary">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {leaderboard.items.map((item, i) => {
                    const imageUrl = getAuthImageUrl(item.image_url);
                    const imageName = item.original_filename || `Image #${item.metric_image_id}`;
                    return (
                      <tr
                        key={item.metric_image_id}
                        className="border-b border-white/5 last:border-0 hover:bg-white/5 cursor-pointer transition-colors"
                        onClick={() => imageUrl && setLightboxImage({ url: imageUrl, name: imageName })}
                      >
                        <td className="px-4 py-3">
                          <span className={`font-mono ${i < 3 ? "text-primary-400 font-bold" : "text-text-primary"}`}>
                            #{item.rank}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-3">
                            {imageUrl && (
                              <div className="relative group/img">
                                <img
                                  src={imageUrl}
                                  alt={`Rank ${item.rank}`}
                                  className="w-10 h-10 rounded object-cover"
                                />
                                <div className="absolute inset-0 bg-black/50 rounded opacity-0 group-hover/img:opacity-100 transition-opacity flex items-center justify-center">
                                  <ZoomIn className="w-4 h-4 text-white" />
                                </div>
                              </div>
                            )}
                            <span className="text-text-primary">
                              {imageName}
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <span className="font-mono text-text-primary">
                            {item.ordinal_score.toFixed(2)}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right text-text-secondary">
                          {item.comparison_count}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteClick(item.metric_image_id, imageName);
                            }}
                            className="p-1.5 hover:bg-accent-red/20 text-text-muted hover:text-accent-red rounded-lg transition-colors"
                            title="Remove from metric"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="glass-card p-12 text-center">
              <Trophy className="w-12 h-12 text-text-muted mx-auto mb-4" />
              <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
                No rankings yet
              </h3>
              <p className="text-text-secondary mb-4">
                Complete some comparisons to see the leaderboard
              </p>
              <button
                onClick={() => setActiveTab("ranking")}
                className="btn-primary"
              >
                Start Comparing
              </button>
            </div>
          )}
        </div>
      )}

      {/* Import Dialog */}
      <AnimatePresence>
        {showImportDialog && (
          <ImportDialog
            metricId={metricId}
            onClose={() => setShowImportDialog(false)}
            onImported={() => {
              queryClient.invalidateQueries({ queryKey: ["metric-images", metricId] });
              queryClient.invalidateQueries({ queryKey: ["metric", metricId] });
            }}
          />
        )}
      </AnimatePresence>

      {/* Delete Image Confirmation Modal */}
      <ConfirmModal
        isOpen={!!imageToDelete}
        onClose={() => setImageToDelete(null)}
        onConfirm={handleConfirmDelete}
        title="Remove Image"
        message="Are you sure you want to remove this image from the metric?"
        detail={imageToDelete?.name}
        confirmLabel="Remove"
        cancelLabel="Cancel"
        isLoading={deleteImageMutation.isPending}
        variant="danger"
      />

      {/* Image Lightbox */}
      <AnimatePresence>
        {lightboxImage && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4"
            onClick={() => setLightboxImage(null)}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              className="relative max-w-[90vw] max-h-[90vh]"
              onClick={(e) => e.stopPropagation()}
            >
              <img
                src={lightboxImage.url}
                alt={lightboxImage.name}
                className="max-w-full max-h-[85vh] object-contain rounded-lg"
              />
              <div className="absolute top-0 left-0 right-0 flex items-center justify-between p-4 bg-gradient-to-b from-black/50 to-transparent">
                <span className="text-white font-medium truncate max-w-[80%]">
                  {lightboxImage.name}
                </span>
                <button
                  onClick={() => setLightboxImage(null)}
                  className="p-2 hover:bg-white/20 rounded-lg transition-colors"
                >
                  <X className="w-5 h-5 text-white" />
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
