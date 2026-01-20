"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { X, ExternalLink, RefreshCw, Loader2, AlertCircle } from "lucide-react";
import { clsx } from "clsx";

// Timeout for detecting blocked iframes (X-Frame-Options, CSP)
const IFRAME_LOAD_TIMEOUT_MS = 10000;

export function WebLinkPanel() {
  const t = useTranslations("chat");
  const { isWebLinkPanelOpen, activeWebLink, closeWebLinkPreview } = useChatStore();

  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [isHoveringClose, setIsHoveringClose] = useState(false);
  const isMountedRef = useRef(true);

  // Track mounted state
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Reset state when URL changes
  useEffect(() => {
    if (activeWebLink?.url) {
      setIsLoading(true);
      setHasError(false);
    }
  }, [activeWebLink?.url]);

  // Timeout detection for iframe loads
  // Many sites block iframe embedding via X-Frame-Options/CSP, and onError doesn't fire
  useEffect(() => {
    if (!activeWebLink?.url || hasError || !isLoading) return;

    const timeoutId = setTimeout(() => {
      if (isMountedRef.current && isLoading) {
        console.warn("WebLinkPanel: iframe load timeout, URL may be blocked:", activeWebLink.url);
        setIsLoading(false);
        setHasError(true);
      }
    }, IFRAME_LOAD_TIMEOUT_MS);

    return () => clearTimeout(timeoutId);
  }, [activeWebLink?.url, isLoading, hasError]);

  const handleIframeLoad = useCallback(() => {
    if (!isMountedRef.current) return;
    setIsLoading(false);
    setHasError(false);
  }, []);

  const handleIframeError = useCallback(() => {
    if (!isMountedRef.current) return;
    console.warn("WebLinkPanel: iframe onError fired");
    setIsLoading(false);
    setHasError(true);
  }, []);

  const handleRetry = useCallback(() => {
    const iframe = document.getElementById("web-link-iframe") as HTMLIFrameElement | null;

    if (!iframe) {
      console.error("WebLinkPanel: Cannot retry - iframe element not found");
      setHasError(true);
      setIsLoading(false);
      return;
    }

    if (!activeWebLink?.url) {
      console.error("WebLinkPanel: Cannot retry - no active URL");
      setHasError(true);
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setHasError(false);
    iframe.src = activeWebLink.url;
  }, [activeWebLink?.url]);

  const handleOpenExternal = useCallback(() => {
    if (!activeWebLink?.url) {
      console.error("WebLinkPanel: Cannot open external - no URL available");
      return;
    }

    const newWindow = window.open(activeWebLink.url, "_blank", "noopener,noreferrer");
    if (!newWindow) {
      // Popup was blocked by browser
      console.warn("WebLinkPanel: Popup blocked for URL:", activeWebLink.url);
    }
  }, [activeWebLink?.url]);

  // Keyboard navigation
  useEffect(() => {
    if (!isWebLinkPanelOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        try {
          closeWebLinkPreview();
        } catch (error) {
          console.error("WebLinkPanel: Failed to close on Escape:", error);
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isWebLinkPanelOpen, closeWebLinkPreview]);

  if (!isWebLinkPanelOpen || !activeWebLink) {
    return null;
  }

  // Extract domain from URL for display
  const getDomain = (url: string) => {
    try {
      return new URL(url).hostname;
    } catch (error) {
      // Log for debugging - helps identify malformed URLs from backend
      console.warn("WebLinkPanel: Failed to parse URL domain:", url, error);
      return url;
    }
  };

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
          <span className="text-sm font-medium text-text-primary truncate" title={activeWebLink.title}>
            {activeWebLink.title}
          </span>
          <span className="text-xs text-text-muted truncate hidden sm:block">
            {getDomain(activeWebLink.url)}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleOpenExternal}
            className="p-1.5 rounded-lg transition-all duration-200 text-text-secondary hover:text-text-primary hover:bg-white/10"
            title={t("openInNewTab")}
          >
            <ExternalLink className="w-4 h-4" />
          </button>
          <button
            onClick={handleRetry}
            disabled={isLoading}
            className={clsx(
              "p-1.5 rounded-lg transition-all duration-200",
              "text-text-secondary hover:text-text-primary hover:bg-white/10",
              "disabled:opacity-50 disabled:cursor-not-allowed"
            )}
            title={t("refresh")}
          >
            <RefreshCw className={clsx("w-4 h-4", isLoading && "animate-spin")} />
          </button>
          <button
            onClick={closeWebLinkPreview}
            onMouseEnter={() => setIsHoveringClose(true)}
            onMouseLeave={() => setIsHoveringClose(false)}
            className={clsx(
              "p-1.5 rounded-lg transition-all duration-200",
              "text-text-secondary hover:text-text-primary hover:bg-white/10",
              isHoveringClose && "rotate-90"
            )}
            title={t("close")}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 relative overflow-hidden bg-white">
        {/* Loading state */}
        {isLoading && !hasError && (
          <div className="absolute inset-0 flex items-center justify-center bg-bg-primary z-10">
            <div className="flex flex-col items-center gap-3">
              <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
              <span className="text-sm text-text-secondary">{t("loadingWebPage")}</span>
            </div>
          </div>
        )}

        {/* Error state */}
        {hasError && (
          <div className="absolute inset-0 flex items-center justify-center bg-bg-primary z-10">
            <div className="flex flex-col items-center gap-4 p-6 text-center max-w-sm">
              <AlertCircle className="w-12 h-12 text-orange-400" />
              <div>
                <h3 className="text-text-primary font-medium mb-1">{t("webPageLoadError")}</h3>
                <p className="text-sm text-text-secondary">{t("webPageLoadErrorDesc")}</p>
              </div>
              <div className="flex gap-3">
                <button
                  onClick={handleRetry}
                  className="px-4 py-2 text-sm bg-primary-500/20 hover:bg-primary-500/30 text-primary-400 rounded-lg transition-colors"
                >
                  {t("retry")}
                </button>
                <button
                  onClick={handleOpenExternal}
                  className="px-4 py-2 text-sm bg-white/5 hover:bg-white/10 text-text-secondary rounded-lg transition-colors flex items-center gap-2"
                >
                  <ExternalLink className="w-4 h-4" />
                  {t("openInNewTab")}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Iframe */}
        <iframe
          id="web-link-iframe"
          src={activeWebLink.url}
          className={clsx(
            "w-full h-full border-0",
            (isLoading || hasError) && "opacity-0"
          )}
          onLoad={handleIframeLoad}
          onError={handleIframeError}
          sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
          referrerPolicy="no-referrer"
          title={activeWebLink.title}
        />
      </div>
    </div>
  );
}
