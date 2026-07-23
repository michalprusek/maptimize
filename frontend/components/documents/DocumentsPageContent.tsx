"use client";

/**
 * DocumentsPageContent
 *
 * The document database UI: a searchable library of indexed documents with a
 * persistent upload dropzone, paper discovery ("Find sources"), an inline PDF
 * viewer, and the "Connect to Claude" MCP connector panel. Full-screen and
 * outside /dashboard, using the collapsible navigation sidebar like the editor.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslations } from "next-intl";
import { AnimatePresence, motion } from "framer-motion";
import {
  ChevronRight,
  ChevronLeft,
  FileText,
  Search,
  Plug,
  Loader2,
  X,
} from "lucide-react";
import { useDocumentStore } from "@/stores/documentStore";
import { AppSidebar } from "@/components/layout";
import { DocumentLibrary } from "./DocumentLibrary";
import { DocumentUpload } from "./DocumentUpload";
import { DiscoverSourcesModal } from "./DiscoverSourcesModal";
import { PDFViewerPanel } from "./PDFViewerPanel";
import { IndexingProgress } from "./IndexingProgress";
import { ConnectClaudePanel } from "./ConnectClaudePanel";
import { api, DocumentSearchHit } from "@/lib/api";
import { clsx } from "clsx";

const TABLET_BREAKPOINT = 1024;

function useMediaQuery(breakpoint: number): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    const checkMatch = () => setMatches(window.innerWidth >= breakpoint);
    checkMatch();
    let timeoutId: NodeJS.Timeout;
    const handleResize = () => {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(checkMatch, 100);
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      clearTimeout(timeoutId);
    };
  }, [breakpoint]);
  return matches;
}

function useReducedMotion(): boolean {
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mediaQuery.matches);
    const handler = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mediaQuery.addEventListener("change", handler);
    return () => mediaQuery.removeEventListener("change", handler);
  }, []);
  return reducedMotion;
}

export function DocumentsPageContent() {
  const t = useTranslations("documents");
  const {
    documents,
    indexingStatus,
    isPDFPanelOpen,
    closePDFViewer,
    activePDFDocumentId,
    openPDFViewer,
  } = useDocumentStore();

  const isDesktop = useMediaQuery(TABLET_BREAKPOINT);
  const reducedMotion = useReducedMotion();

  const [showNavigation, setShowNavigation] = useState(false);
  const [isDiscoverOpen, setIsDiscoverOpen] = useState(false);
  const [isConnectOpen, setIsConnectOpen] = useState(false);

  // ---- Library search (semantic, across all indexed documents) ----
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<DocumentSearchHit[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const searchTimeout = useRef<NodeJS.Timeout | null>(null);

  const runSearch = useCallback(async (q: string) => {
    setIsSearching(true);
    setSearchError(null);
    try {
      const res = await api.searchDocuments(q.trim());
      setSearchResults(res.results);
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : t("searchFailed"));
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  }, [t]);

  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (value.trim().length < 2) {
      setSearchResults(null);
      setSearchError(null);
      return;
    }
    searchTimeout.current = setTimeout(() => runSearch(value), 300);
  }, [runSearch]);

  const clearSearch = useCallback(() => {
    setSearchQuery("");
    setSearchResults(null);
    setSearchError(null);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
  }, []);

  const openHit = useCallback((hit: DocumentSearchHit) => {
    openPDFViewer(hit.document_id, hit.page_number);
  }, [openPDFViewer]);

  const isIndexing =
    indexingStatus &&
    (indexingStatus.documents_processing > 0 || indexingStatus.documents_pending > 0);

  const completedDocuments = documents.filter((d) => d.status === "completed");
  const hasDocuments = completedDocuments.length > 0;

  const handleTogglePDFPanel = useCallback(() => {
    if (isPDFPanelOpen) {
      closePDFViewer();
    } else if (completedDocuments.length > 0) {
      openPDFViewer(completedDocuments[0].id, 1);
    }
  }, [isPDFPanelOpen, closePDFViewer, openPDFViewer, completedDocuments]);

  const showingResults = searchResults !== null;

  return (
    <div className="h-screen bg-bg-primary flex overflow-hidden">
      {/* Navigation sidebar toggle trigger */}
      <button
        onClick={() => setShowNavigation(!showNavigation)}
        className={clsx(
          "absolute top-1/2 -translate-y-1/2 z-50 bg-bg-secondary px-1 py-6 rounded-r-lg border-y border-r border-white/5 hover:bg-white/5",
          "transition-all duration-300 ease-out",
          showNavigation ? "left-64" : "left-0"
        )}
        title={showNavigation ? t("hideNavigation") : t("showNavigation")}
      >
        <ChevronRight
          className={clsx(
            "w-4 h-4 text-text-secondary transition-transform duration-200",
            showNavigation && "rotate-180"
          )}
        />
      </button>

      {/* Slide-out navigation sidebar */}
      <AnimatePresence>
        {showNavigation && (
          <AppSidebar
            variant="overlay"
            onClose={() => setShowNavigation(false)}
            activePath="/documents"
          />
        )}
      </AnimatePresence>

      {/* Main content area - shifts based on navigation sidebar */}
      <div
        className={clsx(
          "flex-1 flex transition-all duration-300 ease-out min-w-0",
          showNavigation ? "ml-64" : "ml-0"
        )}
      >
        <div className="flex-1 flex flex-col min-w-0">
          {/* Header */}
          <div className="flex items-center gap-3 px-4 sm:px-6 py-3 border-b border-white/5 bg-bg-secondary/50">
            <FileText className="w-5 h-5 text-primary-400 flex-shrink-0" />
            <h1 className="text-lg font-semibold text-text-primary">{t("title")}</h1>

            {isIndexing && (
              <div className="hidden sm:block ml-2">
                <IndexingProgress />
              </div>
            )}

            <div className="ml-auto flex items-center gap-2">
              <button
                onClick={() => setIsDiscoverOpen(true)}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/[0.03] hover:bg-white/[0.06] border border-white/10 text-text-secondary hover:text-text-primary text-sm font-medium transition-colors"
              >
                <Search className="w-4 h-4" />
                <span className="hidden sm:inline">{t("discoverSources")}</span>
              </button>
              <button
                onClick={() => setIsConnectOpen(true)}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-primary-500/15 hover:bg-primary-500/25 border border-primary-500/20 text-primary-400 text-sm font-medium transition-colors"
              >
                <Plug className="w-4 h-4" />
                <span className="hidden sm:inline">{t("connectToClaude")}</span>
              </button>
            </div>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto">
            <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6 space-y-6">
              {/* Search bar */}
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => handleSearchChange(e.target.value)}
                  placeholder={t("searchPlaceholder")}
                  className="w-full pl-10 pr-10 py-2.5 text-sm bg-white/5 border border-white/10 rounded-xl text-text-primary placeholder:text-text-muted focus:outline-none focus:border-primary-500/50 focus:ring-1 focus:ring-primary-500/25"
                />
                {(isSearching || searchQuery) && (
                  <div className="absolute right-3 top-1/2 -translate-y-1/2">
                    {isSearching ? (
                      <Loader2 className="w-4 h-4 text-primary-400 animate-spin" />
                    ) : (
                      <button
                        onClick={clearSearch}
                        className="text-text-muted hover:text-text-primary transition-colors"
                        title={t("clearSearch")}
                      >
                        <X className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                )}
              </div>

              {showingResults ? (
                /* ---- Search results ---- */
                <div className="space-y-2">
                  {searchError ? (
                    <div className="text-center py-8 text-red-400 text-sm">{searchError}</div>
                  ) : searchResults && searchResults.length === 0 ? (
                    <div className="text-center py-8 text-text-muted text-sm">
                      {t("searchNoResults")}
                    </div>
                  ) : (
                    <>
                      <div className="text-xs font-medium text-text-muted uppercase tracking-wider">
                        {t("searchResults")}
                      </div>
                      {searchResults?.map((hit) => (
                        <button
                          key={hit.page_id}
                          onClick={() => openHit(hit)}
                          className="w-full flex items-center gap-3 px-4 py-3 rounded-lg bg-white/[0.03] border border-white/10 hover:border-primary-500/30 hover:bg-white/[0.05] transition-all text-left"
                        >
                          <FileText className="w-5 h-5 text-text-secondary flex-shrink-0" />
                          <div className="flex-1 min-w-0">
                            <div className="truncate text-sm font-medium text-text-primary">
                              {hit.document_name}
                            </div>
                            <div className="text-xs text-text-muted mt-0.5">
                              {t("page")} {hit.page_number}
                              {hit.total_pages ? ` ${t("of")} ${hit.total_pages}` : ""}
                            </div>
                          </div>
                          <span className="flex-shrink-0 px-2 py-0.5 rounded text-[10px] font-mono tabular-nums bg-primary-500/15 text-primary-400 border border-primary-500/20">
                            {Math.round(hit.similarity_score * 100)}%
                          </span>
                        </button>
                      ))}
                    </>
                  )}
                </div>
              ) : (
                /* ---- Library ---- */
                <>
                  <DocumentUpload />
                  <DocumentLibrary />
                </>
              )}
            </div>
          </div>
        </div>

        {/* PDF Viewer Panel */}
        {isDesktop ? (
          <PDFViewerPanel />
        ) : (
          <AnimatePresence>
            {isPDFPanelOpen && (
              <>
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: reducedMotion ? 0 : 0.2 }}
                  className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm"
                  onClick={closePDFViewer}
                />
                <motion.div
                  initial={{ x: "100%" }}
                  animate={{ x: 0 }}
                  exit={{ x: "100%" }}
                  transition={{
                    type: reducedMotion ? "tween" : "spring",
                    stiffness: 300,
                    damping: 30,
                    duration: reducedMotion ? 0 : undefined,
                  }}
                  className="fixed inset-y-0 right-0 z-50 w-full sm:w-[400px]"
                >
                  <PDFViewerPanel />
                </motion.div>
              </>
            )}
          </AnimatePresence>
        )}
      </div>

      {/* PDF panel toggle trigger */}
      {isDesktop && hasDocuments && (
        <button
          onClick={handleTogglePDFPanel}
          className={clsx(
            "absolute top-1/2 -translate-y-1/2 z-50 bg-bg-secondary px-1 py-6 rounded-l-lg border-y border-l border-white/5 hover:bg-white/5",
            "transition-all duration-300 ease-out",
            isPDFPanelOpen ? "right-[500px]" : "right-0",
            showNavigation && !isPDFPanelOpen && "opacity-0 pointer-events-none"
          )}
          title={isPDFPanelOpen ? t("hidePreview") : t("showPreview")}
        >
          <div className="flex flex-col items-center gap-1">
            <FileText className="w-4 h-4 text-text-secondary" />
            <ChevronLeft
              className={clsx(
                "w-4 h-4 text-text-secondary transition-transform duration-200",
                isPDFPanelOpen && "rotate-180"
              )}
            />
          </div>
        </button>
      )}

      {/* Find sources modal */}
      <DiscoverSourcesModal isOpen={isDiscoverOpen} onClose={() => setIsDiscoverOpen(false)} />

      {/* Connect to Claude modal */}
      <AnimatePresence>
        {isConnectOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
            onClick={() => setIsConnectOpen(false)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="w-full max-w-lg max-h-[85vh] overflow-y-auto bg-bg-secondary rounded-xl border border-white/10 shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 sticky top-0 bg-bg-secondary">
                <div className="flex items-center gap-2">
                  <Plug className="w-5 h-5 text-primary-400" />
                  <h2 className="text-lg font-semibold text-text-primary">{t("connectToClaude")}</h2>
                </div>
                <button
                  onClick={() => setIsConnectOpen(false)}
                  className="p-1.5 rounded-lg hover:bg-white/10 text-text-secondary hover:text-text-primary transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
              <div className="p-5">
                <p className="text-sm text-text-secondary mb-5">{t("connectDescription")}</p>
                <ConnectClaudePanel />
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
