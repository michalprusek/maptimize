"use client";

import { useRef, useEffect, useMemo, useState, useCallback } from "react";
import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { MessageBubble } from "./MessageBubble";
import { ChatInput } from "./ChatInput";
import { WelcomeSearch } from "./WelcomeSearch";
import { Sparkles, X } from "lucide-react";
import { clsx } from "clsx";

// Skeleton loading component with shimmer effect
function MessageSkeleton({ isUser = false }: { isUser?: boolean }) {
  return (
    <div
      className={clsx(
        "flex gap-3",
        isUser ? "flex-row-reverse" : "flex-row",
        "animate-fade-in"
      )}
    >
      {/* Avatar skeleton */}
      <div
        className={clsx(
          "flex-shrink-0 w-8 h-8 rounded-full",
          "bg-gradient-to-r from-white/5 via-white/10 to-white/5 bg-[length:200%_100%] animate-shimmer"
        )}
      />
      {/* Content skeleton */}
      <div
        className={clsx(
          "flex flex-col gap-2",
          isUser ? "items-end" : "items-start"
        )}
      >
        <div
          className={clsx(
            "h-4 rounded-lg",
            "bg-gradient-to-r from-white/5 via-white/10 to-white/5 bg-[length:200%_100%] animate-shimmer",
            isUser ? "w-48" : "w-64"
          )}
        />
        <div
          className={clsx(
            "h-4 rounded-lg",
            "bg-gradient-to-r from-white/5 via-white/10 to-white/5 bg-[length:200%_100%] animate-shimmer",
            isUser ? "w-32" : "w-56"
          )}
        />
        {!isUser && (
          <div className="h-4 w-40 rounded-lg bg-gradient-to-r from-white/5 via-white/10 to-white/5 bg-[length:200%_100%] animate-shimmer" />
        )}
      </div>
    </div>
  );
}

// Animated thinking indicator with modern design, elapsed time and cancel button
interface TypingIndicatorProps {
  elapsedSeconds?: number;
  onCancel?: () => void;
  canCancel?: boolean;
}

