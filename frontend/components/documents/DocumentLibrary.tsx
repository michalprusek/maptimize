"use client";

import { useMemo, useState, useCallback } from "react";
import { useTranslations } from "next-intl";
import { useDocumentStore } from "@/stores/documentStore";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { Dialog } from "@/components/ui/Dialog";
import type { Folder, RAGDocument } from "@/lib/api";
import {
  Folder as FolderIcon,
  FolderOpen,
  FolderPlus,
  FolderInput,
  Home,
  ChevronRight,
  FileText,
  AlertCircle,
  Trash2,
  Eye,
  RefreshCw,
  Pencil,
  X,
  CornerLeftUp,
} from "lucide-react";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";
import { IndexStatusDot, IndexStatusLegend } from "./IndexStatusIndicator";

// ---------------------------------------------------------------------------
// Folder-tree helpers (the API returns a flat list; we derive structure here)
// ---------------------------------------------------------------------------

/** Direct child folders of `parentId` (null = root), sorted by name. */
function childFolders(folders: Folder[], parentId: number | null): Folder[] {
  return folders
    .filter((f) => (f.parent_id ?? null) === parentId)
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** Path from the root down to (and including) `folderId`. */
function breadcrumbTrail(folders: Folder[], folderId: number | null): Folder[] {
  const byId = new Map(folders.map((f) => [f.id, f]));
  const trail: Folder[] = [];
  let current = folderId != null ? byId.get(folderId) : undefined;
  while (current) {
    trail.unshift(current);
    current = current.parent_id != null ? byId.get(current.parent_id) : undefined;
  }
  return trail;
}

/** All descendant folder ids of `folderId` (used to block invalid moves). */
function descendantIds(folders: Folder[], folderId: number): Set<number> {
  const result = new Set<number>();
  const walk = (parent: number) => {
    for (const f of folders) {
      if ((f.parent_id ?? null) === parent && !result.has(f.id)) {
        result.add(f.id);
        walk(f.id);
      }
    }
  };
  walk(folderId);
  return result;
}

// A drag payload / move-target descriptor.
type ExplorerItem = { type: "folder" | "document"; id: number; name: string };
type DropTarget = number | "root";

// ---------------------------------------------------------------------------
// Main explorer
// ---------------------------------------------------------------------------

/**
 * DocumentLibrary — a file-explorer view over the document library: breadcrumb
 * navigation, subfolder + document cards in the current folder, drag-and-drop
 * (with a "Move to…" menu fallback), and per-document indexing-status dots.
 */
export function DocumentLibrary() {
  const t = useTranslations("folders");
  const tDoc = useTranslations("documents");
  const tCommon = useTranslations("common");
  const {
    documents,
    folders,
    currentFolderId,
    setCurrentFolder,
    createFolder,
    renameFolder,
    deleteFolder,
    moveFolder,
    moveDocument,
    deleteDocument,
    reindexDocument,
    isDeletingDocument,
    openPDFViewer,
  } = useDocumentStore();

  // Delete / rename / move dialog subjects
  const [documentToDelete, setDocumentToDelete] = useState<RAGDocument | null>(null);
  const [folderToDelete, setFolderToDelete] = useState<Folder | null>(null);
  const [folderToRename, setFolderToRename] = useState<Folder | null>(null);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [movingItem, setMovingItem] = useState<ExplorerItem | null>(null);
  const [isDeletingFolder, setIsDeletingFolder] = useState(false);

  // Drag-and-drop state
  const [dragItem, setDragItem] = useState<ExplorerItem | null>(null);
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null);

  // Surface move/create/rename failures inline (not wiped by the 5s poll that
  // resets the store's own `error`).
  const [actionError, setActionError] = useState<string | null>(null);

  const subfolders = useMemo(
    () => childFolders(folders, currentFolderId),
    [folders, currentFolderId]
  );
  const trail = useMemo(
    () => breadcrumbTrail(folders, currentFolderId),
    [folders, currentFolderId]
  );

  const showError = useCallback((e: unknown, fallback: string) => {
    setActionError(e instanceof Error ? e.message : fallback);
  }, []);

  // --- Move logic (shared by drag-drop and the "Move to…" menu) -------------

  /** Which targets a given item may NOT move into. */
  const invalidTargets = useCallback(
    (item: ExplorerItem): Set<number> => {
      if (item.type !== "folder") return new Set();
      const blocked = descendantIds(folders, item.id);
      blocked.add(item.id); // a folder can't go into itself
      return blocked;
    },
    [folders]
  );

  const performMove = useCallback(
    async (item: ExplorerItem, target: number | null) => {
      setActionError(null);
      // No-op if the item is already there.
      if (item.type === "folder") {
        const folder = folders.find((f) => f.id === item.id);
        if ((folder?.parent_id ?? null) === target) return;
        if (target != null && invalidTargets(item).has(target)) {
          setActionError(t("cannotMoveIntoSelf"));
          return;
        }
      } else if (target === currentFolderId) {
        return;
      }
      try {
        if (item.type === "folder") await moveFolder(item.id, target);
        else await moveDocument(item.id, target);
      } catch (e) {
        showError(e, t("moveFailed"));
      }
    },
    [folders, currentFolderId, invalidTargets, moveFolder, moveDocument, showError, t]
  );

  // --- Drag-and-drop handlers ----------------------------------------------

  const canDropOn = useCallback(
    (target: DropTarget): boolean => {
      if (!dragItem) return false;
      if (target === "root") return true;
      if (dragItem.type === "folder") return !invalidTargets(dragItem).has(target);
      return true;
    },
    [dragItem, invalidTargets]
  );

  const handleDropOn = useCallback(
    (target: DropTarget) => {
      const item = dragItem;
      setDropTarget(null);
      setDragItem(null);
      if (!item || !canDropOn(target)) return;
      void performMove(item, target === "root" ? null : target);
    },
    [dragItem, canDropOn, performMove]
  );

  const dragProps = (item: ExplorerItem) => ({
    draggable: true,
    onDragStart: (e: React.DragEvent) => {
      e.dataTransfer.effectAllowed = "move";
      setDragItem(item);
    },
    onDragEnd: () => {
      setDragItem(null);
      setDropTarget(null);
    },
  });

  const dropProps = (target: DropTarget) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!canDropOn(target)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      setDropTarget(target);
    },
    onDragLeave: () => setDropTarget((cur) => (cur === target ? null : cur)),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      handleDropOn(target);
    },
  });

  // --- Create / rename / delete --------------------------------------------

  const handleCreateFolder = useCallback(
    async (name: string) => {
      try {
        await createFolder(name);
        setIsCreateOpen(false);
      } catch (e) {
        showError(e, t("createFailed"));
      }
    },
    [createFolder, showError, t]
  );

  const handleRenameFolder = useCallback(
    async (name: string) => {
      if (!folderToRename) return;
      try {
        await renameFolder(folderToRename.id, name);
        setFolderToRename(null);
      } catch (e) {
        showError(e, t("renameFailed"));
      }
    },
    [folderToRename, renameFolder, showError, t]
  );

  const confirmDeleteFolder = useCallback(async () => {
    if (!folderToDelete) return;
    setIsDeletingFolder(true);
    try {
      await deleteFolder(folderToDelete.id);
      setFolderToDelete(null);
    } catch (e) {
      showError(e, t("deleteFailed"));
    } finally {
      setIsDeletingFolder(false);
    }
  }, [folderToDelete, deleteFolder, showError, t]);

  const confirmDeleteDocument = useCallback(async () => {
    if (!documentToDelete) return;
    await deleteDocument(documentToDelete.id);
    setDocumentToDelete(null);
  }, [documentToDelete, deleteDocument]);

  const isEmpty = subfolders.length === 0 && documents.length === 0;

  return (
    <div className="space-y-4">
      {/* Toolbar: breadcrumb + new folder + legend */}
      <div className="flex flex-wrap items-center gap-3">
        <Breadcrumb
          trail={trail}
          onNavigate={setCurrentFolder}
          dropProps={dropProps}
          dropTarget={dropTarget}
          canDropOn={canDropOn}
          isDragging={dragItem !== null}
        />
        <button
          onClick={() => setIsCreateOpen(true)}
          className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg bg-primary-500/15 hover:bg-primary-500/25 border border-primary-500/20 text-primary-400 text-sm font-medium transition-colors"
        >
          <FolderPlus className="w-4 h-4" />
          <span className="hidden sm:inline">{t("newFolder")}</span>
        </button>
      </div>

      <IndexStatusLegend />

      {/* Inline action error */}
      {actionError && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-rose-500/10 border border-rose-500/20 text-sm text-rose-300">
          <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <span className="flex-1">{actionError}</span>
          <button
            onClick={() => setActionError(null)}
            className="text-rose-300/70 hover:text-rose-200 transition-colors"
            aria-label={tCommon("close")}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Folders */}
      {subfolders.length > 0 && (
        <section className="space-y-2">
          <div className="text-xs font-medium text-text-muted uppercase tracking-wider">
            {t("foldersLabel")}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {subfolders.map((folder) => (
              <FolderCard
                key={folder.id}
                folder={folder}
                isDropTarget={dropTarget === folder.id}
                canDrop={dragItem !== null && canDropOn(folder.id)}
                onOpen={() => setCurrentFolder(folder.id)}
                onRename={() => setFolderToRename(folder)}
                onDelete={() => setFolderToDelete(folder)}
                onMove={() =>
                  setMovingItem({ type: "folder", id: folder.id, name: folder.name })
                }
                dragProps={dragProps({ type: "folder", id: folder.id, name: folder.name })}
                dropProps={dropProps(folder.id)}
              />
            ))}
          </div>
        </section>
      )}

      {/* Documents */}
      {documents.length > 0 && (
        <section className="space-y-2">
          <div className="text-xs font-medium text-text-muted uppercase tracking-wider">
            {t("documentsLabel")}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {documents.map((doc) => (
              <DocumentCard
                key={doc.id}
                doc={doc}
                onView={() => doc.status === "completed" && openPDFViewer(doc.id, 1)}
                onReindex={() => reindexDocument(doc.id)}
                onDelete={() => setDocumentToDelete(doc)}
                onMove={() => setMovingItem({ type: "document", id: doc.id, name: doc.name })}
                dragProps={dragProps({ type: "document", id: doc.id, name: doc.name })}
              />
            ))}
          </div>
        </section>
      )}

      {/* Empty state */}
      {isEmpty && (
        <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
          <FolderOpen className="w-10 h-10 text-text-muted/60" />
          <div className="text-sm font-medium text-text-secondary">{t("emptyFolder")}</div>
          <div className="text-xs text-text-muted">{t("emptyFolderHint")}</div>
        </div>
      )}

      {/* Create folder */}
      <NameDialog
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        onSubmit={handleCreateFolder}
        title={t("newFolder")}
        label={t("folderName")}
        placeholder={t("folderNamePlaceholder")}
        submitLabel={tCommon("create")}
      />

      {/* Rename folder */}
      <NameDialog
        isOpen={folderToRename !== null}
        onClose={() => setFolderToRename(null)}
        onSubmit={handleRenameFolder}
        title={t("renameFolder")}
        label={t("folderName")}
        placeholder={t("folderNamePlaceholder")}
        submitLabel={t("rename")}
        initialValue={folderToRename?.name ?? ""}
      />

      {/* Move to… */}
      {movingItem && (
        <MoveToDialog
          item={movingItem}
          folders={folders}
          blocked={invalidTargets(movingItem)}
          currentParentId={
            movingItem.type === "folder"
              ? folders.find((f) => f.id === movingItem.id)?.parent_id ?? null
              : currentFolderId
          }
          onClose={() => setMovingItem(null)}
          onMove={(target) => {
            const item = movingItem;
            setMovingItem(null);
            void performMove(item, target);
          }}
        />
      )}

      {/* Delete folder confirmation */}
      <ConfirmModal
        isOpen={folderToDelete !== null}
        onClose={() => setFolderToDelete(null)}
        onConfirm={confirmDeleteFolder}
        title={t("deleteFolder")}
        message={t("deleteFolderWarning")}
        detail={folderToDelete?.name}
        confirmLabel={tCommon("delete")}
        cancelLabel={tCommon("cancel")}
        isLoading={isDeletingFolder}
        variant="warning"
        icon={<FolderIcon className="w-6 h-6 text-accent-amber" />}
      />

      {/* Delete document confirmation */}
      <ConfirmModal
        isOpen={documentToDelete !== null}
        onClose={() => setDocumentToDelete(null)}
        onConfirm={confirmDeleteDocument}
        title={tDoc("deleteDocumentTitle")}
        message={tDoc("deleteDocumentWarning")}
        detail={documentToDelete?.name}
        confirmLabel={tCommon("delete")}
        cancelLabel={tCommon("cancel")}
        isLoading={isDeletingDocument}
        variant="danger"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Breadcrumb
// ---------------------------------------------------------------------------

function Breadcrumb({
  trail,
  onNavigate,
  dropProps,
  dropTarget,
  canDropOn,
  isDragging,
}: {
  trail: Folder[];
  onNavigate: (id: number | null) => void;
  dropProps: (target: DropTarget) => Record<string, unknown>;
  dropTarget: DropTarget | null;
  canDropOn: (target: DropTarget) => boolean;
  isDragging: boolean;
}) {
  const t = useTranslations("folders");

  const crumbClass = (target: DropTarget, isCurrent: boolean) =>
    clsx(
      "flex items-center gap-1.5 px-2 py-1 rounded-lg text-sm transition-colors",
      isCurrent
        ? "text-text-primary font-medium"
        : "text-text-secondary hover:text-text-primary hover:bg-white/5",
      isDragging && canDropOn(target) && "ring-1 ring-primary-500/40",
      dropTarget === target && "bg-primary-500/20 ring-2 ring-primary-500/60"
    );

  return (
    <nav className="flex items-center gap-0.5 min-w-0 flex-wrap" aria-label={t("breadcrumb")}>
      <button
        onClick={() => onNavigate(null)}
        className={crumbClass("root", trail.length === 0)}
        title={t("root")}
        {...dropProps("root")}
      >
        <Home className="w-4 h-4 flex-shrink-0" />
        <span className="hidden sm:inline">{t("root")}</span>
      </button>
      {trail.map((folder, i) => {
        const isCurrent = i === trail.length - 1;
        return (
          <div key={folder.id} className="flex items-center min-w-0">
            <ChevronRight className="w-4 h-4 text-text-muted flex-shrink-0" />
            <button
              onClick={() => onNavigate(folder.id)}
              className={clsx(crumbClass(folder.id, isCurrent), "min-w-0")}
              {...dropProps(folder.id)}
            >
              <span className="truncate max-w-[10rem]">{folder.name}</span>
            </button>
          </div>
        );
      })}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Folder card
// ---------------------------------------------------------------------------

function FolderCard({
  folder,
  isDropTarget,
  canDrop,
  onOpen,
  onRename,
  onDelete,
  onMove,
  dragProps,
  dropProps,
}: {
  folder: Folder;
  isDropTarget: boolean;
  canDrop: boolean;
  onOpen: () => void;
  onRename: () => void;
  onDelete: () => void;
  onMove: () => void;
  dragProps: Record<string, unknown>;
  dropProps: Record<string, unknown>;
}) {
  const t = useTranslations("folders");
  const tCommon = useTranslations("common");

  return (
    <div
      onDoubleClick={onOpen}
      className={clsx(
        "group relative flex items-center gap-3 px-3 py-3 rounded-xl border transition-all duration-200 cursor-pointer",
        isDropTarget
          ? "border-primary-500/70 bg-primary-500/15 ring-2 ring-primary-500/40"
          : canDrop
            ? "border-primary-500/30 bg-white/[0.03] border-dashed"
            : "border-white/10 bg-white/[0.03] hover:border-primary-500/30 hover:bg-white/[0.05]"
      )}
      {...dragProps}
      {...dropProps}
    >
      <button
        onClick={onOpen}
        className="flex items-center gap-3 min-w-0 flex-1 text-left"
        title={t("open")}
      >
        <span className="relative flex-shrink-0 text-primary-400">
          <FolderIcon className="w-6 h-6 group-hover:hidden" />
          <FolderOpen className="w-6 h-6 hidden group-hover:block" />
        </span>
        <span className="truncate text-sm font-medium text-text-primary">{folder.name}</span>
      </button>

      <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={onMove}
          className="p-1.5 rounded-lg text-text-muted hover:text-primary-400 hover:bg-white/10 transition-colors"
          title={t("moveTo")}
        >
          <FolderInput className="w-4 h-4" />
        </button>
        <button
          onClick={onRename}
          className="p-1.5 rounded-lg text-text-muted hover:text-primary-400 hover:bg-white/10 transition-colors"
          title={t("rename")}
        >
          <Pencil className="w-4 h-4" />
        </button>
        <button
          onClick={onDelete}
          className="p-1.5 rounded-lg text-text-muted hover:text-rose-400 hover:bg-rose-500/20 transition-colors"
          title={tCommon("delete")}
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Document card
// ---------------------------------------------------------------------------

function DocumentCard({
  doc,
  onView,
  onReindex,
  onDelete,
  onMove,
  dragProps,
}: {
  doc: RAGDocument;
  onView: () => void;
  onReindex: () => void;
  onDelete: () => void;
  onMove: () => void;
  dragProps: Record<string, unknown>;
}) {
  const t = useTranslations("folders");
  const tDoc = useTranslations("documents");
  const tCommon = useTranslations("common");
  const isOwner = doc.is_owner !== false;
  const isCompleted = doc.status === "completed";
  const isFailed = doc.status === "failed";

  return (
    <div
      onDoubleClick={onView}
      className={clsx(
        "group relative flex flex-col gap-2 px-3 py-3 rounded-xl border bg-white/[0.03] transition-all duration-200",
        isCompleted
          ? "border-white/10 hover:border-primary-500/30 hover:bg-white/[0.05] cursor-pointer"
          : "border-white/10"
      )}
      {...dragProps}
    >
      <div className="flex items-start gap-2 min-w-0">
        <span className="relative flex-shrink-0 mt-0.5">
          <FileText className="w-5 h-5 text-text-secondary" />
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 min-w-0">
            <IndexStatusDot doc={doc} />
            <span className="truncate text-sm font-medium text-text-primary" title={doc.name}>
              {doc.name}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 flex-wrap text-xs text-text-muted">
            {doc.is_owner === false && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary-500/15 text-primary-400 border border-primary-500/20">
                {tDoc("sharedBadge")}
              </span>
            )}
            <span>
              {doc.page_count} {tDoc("pages")}
            </span>
            {(doc.indexed_at || doc.created_at) && (
              <>
                <span aria-hidden>•</span>
                <span>
                  {formatDistanceToNow(new Date(doc.indexed_at || doc.created_at), {
                    addSuffix: true,
                  })}
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Partial-progress bar */}
      {doc.status === "processing" && (
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1.5 bg-amber-500/20 rounded-full overflow-hidden">
            <div
              className="h-full bg-amber-400 transition-all duration-300"
              style={{ width: `${Math.round((doc.progress ?? 0) * 100)}%` }}
            />
          </div>
          <span className="text-[11px] tabular-nums text-amber-400">
            {Math.round((doc.progress ?? 0) * 100)}%
          </span>
        </div>
      )}

      {/* Failure reason */}
      {isFailed && (
        <div
          className="text-xs text-rose-400/80 truncate"
          title={doc.error_message || tDoc("unknownError")}
        >
          {doc.error_message || tDoc("unknownError")}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
        {isCompleted && (
          <button
            onClick={onView}
            className="p-1.5 rounded-lg text-text-muted hover:text-primary-400 hover:bg-primary-500/20 transition-colors"
            title={tDoc("view")}
          >
            <Eye className="w-4 h-4" />
          </button>
        )}
        {isOwner && (
          <button
            onClick={onMove}
            className="p-1.5 rounded-lg text-text-muted hover:text-primary-400 hover:bg-white/10 transition-colors"
            title={t("moveTo")}
          >
            <FolderInput className="w-4 h-4" />
          </button>
        )}
        {isOwner && (
          <button
            onClick={onReindex}
            className="p-1.5 rounded-lg text-text-muted hover:text-primary-400 hover:bg-white/10 transition-colors"
            title={tDoc("reindex")}
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        )}
        {isOwner && (
          <button
            onClick={onDelete}
            className="p-1.5 rounded-lg text-text-muted hover:text-rose-400 hover:bg-rose-500/20 transition-colors"
            title={tCommon("delete")}
          >
            <Trash2 className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Name dialog (create / rename)
// ---------------------------------------------------------------------------

function NameDialog({
  isOpen,
  onClose,
  onSubmit,
  title,
  label,
  placeholder,
  submitLabel,
  initialValue = "",
}: {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (name: string) => void | Promise<void>;
  title: string;
  label: string;
  placeholder: string;
  submitLabel: string;
  initialValue?: string;
}) {
  const tCommon = useTranslations("common");
  const [value, setValue] = useState(initialValue);
  const [submitting, setSubmitting] = useState(false);
  // Re-seed the field whenever the dialog opens for a (possibly different) subject.
  const [seededFor, setSeededFor] = useState<string | null>(null);
  const openKey = isOpen ? `${title}:${initialValue}` : null;
  if (openKey !== seededFor) {
    setSeededFor(openKey);
    setValue(initialValue);
  }

  const submit = async () => {
    const name = value.trim();
    if (!name || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit(name);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog isOpen={isOpen} onClose={onClose} title={title} icon={<FolderPlus className="w-5 h-5 text-primary-400" />}>
      <div className="space-y-4">
        <label className="block">
          <span className="text-sm text-text-secondary">{label}</span>
          <input
            type="text"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void submit();
              }
            }}
            placeholder={placeholder}
            className="mt-1.5 w-full px-3 py-2 text-sm bg-bg-secondary border border-white/10 rounded-lg text-text-primary placeholder:text-text-muted focus:outline-none focus:border-primary-500/50 focus:ring-1 focus:ring-primary-500/25"
          />
        </label>
        <div className="flex justify-end gap-3 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors"
          >
            {tCommon("cancel")}
          </button>
          <button
            onClick={() => void submit()}
            disabled={!value.trim() || submitting}
            className="btn-primary !px-4 !py-2 !text-sm"
          >
            {submitLabel}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Move-to dialog (folder picker)
// ---------------------------------------------------------------------------

function MoveToDialog({
  item,
  folders,
  blocked,
  currentParentId,
  onClose,
  onMove,
}: {
  item: ExplorerItem;
  folders: Folder[];
  blocked: Set<number>;
  currentParentId: number | null;
  onClose: () => void;
  onMove: (target: number | null) => void;
}) {
  const t = useTranslations("folders");

  // Render the whole tree indented; disable blocked targets and the item's
  // current location (a no-op move).
  const rows: Array<{ folder: Folder; depth: number }> = [];
  const build = (parentId: number | null, depth: number) => {
    for (const f of childFolders(folders, parentId)) {
      rows.push({ folder: f, depth });
      build(f.id, depth + 1);
    }
  };
  build(null, 0);

  const RowButton = ({
    label,
    depth,
    disabled,
    onClick,
    icon,
  }: {
    label: string;
    depth: number;
    disabled: boolean;
    onClick: () => void;
    icon: React.ReactNode;
  }) => (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{ paddingLeft: `${depth * 16 + 12}px` }}
      className={clsx(
        "w-full flex items-center gap-2 py-2 pr-3 rounded-lg text-sm text-left transition-colors",
        disabled
          ? "text-text-muted/50 cursor-not-allowed"
          : "text-text-secondary hover:text-text-primary hover:bg-white/5"
      )}
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );

  return (
    <Dialog
      isOpen
      onClose={onClose}
      title={t("moveTitle", { name: item.name })}
      icon={<FolderInput className="w-5 h-5 text-primary-400" />}
    >
      <div className="space-y-1">
        <div className="text-xs text-text-muted mb-2">{t("selectDestination")}</div>
        <RowButton
          label={t("root")}
          depth={0}
          disabled={currentParentId === null}
          onClick={() => onMove(null)}
          icon={<Home className="w-4 h-4 flex-shrink-0" />}
        />
        {rows.map(({ folder, depth }) => {
          const disabled = blocked.has(folder.id) || currentParentId === folder.id;
          return (
            <RowButton
              key={folder.id}
              label={folder.name}
              depth={depth + 1}
              disabled={disabled}
              onClick={() => onMove(folder.id)}
              icon={
                disabled ? (
                  <CornerLeftUp className="w-4 h-4 flex-shrink-0 opacity-40" />
                ) : (
                  <FolderIcon className="w-4 h-4 flex-shrink-0 text-primary-400/70" />
                )
              }
            />
          );
        })}
      </div>
    </Dialog>
  );
}
