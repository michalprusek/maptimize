"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, PTMDetailed, PTMCreate, PTMKind, PTMUpdate } from "@/lib/api";
// The vocabulary's kinds and their normaliser live with the marker rules they
// drive; a second copy here is a second place for the two to disagree.
import { MARKER_KINDS, ptmKindOf } from "@/components/visualization/pointMarker";
import { ConfirmModal, Dialog, EmptyState, LoadingContainer } from "@/components/ui";
import { staggerContainerVariants, staggerItemVariants } from "@/lib/animations";
import {
  Plus,
  Atom,
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

const DEFAULT_FORM_DATA: PTMCreate = {
  name: "",
  abbreviation: "",
  modified_residue: "",
  enzyme: "",
  description: "",
  color: "",
  // Most entries are tubulin marks; the two that are not are the exception the
  // user opts into.
  kind: "modification",
};

/** i18n key per kind, for the selector and the card badge. */
const KIND_LABEL: Record<PTMKind, string> = {
  modification: "kindModification",
  control: "kindControl",
  none: "kindNone",
};

export default function PtmsPage(): JSX.Element {
  const t = useTranslations("ptmsPage");
  const tCommon = useTranslations("common");
  const queryClient = useQueryClient();

  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<PTMDetailed | null>(null);
  const [toDelete, setToDelete] = useState<PTMDetailed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [formData, setFormData] = useState<PTMCreate>(DEFAULT_FORM_DATA);

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["ptms"] });
  }, [queryClient]);

  const showSuccess = useCallback((message: string) => {
    setSuccessMessage(message);
    setTimeout(() => setSuccessMessage(null), 3000);
  }, []);

  const { data: ptms, isLoading, isError, refetch } = useQuery({
    queryKey: ["ptms"],
    queryFn: () => api.getPtms(),
  });

  const closeModal = useCallback(() => {
    setShowModal(false);
    setEditing(null);
    setError(null);
  }, []);

  const createMutation = useMutation({
    mutationFn: (data: PTMCreate) => api.createPtm(data),
    onSuccess: () => { invalidate(); closeModal(); },
    onError: (err: Error) => {
      console.error("Failed to create PTM:", err);
      setError(err.message || t("saveError"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: PTMUpdate }) =>
      api.updatePtm(id, data),
    onSuccess: () => { invalidate(); closeModal(); },
    onError: (err: Error) => {
      console.error("Failed to update PTM:", err);
      setError(err.message || t("saveError"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deletePtm(id),
    onSuccess: () => { invalidate(); setToDelete(null); showSuccess(t("deleteSuccess")); },
    onError: (err: Error) => { setError(err.message || t("deleteError")); setToDelete(null); },
  });

  const openCreateModal = () => {
    setEditing(null);
    setFormData(DEFAULT_FORM_DATA);
    setShowModal(true);
    setError(null);
  };

  const openEditModal = (p: PTMDetailed) => {
    setEditing(p);
    setFormData({
      name: p.name,
      abbreviation: p.abbreviation || "",
      modified_residue: p.modified_residue || "",
      enzyme: p.enzyme || "",
      description: p.description || "",
      color: p.color || "",
      // Through the same normaliser the plot uses, so the editor can never show
      // a row as one kind while the projection draws it as another.
      kind: ptmKindOf(p.kind),
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
          // A load failure must not masquerade as "no PTMs" — empty is a
          // legitimate state, so surface the error instead.
          <div className="glass-card p-8 flex flex-col items-center text-center gap-3">
            <AlertCircle className="w-10 h-10 text-accent-red" />
            <p className="text-text-secondary max-w-md">{t("loadError")}</p>
            <button onClick={() => refetch()} className="btn-secondary flex items-center gap-2">
              <RefreshCw className="w-4 h-4" />
              {tCommon("retry")}
            </button>
          </div>
        ) : ptms && ptms.length > 0 ? (
          <motion.div variants={staggerContainerVariants} initial="hidden" animate="visible"
            className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {ptms.map((p) => (
              <motion.div key={p.id} variants={staggerItemVariants}
                className="glass-card p-6 group hover:border-primary-500/30 transition-all duration-300">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="w-4 h-4 rounded-full" style={{ backgroundColor: p.color || "#888" }} />
                    <div>
                      <h3 className="font-display font-semibold text-lg text-text-primary">{p.name}</h3>
                      {p.abbreviation && <p className="text-sm text-text-secondary">{p.abbreviation}</p>}
                      {/* Only the two entries that are not modifications get a
                          badge: labelling the other nine would be noise, and a
                          control filed as a modification is the mistake worth
                          seeing without opening the editor. */}
                      {ptmKindOf(p.kind) !== "modification" && (
                        <span className="inline-block mt-1 px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide bg-white/5 text-text-muted">
                          {t(KIND_LABEL[ptmKindOf(p.kind)])}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => openEditModal(p)} className="p-1.5 hover:bg-white/5 rounded-lg transition-colors" title={t("edit")}>
                      <Edit3 className="w-4 h-4 text-text-muted hover:text-primary-400" />
                    </button>
                    <button onClick={() => setToDelete(p)} className="p-1.5 hover:bg-accent-red/10 rounded-lg transition-colors"
                      title={tCommon("delete")} disabled={p.experiment_count > 0}>
                      <Trash2 className={`w-4 h-4 ${p.experiment_count > 0 ? "text-text-muted/30 cursor-not-allowed" : "text-text-muted hover:text-accent-red"}`} />
                    </button>
                  </div>
                </div>
                <div className="space-y-2 text-sm">
                  {p.modified_residue && <div className="flex items-center gap-2 text-text-secondary"><span className="text-text-muted">{t("modifiedResidue")}:</span><span>{p.modified_residue}</span></div>}
                  {p.enzyme && <div className="flex items-center gap-2 text-text-secondary"><span className="text-text-muted">{t("enzyme")}:</span><span>{p.enzyme}</span></div>}
                </div>
                <div className="flex items-center justify-between mt-4 pt-4 border-t border-white/5">
                  <div className="flex items-center gap-1 text-sm text-text-muted">
                    <FolderOpen className="w-4 h-4" />
                    <span>{p.experiment_count} {t("experiments")}</span>
                  </div>
                </div>
              </motion.div>
            ))}
          </motion.div>
        ) : (
          <EmptyState icon={Atom} title={t("noPtms")} description={t("startFirst")}
            action={{ label: t("create"), onClick: openCreateModal, icon: Plus }} />
        )}
      </LoadingContainer>

      {/* Create/Edit modal */}
      <Dialog isOpen={showModal} onClose={closeModal} title={editing ? t("edit") : t("create")} maxWidth="lg">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{t("name")} *</label>
            <input type="text" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="input-field" placeholder={t("namePlaceholder")} required />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">{t("abbreviation")}</label>
              <input type="text" value={formData.abbreviation} onChange={(e) => setFormData({ ...formData, abbreviation: e.target.value })}
                className="input-field" placeholder={t("abbreviationPlaceholder")} />
            </div>
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">{t("modifiedResidue")}</label>
              <input type="text" value={formData.modified_residue} onChange={(e) => setFormData({ ...formData, modified_residue: e.target.value })}
                className="input-field" placeholder={t("modifiedResiduePlaceholder")} />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{t("enzyme")}</label>
            <input type="text" value={formData.enzyme} onChange={(e) => setFormData({ ...formData, enzyme: e.target.value })}
              className="input-field" placeholder={t("enzymePlaceholder")} />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{t("kind")}</label>
            <select value={formData.kind ?? "modification"}
              onChange={(e) => setFormData({ ...formData, kind: e.target.value as PTMKind })}
              className="input-field">
              {MARKER_KINDS.map((kind) => (
                <option key={kind} value={kind}>{t(KIND_LABEL[kind])}</option>
              ))}
            </select>
            <p className="text-xs text-text-muted mt-1.5">{t("kindHint")}</p>
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
