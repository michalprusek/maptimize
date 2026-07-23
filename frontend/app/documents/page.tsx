"use client";

/**
 * Documents Page
 *
 * Standalone document database UI (replaces the former /chat agent page).
 * Located outside /dashboard to avoid inheriting the dashboard layout; uses the
 * collapsible navigation sidebar (same pattern as the editor).
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { useDocumentStore } from "@/stores/documentStore";
import { DocumentsPageContent } from "@/components/documents";

export default function DocumentsPage() {
  const router = useRouter();

  const { isAuthenticated, isLoading: authLoading, checkAuth } = useAuthStore();
  const { loadDocuments, refreshIndexingStatus, loadFolders } = useDocumentStore();

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    if (!authLoading && !isAuthenticated) {
      router.push("/auth");
    }
  }, [authLoading, isAuthenticated, router]);

  // Load initial data, then poll so upload/indexing progress stays fresh.
  useEffect(() => {
    if (isAuthenticated) {
      loadFolders();
      loadDocuments();
      refreshIndexingStatus();

      const interval = setInterval(() => {
        refreshIndexingStatus();
        loadDocuments(); // also refresh documents to update indexing progress
      }, 5000);
      return () => clearInterval(interval);
    }
  }, [isAuthenticated, loadDocuments, refreshIndexingStatus, loadFolders]);

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

  return <DocumentsPageContent />;
}
