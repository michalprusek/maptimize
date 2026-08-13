"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, type ExperimentFolder } from "@/lib/api";
import { ColorTagSelect, ConfirmModal, toColorTagOptions } from "@/components/ui";
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
  Download,
  Upload,
  User,
} from "lucide-react";
import { ExportModal, ImportModal } from "@/components/export";
import {
  ExperimentFolderPanel,
  type FolderSelection,
} from "@/components/experiment";
import { parentLabel } from "@/lib/folderTree";
import { useAssignMicroscope, useAssignPtm } from "@/hooks";
import { useAuthStore } from "@/stores/authStore";

export default function ExperimentsPage(): JSX.Element {
  const t = useTranslations("experiments");
  const tCommon = useTranslations("common");
  const tProteins = useTranslations("proteins");
  // Deleting an experiment is owner-only server-side; protein, microscope and
  // PTM are the deliberate group-writable exceptions. The card offers all four,
  // so it has to know which one needs the owner.
  const currentUserId = useAuthStore((state) => state.user?.id);
  const tExportImport = useTranslations("exportImport");
  const tGroups = useTranslations("groups");
  const tFolders = useTranslations("experimentFolders");
  // Which slice of the tree the grid is showing. "all" and "unfiled" are views,
  // not folders — see ExperimentFolderPanel.
  const [folderSelection, setFolderSelection] = useState<FolderSelection>("all");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showExportModal, setShowExportModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [newExpName, setNewExpName] = useState("");
  const [newExpDescription, setNewExpDescription] = useState("");
  const [selectedProteinId, setSelectedProteinId] = useState<number | null>(null);
  const [selectedMicroscopeId, setSelectedMicroscopeId] = useState<number | null>(null);
  const [selectedPtmId, setSelectedPtmId] = useState<number | null>(null);
  // Which card has one of its tag menus (microscope, PTM) open. Each card is a
  // framer-motion element, so it creates a stacking context the menu cannot
  // escape -- without lifting the open card, the next card in the grid paints
  // over the menu. A single id covers both chips because only one menu is ever
  // open at a time: opening the second closes the first on mousedown, which
  // lands before the click that opens it.
  const [openMenuCardId, setOpenMenuCardId] = useState<number | null>(null);
  const [experimentToDelete, setExperimentToDelete] = useState<{ id: number; name: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  // 0 is the backend's "unfiled" sentinel; undefined means no filter at all.
  // Three distinct answers, so this maps them explicitly rather than leaning on
  // truthiness, which would merge "unfiled" into "everything".
  const folderQuery =
    folderSelection === "all"
      ? undefined
      : folderSelection === "unfiled"
        ? 0
        : folderSelection;

  const { data: experiments, isLoading } = useQuery({
    // The folder is part of the key: it changes the response, not the rendering.
    queryKey: ["experiments", folderQuery],
    queryFn: () => api.getExperiments({ folderId: folderQuery }),
  });

  // Only used to decide whether a NEW top-level folder has to name a group.
  const { data: myGroups } = useQuery({
    queryKey: ["my-groups"],
    queryFn: () => api.getMyGroups(),
  });

  const { data: tree } = useQuery({
    queryKey: ["experiment-folders"],
    queryFn: () => api.getExperimentFolders(),
  });
  const folders = tree?.folders ?? [];
  const unfiledCount = tree?.unfiled_count ?? 0;
  // Each experiment sits in at most one folder, so a flat sum over the folder
  // rows is the filed total — no subtree walk, and no double counting.
  const totalExperimentCount =
    folders.reduce((sum, folder) => sum + folder.experiment_count, 0) +
    unfiledCount;

  const [folderToDelete, setFolderToDelete] =
    useState<ExperimentFolder | null>(null);

  // Folder names repeat between branches (nine folders named manip1..3 under
  // three parents), so each row names the folder above it. The immediate parent
  // and not the whole trail -- a row is one line wide, and a top-down trail
  // truncates away exactly the part that differs. See `parentLabel`.
  const folderOptions = useMemo(
    () => toColorTagOptions(folders, (folder) => parentLabel(folders, folder.id)),
    [folders]
  );

  const invalidateTree = () => {
    queryClient.invalidateQueries({ queryKey: ["experiments"] });
    queryClient.invalidateQueries({ queryKey: ["experiment-folders"] });
  };

  const folderMutation = useMutation({
    mutationFn: ({ experimentId, folderId }: {
      experimentId: number;
      folderId: number | null;
    }) => api.setExperimentFolder(experimentId, folderId),
    onSuccess: invalidateTree,
    onError: (err: Error) => setError(err.message),
  });

  const createFolderMutation = useMutation({
    mutationFn: ({ name, parentId, groupId }: {
      name: string;
      parentId: number | null;
      groupId: number | null;
    }) => api.createExperimentFolder(name, parentId, groupId),
    onSuccess: invalidateTree,
    onError: (err: Error) => setError(err.message),
  });

  const renameFolderMutation = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      api.updateExperimentFolder(id, { name }),
    onSuccess: invalidateTree,
    onError: (err: Error) => setError(err.message),
  });

  const deleteFolderMutation = useMutation({
    mutationFn: (id: number) => api.deleteExperimentFolder(id),
    onSuccess: () => {
      // The folder dissolved into its parent, so whatever was selected may no
      // longer exist. Fall back to the view that always does.
      setFolderSelection("all");
      invalidateTree();
    },
    onError: (err: Error) => setError(err.message),
  });

  const { data: proteins } = useQuery({
    queryKey: ["proteins"],
    queryFn: () => api.getProteins(),
  });

  const { data: microscopes } = useQuery({
    queryKey: ["microscopes"],
    queryFn: () => api.getMicroscopes(),
  });

  const { data: ptms } = useQuery({
    queryKey: ["ptms"],
    queryFn: () => api.getPtms(),
  });

  const proteinOptions = toColorTagOptions(proteins, (p) => p.full_name);
  const microscopeOptions = toColorTagOptions(microscopes, (m) => m.manufacturer);
  const ptmOptions = toColorTagOptions(ptms, (p) => p.abbreviation);

  const createMutation = useMutation({
    mutationFn: (data: { name: string; description?: string; map_protein_id?: number; microscope_id?: number; ptm_id?: number }) =>
      api.createExperiment(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      setShowCreateModal(false);
      setNewExpName("");
      setNewExpDescription("");
      setSelectedProteinId(null);
      setSelectedMicroscopeId(null);
      setSelectedPtmId(null);
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

  const assignProteinMutation = useMutation({
    mutationFn: ({ experimentId, proteinId }: { experimentId: number; proteinId: number | null }) =>
      api.updateExperimentProtein(experimentId, proteinId),
    onSuccess: () => {
      setError(null);
      // The protein cascades onto images and crops, so the plots that colour by
      // it go stale too — not just the experiment list.
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      queryClient.invalidateQueries({ queryKey: ["umap"] });
    },
    onError: (err: Error) => {
      console.error("Failed to assign protein:", err);
      setError(err.message || t("assignProteinError"));
    },
  });

  const assignMicroscopeMutation = useAssignMicroscope({
    fallbackMessage: t("assignMicroscopeError"),
    onError: setError,
    onSuccess: () => setError(null),
  });

  const assignPtmMutation = useAssignPtm({
    fallbackMessage: t("assignPtmError"),
    onError: setError,
    onSuccess: () => setError(null),
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    createMutation.mutate({
      name: newExpName,
      description: newExpDescription || undefined,
      map_protein_id: selectedProteinId ?? undefined,
      microscope_id: selectedMicroscopeId ?? undefined,
      ptm_id: selectedPtmId ?? undefined,
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

      {/* Folder tree beside the grid. The tree column keeps a fixed width so the
          grid does not reflow every time a folder name changes length. */}
      <div className="flex flex-col lg:flex-row gap-6 items-start">
      <aside className="w-full lg:w-64 shrink-0 lg:sticky lg:top-4">
        <ExperimentFolderPanel
          folders={folders}
          selection={folderSelection}
          onSelect={setFolderSelection}
          onCreate={(name, parentId, groupId) =>
            createFolderMutation.mutate({ name, parentId, groupId })
          }
          groups={(myGroups?.items ?? []).map((m) => m.group)}
          onRename={(folder, name) =>
            renameFolderMutation.mutate({ id: folder.id, name })
          }
          onDelete={(folder) => setFolderToDelete(folder)}
          unfiledCount={unfiledCount}
          totalCount={totalExperimentCount}
        />
      </aside>

      <div className="flex-1 min-w-0">
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
              className={`relative ${openMenuCardId === exp.id ? "z-50" : ""}`}
            >
              <div className="glass-card p-6 h-full group hover:border-primary-500/30 transition-all duration-300 card-hover">
                {/* The microscope and PTM pickers below sit outside this Link on
                    purpose: nested inside it, every click would navigate away. */}
                <Link href={`/dashboard/experiments/${exp.id}`} className="block cursor-pointer">
                  <div className="flex items-start justify-between mb-4">
                    <div className="p-3 bg-primary-500/10 rounded-xl">
                      <FolderOpen className="w-6 h-6 text-primary-400" />
                    </div>
                    {/* Hidden, not disabled, on a colleague's card: deleting an
                        experiment is owner-only server-side and cascades to every
                        image and crop, so offering the button to the 40 of 46
                        cards that would 403 is worse than not showing it. The
                        protein chip below is the opposite case -- group-writable,
                        so it stays live for everyone. */}
                    {exp.user_id === currentUserId && (
                      <button
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          setExperimentToDelete({ id: exp.id, name: exp.name });
                        }}
                        className="p-1.5 hover:bg-accent-red/20 text-text-muted hover:text-accent-red rounded-lg transition-colors opacity-0 group-hover:opacity-100"
                        title={tCommon("delete")}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>

                  <h3 className="font-display font-semibold text-lg text-text-primary mb-1 group-hover:text-primary-400 transition-colors">
                    {exp.name}
                  </h3>

                  {exp.creator_name && exp.group_id && (
                    <p className="flex items-center gap-1.5 text-xs text-text-muted mb-2">
                      <User className="w-3 h-3" />
                      {tGroups("createdBy", { name: exp.creator_name })}
                    </p>
                  )}

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
                    {/* Sits here rather than beside the chips: three assignment
                        chips fill that row, and the date was being overlapped. */}
                    <span className="ml-auto text-xs">
                      {new Date(exp.created_at).toLocaleDateString()}
                    </span>
                  </div>
                </Link>

                <div className="flex items-center justify-between gap-2 mt-4 pt-4 border-t border-white/5">
                  <div className="flex items-center gap-2 min-w-0 flex-wrap">
                    <ColorTagSelect
                      options={proteinOptions}
                      value={exp.map_protein?.id ?? null}
                      onChange={(proteinId) =>
                        assignProteinMutation.mutate({ experimentId: exp.id, proteinId })
                      }
                      onOpenChange={(open) =>
                        setOpenMenuCardId(open ? exp.id : null)
                      }
                      placeholder={t("assignProtein")}
                      // Without this the clear row reads "Assign MAP Protein",
                      // so clicking it on a card that HAS one silently unassigns
                      // and cascades NULL to every image and crop.
                      clearLabel={tCommon("none")}
                      hint={t("experimentProteinHint")}
                      variant="chip"
                      size="sm"
                    />
                    <ColorTagSelect
                      options={microscopeOptions}
                      value={exp.microscope?.id ?? null}
                      onChange={(microscopeId) =>
                        assignMicroscopeMutation.mutate({ experimentId: exp.id, microscopeId })
                      }
                      onOpenChange={(open) =>
                        setOpenMenuCardId(open ? exp.id : null)
                      }
                      placeholder={t("unassignedMicroscope")}
                      variant="chip"
                      size="sm"
                    />
                    {/* Right-aligned because this chip sits further into the card:
                        a left-aligned menu would hang off the card's edge. */}
                    <ColorTagSelect
                      options={ptmOptions}
                      value={exp.ptm?.id ?? null}
                      onChange={(ptmId) =>
                        assignPtmMutation.mutate({ experimentId: exp.id, ptmId })
                      }
                      onOpenChange={(open) =>
                        setOpenMenuCardId(open ? exp.id : null)
                      }
                      placeholder={t("unassignedPtm")}
                      variant="chip"
                      size="sm"
                      align="right"
                    />
                    {/* Filing is group-writable like the three chips above, so
                        this stays enabled on a colleague's card. It changes
                        where the experiment appears and nothing else — sharing
                        is owner-only and lives on the detail page. */}
                    <ColorTagSelect
                      options={folderOptions}
                      value={exp.folder_id ?? null}
                      onChange={(folderId) =>
                        folderMutation.mutate({ experimentId: exp.id, folderId })
                      }
                      onOpenChange={(open) =>
                        setOpenMenuCardId(open ? exp.id : null)
                      }
                      placeholder={tFolders("unfiled")}
                      clearLabel={tFolders("unfiled")}
                      variant="chip"
                      size="sm"
                      align="right"
                    />
                  </div>
                  {/* Deliberately NOT a second <Link>: the card would then expose
                      two anchors to the same href, and the e2e suite counts cards
                      by `a[href*="/experiments/"]`. The body Link above navigates. */}
                  <div className="flex items-center flex-shrink-0">
                    <ArrowRight className="w-5 h-5 text-text-muted group-hover:text-primary-400 group-hover:translate-x-1 transition-all" />
                  </div>
                </div>
              </div>
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
      </div>
      </div>

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
                  <ColorTagSelect
                    options={proteinOptions}
                    value={selectedProteinId}
                    onChange={setSelectedProteinId}
                    placeholder={tProteins("unassigned")}
                  />
                </div>

                {/* Microscope selector */}
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-2">
                    {t("assignMicroscope")}
                  </label>
                  <ColorTagSelect
                    options={microscopeOptions}
                    value={selectedMicroscopeId}
                    onChange={setSelectedMicroscopeId}
                    placeholder={t("unassignedMicroscope")}
                  />
                </div>

                {/* PTM selector */}
                <div>
                  <label className="block text-sm font-medium text-text-secondary mb-2">
                    {t("assignPtm")}
                  </label>
                  <ColorTagSelect
                    options={ptmOptions}
                    value={selectedPtmId}
                    onChange={setSelectedPtmId}
                    placeholder={t("unassignedPtm")}
                  />
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

      {/* Deleting a folder dissolves it — the wording says so, because "delete"
          on a folder holding a batch reads as "delete the batch". */}
      <ConfirmModal
        isOpen={!!folderToDelete}
        onClose={() => setFolderToDelete(null)}
        onConfirm={() => {
          if (folderToDelete) deleteFolderMutation.mutate(folderToDelete.id);
          setFolderToDelete(null);
        }}
        title={tFolders("deleteFolder")}
        message={tFolders("deleteConfirm")}
        detail={folderToDelete?.name}
        confirmLabel={tCommon("delete")}
        cancelLabel={tCommon("cancel")}
        isLoading={deleteFolderMutation.isPending}
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
