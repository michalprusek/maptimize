"use client";

import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { ConfirmModal } from "@/components/ui";
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
  ChevronDown,
  Download,
  Upload,
} from "lucide-react";
import { ExportModal, ImportModal } from "@/components/export";

export default function ExperimentsPage(): JSX.Element {
  const t = useTranslations("experiments");
  const tCommon = useTranslations("common");
  const tProteins = useTranslations("proteins");
  const tExportImport = useTranslations("exportImport");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showExportModal, setShowExportModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [newExpName, setNewExpName] = useState("");
  const [newExpDescription, setNewExpDescription] = useState("");
  const [selectedProteinId, setSelectedProteinId] = useState<number | null>(null);
  const [proteinDropdownOpen, setProteinDropdownOpen] = useState(false);
  const [experimentToDelete, setExperimentToDelete] = useState<{ id: number; name: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const proteinDropdownRef = useRef<HTMLDivElement>(null);

  const { data: experiments, isLoading } = useQuery({
    queryKey: ["experiments"],
    queryFn: () => api.getExperiments(),
  });

  const { data: proteins } = useQuery({
    queryKey: ["proteins"],
    queryFn: () => api.getProteins(),
  });

  // Close protein dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (proteinDropdownRef.current && !proteinDropdownRef.current.contains(event.target as Node)) {
        setProteinDropdownOpen(false);
      }
    };
    if (proteinDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [proteinDropdownOpen]);

  const selectedProtein = proteins?.find(p => p.id === selectedProteinId);

  const createMutation = useMutation({
    mutationFn: (data: { name: string; description?: string; map_protein_id?: number }) =>
      api.createExperiment(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      setShowCreateModal(false);
      setNewExpName("");
      setNewExpDescription("");
      setSelectedProteinId(null);
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
      map_protein_id: selectedProteinId ?? undefined,
    });
  };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-bold text-text-primary">
            {t("title")}
          </h1>
        </div>
        <div className="flex items-center gap-3">
          {/* Import button */}
          <button
            onClick={() => setShowImportModal(true)}
            className="btn-secondary flex items-center gap-2"
          >
            <Upload className="w-5 h-5" />
            {tExportImport("import")}
          </button>
          {/* Export button - only show when experiments exist */}
          {experiments && experiments.length > 0 && (
            <button
              onClick={() => setShowExportModal(true)}
              className="btn-secondary flex items-center gap-2"
            >
              <Download className="w-5 h-5" />
              {tExportImport("export")}
            </button>
          )}
          {/* Create button */}
          <button
            onClick={() => setShowCreateModal(true)}
            className="btn-primary flex items-center gap-2"
          >
            <Plus className="w-5 h-5" />
            {t("create")}
          </button>
        </div>
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
                      <span>{exp.image_count} {t("images")}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <Layers className="w-4 h-4" />
                      <span>{exp.cell_count} {t("crops")}</span>
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
            {t("noExperiments")}
          </h3>
          <p className="text-text-secondary mb-6 max-w-md mx-auto">
            {t("startFirst")}
          </p>
          <button
            onClick={() => setShowCreateModal(true)}
            className="btn-primary inline-flex items-center gap-2"
          >
            <Plus className="w-5 h-5" />
            {t("create")}
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
                  {t("create")}
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
                    {t("name")}
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
                    {t("description")}
                  </label>
                  <textarea
                    value={newExpDescription}
                    onChange={(e) => setNewExpDescription(e.target.value)}
                    className="input-field min-h-[80px] resize-none"
                  />
                </div>

                {/* Protein selector */}
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-2">
                    {t("assignProtein")}
                  </label>
                  <div ref={proteinDropdownRef} className="relative">
                    <button
                      type="button"
                      onClick={() => setProteinDropdownOpen(!proteinDropdownOpen)}
                      className="input-field w-full flex items-center justify-between text-left"
                    >
                      <span className="flex items-center gap-2">
                        {selectedProtein ? (
                          <>
                            <span
                              className="w-3 h-3 rounded-full"
                              style={{ backgroundColor: selectedProtein.color || "#888" }}
                            />
                            {selectedProtein.name}
                          </>
                        ) : (
                          <span className="text-text-muted">{tProteins("unassigned")}</span>
                        )}
                      </span>
                      <ChevronDown className={`w-4 h-4 text-text-muted transition-transform ${proteinDropdownOpen ? "rotate-180" : ""}`} />
                    </button>
                    {proteinDropdownOpen && (
                      <div className="absolute top-full left-0 right-0 mt-1 bg-bg-elevated border border-white/10 rounded-lg shadow-xl z-50 py-1 max-h-48 overflow-y-auto">
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedProteinId(null);
                            setProteinDropdownOpen(false);
                          }}
                          className="w-full px-3 py-2 text-left hover:bg-white/5 transition-colors flex items-center gap-2"
                        >
                          <span className="w-3 h-3 rounded-full bg-text-muted/30" />
                          <span className="text-text-muted">{tProteins("unassigned")}</span>
                        </button>
                        {proteins?.map((protein) => (
                          <button
                            key={protein.id}
                            type="button"
                            onClick={() => {
                              setSelectedProteinId(protein.id);
                              setProteinDropdownOpen(false);
                            }}
                            className="w-full px-3 py-2 text-left hover:bg-white/5 transition-colors flex items-center gap-2"
                          >
                            <span
                              className="w-3 h-3 rounded-full"
                              style={{ backgroundColor: protein.color || "#888" }}
                            />
                            <span className="text-text-primary">{protein.name}</span>
                            {protein.full_name && (
                              <span className="text-xs text-text-muted ml-1">({protein.full_name})</span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex gap-3 pt-4">
                  <button
                    type="button"
                    onClick={() => setShowCreateModal(false)}
                    className="btn-secondary flex-1"
                  >
                    {tCommon("cancel")}
                  </button>
                  <button
                    type="submit"
                    disabled={createMutation.isPending || !newExpName.trim()}
                    className="btn-primary flex-1 flex items-center justify-center gap-2"
                  >
                    {createMutation.isPending ? (
                      <Loader2 className="w-5 h-5 animate-spin" />
                    ) : (
                      tCommon("create")
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
        title={tCommon("delete")}
        message={t("deleteConfirm")}
        detail={experimentToDelete?.name}
        confirmLabel={tCommon("delete")}
        cancelLabel={tCommon("cancel")}
        isLoading={deleteMutation.isPending}
        variant="danger"
      />

      {/* Export Modal */}
      <ExportModal
        isOpen={showExportModal}
        onClose={() => setShowExportModal(false)}
        experiments={experiments || []}
      />

      {/* Import Modal */}
      <ImportModal
        isOpen={showImportModal}
        onClose={() => setShowImportModal(false)}
        onImportComplete={() => {
          queryClient.invalidateQueries({ queryKey: ["experiments"] });
          setShowImportModal(false);
        }}
      />
    </div>
  );
}
