"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import {
  api,
  MapProteinDetailed,
  MapProteinCreate,
  MapProteinUpdate,
} from "@/lib/api";
import {
  ConfirmModal,
  Dialog,
  EmptyState,
  LoadingContainer,
} from "@/components/ui";
import { ProteinUmapVisualization } from "@/components/visualization/ProteinUmapVisualization";
import {
  staggerContainerVariants,
  staggerItemVariants,
} from "@/lib/animations";
import {
  Plus,
  Dna,
  Loader2,
  Trash2,
  Edit3,
  Sparkles,
  Image as ImageIcon,
  AlertCircle,
  CheckCircle,
  X,
  Download,
} from "lucide-react";

const DEFAULT_COLOR = "#3b82f6";

const DEFAULT_FORM_DATA: MapProteinCreate = {
  name: "",
  full_name: "",
  color: DEFAULT_COLOR,
  uniprot_id: "",
  fasta_sequence: "",
  gene_name: "",
  organism: "",
};

const PROTEIN_QUERY_KEYS = ["proteins-detailed", "proteins", "protein-umap"] as const;

interface ProteinEmbeddingStatusProps {
  protein: MapProteinDetailed;
  onCompute: () => void;
  isComputing: boolean;
  t: (key: string) => string;
}

function ProteinEmbeddingStatus({
  protein,
  onCompute,
  isComputing,
  t,
}: ProteinEmbeddingStatusProps): JSX.Element {
  if (protein.has_embedding) {
    return (
      <div className="flex items-center gap-1 text-green-400">
        <CheckCircle className="w-4 h-4" />
        <span className="text-xs">{t("hasEmbedding")}</span>
      </div>
    );
  }

  if (protein.fasta_sequence) {
    return (
      <button
        onClick={onCompute}
        disabled={isComputing}
        className="flex items-center gap-1 text-primary-400 hover:text-primary-300 transition-colors"
      >
        {isComputing ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Sparkles className="w-4 h-4" />
        )}
        <span className="text-xs">{t("computeEmbedding")}</span>
      </button>
    );
  }

  return <span className="text-xs text-text-muted">{t("noEmbedding")}</span>;
}

