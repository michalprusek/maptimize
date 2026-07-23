"use client";

import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { useTranslations } from "next-intl";
import { useDocumentStore } from "@/stores/documentStore";
import {
  X,
  ZoomIn,
  ZoomOut,
  Download,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronDown,
  Maximize2,
  Search,
} from "lucide-react";
import { clsx } from "clsx";
import { api } from "@/lib/api";

interface SearchMatch {
  page_number: number;
  match_count: number;
  snippet: string;
}

interface SearchResult {
  query: string;
  total_matches: number;
  pages_with_matches: number;
  matches: SearchMatch[];
}

export function PDFViewerPanel() {
  const t = useTranslations("documents");
  const {
    isPDFPanelOpen,
    activePDFDocumentId,
    activePDFPage,
    closePDFViewer,
    setActivePDFPage,
    documents,
  } = useDocumentStore();

  const [zoom, setZoom] = useState(1);
  const [isHoveringClose, setIsHoveringClose] = useState(false);
  const [loadedPages, setLoadedPages] = useState<Set<number>>(new Set());
  const [failedPages, setFailedPages] = useState<Set<number>>(new Set());
  const [errorDetails, setErrorDetails] = useState<Map<number, string>>(new Map());

  // Search state
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Use a ref to track mounted state
  const isMountedRef = useRef(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const pageElementsRef = useRef<Map<number, HTMLDivElement>>(new Map());
  const isScrollingToPageRef = useRef(false);
  const observerRef = useRef<IntersectionObserver | null>(null);
  // Stable ref callback per page number, so React doesn't detach/re-attach (and
  // the observer doesn't unobserve/re-observe) on every render.
  const pageRefCallbacks = useRef<Map<number, (el: HTMLDivElement | null) => void>>(new Map());

  const activeDocument = documents.find((d) => d.id === activePDFDocumentId);
  const totalPages = activeDocument?.page_count || 1;

  // Generate array of page numbers
  const pages = useMemo(() => {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }, [totalPages]);

  // Real page aspect ratio (width / height), measured once from the first loaded
  // image. Every page reserves a box of this ratio BEFORE its image loads, so the
  // column height is stable and native lazy-loading can size pages correctly — no
  // custom virtualization, no scroll feedback loop, no layout shift.
  const [naturalAspect, setNaturalAspect] = useState<number | null>(null);

  // Resizable panel width (desktop only). Persisted so the user's chosen width
  // survives reloads. Mobile renders full-width inside an overlay, so the inline
  // width and the drag handle only apply at lg+.
  const PANEL_MIN_WIDTH = 360;
  const PANEL_MAX_WIDTH = 1000;
  const [isDesktop, setIsDesktop] = useState(false);
  const [panelWidth, setPanelWidth] = useState(500);
  const panelWidthRef = useRef(panelWidth);
  // Keep the ref in sync in an effect, not during render (render purity /
  // StrictMode) — startResize reads it at drag-start time, and by then the
  // effect from the previous commit has already run.
  useEffect(() => {
    panelWidthRef.current = panelWidth;
  }, [panelWidth]);

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const update = () => setIsDesktop(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  // Load persisted width once on mount.
  useEffect(() => {
    const saved = parseInt(localStorage.getItem("pdfPanelWidth") || "", 10);
    if (Number.isFinite(saved)) {
      setPanelWidth(Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, saved)));
    }
  }, []);

  // Persistence happens once the drag ends (see startResize's teardown below),
  // not on every width change — otherwise every mousemove frame would hit
  // localStorage.

  // Teardown for an in-flight resize drag, kept in a ref so it can be invoked
  // from onUp, from a component-unmount cleanup, AND from a window "blur"
  // safety net (mouse released outside the window; Escape closes the panel
  // mid-drag, which unmounts this component before onUp ever fires). Without
  // this, the mousemove/mouseup listeners leak and document.body is left
  // stuck with cursor: col-resize + unselectable text app-wide.
  const resizeTeardownRef = useRef<(() => void) | null>(null);

  // Drag the panel's left edge to resize. The panel sits on the right of the
  // layout, so dragging left widens it.
  const startResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    // Tear down any previous drag first (shouldn't normally overlap, but keeps
    // startResize itself idempotent/safe to call repeatedly).
    resizeTeardownRef.current?.();

    const startX = e.clientX;
    const startW = panelWidthRef.current;
    const onMove = (ev: MouseEvent) => {
      const delta = startX - ev.clientX;
      setPanelWidth(Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, startW + delta)));
    };
    const teardown = () => {
      // Idempotent: bail if this drag's teardown already ran (e.g. onUp AND
      // unmount cleanup both fire).
      if (resizeTeardownRef.current !== teardown) return;
      resizeTeardownRef.current = null;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      // Persist only now that the drag has actually ended.
      localStorage.setItem("pdfPanelWidth", String(panelWidthRef.current));
    };
    const onUp = () => teardown();
    resizeTeardownRef.current = teardown;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    // Safety net: mouse released outside the window never fires "mouseup".
    window.addEventListener("blur", onUp);
  }, []);

  // Ensure a drag in progress is always torn down when this panel unmounts
  // (e.g. Escape mid-drag calls closePDFViewer(), which returns null below
  // before onUp ever gets a chance to fire).
  useEffect(() => {
    return () => resizeTeardownRef.current?.();
  }, []);

  // Pages with search matches (for highlighting)
  const pagesWithMatches = useMemo(() => {
    if (!searchResult?.matches) return new Set<number>();
    return new Set(searchResult.matches.map((m) => m.page_number));
  }, [searchResult]);

  // Track mounted state
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Debounced search
  const performSearch = useCallback(async (query: string) => {
    if (!activePDFDocumentId || query.length < 2) {
      setSearchResult(null);
      setSearchError(null);
      return;
    }

    setIsSearching(true);
    setSearchError(null);
    try {
      const result = await api.searchWithinDocument(activePDFDocumentId, query);
      if (isMountedRef.current) {
        setSearchResult(result);
        setCurrentMatchIndex(0);
        // Auto-scroll to first match
        if (result.matches.length > 0) {
          setActivePDFPage(result.matches[0].page_number);
        }
      }
    } catch (error) {
      console.error("[PDFViewer] Search failed:", error);
      if (isMountedRef.current) {
        setSearchResult(null);
        setSearchError(error instanceof Error ? error.message : "Search failed");
      }
    } finally {
      if (isMountedRef.current) {
        setIsSearching(false);
      }
    }
  }, [activePDFDocumentId, setActivePDFPage]);

  // Handle search input change with debounce
  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value);

    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    if (value.length < 2) {
      setSearchResult(null);
      return;
    }

    searchTimeoutRef.current = setTimeout(() => {
      performSearch(value);
    }, 300);
  }, [performSearch]);

  // Navigate to previous match
  const handlePrevMatch = useCallback(() => {
    if (!searchResult?.matches.length) return;
    const newIndex = currentMatchIndex > 0 ? currentMatchIndex - 1 : searchResult.matches.length - 1;
    setCurrentMatchIndex(newIndex);
    setActivePDFPage(searchResult.matches[newIndex].page_number);
  }, [searchResult, currentMatchIndex, setActivePDFPage]);

  // Navigate to next match
  const handleNextMatch = useCallback(() => {
    if (!searchResult?.matches.length) return;
    const newIndex = currentMatchIndex < searchResult.matches.length - 1 ? currentMatchIndex + 1 : 0;
    setCurrentMatchIndex(newIndex);
    setActivePDFPage(searchResult.matches[newIndex].page_number);
  }, [searchResult, currentMatchIndex, setActivePDFPage]);

  // Close search
  const closeSearch = useCallback(() => {
    setIsSearchOpen(false);
    setSearchQuery("");
    setSearchResult(null);
    setSearchError(null);
    setCurrentMatchIndex(0);
  }, []);

  // Set up IntersectionObserver with defensive checks
  useEffect(() => {
    if (!isPDFPanelOpen || !activePDFDocumentId || !containerRef.current) return;

    // Clean up previous observer
    if (observerRef.current) {
      observerRef.current.disconnect();
      observerRef.current = null;
    }

    const options: IntersectionObserverInit = {
      root: containerRef.current,
      rootMargin: "-20% 0px -60% 0px",
      threshold: 0,
    };

    observerRef.current = new IntersectionObserver((entries) => {
      // Don't update during programmatic scroll or if unmounted
      if (isScrollingToPageRef.current || !isMountedRef.current) return;

      for (const entry of entries) {
        if (entry.isIntersecting && entry.target.isConnected) {
          const pageNum = parseInt(entry.target.getAttribute("data-page") || "1", 10);
          if (!isNaN(pageNum)) {
            setActivePDFPage(pageNum);
          }
          break;
        }
      }
    }, options);

    // Observe existing page elements with safety checks
    pageElementsRef.current.forEach((element, pageNum) => {
      if (element && element.isConnected && observerRef.current) {
        try {
          observerRef.current.observe(element);
        } catch (e) {
          console.warn(`Failed to observe page ${pageNum}:`, e);
        }
      }
    });

    return () => {
      if (observerRef.current) {
        observerRef.current.disconnect();
        observerRef.current = null;
      }
    };
  }, [isPDFPanelOpen, activePDFDocumentId, totalPages, setActivePDFPage]);

  // Scroll to page when activePDFPage changes
  useEffect(() => {
    if (!isPDFPanelOpen || !activePDFDocumentId || !containerRef.current) return;

    const scrollToPage = () => {
      const pageElement = pageElementsRef.current.get(activePDFPage);
      if (!pageElement || !pageElement.isConnected) return false;

      // Check if element is already mostly visible
      const containerRect = containerRef.current!.getBoundingClientRect();
      const pageRect = pageElement.getBoundingClientRect();
      const isVisible =
        pageRect.top >= containerRect.top + 50 &&
        pageRect.top < containerRect.bottom - 150;

      if (!isVisible) {
        isScrollingToPageRef.current = true;

        // Use instant scroll for better reliability, especially when navigating from citations
        // Smooth scroll can get interrupted or not complete properly
        pageElement.scrollIntoView({ behavior: "instant", block: "start" });

        // Add small offset from top for better visibility
        if (containerRef.current) {
          containerRef.current.scrollTop -= 20;
        }

        // Reset flag after a short delay
        setTimeout(() => {
          isScrollingToPageRef.current = false;
        }, 100);
      }
      return true;
    };

    // Try immediately
    if (scrollToPage()) return;

    // If page element not ready, retry with increasing intervals
    let attempts = 0;
    const maxAttempts = 20;
    const retryInterval = setInterval(() => {
      attempts++;
      if (scrollToPage() || attempts >= maxAttempts) {
        clearInterval(retryInterval);
      }
    }, 50);

    return () => clearInterval(retryInterval);
    // naturalAspect is included because every page box is reserved at the
    // 8.5/11 fallback ratio until it's measured; when it updates, all boxes
    // resize and an already-scrolled-to target can end up off-screen. The
    // effect itself no-ops once the target page is visible, so re-running it
    // here is safe/idempotent.
  }, [activePDFPage, isPDFPanelOpen, activePDFDocumentId, naturalAspect]);

  // Reset state when document changes
  useEffect(() => {
    setLoadedPages(new Set());
    setFailedPages(new Set());
    setErrorDetails(new Map());
    setNaturalAspect(null);
    // Don't clear pageElementsRef here: the observer effect above already
    // observed the newly mounted page elements by the time this runs, and
    // each page's own ref callback (below) deletes its entry when its div
    // unmounts — which always happens on document change since `key`
    // includes the document id. Clearing here only "worked" by accident.
    pageRefCallbacks.current.clear();
    // Reset search when document changes
    closeSearch();
  }, [activePDFDocumentId, closeSearch]);

  const handlePrevPage = useCallback(() => {
    if (activePDFPage > 1) {
      setActivePDFPage(activePDFPage - 1);
    }
  }, [activePDFPage, setActivePDFPage]);

  const handleNextPage = useCallback(() => {
    if (activePDFPage < totalPages) {
      setActivePDFPage(activePDFPage + 1);
    }
  }, [activePDFPage, totalPages, setActivePDFPage]);

  const handleZoomIn = useCallback(() => {
    setZoom((z) => Math.min(z + 0.25, 3));
  }, []);

  const handleZoomOut = useCallback(() => {
    setZoom((z) => Math.max(z - 0.25, 0.5));
  }, []);

  const handleFitWidth = useCallback(() => {
    setZoom(1);
  }, []);

  const handleDownload = useCallback(() => {
    if (!activePDFDocumentId) return;
    const url = api.getRAGDocumentPdfUrl(activePDFDocumentId);
    window.open(url, "_blank");
  }, [activePDFDocumentId]);

  const handlePageLoad = useCallback(
    (pageNum: number, event: React.SyntheticEvent<HTMLImageElement>) => {
      if (!isMountedRef.current) return;
      setLoadedPages((prev) => new Set(prev).add(pageNum));
      setFailedPages((prev) => {
        const next = new Set(prev);
        next.delete(pageNum);
        return next;
      });
      // Measure the page aspect ratio once; all pages in a document share it, so
      // this pins every reserved box to the true ratio (no letterbox, no shift).
      const img = event.currentTarget;
      if (img.naturalWidth > 0 && img.naturalHeight > 0) {
        setNaturalAspect((prev) => prev ?? img.naturalWidth / img.naturalHeight);
      }
    },
    []
  );

  const handlePageError = useCallback((pageNum: number, event: React.SyntheticEvent<HTMLImageElement>) => {
    if (!isMountedRef.current) return;

    const img = event.currentTarget;
    const src = img.src;
    console.error(`Failed to load page ${pageNum}. URL: ${src}`);

    setFailedPages((prev) => new Set(prev).add(pageNum));
    setErrorDetails((prev) => {
      const next = new Map(prev);
      next.set(pageNum, `Failed to load: ${src.substring(0, 100)}...`);
      return next;
    });
  }, []);

  // Return a STABLE ref callback for a given page number. Memoizing per page
  // (instead of returning a fresh closure each render) stops React from
  // detaching/re-attaching the ref — and the observer from unobserve/observe
  // churn — on every re-render.
  const getPageRef = useCallback((pageNum: number) => {
    let cb = pageRefCallbacks.current.get(pageNum);
    if (!cb) {
      cb = (element: HTMLDivElement | null) => {
        if (element) {
          pageElementsRef.current.set(pageNum, element);
          if (observerRef.current && element.isConnected) {
            try {
              observerRef.current.observe(element);
            } catch (e) {
              // Observer may be disconnected during Fast Refresh; the observer
              // effect re-observes all registered elements when it re-runs.
              console.debug(`Failed to observe page ${pageNum}:`, e);
            }
          }
        } else {
          const existing = pageElementsRef.current.get(pageNum);
          if (existing && observerRef.current) {
            try {
              observerRef.current.unobserve(existing);
            } catch (e) {
              // Element already disconnected during cleanup — safe to ignore.
              console.debug(`Failed to unobserve page ${pageNum}:`, e);
            }
          }
          pageElementsRef.current.delete(pageNum);
        }
      };
      pageRefCallbacks.current.set(pageNum, cb);
    }
    return cb;
  }, []);

  // Keyboard navigation including Ctrl+F
  useEffect(() => {
    if (!isPDFPanelOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl+F or Cmd+F to open search
      if ((e.ctrlKey || e.metaKey) && e.key === "f") {
        e.preventDefault();
        setIsSearchOpen(true);
        setTimeout(() => searchInputRef.current?.focus(), 50);
        return;
      }

      // Escape to close search or panel
      if (e.key === "Escape") {
        if (isSearchOpen) {
          closeSearch();
        } else {
          closePDFViewer();
        }
        return;
      }

      // Enter to go to next match when search is open
      if (e.key === "Enter" && isSearchOpen && searchResult?.matches.length) {
        if (e.shiftKey) {
          handlePrevMatch();
        } else {
          handleNextMatch();
        }
        return;
      }

      // Don't handle arrow keys if search input is focused
      if (document.activeElement === searchInputRef.current) return;

      if (e.key === "ArrowLeft") {
        handlePrevPage();
      } else if (e.key === "ArrowRight") {
        handleNextPage();
      } else if (e.key === "+" || e.key === "=") {
        handleZoomIn();
      } else if (e.key === "-") {
        handleZoomOut();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [
    isPDFPanelOpen,
    isSearchOpen,
    searchResult,
    handlePrevPage,
    handleNextPage,
    closePDFViewer,
    closeSearch,
    handleZoomIn,
    handleZoomOut,
    handlePrevMatch,
    handleNextMatch,
  ]);

  if (!isPDFPanelOpen || !activePDFDocumentId) {
    return null;
  }

  return (
    <div
      className={clsx(
        "relative flex-shrink-0 border-l border-white/5 bg-bg-secondary flex flex-col h-full",
        "animate-slide-in-right",
        isDesktop ? "" : "w-full"
      )}
      style={isDesktop ? { width: panelWidth } : undefined}
    >
      {/* Resize handle (desktop): drag the left edge to widen / narrow */}
      {isDesktop && (
        <div
          onMouseDown={startResize}
          className="group/resize absolute left-0 top-0 h-full w-2 -translate-x-1/2 z-20 cursor-col-resize"
          title={t("resizePanel") || "Drag to resize"}
        >
          <div className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-transparent group-hover/resize:bg-primary-500/60 transition-colors" />
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/5 bg-white/[0.02] backdrop-blur-sm">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="text-sm font-medium text-text-primary truncate">
            {activeDocument?.name || t("pdfViewer")}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => {
              setIsSearchOpen(!isSearchOpen);
              if (!isSearchOpen) {
                setTimeout(() => searchInputRef.current?.focus(), 50);
              }
            }}
            className={clsx(
              "p-1.5 rounded-lg transition-all duration-200",
              isSearchOpen
                ? "bg-primary-500/20 text-primary-400"
                : "text-text-secondary hover:text-text-primary hover:bg-white/10"
            )}
            title="Search (Ctrl+F)"
          >
            <Search className="w-4 h-4" />
          </button>
          <button
            onClick={closePDFViewer}
            onMouseEnter={() => setIsHoveringClose(true)}
            onMouseLeave={() => setIsHoveringClose(false)}
            className={clsx(
              "p-1.5 rounded-lg transition-all duration-200",
              "text-text-secondary hover:text-text-primary hover:bg-white/10",
              isHoveringClose && "rotate-90"
            )}
            title={t("close") || "Close"}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Search bar */}
      {isSearchOpen && (
        <div className="flex items-center gap-2 px-3 py-2 border-b border-white/10 bg-white/[0.02]">
          <div className="flex-1 relative">
            <input
              ref={searchInputRef}
              type="text"
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              placeholder={t("searchInDocument") || "Search in document..."}
              className="w-full px-3 py-1.5 text-sm bg-white/5 border border-white/10 rounded-lg text-text-primary placeholder:text-text-muted focus:outline-none focus:border-primary-500/50 focus:ring-1 focus:ring-primary-500/25"
            />
            {isSearching && (
              <div className="absolute right-3 top-1/2 -translate-y-1/2">
                <div className="w-4 h-4 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
              </div>
            )}
          </div>

          {/* Match count and navigation */}
          {searchError ? (
            <span className="text-xs text-red-400 min-w-[60px] text-center">
              {t("searchError") || "Search failed"}
            </span>
          ) : searchResult ? (
            <div className="flex items-center gap-1">
              <span className="text-xs text-text-secondary tabular-nums min-w-[60px] text-center">
                {searchResult.matches.length > 0
                  ? `${currentMatchIndex + 1}/${searchResult.matches.length}`
                  : t("noMatches") || "No matches"}
              </span>
              <button
                onClick={handlePrevMatch}
                disabled={!searchResult.matches.length}
                className={clsx(
                  "p-1 rounded transition-colors",
                  searchResult.matches.length
                    ? "hover:bg-white/10 text-text-secondary hover:text-text-primary"
                    : "text-text-muted cursor-not-allowed"
                )}
                title={t("previousMatch") || "Previous match"}
              >
                <ChevronUp className="w-4 h-4" />
              </button>
              <button
                onClick={handleNextMatch}
                disabled={!searchResult.matches.length}
                className={clsx(
                  "p-1 rounded transition-colors",
                  searchResult.matches.length
                    ? "hover:bg-white/10 text-text-secondary hover:text-text-primary"
                    : "text-text-muted cursor-not-allowed"
                )}
                title={t("nextMatch") || "Next match"}
              >
                <ChevronDown className="w-4 h-4" />
              </button>
            </div>
          ) : null}

          <button
            onClick={closeSearch}
            className="p-1 rounded hover:bg-white/10 text-text-secondary hover:text-text-primary transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-white/10 bg-white/[0.02]">
        {/* Page navigation */}
        <div className="flex items-center gap-1">
          <button
            onClick={handlePrevPage}
            disabled={activePDFPage <= 1}
            className={clsx(
              "p-1.5 rounded-lg transition-all duration-200",
              activePDFPage > 1
                ? "hover:bg-white/10 text-text-secondary hover:text-text-primary hover:scale-110"
                : "text-text-muted cursor-not-allowed"
            )}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-sm text-text-secondary min-w-[80px] text-center tabular-nums">
            {t("page")} {activePDFPage} {t("of")} {totalPages}
          </span>
          <button
            onClick={handleNextPage}
            disabled={activePDFPage >= totalPages}
            className={clsx(
              "p-1.5 rounded-lg transition-all duration-200",
              activePDFPage < totalPages
                ? "hover:bg-white/10 text-text-secondary hover:text-text-primary hover:scale-110"
                : "text-text-muted cursor-not-allowed"
            )}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>

        {/* Zoom controls */}
        <div className="flex items-center gap-1">
          <button
            onClick={handleZoomOut}
            disabled={zoom <= 0.5}
            className={clsx(
              "p-1.5 rounded-lg transition-all duration-200",
              zoom > 0.5
                ? "hover:bg-white/10 text-text-secondary hover:text-text-primary hover:scale-110"
                : "text-text-muted cursor-not-allowed"
            )}
            title={t("zoomOut")}
          >
            <ZoomOut className="w-4 h-4" />
          </button>
          <span className="text-xs text-text-secondary min-w-[45px] text-center tabular-nums">
            {Math.round(zoom * 100)}%
          </span>
          <button
            onClick={handleZoomIn}
            disabled={zoom >= 3}
            className={clsx(
              "p-1.5 rounded-lg transition-all duration-200",
              zoom < 3
                ? "hover:bg-white/10 text-text-secondary hover:text-text-primary hover:scale-110"
                : "text-text-muted cursor-not-allowed"
            )}
            title={t("zoomIn")}
          >
            <ZoomIn className="w-4 h-4" />
          </button>
          <button
            onClick={handleFitWidth}
            className="p-1.5 rounded-lg hover:bg-white/10 transition-all duration-200 text-text-secondary hover:text-text-primary hover:scale-110"
            title={t("fitWidth")}
          >
            <Maximize2 className="w-4 h-4" />
          </button>
        </div>

        {/* Download */}
        <button
          onClick={handleDownload}
          className="p-1.5 rounded-lg hover:bg-white/10 transition-all duration-200 text-text-secondary hover:text-text-primary hover:scale-110"
          title={t("download")}
        >
          <Download className="w-4 h-4" />
        </button>
      </div>

      {/* PDF Content - Seamless scroll container */}
      <div
        ref={containerRef}
        className="flex-1 overflow-auto p-4 bg-bg-primary/50"
      >
        <div
          className="flex flex-col items-center gap-4 mx-auto"
          // Width-based zoom: >100% overflows and the container scrolls; <100%
          // stays centered via mx-auto. (The old transform+counter-width canceled
          // out, so zoom did nothing and overflowed at <1.)
          style={{ width: `${zoom * 100}%` }}
        >
          {pages.map((pageNum) => {
            const hasMatch = pagesWithMatches.has(pageNum);
            const isCurrentMatch = searchResult?.matches[currentMatchIndex]?.page_number === pageNum;
            const isLoaded = loadedPages.has(pageNum);
            const isFailed = failedPages.has(pageNum);

            return (
              <div
                key={`doc-${activePDFDocumentId}-page-${pageNum}`}
                ref={getPageRef(pageNum)}
                data-page={pageNum}
                // Reserve each page's space up front via a fixed aspect ratio, so the
                // column height is stable before any image loads. Every page renders a
                // native lazy <img> that the browser fetches only as it nears the
                // viewport — no custom virtualization, no scroll feedback loop, no
                // layout shift.
                style={{ aspectRatio: String(naturalAspect ?? 8.5 / 11) }}
                className={clsx(
                  "relative w-full",
                  hasMatch && "ring-2 ring-primary-500/50 ring-offset-2 ring-offset-bg-primary rounded-sm",
                  isCurrentMatch && "ring-primary-500"
                )}
              >
                {/* Page number badge */}
                <div className={clsx(
                  "absolute top-2 left-2 z-10 px-2 py-0.5 rounded text-xs backdrop-blur-sm",
                  hasMatch
                    ? "bg-primary-500/80 text-white"
                    : "bg-black/50 text-white/70"
                )}>
                  {pageNum}
                  {hasMatch && (
                    <span className="ml-1 opacity-75">
                      ({searchResult?.matches.find(m => m.page_number === pageNum)?.match_count})
                    </span>
                  )}
                </div>

                {isFailed ? (
                  /* Error state with retry button (fills the reserved box) */
                  <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-white/5 rounded-sm border border-white/5 text-text-muted p-4">
                    <span>{t("failedToLoadPage") || "Failed to load page"}</span>
                    <button
                      onClick={() => {
                        setFailedPages((prev) => {
                          const next = new Set(prev);
                          next.delete(pageNum);
                          return next;
                        });
                      }}
                      className="px-3 py-1.5 text-xs bg-primary-500/20 hover:bg-primary-500/30 text-primary-400 rounded transition-colors"
                    >
                      {t("retry") || "Retry"}
                    </button>
                    {errorDetails.get(pageNum) && (
                      <span className="text-xs text-text-muted/50 max-w-full truncate">
                        {errorDetails.get(pageNum)}
                      </span>
                    )}
                  </div>
                ) : (
                  <>
                    {/* Loading skeleton — fills the reserved box, no size of its own */}
                    {!isLoaded && (
                      <div className="absolute inset-0 flex items-center justify-center bg-white/5 rounded-sm animate-pulse">
                        <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
                      </div>
                    )}

                    {/* Page image — fills the reserved box, native lazy-loaded */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={api.getRAGPageImageUrl(activePDFDocumentId, pageNum)}
                      alt={`Page ${pageNum}`}
                      className={clsx(
                        "w-full h-full object-contain shadow-lg rounded-sm border border-white/5 transition-opacity duration-300",
                        isLoaded ? "opacity-100" : "opacity-0"
                      )}
                      loading="lazy"
                      onLoad={(e) => handlePageLoad(pageNum, e)}
                      onError={(e) => handlePageError(pageNum, e)}
                    />
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
