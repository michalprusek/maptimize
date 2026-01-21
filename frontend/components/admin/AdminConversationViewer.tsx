"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { MessageSquare, ChevronRight, User, Bot, FileText, Image as ImageIcon, AlertCircle, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { AdminChatMessage } from "@/lib/api";
import { Spinner } from "@/components/ui";
import { formatShortDateTime } from "@/lib/utils";

interface AdminConversationViewerProps {
  userId: number;
}

export function AdminConversationViewer({ userId }: AdminConversationViewerProps) {
  const t = useTranslations("admin.userDetail");
  const [selectedThread, setSelectedThread] = useState<number | null>(null);

  const { data: threadsData, isLoading: threadsLoading, isError: threadsError, refetch: refetchThreads } = useQuery({
    queryKey: ["admin", "user", userId, "conversations"],
    queryFn: () => api.getAdminUserConversations(userId),
  });

  const { data: messagesData, isLoading: messagesLoading, isError: messagesError, refetch: refetchMessages } = useQuery({
    queryKey: ["admin", "user", userId, "conversation", selectedThread],
    queryFn: () => api.getAdminConversationMessages(userId, selectedThread!),
    enabled: !!selectedThread,
  });

  if (threadsLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner size="lg" />
      </div>
    );
  }

  if (threadsError) {
    return (
      <div className="text-center py-12">
        <AlertCircle className="w-12 h-12 mx-auto mb-3 text-accent-red opacity-70" />
        <p className="text-text-muted mb-4">{t("loadError")}</p>
        <button
          onClick={() => refetchThreads()}
          className="btn-secondary inline-flex items-center gap-2"
        >
          <RefreshCw className="w-4 h-4" />
          {t("retry")}
        </button>
      </div>
    );
  }

  const threads = threadsData?.threads || [];

  if (threads.length === 0) {
    return (
      <div className="text-center py-12 text-text-muted">
        <MessageSquare className="w-12 h-12 mx-auto mb-3 opacity-50" />
        <p>{t("noConversations")}</p>
      </div>
    );
  }

  return (
    <div className="flex gap-4 h-[500px]">
      {/* Thread list */}
      <div className="w-1/3 glass-card overflow-hidden flex flex-col">
        <div className="px-4 py-3 border-b border-white/10">
          <h4 className="text-sm font-medium text-text-primary">
            {t("conversationsCount", { count: threads.length })}
          </h4>
        </div>
        <div className="flex-1 overflow-y-auto">
          {threads.map((thread) => (
            <button
              key={thread.id}
              onClick={() => setSelectedThread(thread.id)}
              className={`w-full px-4 py-3 text-left border-b border-white/5 hover:bg-white/5 transition-colors ${
                selectedThread === thread.id ? "bg-white/10" : ""
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-text-primary truncate">
                  {thread.name}
                </span>
                <ChevronRight className="w-4 h-4 text-text-muted flex-shrink-0" />
              </div>
              <div className="flex items-center gap-2 text-xs text-text-muted">
                <span>{t("messagesCount", { count: thread.message_count })}</span>
                <span>-</span>
                <span>{formatShortDateTime(thread.updated_at)}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Messages view */}
      <div className="flex-1 glass-card overflow-hidden flex flex-col">
        {!selectedThread ? (
          <div className="flex-1 flex items-center justify-center text-text-muted">
            <p>{t("selectConversation")}</p>
          </div>
        ) : messagesLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <Spinner size="lg" />
          </div>
        ) : messagesError ? (
          <div className="flex-1 flex flex-col items-center justify-center">
            <AlertCircle className="w-8 h-8 text-accent-red opacity-70 mb-2" />
            <p className="text-text-muted mb-3">{t("loadError")}</p>
            <button
              onClick={() => refetchMessages()}
              className="btn-secondary inline-flex items-center gap-2 text-sm"
            >
              <RefreshCw className="w-4 h-4" />
              {t("retry")}
            </button>
          </div>
        ) : (
          <>
            <div className="px-4 py-3 border-b border-white/10">
              <h4 className="text-sm font-medium text-text-primary">
                {messagesData?.thread_name}
              </h4>
              <p className="text-xs text-text-muted">
                {t("messagesCount", { count: messagesData?.total || 0 })}
              </p>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {messagesData?.messages.map((msg) => (
                <MessageBubble key={msg.id} message={msg} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: AdminChatMessage }) {
  const t = useTranslations("admin.userDetail");
  const isUser = message.role === "user";

  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <div
        className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
          isUser ? "bg-primary-500/20 text-primary-400" : "bg-purple-500/20 text-purple-400"
        }`}
      >
        {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
      </div>
      <div
        className={`max-w-[80%] ${
          isUser ? "bg-primary-500/20 border-primary-500/30" : "bg-white/5 border-white/10"
        } border rounded-lg px-4 py-2`}
      >
        <p className="text-sm text-text-primary whitespace-pre-wrap break-words">
          {message.content}
        </p>
        <div className="flex items-center gap-2 mt-2">
          <span className="text-xs text-text-muted">{formatShortDateTime(message.created_at)}</span>
          {message.has_citations && (
            <span className="text-xs text-amber-400 flex items-center gap-1">
              <FileText className="w-3 h-3" /> {t("citations")}
            </span>
          )}
          {message.has_images && (
            <span className="text-xs text-green-400 flex items-center gap-1">
              <ImageIcon className="w-3 h-3" /> {t("imagesLabel")}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
