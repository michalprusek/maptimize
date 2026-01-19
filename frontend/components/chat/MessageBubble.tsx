"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { useTranslations } from "next-intl";
import { useChatStore, ChatImage } from "@/stores/chatStore";
import { useAuthStore } from "@/stores/authStore";
import { useSettingsStore, DisplayMode } from "@/stores/settingsStore";
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
  Download,
  FileSpreadsheet,
  File,
} from "lucide-react";
import { clsx } from "clsx";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MessageBubbleProps {
  message: ChatMessage;
  isNew?: boolean;
}

// Helper to extract image URLs from markdown content
function extractImagesFromMarkdown(content: string, messageId: number): ChatImage[] {
  const images: ChatImage[] = [];
  // Match markdown images: ![alt](src)
  const regex = /!\[([^\]]*)\]\(([^)]+)\)/g;
  let match;
  while ((match = regex.exec(content)) !== null) {
    images.push({
      alt: match[1] || "",
      src: match[2],
      messageId,
    });
  }
  return images;
}

export function MessageBubble({ message, isNew = false }: MessageBubbleProps) {
  const t = useTranslations("chat");
  const {
    openPDFViewer,
    editMessage,
    regenerateMessage,
    isEditingMessage,
    isRegeneratingMessage,
    activeThreadId,
    messages,
    openImagePreview,
  } = useChatStore();
  const { user } = useAuthStore();
  const displayMode = useSettingsStore((state) => state.displayMode);

  // Collect all images from all messages in the current thread
  const allThreadImages = useMemo(() => {
    if (!activeThreadId) return [];
    const threadMessages = messages[activeThreadId] || [];
    const images: ChatImage[] = [];
    for (const msg of threadMessages) {
      if (msg.role === "assistant") {
        images.push(...extractImagesFromMarkdown(msg.content, msg.id));
      }
    }
    return images;
  }, [activeThreadId, messages]);

  // Handle image click - open modal with all images
  const handleImageClick = useCallback((src: string, alt: string) => {
    // Find this image in all thread images
    const index = allThreadImages.findIndex((img) => img.src === src);
    if (index >= 0) {
      openImagePreview(allThreadImages, index);
    } else {
      // Fallback: if not found, open with just this image
      openImagePreview([{ src, alt, messageId: message.id }], 0);
    }
  }, [allThreadImages, openImagePreview, message.id]);
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
            <div className="prose prose-invert prose-sm max-w-none prose-p:leading-relaxed prose-headings:text-text-primary prose-code:text-primary-300 chat-message-content">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // Customize link rendering - special handling for file downloads
                  a: ({ href, children }) => {
                    const isDownloadLink = href && (
                      href.includes("/uploads/exports/") ||
                      href.includes("/uploads/temp/") && /\.(xlsx|csv|pdf|zip)$/i.test(href) ||
                      /\.(xlsx|csv|pdf|zip)$/i.test(href)
                    );

                    if (isDownloadLink && href) {
                      // Get filename from URL
                      const filename = href.split("/").pop() || "file";
                      const ext = filename.split(".").pop()?.toLowerCase();
                      const FileIcon = ext === "xlsx" || ext === "csv" ? FileSpreadsheet : File;
                      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
                      const downloadUrl = href.startsWith("/") ? `${apiUrl}${href}` : href;

                      return (
                        <a
                          href={downloadUrl}
                          download={filename}
                          className={clsx(
                            "inline-flex items-center gap-2 px-3 py-2 my-1 rounded-lg",
                            "bg-primary-500/10 hover:bg-primary-500/20 border border-primary-500/20",
                            "text-primary-400 hover:text-primary-300 transition-all",
                            "no-underline font-medium text-sm"
                          )}
                        >
                          <FileIcon className="w-4 h-4" />
                          <span className="truncate max-w-[200px]">{filename}</span>
                          <Download className="w-4 h-4 ml-1" />
                        </a>
                      );
                    }

                    return (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary-400 hover:text-primary-300 underline underline-offset-2 transition-colors"
                      >
                        {children}
                      </a>
                    );
                  },
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
                  // Table styling for GFM tables
                  table: ({ children }) => (
                    <div className="overflow-x-auto my-3">
                      <table className="min-w-full border-collapse border border-white/10 rounded-lg overflow-hidden">
                        {children}
                      </table>
                    </div>
                  ),
                  thead: ({ children }) => (
                    <thead className="bg-white/[0.05]">{children}</thead>
                  ),
                  tbody: ({ children }) => (
                    <tbody className="divide-y divide-white/[0.05]">{children}</tbody>
                  ),
                  tr: ({ children }) => (
                    <tr className="hover:bg-white/[0.02] transition-colors">{children}</tr>
                  ),
                  th: ({ children }) => (
                    <th className="px-3 py-2 text-left text-xs font-semibold text-text-primary border-b border-white/10">
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td className="px-3 py-2 text-sm text-text-secondary">{children}</td>
                  ),
                  // Paragraph - detect if it contains only images and render as grid
                  // Use <span> with grid display to avoid hydration issues
                  p: ({ children, node }) => {
                    // Check if children are only images (or image wrappers)
                    const childArray = Array.isArray(children) ? children : [children];
                    const hasOnlyImages = childArray.every((child: React.ReactNode) => {
                      if (child === null || child === undefined) return true;
                      if (typeof child === "string" && child.trim() === "") return true;
                      if (typeof child === "object" && child !== null && "props" in child) {
                        // Check if it's an image or our image wrapper span
                        const props = (child as { props?: { src?: string; className?: string } }).props;
                        return props?.src || props?.className?.includes("chat-image-item");
                      }
                      return false;
                    });

                    if (hasOnlyImages && childArray.length > 0) {
                      // Render as a 3-column grid for images (use span to avoid nesting issues)
                      return (
                        <span className="grid grid-cols-3 gap-2 my-2">
                          {children}
                        </span>
                      );
                    }

                    return <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>;
                  },
                  // Image rendering with auth token, backend URL, and LUT styling
                  img: ({ src, alt }) => {
                    // LUT class mapping
                    const lutClasses: Record<DisplayMode, string> = {
                      grayscale: "lut-grayscale",
                      inverted: "lut-inverted",
                      green: "lut-green",
                      fire: "lut-fire",
                    };

                    let imageSrc = src || "";
                    const isApiImage = imageSrc.startsWith("/api/");
                    const isBase64Image = imageSrc.startsWith("data:image/");
                    const isMicroscopyImage = isApiImage && imageSrc.includes("/images/");

                    if (isApiImage) {
                      // Prepend backend URL for API paths
                      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
                      imageSrc = `${apiUrl}${imageSrc}`;

                      // Add auth token
                      const token = typeof window !== "undefined"
                        ? localStorage.getItem("token")
                        : null;
                      if (token) {
                        const separator = imageSrc.includes("?") ? "&" : "?";
                        imageSrc = `${imageSrc}${separator}token=${token}`;
                      }
                    }

                    // Base64 plots: full width, no grid
                    // Use <span> with display:block to avoid hydration error (<div> can't be inside <p>)
                    if (isBase64Image) {
                      return (
                        <span className="chat-plot-item block my-3">
                          <img
                            src={imageSrc}
                            alt={alt || "Plot"}
                            className="rounded-lg max-w-full h-auto border border-white/10 bg-white cursor-pointer hover:opacity-90 transition-opacity"
                            loading="lazy"
                            onClick={() => handleImageClick(imageSrc, alt || "Plot")}
                          />
                          {alt && (
                            <span className="block text-xs text-text-muted mt-1 text-center">
                              {alt}
                            </span>
                          )}
                        </span>
                      );
                    }

                    // Microscopy images and plots: grid layout with LUT
                    // Use <span> with flex to avoid hydration error (<div> can't be inside <p>)
                    return (
                      <span className="chat-image-item flex flex-col">
                        <img
                          src={imageSrc}
                          alt={alt || ""}
                          className={clsx(
                            "rounded-lg w-full h-auto border border-white/10",
                            "hover:border-primary-400/50 transition-colors cursor-pointer",
                            // Apply LUT only to microscopy images
                            isMicroscopyImage && lutClasses[displayMode]
                          )}
                          loading="lazy"
                          onClick={() => handleImageClick(imageSrc, alt || "")}
                        />
                        {alt && (
                          <span className="text-xs text-text-muted mt-1 truncate text-center">
                            {alt}
                          </span>
                        )}
                      </span>
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
