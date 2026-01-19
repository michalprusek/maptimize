"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { DocumentUpload } from "./DocumentUpload";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import type { RAGDocument } from "@/lib/api";
import {
  X,
  FileText,
  Loader2,
  AlertCircle,
  CheckCircle2,
  Trash2,
  Eye,
} from "lucide-react";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";

interface DocumentsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function DocumentsModal({ isOpen, onClose }: DocumentsModalProps) {
  const t = useTranslations("chat");
  const tCommon = useTranslations("common");
  const {
    documents,
    deleteDocument,
    isDeletingDocument,
    openPDFViewer,
  } = useChatStore();

  const [documentToDelete, setDocumentToDelete] = useState<RAGDocument | null>(null);

  const handleDeleteDocument = (doc: RAGDocument, e: React.MouseEvent) => {
    e.stopPropagation();
    setDocumentToDelete(doc);
  };

  const confirmDeleteDocument = async () => {
    if (documentToDelete) {
      await deleteDocument(documentToDelete.id);
      setDocumentToDelete(null);
    }
  };

  const handleViewDocument = (doc: RAGDocument) => {
    if (doc.status === "completed") {
      openPDFViewer(doc.id, 1);
      onClose();
    }
  };

  const processingDocs = documents.filter(
    (d) => d.status === "processing" || d.status === "pending"
  );
  const completedDocs = documents.filter((d) => d.status === "completed");
  const failedDocs = documents.filter((d) => d.status === "failed");

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm animate-fade-in"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="fixed inset-0 z-[101] flex items-center justify-center p-4 pointer-events-none">
        <div
          className={clsx(
            "w-full max-w-lg max-h-[80vh] bg-bg-secondary rounded-xl border border-white/10",
            "shadow-2xl pointer-events-auto flex flex-col",
            "animate-scale-in"
          )}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-white/10">
            <h2 className="text-lg font-semibold text-text-primary">
              {t("documents")}
            </h2>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-white/10 text-text-secondary hover:text-text-primary transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-5 space-y-5">
            {/* Upload Section */}
            <DocumentUpload />

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
                  {t("failed") || "Failed"}
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
                        {doc.error_message || "Unknown error"}
                      </div>
                    </div>
                    <button
                      onClick={(e) => handleDeleteDocument(doc, e)}
                      className="p-2 hover:bg-red-500/20 rounded-lg text-red-400 transition-colors"
                      title={tCommon("delete")}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
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
                      <div className="truncate text-sm font-medium">{doc.name}</div>
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
                        title={t("view") || "View"}
                      >
                        <Eye className="w-4 h-4" />
                      </button>
                      <button
                        onClick={(e) => handleDeleteDocument(doc, e)}
                        className="p-2 hover:bg-red-500/20 rounded-lg text-text-muted hover:text-red-400 transition-colors"
                        title={tCommon("delete")}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Empty state */}
            {documents.length === 0 && (
              <div className="text-center py-8 text-text-muted">
                {t("noDocuments") || "No documents uploaded yet"}
              </div>
            )}
          </div>
        </div>
      </div>

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
    </>
  );
}