function TypingIndicator({ elapsedSeconds: externalElapsedSeconds, onCancel, canCancel }: TypingIndicatorProps) {
  const t = useTranslations("chat");
  const [localElapsedSeconds, setLocalElapsedSeconds] = useState(0);
  const [isCancelling, setIsCancelling] = useState(false);

  // Use external elapsed time if provided (from server), otherwise track locally
  const elapsedSeconds = externalElapsedSeconds ?? localElapsedSeconds;

  // Track elapsed time locally when not provided from server
  useEffect(() => {
    if (externalElapsedSeconds !== undefined) return;

    const startTime = Date.now();
    const interval = setInterval(() => {
      setLocalElapsedSeconds(Math.floor((Date.now() - startTime) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [externalElapsedSeconds]);

  // Show different status based on elapsed time
  const getStatusText = () => {
    if (isCancelling) return t("cancelling");
    if (elapsedSeconds < 5) return t("thinking");
    if (elapsedSeconds < 15) return t("analyzing");
    if (elapsedSeconds < 30) return t("processing");
    return t("processingComplex");
  };

  const handleCancel = useCallback(async () => {
    if (isCancelling || !onCancel) return;
    setIsCancelling(true);
    try {
      await onCancel();
    } catch (error) {
      console.error("Failed to cancel generation:", error);
      // Error is already set in store by cancelGeneration action
    } finally {
      setIsCancelling(false);
    }
  }, [onCancel, isCancelling]);

  return (
    <div className="flex items-start gap-3 animate-fade-in">
      {/* Avatar with pulsing glow */}
      <div className="relative flex-shrink-0">
        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary-500/20 to-primary-600/20 backdrop-blur-sm border border-primary-400/30 flex items-center justify-center animate-thinking-pulse">
          <Sparkles className="w-4 h-4 text-primary-400" />
        </div>
        {/* Glow ring */}
        <div className="absolute inset-0 rounded-full bg-primary-400/20 blur-md animate-thinking-pulse" />
      </div>

      {/* Thinking bubble */}
      <div className="flex items-center gap-3 px-4 py-3 rounded-2xl rounded-bl-md bg-gradient-to-r from-white/[0.03] to-white/[0.01] backdrop-blur-sm border border-white/[0.08] shadow-lg">
        {/* Animated dots */}
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-gradient-to-t from-primary-500 to-primary-400 animate-typing-dot shadow-sm shadow-primary-400/50" />
          <span
            className="w-2 h-2 rounded-full bg-gradient-to-t from-primary-500 to-primary-400 animate-typing-dot shadow-sm shadow-primary-400/50"
            style={{ animationDelay: "0.15s" }}
          />
          <span
            className="w-2 h-2 rounded-full bg-gradient-to-t from-primary-500 to-primary-400 animate-typing-dot shadow-sm shadow-primary-400/50"
            style={{ animationDelay: "0.3s" }}
          />
        </div>

        {/* Shimmer text with status */}
        <span
          className={clsx(
            "text-sm font-medium bg-gradient-to-r bg-[length:200%_100%] bg-clip-text text-transparent animate-thinking-shimmer",
            isCancelling
              ? "from-amber-400 via-amber-300 to-amber-400"
              : "from-text-secondary via-primary-400 to-text-secondary"
          )}
        >
          {getStatusText()}
        </span>

        {/* Elapsed time indicator (shows after 10s) */}
        {elapsedSeconds >= 10 && (
          <span className="text-xs text-text-muted ml-1">
            ({elapsedSeconds}s)
          </span>
        )}

        {/* Cancel button (shows when cancellation is available) */}
        {canCancel && !isCancelling && (
          <button
            onClick={handleCancel}
            className="ml-2 p-1 rounded-full bg-white/5 hover:bg-red-500/20 border border-white/10 hover:border-red-500/30 transition-all duration-200 group"
            title={t("cancelGeneration")}
          >
            <X className="w-3.5 h-3.5 text-text-muted group-hover:text-red-400 transition-colors" />
          </button>
        )}
      </div>
    </div>
  );
}

export function ChatArea() {
  const t = useTranslations("chat");
  const {
    activeThreadId,
    messages,
    isLoadingMessages,
    isSendingMessage,
    isRegeneratingMessage,
    generatingThreadId,
    generationElapsedSeconds,
    cancelGeneration,
    error,
    clearError,
    clearActiveThread,
  } = useChatStore();

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const threadMessages = activeThreadId ? messages[activeThreadId] || [] : [];

  // Check if messages have been loaded for this thread
  // undefined = not loaded yet, [] = loaded but empty
  const messagesLoaded = activeThreadId ? messages[activeThreadId] !== undefined : false;

  // Check if thread is empty (has no messages after loading)
  // Only consider empty if messages were explicitly loaded and the array is empty
  const isEmptyThread = useMemo(() => {
    return activeThreadId && !isLoadingMessages && messagesLoaded && threadMessages.length === 0;
  }, [activeThreadId, isLoadingMessages, messagesLoaded, threadMessages.length]);

  // Redirect to welcome page when thread is empty
  // This handles: deleted threads, threads with all messages deleted, etc.
  useEffect(() => {
    if (isEmptyThread && !isSendingMessage) {
      clearActiveThread();
    }
  }, [isEmptyThread, isSendingMessage, clearActiveThread]);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (messagesEndRef.current && scrollContainerRef.current) {
      const container = scrollContainerRef.current;
      const isNearBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight <
        150;

      // Only auto-scroll if user is near bottom (prevents interrupting reading)
      if (isNearBottom || isSendingMessage || isRegeneratingMessage) {
        messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
      }
    }
  }, [threadMessages.length, isSendingMessage, isRegeneratingMessage]);

  // No active thread - show welcome search
  if (!activeThreadId) {
    return <WelcomeSearch />;
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Error banner with animation */}
      {error && (
        <div className="px-4 py-2 bg-red-500/10 border-b border-red-500/20 flex items-center justify-between animate-slide-in-up">
          <span className="text-red-400 text-sm">{error}</span>
          <button
            onClick={clearError}
            className="text-red-400 hover:text-red-300 text-sm underline transition-colors"
          >
            {t("dismiss")}
          </button>
        </div>
      )}

      {/* Messages area with smooth scrolling */}
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-y-auto p-4 space-y-4 scroll-smooth"
      >
        {isLoadingMessages ? (
          // Skeleton loading state
          <div className="space-y-4 animate-fade-in">
            <MessageSkeleton isUser={false} />
            <MessageSkeleton isUser={true} />
            <MessageSkeleton isUser={false} />
          </div>
        ) : threadMessages.length === 0 ? (
          // Empty thread will redirect to welcome - show nothing during transition
          null
        ) : (
          // Message list with staggered animations
          <>
            {threadMessages.map((message, index) => {
              // Calculate if this is a newly added message (for animation)
              const isNewMessage = index >= threadMessages.length - 1;
              // Stagger delay based on position from end (new messages animate first)
              const staggerDelay = isNewMessage
                ? 0
                : Math.min(index * 0.03, 0.15);

              return (
                <div
                  key={message.id}
                  className="animate-message-in"
                  style={{
                    animationDelay: `${staggerDelay}s`,
                    opacity: 0,
                  }}
                >
                  <MessageBubble message={message} isNew={isNewMessage} />
                </div>
              );
            })}

            {/* Typing indicator when AI is responding or regenerating */}
            {(isSendingMessage || isRegeneratingMessage || generatingThreadId === activeThreadId) && (
              <TypingIndicator
                elapsedSeconds={generatingThreadId === activeThreadId ? generationElapsedSeconds : undefined}
                onCancel={cancelGeneration}
                canCancel={generatingThreadId === activeThreadId}
              />
            )}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <ChatInput />
    </div>
  );
}