export default function ProteinsPage(): JSX.Element {
  const t = useTranslations("proteinsPage");
  const tCommon = useTranslations("common");
  const queryClient = useQueryClient();

  const [showModal, setShowModal] = useState(false);
  const [editingProtein, setEditingProtein] = useState<MapProteinDetailed | null>(null);
  const [proteinToDelete, setProteinToDelete] = useState<MapProteinDetailed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [formData, setFormData] = useState<MapProteinCreate>(DEFAULT_FORM_DATA);
  const [fetchingFasta, setFetchingFasta] = useState(false);

  const invalidateProteinQueries = useCallback(() => {
    PROTEIN_QUERY_KEYS.forEach((key) => {
      queryClient.invalidateQueries({ queryKey: [key] });
    });
  }, [queryClient]);

  const showSuccess = useCallback((message: string) => {
    setSuccessMessage(message);
    setTimeout(() => setSuccessMessage(null), 3000);
  }, []);

  const fetchFastaFromUniprot = useCallback(async (uniprotId: string) => {
    if (!uniprotId.trim()) return;

    setFetchingFasta(true);
    setError(null);

    try {
      const response = await fetch(
        `https://rest.uniprot.org/uniprotkb/${uniprotId.trim()}.fasta`
      );

      if (!response.ok) {
        throw new Error(t("uniprotFetchError"));
      }

      const fastaText = await response.text();
      if (fastaText) {
        setFormData((prev) => ({ ...prev, fasta_sequence: fastaText }));
        showSuccess(t("uniprotFetchSuccess"));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("uniprotFetchError"));
    } finally {
      setFetchingFasta(false);
    }
  }, [t, showSuccess]);

  // Auto-fetch FASTA when UniProt ID changes (debounced)
  const prevUniprotIdRef = useRef<string>("");
  useEffect(() => {
    const uniprotId = formData.uniprot_id?.trim() || "";

    // Only fetch if:
    // 1. UniProt ID is not empty
    // 2. UniProt ID changed from previous value
    // 3. UniProt ID looks valid (at least 5 chars, e.g., Q49MG5)
    // 4. FASTA is not already filled
    if (
      uniprotId.length >= 5 &&
      uniprotId !== prevUniprotIdRef.current &&
      !formData.fasta_sequence?.trim()
    ) {
      const timeoutId = setTimeout(() => {
        fetchFastaFromUniprot(uniprotId);
      }, 800); // 800ms debounce

      prevUniprotIdRef.current = uniprotId;
      return () => clearTimeout(timeoutId);
    }

    prevUniprotIdRef.current = uniprotId;
  }, [formData.uniprot_id, formData.fasta_sequence, fetchFastaFromUniprot]);

  const { data: proteins, isLoading } = useQuery({
    queryKey: ["proteins-detailed"],
    queryFn: () => api.getProteinsDetailed(),
  });

  const createMutation = useMutation({
    mutationFn: (data: MapProteinCreate) => api.createProtein(data),
    onSuccess: (protein) => {
      invalidateProteinQueries();
      closeModal();
      // Auto-compute embedding if FASTA is provided
      if (protein.fasta_sequence && !protein.has_embedding) {
        computeEmbeddingMutation.mutate(protein.id);
      }
    },
    onError: (err: Error) => setError(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: MapProteinUpdate }) =>
      api.updateProtein(id, data),
    onSuccess: (protein) => {
      invalidateProteinQueries();
      closeModal();
      // Auto-compute embedding if FASTA changed and no embedding yet
      if (protein.fasta_sequence && !protein.has_embedding) {
        computeEmbeddingMutation.mutate(protein.id);
      }
    },
    onError: (err: Error) => setError(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteProtein(id),
    onSuccess: () => {
      invalidateProteinQueries();
      setProteinToDelete(null);
      showSuccess(t("deleteSuccess"));
    },
    onError: (err: Error) => {
      setError(err.message || t("deleteError"));
      setProteinToDelete(null);
    },
  });

  const computeEmbeddingMutation = useMutation({
    mutationFn: (id: number) => api.computeProteinEmbedding(id),
    onSuccess: () => {
      invalidateProteinQueries();
      showSuccess(t("embeddingSuccess"));
    },
    onError: (err: Error) => setError(err.message || t("embeddingError")),
  });

  const openCreateModal = () => {
    setEditingProtein(null);
    setFormData(DEFAULT_FORM_DATA);
    prevUniprotIdRef.current = ""; // Reset to allow auto-fetch
    setShowModal(true);
    setError(null);
  };

  const openEditModal = (protein: MapProteinDetailed) => {
    setEditingProtein(protein);
    setFormData({
      name: protein.name,
      full_name: protein.full_name || "",
      color: protein.color || DEFAULT_COLOR,
      uniprot_id: protein.uniprot_id || "",
      fasta_sequence: protein.fasta_sequence || "",
      gene_name: protein.gene_name || "",
      organism: protein.organism || "",
    });
    // Set ref to current value to prevent immediate auto-fetch
    prevUniprotIdRef.current = protein.uniprot_id || "";
    setShowModal(true);
    setError(null);
  };

  const closeModal = () => {
    setShowModal(false);
    setEditingProtein(null);
    setError(null);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (editingProtein) {
      updateMutation.mutate({ id: editingProtein.id, data: formData });
    } else {
      createMutation.mutate(formData);
    }
  };

  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-bold text-text-primary">
            {t("title")}
          </h1>
          <p className="text-text-secondary mt-1">{t("subtitle")}</p>
        </div>
        <button onClick={openCreateModal} className="btn-primary flex items-center gap-2">
          <Plus className="w-5 h-5" />
          {t("create")}
        </button>
      </div>

      {/* Success notification */}
      <AnimatePresence>
        {successMessage && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="p-4 bg-green-500/10 border border-green-500/20 rounded-lg flex items-center gap-3"
          >
            <CheckCircle className="w-5 h-5 text-green-400" />
            <span className="text-green-400">{successMessage}</span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Error notification */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="p-4 bg-accent-red/10 border border-accent-red/20 rounded-lg flex items-center gap-3"
          >
            <AlertCircle className="w-5 h-5 text-accent-red" />
            <span className="text-accent-red flex-1">{error}</span>
            <button onClick={() => setError(null)} className="text-text-muted hover:text-text-primary">
              <X className="w-4 h-4" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* UMAP Visualization */}
      <ProteinUmapVisualization height={400} />

      {/* Proteins Grid */}
      <LoadingContainer isLoading={isLoading}>
        {proteins && proteins.length > 0 ? (
          <motion.div
            variants={staggerContainerVariants}
            initial="hidden"
            animate="visible"
            className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"
          >
            {proteins.map((protein) => (
              <motion.div
                key={protein.id}
                variants={staggerItemVariants}
                className="glass-card p-6 group hover:border-primary-500/30 transition-all duration-300"
              >
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div
                      className="w-4 h-4 rounded-full"
                      style={{ backgroundColor: protein.color || "#888" }}
                    />
                    <div>
                      <h3 className="font-display font-semibold text-lg text-text-primary">
                        {protein.name}
                      </h3>
                      {protein.full_name && (
                        <p className="text-sm text-text-secondary">{protein.full_name}</p>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => openEditModal(protein)}
                      className="p-1.5 hover:bg-white/5 rounded-lg transition-colors"
                      title={t("edit")}
                    >
                      <Edit3 className="w-4 h-4 text-text-muted hover:text-primary-400" />
                    </button>
                    <button
                      onClick={() => setProteinToDelete(protein)}
                      className="p-1.5 hover:bg-accent-red/10 rounded-lg transition-colors"
                      title={tCommon("delete")}
                      disabled={protein.image_count > 0}
                    >
                      <Trash2
                        className={`w-4 h-4 ${
                          protein.image_count > 0
                            ? "text-text-muted/30 cursor-not-allowed"
                            : "text-text-muted hover:text-accent-red"
                        }`}
                      />
                    </button>
                  </div>
                </div>

                <div className="space-y-2 text-sm">
                  {protein.gene_name && (
                    <div className="flex items-center gap-2 text-text-secondary">
                      <span className="text-text-muted">Gene:</span>
                      <span>{protein.gene_name}</span>
                    </div>
                  )}
                  {protein.organism && (
                    <div className="flex items-center gap-2 text-text-secondary">
                      <span className="text-text-muted">Organism:</span>
                      <span>{protein.organism}</span>
                    </div>
                  )}
                  {protein.sequence_length && (
                    <div className="flex items-center gap-2 text-text-secondary">
                      <span className="text-text-muted">{t("sequenceLengthShort")}:</span>
                      <span>{protein.sequence_length} {t("aminoAcids")}</span>
                    </div>
                  )}
                </div>

                <div className="flex items-center justify-between mt-4 pt-4 border-t border-white/5">
                  <div className="flex items-center gap-4 text-sm text-text-muted">
                    <div className="flex items-center gap-1">
                      <ImageIcon className="w-4 h-4" />
                      <span>{protein.image_count}</span>
                    </div>
                    <ProteinEmbeddingStatus
                      protein={protein}
                      onCompute={() => computeEmbeddingMutation.mutate(protein.id)}
                      isComputing={computeEmbeddingMutation.isPending}
                      t={t}
                    />
                  </div>
                </div>
              </motion.div>
            ))}
        </motion.div>
      ) : (
        <EmptyState
          icon={Dna}
          title={t("noProteins")}
          description={t("startFirst")}
          action={{
            label: t("create"),
            onClick: openCreateModal,
            icon: Plus,
          }}
        />
      )}
      </LoadingContainer>

      {/* Create/Edit Modal */}
      <Dialog
        isOpen={showModal}
        onClose={closeModal}
        title={editingProtein ? t("edit") : t("create")}
        maxWidth="lg"
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              {t("name")} *
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="input-field"
              placeholder="e.g., PRC1, Tau4R"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              {t("fullName")}
            </label>
            <input
              type="text"
              value={formData.full_name}
              onChange={(e) => setFormData({ ...formData, full_name: e.target.value })}
              className="input-field"
              placeholder="e.g., Protein Regulator of Cytokinesis 1"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">
                {t("uniprotId")}
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={formData.uniprot_id}
                  onChange={(e) => setFormData({ ...formData, uniprot_id: e.target.value })}
                  className="input-field flex-1"
                  placeholder="e.g., O43663"
                />
                <button
                  type="button"
                  onClick={() => fetchFastaFromUniprot(formData.uniprot_id || "")}
                  disabled={!formData.uniprot_id?.trim() || fetchingFasta}
                  className="px-3 py-2 bg-primary-500/20 hover:bg-primary-500/30 disabled:bg-white/5 disabled:text-text-muted text-primary-400 rounded-lg transition-colors flex items-center gap-1"
                  title={t("fetchFasta")}
                >
                  {fetchingFasta ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Download className="w-4 h-4" />
                  )}
                </button>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">
                {t("geneName")}
              </label>
              <input
                type="text"
                value={formData.gene_name}
                onChange={(e) => setFormData({ ...formData, gene_name: e.target.value })}
                className="input-field"
                placeholder="e.g., PRC1"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              {t("organism")}
            </label>
            <input
              type="text"
              value={formData.organism}
              onChange={(e) => setFormData({ ...formData, organism: e.target.value })}
              className="input-field"
              placeholder="e.g., Homo sapiens"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              {t("fastaSequence")}
              <span className="text-text-muted font-normal ml-2">
                ({t("addFasta")})
              </span>
            </label>
            <textarea
              value={formData.fasta_sequence}
              onChange={(e) => setFormData({ ...formData, fasta_sequence: e.target.value })}
              className="input-field min-h-[120px] resize-none font-mono text-sm"
              placeholder={">protein_name\nMKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKV..."}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">
              {t("color")}
            </label>
            <div className="flex items-center gap-3">
              <input
                type="color"
                value={formData.color}
                onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                className="w-10 h-10 rounded-lg cursor-pointer border-0 bg-transparent"
              />
              <input
                type="text"
                value={formData.color}
                onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                className="input-field flex-1 font-mono"
                placeholder={DEFAULT_COLOR}
              />
            </div>
          </div>

          <div className="flex gap-3 pt-4">
            <button type="button" onClick={closeModal} className="btn-secondary flex-1">
              {tCommon("cancel")}
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !formData.name.trim()}
              className="btn-primary flex-1 flex items-center justify-center gap-2"
            >
              {isSubmitting ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                tCommon(editingProtein ? "save" : "create")
              )}
            </button>
          </div>
        </form>
      </Dialog>

      {/* Delete Confirmation Modal */}
      <ConfirmModal
        isOpen={!!proteinToDelete}
        onClose={() => setProteinToDelete(null)}
        onConfirm={() => proteinToDelete && deleteMutation.mutate(proteinToDelete.id)}
        title={tCommon("delete")}
        message={t("deleteConfirm")}
        detail={proteinToDelete?.name}
        confirmLabel={tCommon("delete")}
        cancelLabel={tCommon("cancel")}
        isLoading={deleteMutation.isPending}
        variant="danger"
      />
    </div>
  );
}
