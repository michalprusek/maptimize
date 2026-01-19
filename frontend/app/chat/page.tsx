"use client";

/**
 * Chat Page
 *
 * Full-screen chat interface with RAG functionality.
 * Located outside /dashboard to avoid inheriting the dashboard layout.
 * Uses collapsible navigation sidebar (same pattern as editor).
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { useChatStore } from "@/stores/chatStore";
import { ChatPageContent } from "@/components/chat/ChatPageContent";

export default function ChatPage() {
  const router = useRouter();

  // Auth check
  const { isAuthenticated, isLoading: authLoading, checkAuth } = useAuthStore();
  const { loadThreads, loadDocuments, refreshIndexingStatus } = useChatStore();

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    if (!authLoading && !isAuthenticated) {
      router.push("/auth");
    }
  }, [authLoading, isAuthenticated, router]);

  // Load initial data
  useEffect(() => {
    if (isAuthenticated) {
      loadThreads();
      loadDocuments();
      refreshIndexingStatus();

      // Refresh indexing status and documents periodically
      const interval = setInterval(() => {
        refreshIndexingStatus();
        loadDocuments(); // Also refresh documents to update progress
      }, 5000); // Refresh every 5 seconds for better UX
      return () => clearInterval(interval);
    }
  }, [isAuthenticated, loadThreads, loadDocuments, refreshIndexingStatus]);

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-primary">
        <div className="w-12 h-12 border-4 border-primary-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  return <ChatPageContent />;
}
