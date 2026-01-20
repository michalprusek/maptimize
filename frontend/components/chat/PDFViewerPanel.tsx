"use client";

import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
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
  const t = useTranslations("chat");
  const {
    isPDFPanelOpen,
    activePDFDocumentId,
    activePDFPage,
    closePDFViewer,
    setActivePDFPage,
    documents,
  } = useChatStore();

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

  // Scroll direction tracking for predictive prefetch
  const scrollDirectionRef = useRef<"up" | "down" | null>(null);
  const lastScrollTopRef = useRef(0);

  const activeDocument = documents.find((d) => d.id === activePDFDocumentId);
  const totalPages = activeDocument?.page_count || 1;

  // Generate array of page numbers
  const pages = useMemo(() => {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }, [totalPages]);

  // Virtualization: only render pages near the current page for better performance
  // Use asymmetric buffer based on scroll direction for predictive prefetch
  const [scrollDirection, setScrollDirection] = useState<"up" | "down" | null>(null);

  const visiblePageRange = useMemo(() => {
    // Prefetch more pages in the direction of scroll
    const forwardBuffer = scrollDirection === "down" ? 5 : 2;
    const backwardBuffer = scrollDirection === "up" ? 5 : 2;

    return {
      start: Math.max(1, activePDFPage - backwardBuffer),
      end: Math.min(totalPages, activePDFPage + forwardBuffer),
    };
  }, [activePDFPage, totalPages, scrollDirection]);

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

  // Track scroll direction for predictive prefetching
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !isPDFPanelOpen) return;

    const handleScroll = () => {
      const currentScrollTop = container.scrollTop;
      const direction = currentScrollTop > lastScrollTopRef.current ? "down" : "up";

      // Only update state if direction changed (avoid unnecessary re-renders)
      if (direction !== scrollDirectionRef.current) {
        scrollDirectionRef.current = direction;
        setScrollDirection(direction);
      }

      lastScrollTopRef.current = currentScrollTop;
    };

    container.addEventListener("scroll", handleScroll, { passive: true });
    return () => container.removeEventListener("scroll", handleScroll);
  }, [isPDFPanelOpen]);

  // Prefetch pages beyond the visible buffer using link rel="prefetch"
  useEffect(() => {
    if (!activePDFDocumentId || !isPDFPanelOpen) return;

    // Determine pages to prefetch (just beyond the visible range)
    const pagesToPrefetch: number[] = [];
    const prefetchDistance = 2; // Prefetch 2 pages beyond visible range

    for (let i = 1; i <= prefetchDistance; i++) {
      const nextPage = visiblePageRange.end + i;
      const prevPage = visiblePageRange.start - i;

      if (nextPage <= totalPages) pagesToPrefetch.push(nextPage);
      if (prevPage >= 1) pagesToPrefetch.push(prevPage);
    }

    // Create prefetch links
    const links: HTMLLinkElement[] = [];
    pagesToPrefetch.forEach((pageNum) => {
      const link = document.createElement("link");
      link.rel = "prefetch";
      link.href = api.getRAGPageImageUrl(activePDFDocumentId, pageNum);
      link.as = "image";
      document.head.appendChild(link);
      links.push(link);
    });

    // Cleanup prefetch links on unmount or when range changes
    return () => {
      links.forEach((link) => {
        if (link.parentNode) {
          link.parentNode.removeChild(link);
        }
      });
    };
  }, [activePDFDocumentId, isPDFPanelOpen, visiblePageRange.start, visiblePageRange.end, totalPages]);

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
  }, [activePDFPage, isPDFPanelOpen, activePDFDocumentId]);

  // Reset state when document changes
  useEffect(() => {
    setLoadedPages(new Set());
    setFailedPages(new Set());
    setErrorDetails(new Map());
    pageElementsRef.current.clear();
    // Reset scroll tracking
    scrollDirectionRef.current = null;
    lastScrollTopRef.current = 0;
    setScrollDirection(null);
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

  const handlePageLoad = useCallback((pageNum: number) => {
    if (!isMountedRef.current) return;
    setLoadedPages((prev) => new Set(prev).add(pageNum));
    setFailedPages((prev) => {
      const next = new Set(prev);
      next.delete(pageNum);
      return next;
    });
  }, []);

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

  // Register page element ref and manage observer subscription
  const setPageRef = useCallback(
    (pageNum: number) => (element: HTMLDivElement | null) => {
      if (element) {
        pageElementsRef.current.set(pageNum, element);
        if (observerRef.current && element.isConnected) {
          try {
            observerRef.current.observe(element);
          } catch (error) {
            // Expected during Fast Refresh when observer is disconnected
            if (process.env.NODE_ENV === "development") {
              console.debug(`[PDFViewer] Observer.observe failed for page ${pageNum}:`, error);
            }
          }
        }
      } else {
        const existing = pageElementsRef.current.get(pageNum);
        if (existing && observerRef.current) {
          try {
            observerRef.current.unobserve(existing);
          } catch (error) {
            // Element may already be disconnected during cleanup
            if (process.env.NODE_ENV === "development") {
              console.debug(`[PDFViewer] Observer.unobserve failed for page ${pageNum}:`, error);
            }
          }
        }
        pageElementsRef.current.delete(pageNum);
      }
    },
    []
  );

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
        "w-full lg:w-[500px] flex-shrink-0 border-l border-white/5 bg-bg-secondary flex flex-col h-full",
        "animate-slide-in-right"
      )}
    >
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
          className="flex flex-col items-center gap-4"
          style={{
            transform: `scale(${zoom})`,
            transformOrigin: "top center",
            width: zoom !== 1 ? `${100 / zoom}%` : "100%",
          }}
        >
          {pages.map((pageNum) => {
            const hasMatch = pagesWithMatches.has(pageNum);
            const isCurrentMatch = searchResult?.matches[currentMatchIndex]?.page_number === pageNum;
            // Virtualization: only render images for pages near the current view
            const isInVirtualRange = pageNum >= visiblePageRange.start && pageNum <= visiblePageRange.end;
            // Always render pages that are already loaded or have search matches
            const shouldRenderImage = isInVirtualRange || loadedPages.has(pageNum) || hasMatch;

            return (
              <div
                key={`doc-${activePDFDocumentId}-page-${pageNum}`}
                ref={setPageRef(pageNum)}
                data-page={pageNum}
                className={clsx(
                  "relative w-full flex justify-center",
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

                {!shouldRenderImage ? (
                  /* Placeholder for virtualized pages outside view range */
                  <div className="w-full min-h-[600px] aspect-[8.5/11] flex items-center justify-center bg-white/[0.02] rounded-sm border border-white/5 text-text-muted text-sm">
                    <span className="opacity-50">Page {pageNum}</span>
                  </div>
                ) : !failedPages.has(pageNum) ? (
                  <>
                    {/* Loading skeleton */}
                    {!loadedPages.has(pageNum) && (
                      <div className="absolute inset-0 flex items-center justify-center bg-white/5 rounded-sm animate-pulse min-h-[400px]">
                        <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
                      </div>
                    )}

                    {/* Page image */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={api.getRAGPageImageUrl(activePDFDocumentId, pageNum)}
                      alt={`Page ${pageNum}`}
                      className={clsx(
                        "max-w-full shadow-lg rounded-sm border border-white/5 transition-opacity duration-300",
                        loadedPages.has(pageNum) ? "opacity-100" : "opacity-0"
                      )}
                      loading="lazy"
                      onLoad={() => handlePageLoad(pageNum)}
                      onError={(e) => handlePageError(pageNum, e)}
                    />
                  </>
                ) : (
                  /* Error state with retry button */
                  <div className="w-full min-h-[400px] aspect-[8.5/11] flex flex-col items-center justify-center gap-3 bg-white/5 rounded-sm border border-white/5 text-text-muted p-4">
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
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
