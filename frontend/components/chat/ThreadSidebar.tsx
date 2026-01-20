"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import type { ChatThread } from "@/lib/api";
import {
  Plus,
  MessageSquare,
  Trash2,
  Pencil,
  Check,
  X,
  FileText,
  ArrowLeft,
} from "lucide-react";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";
import { DocumentsModal } from "./DocumentsModal";

export function ThreadSidebar() {
  const router = useRouter();
  const t = useTranslations("chat");
  const tCommon = useTranslations("common");
  const {
    threads,
    activeThreadId,
    isLoadingThreads,
    isDeletingThread,
    clearActiveThread,
    selectThread,
    renameThread,
    deleteThread,
    documents,
  } = useChatStore();

  const [editingThreadId, setEditingThreadId] = useState<number | null>(null);
  const [editingName, setEditingName] = useState("");
  const [threadToDelete, setThreadToDelete] = useState<ChatThread | null>(null);
  const [showDocumentsModal, setShowDocumentsModal] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleNewThread = () => {
    clearActiveThread();
  };

  const handleGoBack = () => {
    router.back();
  };

  const startEditing = (threadId: number, currentName: string) => {
    setEditingThreadId(threadId);
    setEditingName(currentName);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const saveEdit = async () => {
    if (editingThreadId && editingName.trim()) {
      await renameThread(editingThreadId, editingName.trim());
    }
    setEditingThreadId(null);
    setEditingName("");
  };

  const cancelEdit = () => {
    setEditingThreadId(null);
    setEditingName("");
  };

  const handleDeleteThread = (thread: ChatThread, e: React.MouseEvent) => {
    e.stopPropagation();
    setThreadToDelete(thread);
  };

  const confirmDeleteThread = async () => {
    if (threadToDelete) {
      await deleteThread(threadToDelete.id);
      setThreadToDelete(null);
    }
  };

  // Count documents being processed
  const processingCount = documents.filter(
    (d) => d.status === "processing" || d.status === "pending"
  ).length;

  return (
    <div className="flex flex-col h-full">
      {/* Header with Back + New Thread + Documents button */}
      <div className="p-4 border-b border-white/5">
        <div className="flex items-center gap-2">
          {/* Back button */}
          <button
            onClick={handleGoBack}
            className={clsx(
              "p-2.5 rounded-lg transition-all duration-200",
              "bg-white/[0.05] border border-white/10 hover:border-white/20",
              "text-text-secondary hover:text-text-primary hover:bg-white/[0.08]"
            )}
            title={tCommon("back")}
          >
            <ArrowLeft className="w-5 h-5" />
          </button>

          <button
            onClick={handleNewThread}
            className={clsx(
              "flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg font-medium",
              "bg-gradient-to-r from-primary-500 to-primary-600",
              "text-white whitespace-nowrap text-sm",
              "hover:from-primary-400 hover:to-primary-500",
              "shadow-lg shadow-primary-500/20 hover:shadow-primary-500/30",
              "transform transition-all duration-200",
              "hover:scale-[1.02] active:scale-[0.98]"
            )}
          >
            <Plus className="w-4 h-4 flex-shrink-0" />
            {t("newThread")}
          </button>

          {/* Documents button */}
          <button
            onClick={() => setShowDocumentsModal(true)}
            className={clsx(
              "relative p-2.5 rounded-lg transition-all duration-200",
              "bg-white/[0.05] border border-white/10 hover:border-white/20",
              "text-text-secondary hover:text-text-primary hover:bg-white/[0.08]"
            )}
            title={t("documents")}
          >
            <FileText className="w-5 h-5" />
            {/* Badge for document count */}
            {documents.length > 0 && (
              <span
                className={clsx(
                  "absolute -top-1 -right-1 min-w-[18px] h-[18px] flex items-center justify-center",
                  "text-[10px] font-bold rounded-full px-1",
                  processingCount > 0
                    ? "bg-amber-500 text-white animate-pulse"
                    : "bg-primary-500 text-white"
                )}
              >
                {documents.length}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* Conversations Label */}
      <div className="px-4 py-2 border-b border-white/5">
        <span className="text-xs font-medium text-text-muted uppercase tracking-wider">
          {t("conversations")}
        </span>
      </div>

      {/* Threads List */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {isLoadingThreads ? (
          // Skeleton loading
          <div className="space-y-2 p-2">
            {[...Array(4)].map((_, i) => (
              <div
                key={i}
                className="h-14 rounded-lg bg-gradient-to-r from-white/5 via-white/8 to-white/5 bg-[length:200%_100%] animate-shimmer"
                style={{ animationDelay: `${i * 0.1}s` }}
              />
            ))}
          </div>
        ) : threads.length === 0 ? (
          <div className="text-center py-8 text-text-secondary text-sm animate-fade-in">
            {t("noThreads")}
          </div>
        ) : (
          threads.map((thread, index) => (
            <div
              key={thread.id}
              onClick={() => selectThread(thread.id)}
              className={clsx(
                "group flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer",
                "transition-all duration-200",
                "animate-slide-in-left",
                activeThreadId === thread.id
                  ? "bg-primary-500/10 text-primary-400 border-l-2 border-primary-400"
                  : "hover:bg-white/5 text-text-secondary hover:text-text-primary border-l-2 border-transparent"
              )}
              style={{
                animationDelay: `${index * 0.03}s`,
                opacity: 0,
                animationFillMode: "forwards",
              }}
            >
              <MessageSquare className="w-4 h-4 flex-shrink-0" />

              {editingThreadId === thread.id ? (
                <div className="flex-1 flex items-center gap-1">
                  <input
                    ref={inputRef}
                    value={editingName}
                    onChange={(e) => setEditingName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") saveEdit();
                      if (e.key === "Escape") cancelEdit();
                    }}
                    onClick={(e) => e.stopPropagation()}
                    className="flex-1 bg-white/5 border border-white/10 px-2 py-1 rounded text-sm text-text-primary focus:outline-none focus:border-primary-500/50"
                  />
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      saveEdit();
                    }}
                    className="p-1 hover:bg-green-500/20 rounded text-green-400 transition-colors"
                  >
                    <Check className="w-3 h-3" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      cancelEdit();
                    }}
                    className="p-1 hover:bg-red-500/20 rounded text-red-400 transition-colors"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              ) : (
                <>
                  <div className="flex-1 min-w-0">
                    <div className="truncate text-sm font-medium">
                      {thread.name}
                    </div>
                    <div className="text-xs text-text-muted">
                      {thread.message_count} {t("messages")} •{" "}
                      {formatDistanceToNow(new Date(thread.updated_at), {
                        addSuffix: true,
                      })}
                    </div>
                  </div>

                  {/* Action buttons */}
                  <div className="hidden group-hover:flex items-center gap-1">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        startEditing(thread.id, thread.name);
                      }}
                      className="p-1.5 hover:bg-white/10 rounded text-text-muted hover:text-text-primary transition-all duration-200 hover:scale-110"
                    >
                      <Pencil className="w-3 h-3" />
                    </button>
                    <button
                      onClick={(e) => handleDeleteThread(thread, e)}
                      className="p-1.5 hover:bg-red-500/20 rounded text-text-muted hover:text-red-400 transition-all duration-200 hover:scale-110"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                </>
              )}
            </div>
          ))
        )}
      </div>

      {/* Documents Modal */}
      <DocumentsModal
        isOpen={showDocumentsModal}
        onClose={() => setShowDocumentsModal(false)}
      />

      {/* Delete Thread Confirmation Modal */}
      <ConfirmModal
        isOpen={threadToDelete !== null}
        onClose={() => setThreadToDelete(null)}
        onConfirm={confirmDeleteThread}
        title={t("deleteThreadTitle")}
        message={t("deleteThreadWarning")}
        detail={threadToDelete?.name}
        confirmLabel={tCommon("delete")}
        cancelLabel={tCommon("cancel")}
        isLoading={isDeletingThread}
        variant="danger"
      />
    </div>
  );
}
