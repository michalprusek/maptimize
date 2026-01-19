"use client";

import { useState, useRef, useEffect } from "react";
import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { useAuthStore } from "@/stores/authStore";
import type { ChatMessage, ChatCitation } from "@/lib/api";
import {
  User,
  Bot,
  FileText,
  Image as ImageIcon,
  Pencil,
  RefreshCw,
  Check,
  X,
  Loader2,
} from "lucide-react";
import { clsx } from "clsx";
import ReactMarkdown from "react-markdown";

interface MessageBubbleProps {
  message: ChatMessage;
  isNew?: boolean;
}

export function MessageBubble({ message, isNew = false }: MessageBubbleProps) {
  const t = useTranslations("chat");
  const {
    openPDFViewer,
    editMessage,
    regenerateMessage,
    isEditingMessage,
    isRegeneratingMessage,
  } = useChatStore();
  const { user } = useAuthStore();
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState(message.content);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const isUser = message.role === "user";
  const userAvatarUrl = user?.avatar_url;
  const hasCitations = message.citations && message.citations.length > 0;
  const isThisMessageEditing = isEditing && isEditingMessage;
  const isThisMessageRegenerating = isRegeneratingMessage;

  // Auto-resize textarea when editing
  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
      textareaRef.current.focus();
    }
  }, [isEditing, editContent]);

  const handleStartEdit = () => {
    setEditContent(message.content);
    setIsEditing(true);
  };

  const handleCancelEdit = () => {
    setIsEditing(false);
    setEditContent(message.content);
  };

  const handleSaveEdit = async () => {
    if (editContent.trim() && editContent !== message.content) {
      await editMessage(message.id, editContent.trim());
    }
    setIsEditing(false);
  };

  const handleRegenerate = async () => {
    await regenerateMessage(message.id);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSaveEdit();
    } else if (e.key === "Escape") {
      handleCancelEdit();
    }
  };

  const handleCitationClick = (citation: ChatCitation) => {
    if (citation.type === "document" && citation.doc_id) {
      openPDFViewer(citation.doc_id, citation.page || 1);
    }
    // For FOV citations, we could navigate to the image viewer
    // TODO: Implement FOV navigation
  };

  return (
    <div
      className={clsx(
        "flex gap-3 group",
        isUser ? "flex-row-reverse" : "flex-row"
      )}
    >
      {/* Avatar with optional pulse animation for new messages */}
      <div
        className={clsx(
          "flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center overflow-hidden",
          "transition-all duration-300",
          "bg-white/[0.03] backdrop-blur-sm border border-white/[0.08]",
          isNew && !isUser && "animate-avatar-pulse"
        )}
      >
        {isUser ? (
          userAvatarUrl ? (
            <img
              src={userAvatarUrl}
              alt="User avatar"
              className="w-full h-full object-cover"
            />
          ) : (
            <User className="w-4 h-4 text-primary-400" />
          )
        ) : (
          <Bot className="w-4 h-4 text-primary-400" />
        )}
      </div>

      {/* Message content */}
      <div
        className={clsx(
          "flex flex-col max-w-[75%] sm:max-w-[70%] lg:max-w-[65%]",
          isUser ? "items-end" : "items-start"
        )}
      >
        {/* Message bubble with action buttons */}
        <div className="group/bubble">
          {/* Message content */}
          <div
            className={clsx(
              "px-4 py-3 rounded-2xl transition-all duration-200",
              isUser
                ? // User message: gradient with depth
                  "bg-gradient-to-br from-primary-500 to-primary-600 text-white rounded-br-md shadow-lg shadow-primary-500/10"
                : // Assistant message: glassmorphism
                  "bg-white/[0.03] backdrop-blur-sm border border-white/[0.05] text-text-primary rounded-bl-md shadow-lg shadow-black/5"
            )}
          >
            {isUser ? (
              isEditing ? (
                // Edit mode for user messages
                <div className="flex flex-col gap-2">
                  <textarea
                    ref={textareaRef}
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    onKeyDown={handleKeyDown}
                    className={clsx(
                      "w-full bg-white/10 rounded-lg px-3 py-2 text-white",
                      "resize-none min-h-[40px] max-h-[200px]",
                      "focus:outline-none focus:ring-2 focus:ring-white/20",
                      "placeholder:text-white/50"
                    )}
                    placeholder={t("editPlaceholder")}
                    disabled={isEditingMessage}
                  />
                  <div className="flex justify-end gap-2">
                    <button
                      onClick={handleCancelEdit}
                      disabled={isEditingMessage}
                      className={clsx(
                        "p-1.5 rounded-lg transition-all",
                        "bg-white/10 hover:bg-white/20",
                        "disabled:opacity-50"
                      )}
                      title={t("cancel")}
                    >
                      <X className="w-4 h-4" />
                    </button>
                    <button
                      onClick={handleSaveEdit}
                      disabled={isEditingMessage || !editContent.trim()}
                      className={clsx(
                        "p-1.5 rounded-lg transition-all",
                        "bg-white/20 hover:bg-white/30",
                        "disabled:opacity-50"
                      )}
                      title={t("saveEdit")}
                    >
                      {isEditingMessage ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Check className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                </div>
              ) : (
                <p className="whitespace-pre-wrap leading-relaxed">{message.content}</p>
              )
            ) : (
            <div className="prose prose-invert prose-sm max-w-none prose-p:leading-relaxed prose-headings:text-text-primary prose-code:text-primary-300">
              <ReactMarkdown
                components={{
                  // Customize link rendering
                  a: ({ href, children }) => (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary-400 hover:text-primary-300 underline underline-offset-2 transition-colors"
                    >
                      {children}
                    </a>
                  ),
                  // Customize code blocks with better styling
                  code: ({ className, children }) => {
                    const isInline = !className;
                    return isInline ? (
                      <code className="bg-white/[0.08] text-primary-300 px-1.5 py-0.5 rounded-md text-sm font-mono">
                        {children}
                      </code>
                    ) : (
                      <code className={clsx(className, "text-sm")}>{children}</code>
                    );
                  },
                  // Better pre block styling for code blocks
                  pre: ({ children }) => (
                    <pre className="bg-black/20 rounded-lg p-3 overflow-x-auto border border-white/[0.05] my-2">
                      {children}
                    </pre>
                  ),
                  // Improved list styling
                  ul: ({ children }) => (
                    <ul className="list-disc list-inside space-y-1 my-2">{children}</ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="list-decimal list-inside space-y-1 my-2">{children}</ol>
                  ),
                  // Paragraph spacing
                  p: ({ children }) => (
                    <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>
                  ),
                  // Image rendering with auth token
                  img: ({ src, alt }) => {
                    // Add auth token to API image URLs
                    let imageSrc = src || "";
                    if (imageSrc.startsWith("/api/")) {
                      const token = typeof window !== "undefined"
                        ? localStorage.getItem("token")
                        : null;
                      if (token) {
                        const separator = imageSrc.includes("?") ? "&" : "?";
                        imageSrc = `${imageSrc}${separator}token=${token}`;
                      }
                    }
                    return (
                      <img
                        src={imageSrc}
                        alt={alt || ""}
                        className="rounded-lg max-w-full h-auto my-2 border border-white/10"
                        loading="lazy"
                      />
                    );
                  },
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}
          </div>

          {/* Action buttons and timestamp - appear on hover below the message */}
          <div
            className={clsx(
              "flex items-center gap-2 mt-1 transition-opacity duration-200",
              "opacity-0 group-hover/bubble:opacity-100",
              isUser ? "justify-end flex-row-reverse" : "justify-start"
            )}
          >
            {isUser ? (
              // Edit button for user messages
              <button
                onClick={handleStartEdit}
                disabled={isEditingMessage || isRegeneratingMessage}
                className={clsx(
                  "p-1 rounded-md transition-all duration-200",
                  "text-text-muted hover:text-primary-400",
                  "disabled:opacity-50 disabled:cursor-not-allowed"
                )}
                title={t("editMessage")}
              >
                <Pencil className="w-3.5 h-3.5" />
              </button>
            ) : (
              // Regenerate button for assistant messages
              <button
                onClick={handleRegenerate}
                disabled={isEditingMessage || isRegeneratingMessage}
                className={clsx(
                  "p-1 rounded-md transition-all duration-200",
                  "text-text-muted hover:text-primary-400",
                  "disabled:opacity-50 disabled:cursor-not-allowed",
                  isThisMessageRegenerating && "animate-spin"
                )}
                title={t("regenerateMessage")}
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            )}
            {/* Timestamp */}
            <span className="text-xs text-text-muted">
              {new Date(message.created_at).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
          </div>
        </div>

        {/* Citations with improved styling */}
        {hasCitations && (
          <div className="flex flex-wrap gap-2 mt-2">
            {message.citations.map((citation, idx) => (
              <button
                key={idx}
                onClick={() => handleCitationClick(citation)}
                className={clsx(
                  "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium",
                  "transition-all duration-200 transform hover:scale-105",
                  "shadow-sm hover:shadow-md",
                  citation.type === "document"
                    ? "bg-primary-500/10 text-primary-400 hover:bg-primary-500/20 border border-primary-500/20"
                    : "bg-accent-pink/10 text-accent-pink hover:bg-accent-pink/20 border border-accent-pink/20"
                )}
                title={t("citationTooltip")}
              >
                {citation.type === "document" ? (
                  <FileText className="w-3.5 h-3.5" />
                ) : (
                  <ImageIcon className="w-3.5 h-3.5" />
                )}
                <span>
                  {citation.title || t("untitled")}
                  {citation.page && ` p.${citation.page}`}
                </span>
              </button>
            ))}
          </div>
        )}

      </div>
    </div>
  );
}
