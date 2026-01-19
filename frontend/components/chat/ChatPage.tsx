"use client";

import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { ThreadSidebar } from "./ThreadSidebar";
import { ChatArea } from "./ChatArea";
import { IndexingProgress } from "./IndexingProgress";
import { PDFViewerPanel } from "./PDFViewerPanel";
import { PanelLeftClose, PanelLeft } from "lucide-react";
import { clsx } from "clsx";

export function ChatPage() {
  const t = useTranslations("chat");
  const {
    isThreadSidebarOpen,
    toggleThreadSidebar,
    indexingStatus,
  } = useChatStore();

  const isIndexing =
    indexingStatus &&
    (indexingStatus.documents_processing > 0 ||
      indexingStatus.documents_pending > 0);

  return (
    <div className="flex h-full bg-bg-primary">
      {/* Thread Sidebar */}
      <div
        className={clsx(
          "flex-shrink-0 transition-all duration-300 ease-in-out border-r border-white/5 bg-bg-secondary",
          isThreadSidebarOpen ? "w-72" : "w-0 overflow-hidden"
        )}
      >
        <ThreadSidebar />
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header with toggle */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-white/5 bg-bg-secondary/50">
          <button
            onClick={toggleThreadSidebar}
            className="p-2 rounded-lg hover:bg-white/5 text-text-secondary hover:text-text-primary transition-colors"
            title={isThreadSidebarOpen ? t("hideSidebar") : t("showSidebar")}
          >
            {isThreadSidebarOpen ? (
              <PanelLeftClose className="w-5 h-5" />
            ) : (
              <PanelLeft className="w-5 h-5" />
            )}
          </button>
          <h1 className="text-lg font-semibold text-text-primary">{t("title")}</h1>

          {/* Indexing indicator */}
          {isIndexing && (
            <div className="ml-auto">
              <IndexingProgress />
            </div>
          )}
        </div>

        {/* Chat Content */}
        <ChatArea />
      </div>

      {/* PDF Viewer Panel (right side) */}
      <PDFViewerPanel />
    </div>
  );
}
