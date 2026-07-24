"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, MicroscopeDetailed, MicroscopeCreate, MicroscopeUpdate } from "@/lib/api";
import { ConfirmModal, Dialog, EmptyState, LoadingContainer } from "@/components/ui";
import { staggerContainerVariants, staggerItemVariants } from "@/lib/animations";
import {
  Plus,
  Microscope as MicroscopeIcon,
  Loader2,
  Trash2,
  Edit3,
  AlertCircle,
  CheckCircle,
  X,
  FolderOpen,
  RefreshCw,
} from "lucide-react";

// Neutral swatch shown by the native color input when no color is chosen. Never
// persisted — formData.color stays "" so the backend auto-assigns an unused color.
const COLOR_PLACEHOLDER = "#64748b";

const DEFAULT_FORM_DATA: MicroscopeCreate = {
  name: "",
  manufacturer: "",
  model: "",
  objective: "",
  magnification: "",
  description: "",
  color: "",
};

export default function MicroscopesPage(): JSX.Element {
  const t = useTranslations("microscopesPage");
  const tCommon = useTranslations("common");
  const queryClient = useQueryClient();

  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<MicroscopeDetailed | null>(null);
  const [toDelete, setToDelete] = useState<MicroscopeDetailed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [formData, setFormData] = useState<MicroscopeCreate>(DEFAULT_FORM_DATA);

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["microscopes"] });
  }, [queryClient]);

  const showSuccess = useCallback((message: string) => {
    setSuccessMessage(message);
    setTimeout(() => setSuccessMessage(null), 3000);
  }, []);

  const { data: microscopes, isLoading, isError, refetch } = useQuery({
    queryKey: ["microscopes"],
    queryFn: () => api.getMicroscopes(),
  });

  const closeModal = useCallback(() => {
    setShowModal(false);
    setEditing(null);
    setError(null);
  }, []);

  const createMutation = useMutation({
    mutationFn: (data: MicroscopeCreate) => api.createMicroscope(data),
    onSuccess: () => { invalidate(); closeModal(); },
    onError: (err: Error) => {
      console.error("Failed to create microscope:", err);
      setError(err.message || t("saveError"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: MicroscopeUpdate }) =>
      api.updateMicroscope(id, data),
    onSuccess: () => { invalidate(); closeModal(); },
    onError: (err: Error) => {
      console.error("Failed to update microscope:", err);
      setError(err.message || t("saveError"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteMicroscope(id),
    onSuccess: () => { invalidate(); setToDelete(null); showSuccess(t("deleteSuccess")); },
    onError: (err: Error) => { setError(err.message || t("deleteError")); setToDelete(null); },
  });

  const openCreateModal = () => {
    setEditing(null);
    setFormData(DEFAULT_FORM_DATA);
    setShowModal(true);
    setError(null);
  };

  const openEditModal = (m: MicroscopeDetailed) => {
    setEditing(m);
    setFormData({
      name: m.name,
      manufacturer: m.manufacturer || "",
      model: m.model || "",
      objective: m.objective || "",
      magnification: m.magnification || "",
      description: m.description || "",
      color: m.color || "",
    });
    setShowModal(true);
    setError(null);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // "" fails the backend hex pattern, so it never goes on the wire. POST reads
    // a missing color as "auto-assign"; PATCH reads a missing field as "leave
    // unchanged", so an edit sends explicit null to mean "re-pick".
    const { color, ...rest } = formData;
    if (editing) {
      updateMutation.mutate({ id: editing.id, data: { ...rest, color: color || null } });
    } else {
      createMutation.mutate(color ? { ...rest, color } : rest);
    }
  };

  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-bold text-text-primary">{t("title")}</h1>
          <p className="text-text-secondary mt-1">{t("subtitle")}</p>
        </div>
        <button onClick={openCreateModal} className="btn-primary flex items-center gap-2">
          <Plus className="w-5 h-5" />
          {t("create")}
        </button>
      </div>

      {/* Success / error banners */}
      <AnimatePresence>
        {successMessage && (
          <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}
            className="p-4 bg-green-500/10 border border-green-500/20 rounded-lg flex items-center gap-3">
            <CheckCircle className="w-5 h-5 text-green-400" />
            <span className="text-green-400">{successMessage}</span>
          </motion.div>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {error && (
          <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}
            className="p-4 bg-accent-red/10 border border-accent-red/20 rounded-lg flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-accent-red" />
            <span className="text-accent-red flex-1">{error}</span>
            <button onClick={() => setError(null)} className="text-text-muted hover:text-text-primary">
              <X className="w-4 h-4" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Grid */}
      <LoadingContainer isLoading={isLoading}>
        {isError ? (
          // A load failure must not masquerade as "no microscopes" — with no
          // seeded defaults, empty is a legitimate state, so surface the error.
          <div className="glass-card p-8 flex flex-col items-center text-center gap-3">
            <AlertCircle className="w-10 h-10 text-accent-red" />
            <p className="text-text-secondary max-w-md">{t("loadError")}</p>
            <button onClick={() => refetch()} className="btn-secondary flex items-center gap-2">
              <RefreshCw className="w-4 h-4" />
              {tCommon("retry")}
            </button>
          </div>
        ) : microscopes && microscopes.length > 0 ? (
          <motion.div variants={staggerContainerVariants} initial="hidden" animate="visible"
            className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {microscopes.map((m) => (
              <motion.div key={m.id} variants={staggerItemVariants}
                className="glass-card p-6 group hover:border-primary-500/30 transition-all duration-300">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="w-4 h-4 rounded-full" style={{ backgroundColor: m.color || "#888" }} />
                    <div>
                      <h3 className="font-display font-semibold text-lg text-text-primary">{m.name}</h3>
                      {m.manufacturer && <p className="text-sm text-text-secondary">{m.manufacturer}</p>}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => openEditModal(m)} className="p-1.5 hover:bg-white/5 rounded-lg transition-colors" title={t("edit")}>
                      <Edit3 className="w-4 h-4 text-text-muted hover:text-primary-400" />
                    </button>
                    <button onClick={() => setToDelete(m)} className="p-1.5 hover:bg-accent-red/10 rounded-lg transition-colors"
                      title={tCommon("delete")} disabled={m.experiment_count > 0}>
                      <Trash2 className={`w-4 h-4 ${m.experiment_count > 0 ? "text-text-muted/30 cursor-not-allowed" : "text-text-muted hover:text-accent-red"}`} />
                    </button>
                  </div>
                </div>
                <div className="space-y-2 text-sm">
                  {m.model && <div className="flex items-center gap-2 text-text-secondary"><span className="text-text-muted">{t("model")}:</span><span>{m.model}</span></div>}
                  {m.objective && <div className="flex items-center gap-2 text-text-secondary"><span className="text-text-muted">{t("objective")}:</span><span>{m.objective}</span></div>}
                  {m.magnification && <div className="flex items-center gap-2 text-text-secondary"><span className="text-text-muted">{t("magnification")}:</span><span>{m.magnification}</span></div>}
                </div>
                <div className="flex items-center justify-between mt-4 pt-4 border-t border-white/5">
                  <div className="flex items-center gap-1 text-sm text-text-muted">
                    <FolderOpen className="w-4 h-4" />
                    <span>{m.experiment_count} {t("experiments")}</span>
                  </div>
                </div>
              </motion.div>
            ))}
          </motion.div>
        ) : (
          <EmptyState icon={MicroscopeIcon} title={t("noMicroscopes")} description={t("startFirst")}
            action={{ label: t("create"), onClick: openCreateModal, icon: Plus }} />
        )}
      </LoadingContainer>

      {/* Create/Edit modal */}
      <Dialog isOpen={showModal} onClose={closeModal} title={editing ? t("edit") : t("create")} maxWidth="lg">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{t("name")} *</label>
            <input type="text" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="input-field" placeholder="e.g., Zeiss LSM 880" required />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">{t("manufacturer")}</label>
              <input type="text" value={formData.manufacturer} onChange={(e) => setFormData({ ...formData, manufacturer: e.target.value })}
                className="input-field" placeholder="e.g., Zeiss" />
            </div>
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">{t("model")}</label>
              <input type="text" value={formData.model} onChange={(e) => setFormData({ ...formData, model: e.target.value })}
                className="input-field" placeholder="e.g., LSM 880" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">{t("objective")}</label>
              <input type="text" value={formData.objective} onChange={(e) => setFormData({ ...formData, objective: e.target.value })}
                className="input-field" placeholder="e.g., Plan-Apochromat 63×/1.4 Oil" />
            </div>
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">{t("magnification")}</label>
              <input type="text" value={formData.magnification} onChange={(e) => setFormData({ ...formData, magnification: e.target.value })}
                className="input-field" placeholder="e.g., 63×" />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{t("description")}</label>
            <textarea value={formData.description} onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              className="input-field min-h-[80px] resize-none" />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{t("color")}</label>
            <div className="flex items-center gap-3">
              <input type="color" value={formData.color || COLOR_PLACEHOLDER} onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                className="w-10 h-10 rounded-lg cursor-pointer border-0 bg-transparent" aria-label={t("color")} />
              <input type="text" value={formData.color} onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                className="input-field flex-1 font-mono" placeholder={t("colorAutoPlaceholder")} />
              {formData.color && (
                <button type="button" onClick={() => setFormData({ ...formData, color: "" })}
                  className="px-3 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors">
                  {t("colorAuto")}
                </button>
              )}
            </div>
            {!formData.color && <p className="text-xs text-text-muted mt-1.5">{t("colorAutoHint")}</p>}
          </div>
          <div className="flex gap-3 pt-4">
            <button type="button" onClick={closeModal} className="btn-secondary flex-1">{tCommon("cancel")}</button>
            <button type="submit" disabled={isSubmitting || !formData.name.trim()}
              className="btn-primary flex-1 flex items-center justify-center gap-2">
              {isSubmitting ? <Loader2 className="w-5 h-5 animate-spin" /> : tCommon(editing ? "save" : "create")}
            </button>
          </div>
        </form>
      </Dialog>

      {/* Delete confirmation */}
      <ConfirmModal isOpen={!!toDelete} onClose={() => setToDelete(null)}
        onConfirm={() => toDelete && deleteMutation.mutate(toDelete.id)}
        title={tCommon("delete")} message={t("deleteConfirm")} detail={toDelete?.name}
        confirmLabel={tCommon("delete")} cancelLabel={tCommon("cancel")}
        isLoading={deleteMutation.isPending} variant="danger" />
    </div>
  );
}
