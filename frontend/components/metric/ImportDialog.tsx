"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Loader2, Check, X, Download } from "lucide-react";

interface ImportDialogProps {
  metricId: number;
  onClose: () => void;
  onImported: () => void;
}

export function ImportDialog({
  metricId,
  onClose,
  onImported,
}: ImportDialogProps): JSX.Element {
  const [selectedExperiments, setSelectedExperiments] = useState<number[]>([]);

  const { data: experiments, isLoading } = useQuery({
    queryKey: ["experiments-for-import", metricId],
    queryFn: () => api.getExperimentsForImport(metricId),
  });

  const importMutation = useMutation({
    mutationFn: () => api.importCropsToMetric(metricId, selectedExperiments),
    onSuccess: () => {
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
