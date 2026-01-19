"use client";

/**
 * ChatPageContent Component
 *
 * Full-screen chat interface with responsive design:
 * - Mobile (<640px): Thread sidebar as overlay, PDF panel as modal
 * - Tablet (640-1024px): Collapsible sidebars with narrower panels
 * - Desktop (>1024px): Full layout with inline panels
 */

import { useState, useEffect, useCallback } from "react";
import { useTranslations } from "next-intl";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronRight, ChevronLeft, PanelLeftClose, PanelLeft, X, FileText } from "lucide-react";
import { useChatStore } from "@/stores/chatStore";
import { AppSidebar } from "@/components/layout";
import { ThreadSidebar } from "./ThreadSidebar";
import { ChatArea } from "./ChatArea";
import { IndexingProgress } from "./IndexingProgress";
import { PDFViewerPanel } from "./PDFViewerPanel";
import { clsx } from "clsx";

// Breakpoints for responsive design
const MOBILE_BREAKPOINT = 640;
const TABLET_BREAKPOINT = 1024;

// Custom hook for media queries with debounced resize
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

// Custom hook for reduced motion preference
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

export function ChatPageContent() {
  const t = useTranslations("chat");
  const {
    isThreadSidebarOpen,
    toggleThreadSidebar,
    indexingStatus,
    isPDFPanelOpen,
    closePDFViewer,
    documents,
    activePDFDocumentId,
    openPDFViewer,
  } = useChatStore();

  // Responsive state
  const isTablet = useMediaQuery(MOBILE_BREAKPOINT);
  const isDesktop = useMediaQuery(TABLET_BREAKPOINT);
  const reducedMotion = useReducedMotion();

  // Navigation sidebar state (collapsible like editor)
  const [showNavigation, setShowNavigation] = useState(false);
  // Mobile thread sidebar overlay state
  const [mobileThreadOpen, setMobileThreadOpen] = useState(false);

  const isIndexing =
    indexingStatus &&
    (indexingStatus.documents_processing > 0 ||
      indexingStatus.documents_pending > 0);

  // Get completed documents for the preview panel trigger
  const completedDocuments = documents.filter((d) => d.status === "completed");
  const hasDocuments = completedDocuments.length > 0;

  // Toggle PDF panel - open with first document if none selected
  const handleTogglePDFPanel = useCallback(() => {
    if (isPDFPanelOpen) {
      closePDFViewer();
    } else if (completedDocuments.length > 0) {
      // Open with most recent document
      openPDFViewer(completedDocuments[0].id, 1);
    }
  }, [isPDFPanelOpen, closePDFViewer, openPDFViewer, completedDocuments]);

  // Close mobile sidebar when switching to desktop
  useEffect(() => {
    if (isDesktop) {
      setMobileThreadOpen(false);
    }
  }, [isDesktop]);

  const handleToggleThreadSidebar = useCallback(() => {
    if (!isDesktop) {
      setMobileThreadOpen((prev) => !prev);
    } else {
      toggleThreadSidebar();
    }
  }, [isDesktop, toggleThreadSidebar]);

  // Animation variants
  const sidebarVariants = {
    hidden: { x: -320, opacity: 0 },
    visible: { x: 0, opacity: 1 },
  };

  const overlayVariants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1 },
  };

  return (
    <div className="h-screen bg-bg-primary flex overflow-hidden">
      {/* Navigation sidebar toggle trigger - moves with sidebar */}
      <button
        onClick={() => setShowNavigation(!showNavigation)}
        className={clsx(
          "absolute top-1/2 -translate-y-1/2 z-50 bg-bg-secondary px-1 py-6 rounded-r-lg border-y border-r border-white/5 hover:bg-white/5",
          "transition-all duration-300 ease-out",
          showNavigation ? "left-64" : "left-0",
          // Hide on mobile when thread sidebar overlay is open
          !isDesktop && mobileThreadOpen && "opacity-0 pointer-events-none"
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
            activePath="/chat"
          />
        )}
      </AnimatePresence>

      {/* Mobile/Tablet Thread Sidebar Overlay */}
      <AnimatePresence>
        {!isDesktop && mobileThreadOpen && (
          <>
            {/* Backdrop */}
            <motion.div
              initial="hidden"
              animate="visible"
              exit="hidden"
              variants={overlayVariants}
              transition={{ duration: reducedMotion ? 0 : 0.2 }}
              className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
              onClick={() => setMobileThreadOpen(false)}
            />
            {/* Sidebar */}
            <motion.div
              initial="hidden"
              animate="visible"
              exit="hidden"
              variants={sidebarVariants}
              transition={{
                type: reducedMotion ? "tween" : "spring",
                stiffness: 300,
                damping: 30,
                duration: reducedMotion ? 0 : undefined,
              }}
              className={clsx(
                "fixed inset-y-0 left-0 z-50 bg-bg-secondary border-r border-white/5",
                "w-full sm:w-80"
              )}
            >
              {/* Close button for mobile */}
              <button
                onClick={() => setMobileThreadOpen(false)}
                className="absolute top-3 right-3 p-2 rounded-lg hover:bg-white/5 text-text-secondary hover:text-text-primary transition-colors z-10"
              >
                <X className="w-5 h-5" />
              </button>
              <ThreadSidebar />
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {/* Main content area - shifts based on navigation sidebar */}
      <div
        className={clsx(
          "flex-1 flex transition-all duration-300 ease-out",
          showNavigation ? "ml-64" : "ml-0"
        )}
      >
        {/* Thread Sidebar - Desktop only inline */}
        {isDesktop && (
          <div
            className={clsx(
              "flex-shrink-0 border-r border-white/5 bg-bg-secondary",
              "transition-all duration-300 ease-out will-change-[width]",
              isThreadSidebarOpen ? "w-72" : "w-0 overflow-hidden"
            )}
          >
            <ThreadSidebar />
          </div>
        )}

        {/* Main Chat Area */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Header with toggle */}
          <div className="flex items-center gap-3 px-4 py-3 border-b border-white/5 bg-bg-secondary/50">
            <button
              onClick={handleToggleThreadSidebar}
              className="p-2 rounded-lg hover:bg-white/5 text-text-secondary hover:text-text-primary transition-colors"
              title={isThreadSidebarOpen ? t("hideSidebar") : t("showSidebar")}
            >
              {isThreadSidebarOpen && isDesktop ? (
                <PanelLeftClose className="w-5 h-5" />
              ) : (
                <PanelLeft className="w-5 h-5" />
              )}
            </button>
            <h1 className="text-lg font-semibold text-text-primary">
              {t("title")}
            </h1>

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

        {/* PDF Viewer Panel - responsive widths */}
        {isDesktop ? (
          <PDFViewerPanel />
        ) : (
          // Mobile/Tablet: PDF panel as modal overlay
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
                  className={clsx(
                    "fixed inset-y-0 right-0 z-50",
                    "w-full sm:w-[400px]"
                  )}
                >
                  <PDFViewerPanel />
                </motion.div>
              </>
            )}
          </AnimatePresence>
        )}
      </div>

      {/* PDF Preview Panel toggle trigger - right side */}
      {isDesktop && hasDocuments && (
        <button
          onClick={handleTogglePDFPanel}
          className={clsx(
            "absolute top-1/2 -translate-y-1/2 z-50 bg-bg-secondary px-1 py-6 rounded-l-lg border-y border-l border-white/5 hover:bg-white/5",
            "transition-all duration-300 ease-out",
            isPDFPanelOpen ? "right-[500px]" : "right-0",
            // Hide when navigation is open and would overlap
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

      {/* Subtle gradient overlay at edges for depth */}
      <div className="pointer-events-none fixed inset-y-0 left-0 w-8 bg-gradient-to-r from-black/10 to-transparent" />
      <div className={clsx(
        "pointer-events-none fixed inset-y-0 right-0 w-8 bg-gradient-to-l from-black/10 to-transparent",
        "transition-all duration-300",
        isPDFPanelOpen && isDesktop && "right-[500px]"
      )} />
    </div>
  );
}
