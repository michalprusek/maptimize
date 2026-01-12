"use client";

import { useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, Metric } from "@/lib/api";
import {
  Scale,
  Plus,
  Image as ImageIcon,
  BarChart3,
  Loader2,
  Trash2,
  ArrowRight,
  X,
} from "lucide-react";

function CreateMetricDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const createMutation = useMutation({
    mutationFn: () => api.createMetric({ name, description: description || undefined }),
    onSuccess: () => {
      onCreated();
      onClose();
    },
  });

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
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-lg font-display font-semibold text-text-primary">
            Create New Metric
          </h3>
          <button
            onClick={onClose}
            className="p-1 hover:bg-white/10 rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-text-muted" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              Metric Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Bundleness, Polarity, Length"
              className="input-field w-full"
              autoFocus
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              Description (optional)
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this metric measure?"
              className="input-field w-full h-24 resize-none"
            />
          </div>
        </div>

        <div className="flex gap-3 justify-end mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-text-secondary hover:text-text-primary transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={!name.trim() || createMutation.isPending}
            className="btn-primary flex items-center gap-2"
          >
            {createMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Creating...
              </>
            ) : (
              <>
                <Plus className="w-4 h-4" />
                Create Metric
              </>
            )}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

function DeleteMetricDialog({
  metric,
  onClose,
  onConfirm,
  isDeleting,
}: {
  metric: Metric;
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
            Delete Metric
          </h3>
        </div>

        <p className="text-text-secondary mb-2">
          Are you sure you want to delete this metric? This will remove all images and rankings.
        </p>
        <p className="text-sm text-text-muted mb-6 font-mono bg-bg-secondary px-3 py-2 rounded">
          {metric.name}
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

export default function RankingPage() {
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [metricToDelete, setMetricToDelete] = useState<Metric | null>(null);
  const queryClient = useQueryClient();

  const { data: metricsData, isLoading } = useQuery({
    queryKey: ["metrics"],
    queryFn: () => api.getMetrics(),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteMetric(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["metrics"] });
      setMetricToDelete(null);
    },
  });

  const metrics = metricsData?.items || [];

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-bold text-text-primary">
            Ranking Metrics
          </h1>
          <p className="text-text-secondary mt-2">
            Create metrics and rank images through pairwise comparisons
          </p>
        </div>
        <button
          onClick={() => setShowCreateDialog(true)}
          className="btn-primary flex items-center gap-2"
        >
          <Plus className="w-5 h-5" />
          New Metric
        </button>
      </div>

      {/* Metrics Grid */}
      {isLoading ? (
        <div className="glass-card p-12 flex justify-center">
          <Loader2 className="w-10 h-10 text-primary-500 animate-spin" />
        </div>
      ) : metrics.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {metrics.map((metric, i) => (
            <motion.div
              key={metric.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05 }}
              className="glass-card p-6 group relative"
            >
              {/* Delete button */}
              <button
                onClick={(e) => {
                  e.preventDefault();
                  setMetricToDelete(metric);
                }}
                className="absolute top-4 right-4 p-1.5 bg-black/30 hover:bg-accent-red/80 rounded-lg opacity-0 group-hover:opacity-100 transition-all"
                title="Delete metric"
              >
                <Trash2 className="w-4 h-4 text-white" />
              </button>

              <Link href={`/dashboard/ranking/${metric.id}`}>
                <div className="flex items-center gap-3 mb-4">
                  <div className="p-3 bg-primary-500/20 rounded-xl">
                    <Scale className="w-6 h-6 text-primary-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="font-display font-semibold text-text-primary truncate">
                      {metric.name}
                    </h3>
                    {metric.description && (
                      <p className="text-sm text-text-muted truncate">
                        {metric.description}
                      </p>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-4 text-sm text-text-secondary mb-4">
                  <div className="flex items-center gap-1.5">
                    <ImageIcon className="w-4 h-4 text-text-muted" />
                    <span>{metric.image_count} images</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <BarChart3 className="w-4 h-4 text-text-muted" />
                    <span>{metric.comparison_count} comparisons</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-4 border-t border-white/5">
                  <span className="text-sm text-text-muted">
                    Created {new Date(metric.created_at).toLocaleDateString()}
                  </span>
                  <span className="flex items-center gap-1 text-primary-400 text-sm font-medium group-hover:gap-2 transition-all">
                    Open
                    <ArrowRight className="w-4 h-4" />
                  </span>
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      ) : (
        <div className="glass-card p-12 text-center">
          <div className="w-16 h-16 bg-primary-500/10 rounded-2xl flex items-center justify-center mx-auto mb-4">
            <Scale className="w-8 h-8 text-primary-400" />
          </div>
          <h3 className="text-lg font-display font-semibold text-text-primary mb-2">
            No metrics yet
          </h3>
          <p className="text-text-secondary mb-6 max-w-md mx-auto">
            Create your first metric to start ranking images. Each metric can have its own set of images and rankings.
          </p>
          <button
            onClick={() => setShowCreateDialog(true)}
            className="btn-primary inline-flex items-center gap-2"
          >
            <Plus className="w-5 h-5" />
            Create First Metric
          </button>
        </div>
      )}

      {/* Create Dialog */}
      <AnimatePresence>
        {showCreateDialog && (
          <CreateMetricDialog
            onClose={() => setShowCreateDialog(false)}
            onCreated={() => queryClient.invalidateQueries({ queryKey: ["metrics"] })}
          />
        )}
      </AnimatePresence>

      {/* Delete Dialog */}
      <AnimatePresence>
        {metricToDelete && (
          <DeleteMetricDialog
            metric={metricToDelete}
            onClose={() => setMetricToDelete(null)}
            onConfirm={() => deleteMutation.mutate(metricToDelete.id)}
            isDeleting={deleteMutation.isPending}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
