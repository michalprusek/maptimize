"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  api,
  MetricImage,
  MetricProgressResponse,
  MetricPairResponse,
  ExperimentForImport,
} from "@/lib/api";
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
  X,
  Download,
} from "lucide-react";

type Tab = "images" | "ranking" | "leaderboard";

function ImportDialog({
  metricId,
  onClose,
  onImported,
}: {
  metricId: number;
  onClose: () => void;
  onImported: () => void;
}) {
  const [selectedExperiments, setSelectedExperiments] = useState<number[]>([]);

  const { data: experiments, isLoading } = useQuery({
    queryKey: ["experiments-for-import", metricId],
    queryFn: () => api.getExperimentsForImport(metricId),
  });

  const importMutation = useMutation({
    mutationFn: () => api.importCropsToMetric(metricId, selectedExperiments),
    onSuccess: (result) => {
      onImported();
      onClose();
    },
  });

  const toggleExperiment = (id: number) => {
    setSelectedExperiments((prev) =>
      prev.includes(id) ? prev.filter((e) => e !== id) : [...prev, id]
    );
  };

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
        className="glass-card p-6 w-full max-w-lg max-h-[80vh] flex flex-col"
      >
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-primary-500/20 rounded-lg">
              <Download className="w-5 h-5 text-primary-400" />
            </div>
            <h3 className="text-lg font-display font-semibold text-text-primary">
              Import from Experiments
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 hover:bg-white/10 rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-text-muted" />
          </button>
        </div>

        <p className="text-text-secondary text-sm mb-4">
          Select experiments to import cell crops from:
        </p>

        <div className="flex-1 overflow-y-auto space-y-2 mb-4">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 text-primary-500 animate-spin" />
            </div>
          ) : experiments && experiments.length > 0 ? (
            experiments.map((exp) => (
              <button
                key={exp.id}
                onClick={() => toggleExperiment(exp.id)}
                className={`w-full p-4 rounded-lg text-left transition-all ${
                  selectedExperiments.includes(exp.id)
                    ? "bg-primary-500/20 border border-primary-500/30"
                    : "bg-bg-secondary hover:bg-bg-hover border border-transparent"
                }`}
              >
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-text-primary">{exp.name}</p>
                    <p className="text-sm text-text-muted">
                      {exp.crop_count} crops · {exp.already_imported} already imported
                    </p>
                  </div>
                  {selectedExperiments.includes(exp.id) && (
                    <Check className="w-5 h-5 text-primary-400" />
                  )}
                </div>
              </button>
            ))
          ) : (
            <p className="text-text-muted text-center py-8">
              No experiments with crops available
            </p>
          )}
        </div>

        <div className="flex gap-3 justify-end pt-4 border-t border-white/5">
          <button
            onClick={onClose}
            className="px-4 py-2 text-text-secondary hover:text-text-primary transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => importMutation.mutate()}
            disabled={selectedExperiments.length === 0 || importMutation.isPending}
            className="btn-primary flex items-center gap-2"
          >
            {importMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Importing...
              </>
            ) : (
              <>
                <Download className="w-4 h-4" />
                Import Selected
              </>
            )}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

