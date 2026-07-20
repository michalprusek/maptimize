"use client";

import { useState, useRef, useEffect } from "react";
import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { Send, Loader2, Paperclip, X, FileText } from "lucide-react";
import { clsx } from "clsx";

// Mirrors the formats the backend indexer supports (see DocumentUpload).
const ACCEPT_ATTACHMENTS =
  ".pdf,.docx,.pptx,.xlsx,.png,.jpg,.jpeg,.gif,.webp";

export function ChatInput() {
  const t = useTranslations("chat");
  const {
    sendMessage,
    isSendingMessage,
    activeThreadId,
    uploadDocument,
    isUploadingDocument,
    threadAttachments,
    deleteDocument,
  } = useChatStore();

  const [input, setInput] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const [isButtonPressed, setIsButtonPressed] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const attachments = activeThreadId ? threadAttachments[activeThreadId] || [] : [];

  const handleAttachClick = () => fileInputRef.current?.click();

  const handleFilesSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    // Reset immediately so picking the same file twice still fires onChange.
    e.target.value = "";
    if (!activeThreadId) return;
    for (const file of files) {
      await uploadDocument(file, activeThreadId);
    }
  };

  // Auto-resize textarea
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
    if (!input.trim() || isSendingMessage || !activeThreadId) return;

    // Button press animation
    setIsButtonPressed(true);
    setTimeout(() => setIsButtonPressed(false), 150);

    const message = input.trim();
    setInput("");
    await sendMessage(message);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const isDisabled = !activeThreadId || isSendingMessage;
  const canSend = input.trim() && !isDisabled;

  return (
    <div className="p-3 sm:p-4 border-t border-white/5 bg-bg-secondary/50">
      <form onSubmit={handleSubmit}>
        {/* Files attached to this conversation - the agent uses them as context */}
        {attachments.length > 0 && (
          <ul className="flex flex-wrap gap-2 mb-2" aria-label={t("attachments")}>
            {attachments.map((doc) => (
              <li
                key={doc.id}
                className={clsx(
                  "flex items-center gap-1.5 pl-2 pr-1 py-1 rounded-lg text-xs",
                  "bg-primary-500/10 border border-primary-500/20 text-text-secondary"
                )}
              >
                <FileText className="w-3.5 h-3.5 flex-shrink-0 text-primary-400" />
                <span className="truncate max-w-[160px]">{doc.name}</span>
                {doc.status !== "completed" && (
                  <Loader2 className="w-3 h-3 animate-spin text-text-muted" />
                )}
                <button
                  type="button"
                  onClick={() => deleteDocument(doc.id)}
                  aria-label={t("removeAttachment")}
                  title={t("removeAttachment")}
                  className="p-0.5 rounded hover:bg-white/10 text-text-muted hover:text-text-primary transition-colors"
                >
                  <X className="w-3 h-3" />
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* Combined input + send button container with glow effect */}
        <div
          className={clsx(
            "flex items-end gap-2 px-3 sm:px-4 py-2 rounded-xl",
            "bg-white/[0.03] border border-white/[0.08]",
            "transition-all duration-200",
            // Focus glow effect
            isFocused && !isDisabled && [
              "border-primary-500/50",
              "shadow-[0_0_0_4px_rgba(0,212,170,0.15),0_0_20px_rgba(0,212,170,0.1)]",
            ],
            isDisabled && "opacity-50"
          )}
        >
          {/* Attach files to this conversation */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPT_ATTACHMENTS}
            onChange={handleFilesSelected}
            className="hidden"
          />
          <button
            type="button"
            onClick={handleAttachClick}
            disabled={isDisabled || isUploadingDocument}
            aria-label={t("attachFile")}
            title={t("attachFile")}
            className={clsx(
              "flex-shrink-0 p-2 rounded-lg self-center transition-colors duration-200",
              isDisabled || isUploadingDocument
                ? "text-text-muted cursor-not-allowed"
                : "text-text-muted hover:text-primary-400 hover:bg-white/5"
            )}
          >
            {isUploadingDocument ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Paperclip className="w-5 h-5" />
            )}
          </button>

          {/* Input area */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            placeholder={t("inputPlaceholder")}
            disabled={isDisabled}
            rows={1}
            className={clsx(
              "flex-1 bg-transparent resize-none py-1.5 min-h-[36px]",
              "text-text-primary placeholder:text-text-muted",
              "text-sm sm:text-base",
              "focus:outline-none",
              "transition-all duration-200",
              isDisabled && "cursor-not-allowed"
            )}
          />

          {/* Character count with fade-in */}
          <span
            className={clsx(
              "text-xs text-text-muted flex-shrink-0 self-center",
              "transition-opacity duration-200",
              input.length > 100 ? "opacity-100" : "opacity-0"
            )}
          >
            {input.length}/10000
          </span>

          {/* Send button with animations */}
          <button
            type="submit"
            disabled={!canSend}
            className={clsx(
              "flex-shrink-0 p-2 rounded-lg transition-all duration-200",
              "transform will-change-transform",
              canSend
                ? [
                    "bg-gradient-to-r from-primary-500 to-primary-600",
                    "text-white",
                    "hover:from-primary-400 hover:to-primary-500",
                    "shadow-lg shadow-primary-500/20 hover:shadow-primary-500/30",
                    // Press animation
                    isButtonPressed ? "scale-90 rotate-12" : "scale-100 rotate-0",
                  ]
                : "text-text-muted cursor-not-allowed"
            )}
          >
            {isSendingMessage ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Send
                className={clsx(
                  "w-5 h-5 transition-transform duration-200",
                  canSend && "group-hover:translate-x-0.5"
                )}
              />
            )}
          </button>
        </div>

        {/* Hint text - visible on mobile */}
        <p className="mt-2 text-xs text-text-muted text-center sm:hidden">
          {t("pressEnterToSend")}
        </p>
      </form>
    </div>
  );
}
