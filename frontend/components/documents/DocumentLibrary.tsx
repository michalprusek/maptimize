"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { useDocumentStore } from "@/stores/documentStore";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import type { RAGDocument } from "@/lib/api";
import {
  FileText,
  Loader2,
  AlertCircle,
  CheckCircle2,
  Trash2,
  Eye,
  RefreshCw,
} from "lucide-react";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";

/**
 * DocumentLibrary
 *
 * The document database's main list, promoted from the old DocumentsModal body.
 * Groups documents by indexing status (processing / failed / indexed) and offers
 * per-document open (into the PDF viewer), reindex and delete.
 */
export function DocumentLibrary() {
  const t = useTranslations("documents");
  const tCommon = useTranslations("common");
  const {
    documents,
    deleteDocument,
    reindexDocument,
    isDeletingDocument,
    openPDFViewer,
  } = useDocumentStore();

  const [documentToDelete, setDocumentToDelete] = useState<RAGDocument | null>(null);

  const confirmDeleteDocument = async () => {
    if (documentToDelete) {
      await deleteDocument(documentToDelete.id);
      setDocumentToDelete(null);
    }
  };

  const handleViewDocument = (doc: RAGDocument) => {
    if (doc.status === "completed") {
      openPDFViewer(doc.id, 1);
    }
  };

  const processingDocs = documents.filter(
    (d) => d.status === "processing" || d.status === "pending"
  );
  const completedDocs = documents.filter((d) => d.status === "completed");
  const failedDocs = documents.filter((d) => d.status === "failed");

  return (
    <div className="space-y-6">
      {/* Processing documents */}
      {processingDocs.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-medium text-text-muted uppercase tracking-wider">
            {t("processing")}
          </div>
          {processingDocs.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center gap-3 px-4 py-3 rounded-lg bg-amber-500/10 border border-amber-500/20 animate-pulse-soft"
            >
              <Loader2 className="w-5 h-5 animate-spin text-amber-400 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="truncate text-sm font-medium">{doc.name}</div>
                <div className="flex items-center gap-2 mt-1">
                  <div className="flex-1 h-1.5 bg-amber-500/20 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-amber-500 transition-all duration-300"
                      style={{ width: `${Math.round((doc.progress ?? 0) * 100)}%` }}
                    />
                  </div>
                  <span className="text-xs text-amber-400 tabular-nums">
                    {Math.round((doc.progress ?? 0) * 100)}%
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Failed documents */}
      {failedDocs.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-medium text-red-400 uppercase tracking-wider">
            {t("failed")}
          </div>
          {failedDocs.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center gap-3 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/20"
            >
              <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="truncate text-sm font-medium">{doc.name}</div>
                <div className="text-xs text-red-400/80 truncate mt-0.5" title={doc.error_message || undefined}>
                  {doc.error_message || t("unknownError")}
                </div>
              </div>
              {doc.is_owner !== false && (
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => reindexDocument(doc.id)}
                    className="p-2 hover:bg-amber-500/20 rounded-lg text-red-400 hover:text-amber-400 transition-colors"
                    title={t("reindex")}
                  >
                    <RefreshCw className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => setDocumentToDelete(doc)}
                    className="p-2 hover:bg-red-500/20 rounded-lg text-red-400 transition-colors"
                    title={tCommon("delete")}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Completed documents */}
      {completedDocs.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-medium text-text-muted uppercase tracking-wider">
            {t("indexed")}
          </div>
          {completedDocs.map((doc) => (
            <div
              key={doc.id}
              onClick={() => handleViewDocument(doc)}
              className={clsx(
                "group flex items-center gap-3 px-4 py-3 rounded-lg",
                "bg-white/[0.03] border border-white/10 hover:border-primary-500/30",
                "cursor-pointer transition-all duration-200 hover:bg-white/[0.05]"
              )}
            >
              <div className="relative flex-shrink-0">
                <FileText className="w-5 h-5 text-text-secondary" />
                <CheckCircle2 className="absolute -bottom-0.5 -right-0.5 w-3 h-3 text-green-400" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 min-w-0">
                  <div className="truncate text-sm font-medium">{doc.name}</div>
                  {doc.is_owner === false && (
                    <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary-500/15 text-primary-400 border border-primary-500/20">
                      {t("sharedBadge")}
                    </span>
                  )}
                </div>
                <div className="text-xs text-text-muted mt-0.5">
                  {doc.page_count} {t("pages")} • {formatDistanceToNow(new Date(doc.indexed_at || doc.created_at), { addSuffix: true })}
                </div>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleViewDocument(doc);
                  }}
                  className="p-2 hover:bg-primary-500/20 rounded-lg text-text-muted hover:text-primary-400 transition-colors"
                  title={t("view")}
                >
                  <Eye className="w-4 h-4" />
                </button>
                {doc.is_owner !== false && (
                  <>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        reindexDocument(doc.id);
                      }}
                      className="p-2 hover:bg-white/10 rounded-lg text-text-muted hover:text-primary-400 transition-colors"
                      title={t("reindex")}
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setDocumentToDelete(doc);
                      }}
                      className="p-2 hover:bg-red-500/20 rounded-lg text-text-muted hover:text-red-400 transition-colors"
                      title={tCommon("delete")}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Empty state */}
      {documents.length === 0 && (
        <div className="text-center py-8 text-text-muted">
          {t("noDocuments")}
        </div>
      )}

      {/* Delete Confirmation Modal */}
      <ConfirmModal
        isOpen={documentToDelete !== null}
        onClose={() => setDocumentToDelete(null)}
        onConfirm={confirmDeleteDocument}
        title={t("deleteDocumentTitle")}
        message={t("deleteDocumentWarning")}
        detail={documentToDelete?.name}
        confirmLabel={tCommon("delete")}
        cancelLabel={tCommon("cancel")}
        isLoading={isDeletingDocument}
        variant="danger"
      />
    </div>
  );
}
