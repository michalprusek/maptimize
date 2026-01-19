"use client";

import { useState, useRef, useEffect } from "react";
import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { Send, Loader2, Sparkles, FileSearch } from "lucide-react";
import { Logo } from "@/components/ui";
import { clsx } from "clsx";

export function WelcomeSearch() {
  const t = useTranslations("chat");
  const { startConversation, isSendingMessage } = useChatStore();

  const [input, setInput] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const [isButtonPressed, setIsButtonPressed] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea (same logic as ChatInput)
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(
        textareaRef.current.scrollHeight,
        200
      )}px`;
    }
  }, [input]);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!input.trim() || isSendingMessage) return;

    setIsButtonPressed(true);
    setTimeout(() => setIsButtonPressed(false), 150);

    const message = input.trim();
    setInput("");
    await startConversation(message);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const canSend = input.trim() && !isSendingMessage;

  return (
    <div className="flex-1 flex flex-col items-center justify-center p-4 sm:p-8 relative overflow-hidden">
      {/* Gradient mesh background */}
      <div className="absolute inset-0 opacity-30 pointer-events-none">
        <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary-500/20 rounded-full blur-3xl" />
        <div className="absolute bottom-1/4 right-1/4 w-80 h-80 bg-accent-cyan/10 rounded-full blur-3xl" />
      </div>

      {/* Floating decorative elements */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div
          className="absolute top-20 left-[15%] w-2 h-2 bg-primary-400/40 rounded-full animate-float"
          style={{ animationDelay: "0s" }}
        />
        <div
          className="absolute top-32 right-[20%] w-1.5 h-1.5 bg-accent-cyan/40 rounded-full animate-float"
          style={{ animationDelay: "1s" }}
        />
        <div
          className="absolute bottom-40 left-[25%] w-2.5 h-2.5 bg-primary-300/30 rounded-full animate-float"
          style={{ animationDelay: "2s" }}
        />
        <div
          className="absolute bottom-28 right-[15%] w-2 h-2 bg-accent-purple/30 rounded-full animate-float"
          style={{ animationDelay: "0.5s" }}
        />
      </div>

      {/* Content */}
      <div className="relative z-10 flex flex-col items-center w-full max-w-2xl animate-fade-in">
        {/* App logo */}
        <div className="relative mb-6">
          <Logo size="xl" className="text-primary-400 !w-24 !h-24 sm:!w-32 sm:!h-32" transparent />
        </div>

        {/* Title with stagger animation */}
        <h2
          className="text-2xl sm:text-3xl font-semibold text-text-primary mb-8 text-center animate-slide-in-up"
          style={{ animationDelay: "0.1s" }}
        >
          {t("welcomeTitle")}
        </h2>

        {/* Search input with scale-in animation */}
        <form
          onSubmit={handleSubmit}
          className="w-full animate-scale-in"
          style={{ animationDelay: "0.3s", opacity: 0, animationFillMode: "forwards" }}
        >
          <div
            className={clsx(
              "flex items-center gap-3 px-4 sm:px-5 py-3 rounded-2xl",
              "bg-white/[0.03] border border-white/[0.08]",
              "backdrop-blur-sm",
              "transition-all duration-300",
              // Focus glow effect - more prominent for welcome
              isFocused && [
                "border-primary-500/50",
                "shadow-[0_0_0_4px_rgba(0,212,170,0.15),0_0_30px_rgba(0,212,170,0.15)]",
              ],
              isSendingMessage && "opacity-50"
            )}
          >
            <FileSearch className="w-5 h-5 text-text-muted flex-shrink-0" />
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              placeholder={t("welcomeInputPlaceholder")}
              disabled={isSendingMessage}
              rows={1}
              className={clsx(
                "flex-1 bg-transparent resize-none py-2",
                "text-text-primary placeholder:text-text-muted",
                "text-base sm:text-lg",
                "focus:outline-none",
                "transition-all duration-200",
                isSendingMessage && "cursor-not-allowed"
              )}
              autoFocus
            />

            {/* Send button */}
            <button
              type="submit"
              disabled={!canSend}
              className={clsx(
                "flex-shrink-0 p-3 rounded-xl transition-all duration-200",
                "transform will-change-transform",
                canSend
                  ? [
                      "bg-gradient-to-r from-primary-500 to-primary-600",
                      "text-white",
                      "hover:from-primary-400 hover:to-primary-500",
                      "shadow-lg shadow-primary-500/25 hover:shadow-primary-500/40",
                      isButtonPressed ? "scale-90 rotate-12" : "scale-100 rotate-0",
                    ]
                  : "text-text-muted cursor-not-allowed"
              )}
            >
              {isSendingMessage ? (
                <Loader2 className="w-6 h-6 animate-spin" />
              ) : (
                <Send className="w-6 h-6" />
              )}
            </button>
          </div>
        </form>

        {/* Quick suggestion chips - optional enhancement */}
        <div
          className="flex flex-wrap justify-center gap-2 mt-6 animate-fade-in"
          style={{ animationDelay: "0.5s", opacity: 0, animationFillMode: "forwards" }}
        >
          {[t("suggestionAnalyze"), t("suggestionCompare"), t("suggestionExplain")].map(
            (suggestion, idx) => (
              <button
                key={idx}
                onClick={() => setInput(suggestion)}
                className={clsx(
                  "px-3 py-1.5 rounded-full text-sm",
                  "bg-white/[0.03] border border-white/[0.08]",
                  "text-text-secondary hover:text-text-primary",
                  "hover:bg-white/[0.06] hover:border-primary-500/30",
                  "transition-all duration-200"
                )}
              >
                {suggestion}
              </button>
            )
          )}
        </div>
      </div>
    </div>
  );
}