export default function MetricDetailPage() {
  const params = useParams();
  const router = useRouter();
  const metricId = Number(params.metricId);
  const queryClient = useQueryClient();

  const [activeTab, setActiveTab] = useState<Tab>("images");
  const [showImportDialog, setShowImportDialog] = useState(false);

  // Ranking state
  const [startTime, setStartTime] = useState<number>(0);
  const [selectedWinner, setSelectedWinner] = useState<number | null>(null);
  const [showKeyboardHint, setShowKeyboardHint] = useState(true);

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

  const deleteImageMutation = useMutation({
    mutationFn: (imageId: number) => api.deleteMetricImage(metricId, imageId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["metric-images", metricId] });
      queryClient.invalidateQueries({ queryKey: ["metric", metricId] });
    },
  });

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
      queryClient.invalidateQueries({ queryKey: ["metric-progress", metricId] });
      setTimeout(() => refetchPair(), 300);
    },
  });

  const undoMutation = useMutation({
    mutationFn: () => api.undoMetricComparison(metricId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["metric-progress", metricId] });
      refetchPair();
    },
  });

  // Reset start time when new pair loads
  useEffect(() => {
    if (pair) {
      setStartTime(Date.now());
    }
  }, [pair]);

  // Keyboard shortcuts for ranking
  const handleKeyPress = useCallback(
    (e: KeyboardEvent) => {
      if (activeTab !== "ranking" || !pair || compareMutation.isPending) return;

      if (e.key === "a" || e.key === "A" || e.key === "ArrowLeft") {
        setSelectedWinner(pair.image_a.id);
        setTimeout(() => compareMutation.mutate(pair.image_a.id), 200);
      } else if (e.key === "d" || e.key === "D" || e.key === "ArrowRight") {
        setSelectedWinner(pair.image_b.id);
        setTimeout(() => compareMutation.mutate(pair.image_b.id), 200);
      } else if (e.key === "z" && (e.ctrlKey || e.metaKey)) {
        undoMutation.mutate();
      }
    },
    [activeTab, pair, compareMutation, undoMutation]
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
        {activeTab === "ranking" && (
          <button
            onClick={() => undoMutation.mutate()}
            disabled={undoMutation.isPending || !progress?.total_comparisons}
            className="btn-secondary flex items-center gap-2"
          >
            <RotateCcw className="w-5 h-5" />
            Undo
          </button>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-white/5 pb-2">
        {[
          { id: "images" as Tab, label: "Images", icon: ImageIcon },
          { id: "ranking" as Tab, label: "Ranking", icon: Scale },
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

      {/* Tab Content */}
      {activeTab === "images" && (
        <div className="space-y-6">
          {/* Import section */}
          <div className="flex justify-end">
            <button
              onClick={() => setShowImportDialog(true)}
              className="btn-primary flex items-center gap-2"
            >
              <Download className="w-4 h-4" />
              Import from Experiments
            </button>
          </div>

          {/* Images grid */}
          {imagesLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-8 h-8 text-primary-500 animate-spin" />
            </div>
          ) : images && images.length > 0 ? (
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
              {images.map((img) => (
                <motion.div
                  key={img.id}
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="glass-card overflow-hidden group"
                >
                  <div className="aspect-square bg-bg-secondary relative">
                    <img
                      src={getImageUrl(img)}
                      alt={`Image ${img.id}`}
                      className="w-full h-full object-cover"
                    />
                    <button
                      onClick={() => deleteImageMutation.mutate(img.id)}
                      className="absolute top-2 right-2 p-1.5 bg-black/50 hover:bg-accent-red/80 rounded-lg opacity-0 group-hover:opacity-100 transition-all"
                    >
                      <Trash2 className="w-4 h-4 text-white" />
                    </button>
                  </div>
                  {img.mu !== undefined && (
                    <div className="p-2 text-xs text-text-muted text-center">
                      Score: {img.ordinal_score?.toFixed(2) || "-"}
                    </div>
                  )}
                </motion.div>
              ))}
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
                        Use <kbd className="px-2 py-1 bg-bg-secondary rounded text-text-primary font-mono text-sm">A</kbd> or{" "}
                        <kbd className="px-2 py-1 bg-bg-secondary rounded text-text-primary font-mono text-sm">←</kbd> for left,{" "}
                        <kbd className="px-2 py-1 bg-bg-secondary rounded text-text-primary font-mono text-sm">D</kbd> or{" "}
                        <kbd className="px-2 py-1 bg-bg-secondary rounded text-text-primary font-mono text-sm">→</kbd> for right
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

              {/* Comparison cards */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {[pair.image_a, pair.image_b].map((img, i) => {
                  const isSelected = selectedWinner === img.id;
                  const isOther = selectedWinner !== null && !isSelected;

                  return (
                    <motion.div
                      key={img.id}
                      layout
                      whileHover={{ scale: compareMutation.isPending ? 1 : 1.02 }}
                      whileTap={{ scale: compareMutation.isPending ? 1 : 0.98 }}
                      animate={{
                        opacity: isOther ? 0.5 : 1,
                        scale: isSelected ? 1.02 : 1,
                      }}
                      className={`glass-card overflow-hidden cursor-pointer transition-all duration-200 ${
                        isSelected
                          ? "ring-2 ring-primary-500 border-primary-500/50"
                          : "hover:border-primary-500/30"
                      } ${compareMutation.isPending ? "pointer-events-none" : ""}`}
                      onClick={() => handleSelect(img.id)}
                    >
                      <div className="aspect-square bg-bg-secondary relative">
                        {img.image_url ? (
                          <img
                            src={`http://localhost:8000${img.image_url}`}
                            alt={`Image ${img.id}`}
                            className="w-full h-full object-contain"
                          />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center">
                            <Target className="w-16 h-16 text-text-muted" />
                          </div>
                        )}

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
                          <span className="px-3 py-1.5 bg-black/50 backdrop-blur-sm rounded-lg text-white font-mono text-sm">
                            {i === 0 ? "A / ←" : "D / →"}
                          </span>
                        </div>
                      </div>

                      <div className="p-4">
                        <p className="text-sm text-text-muted">
                          Image #{img.id}
                          {img.original_filename && ` · ${img.original_filename}`}
                        </p>
                      </div>
                    </motion.div>
                  );
                })}
              </div>

              <div className="text-center text-text-muted">
                Comparison #{pair.comparison_number}
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
                  </tr>
                </thead>
                <tbody>
                  {leaderboard.items.map((item, i) => (
                    <tr key={item.metric_image_id} className="border-b border-white/5 last:border-0">
                      <td className="px-4 py-3">
                        <span className={`font-mono ${i < 3 ? "text-primary-400 font-bold" : "text-text-primary"}`}>
                          #{item.rank}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          {item.image_url && (
                            <img
                              src={`http://localhost:8000${item.image_url}`}
                              alt={`Rank ${item.rank}`}
                              className="w-10 h-10 rounded object-cover"
                            />
                          )}
                          <span className="text-text-primary">
                            {item.original_filename || `Image #${item.metric_image_id}`}
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
                    </tr>
                  ))}
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
                Start Ranking
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
    </div>
  );
}
