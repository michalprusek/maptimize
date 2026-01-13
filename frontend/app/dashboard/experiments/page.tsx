"use client";

import { useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ConfirmModal, StatusBadge } from "@/components/ui";
import {
  Plus,
  FolderOpen,
  Image as ImageIcon,
  ArrowRight,
  X,
  Loader2,
  Trash2,
  AlertCircle,
  Layers,
} from "lucide-react";

export default function ExperimentsPage(): JSX.Element {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newExpName, setNewExpName] = useState("");
  const [newExpDescription, setNewExpDescription] = useState("");
  const [experimentToDelete, setExperimentToDelete] = useState<{ id: number; name: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: experiments, isLoading } = useQuery({
    queryKey: ["experiments"],
    queryFn: () => api.getExperiments(),
  });

  const createMutation = useMutation({
    mutationFn: (data: { name: string; description?: string }) =>
      api.createExperiment(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      setShowCreateModal(false);
      setNewExpName("");
      setNewExpDescription("");
      setError(null);
    },
    onError: (err: Error) => {
      console.error("Failed to create experiment:", err);
      setError(err.message || "Failed to create experiment. Please try again.");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteExperiment(id),
    onSuccess: () => {
      setExperimentToDelete(null);
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      setError(null);
    },
    onError: (err: Error) => {
      console.error("Failed to delete experiment:", err);
      setError(err.message || "Failed to delete experiment. Please try again.");
      setExperimentToDelete(null);
    },
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    createMutation.mutate({
      name: newExpName,
      description: newExpDescription || undefined,
    });
  };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-bold text-text-primary">
            Experiments
          </h1>
          <p className="text-text-secondary mt-2">
            Manage your microscopy image collections
          </p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="btn-primary flex items-center gap-2"
        >
          <Plus className="w-5 h-5" />
          New Experiment
        </button>
      </div>

      {/* Error notification */}
      {error && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="p-4 bg-accent-red/10 border border-accent-red/20 rounded-lg flex items-start gap-3"
        >
          <AlertCircle className="w-5 h-5 text-accent-red flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-accent-red font-medium">Operation failed</p>
            <p className="text-sm text-text-secondary">{error}</p>
          </div>
          <button
            onClick={() => setError(null)}
            className="text-text-muted hover:text-text-primary"
          >
            ×
          </button>
        </motion.div>
      )}

      {/* Experiments Grid */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <div className="w-10 h-10 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : experiments && experiments.length > 0 ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"
        >
          {experiments.map((exp, i) => (
            <motion.div
              key={exp.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05 }}
            >
              <Link href={`/dashboard/experiments/${exp.id}`}>
                <div className="glass-card p-6 h-full cursor-pointer group hover:border-primary-500/30 transition-all duration-300 card-hover">
                  <div className="flex items-start justify-between mb-4">
                    <div className="p-3 bg-primary-500/10 rounded-xl">
                      <FolderOpen className="w-6 h-6 text-primary-400" />
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusBadge status={exp.status} />
                      <button
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          setExperimentToDelete({ id: exp.id, name: exp.name });
                        }}
                        className="p-1.5 hover:bg-accent-red/20 text-text-muted hover:text-accent-red rounded-lg transition-colors opacity-0 group-hover:opacity-100"
                        title="Delete experiment"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  <h3 className="font-display font-semibold text-lg text-text-primary mb-2 group-hover:text-primary-400 transition-colors">
                    {exp.name}
                  </h3>

                  {exp.description && (
                    <p className="text-sm text-text-secondary mb-4 line-clamp-2">
                      {exp.description}
                    </p>
                  )}

                  <div className="flex items-center gap-4 text-sm text-text-muted">
                    <div className="flex items-center gap-1">
                      <ImageIcon className="w-4 h-4" />
                      <span>{exp.image_count} images</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <Layers className="w-4 h-4" />
                      <span>{exp.cell_count} crops</span>
                    </div>
                  </div>

                  <div className="flex items-center justify-between mt-4 pt-4 border-t border-white/5">
                    <span className="text-xs text-text-muted">
                      {new Date(exp.created_at).toLocaleDateString()}
                    </span>
                    <ArrowRight className="w-5 h-5 text-text-muted group-hover:text-primary-400 group-hover:translate-x-1 transition-all" />
                  </div>
                </div>
              </Link>
            </motion.div>
          ))}
        </motion.div>
      ) : (
        <div className="glass-card p-12 text-center">
          <div className="w-20 h-20 bg-primary-500/10 rounded-2xl flex items-center justify-center mx-auto mb-6">
            <FolderOpen className="w-10 h-10 text-primary-400" />
          </div>
          <h3 className="text-xl font-display font-semibold text-text-primary mb-2">
            No experiments yet
          </h3>
          <p className="text-text-secondary mb-6 max-w-md mx-auto">
            Create your first experiment to organize and analyze your microscopy images
          </p>
          <button
            onClick={() => setShowCreateModal(true)}
            className="btn-primary inline-flex items-center gap-2"
          >
            <Plus className="w-5 h-5" />
            Create Experiment
          </button>
        </div>
      )}

      {/* Create Modal */}
      <AnimatePresence>
        {showCreateModal && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4"
            onClick={() => setShowCreateModal(false)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="glass-card p-6 w-full max-w-md glow-primary"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-display font-semibold text-text-primary">
                  New Experiment
                </h2>
                <button
                  onClick={() => setShowCreateModal(false)}
                  className="p-2 hover:bg-white/5 rounded-lg transition-colors"
                >
                  <X className="w-5 h-5 text-text-muted" />
                </button>
              </div>

              <form onSubmit={handleCreate} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-2">
                    Name
                  </label>
                  <input
                    type="text"
                    value={newExpName}
                    onChange={(e) => setNewExpName(e.target.value)}
                    className="input-field"
                    placeholder="e.g., PRC1 Analysis March 2024"
                    required
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-2">
                    Description (optional)
                  </label>
                  <textarea
                    value={newExpDescription}
                    onChange={(e) => setNewExpDescription(e.target.value)}
                    className="input-field min-h-[100px] resize-none"
                    placeholder="Describe the purpose of this experiment..."
                  />
                </div>

                <div className="flex gap-3 pt-4">
                  <button
                    type="button"
                    onClick={() => setShowCreateModal(false)}
                    className="btn-secondary flex-1"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={createMutation.isPending || !newExpName.trim()}
                    className="btn-primary flex-1 flex items-center justify-center gap-2"
                  >
                    {createMutation.isPending ? (
                      <Loader2 className="w-5 h-5 animate-spin" />
                    ) : (
                      <>
                        <Plus className="w-5 h-5" />
                        Create
                      </>
                    )}
                  </button>
                </div>
              </form>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Delete Confirmation Modal */}
      <ConfirmModal
        isOpen={!!experimentToDelete}
        onClose={() => setExperimentToDelete(null)}
        onConfirm={() => experimentToDelete && deleteMutation.mutate(experimentToDelete.id)}
        title="Delete Experiment"
        message="Are you sure you want to delete this experiment? All images and cell crops will be permanently removed."
        detail={experimentToDelete?.name}
        confirmLabel="Delete"
        cancelLabel="Cancel"
        isLoading={deleteMutation.isPending}
        variant="danger"
      />
    </div>
  );
}
