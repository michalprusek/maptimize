import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  api,
  ChatThread,
  ChatMessage,
  RAGDocument,
  RAGIndexingStatus,
  GenerationStatus,
} from "@/lib/api";

// Image preview types
export interface ChatImage {
  src: string;
  alt: string;
  messageId: number;
}

// Web link preview types
export interface WebLink {
  url: string;
  title: string;
}

interface ChatState {
  // Data
  threads: ChatThread[];
  activeThreadId: number | null;
  messages: Record<number, ChatMessage[]>;
  documents: RAGDocument[];
  indexingStatus: RAGIndexingStatus | null;
  indexingStatusError: string | null;

  // UI State
  isThreadSidebarOpen: boolean;
  isPDFPanelOpen: boolean;
  activePDFDocumentId: number | null;
  activePDFPage: number;

  // Web Link Preview State
  isWebLinkPanelOpen: boolean;
  activeWebLink: WebLink | null;

  // Image Preview State
  isImagePreviewOpen: boolean;
  previewImages: ChatImage[];
  previewCurrentIndex: number;

  // Loading states
  isLoadingThreads: boolean;
  isLoadingMessages: boolean;
  isSendingMessage: boolean;
  isEditingMessage: boolean;
  isRegeneratingMessage: boolean;
  isUploadingDocument: boolean;
  isDeletingThread: boolean;
  isDeletingDocument: boolean;

  // Generation state (for async AI responses)
  generatingThreadId: number | null;
  generationTaskId: string | null;
  generationElapsedSeconds: number;

  // Error state
  error: string | null;

  // Actions - Threads
  loadThreads: () => Promise<void>;
  createThread: (name?: string) => Promise<ChatThread | null>;
  selectThread: (threadId: number) => Promise<void>;
  renameThread: (threadId: number, name: string) => Promise<void>;
  deleteThread: (threadId: number) => Promise<void>;

  // Actions - Messages
  sendMessage: (content: string) => Promise<void>;
  startConversation: (message: string) => Promise<void>;
  cancelGeneration: () => Promise<void>;
  checkGenerationStatus: (threadId: number) => Promise<void>;
  editMessage: (messageId: number, content: string) => Promise<void>;
  regenerateMessage: (messageId: number) => Promise<void>;

  // Actions - Documents
  loadDocuments: () => Promise<void>;
  uploadDocument: (file: File) => Promise<RAGDocument | null>;
  deleteDocument: (documentId: number) => Promise<void>;
  refreshIndexingStatus: () => Promise<void>;

  // Actions - UI
  toggleThreadSidebar: () => void;
  clearActiveThread: () => void;
  openPDFViewer: (documentId: number, page?: number) => void;
  closePDFViewer: () => void;
  setActivePDFPage: (page: number) => void;

  // Actions - Web Link Preview
  openWebLinkPreview: (url: string, title: string) => void;
  closeWebLinkPreview: () => void;

  // Actions - Image Preview
  openImagePreview: (images: ChatImage[], index: number) => void;
  closeImagePreview: () => void;
  navigateImagePreview: (index: number) => void;

  // Actions - Error
  clearError: () => void;
}

// Helper to create optimistic user message
function createOptimisticUserMessage(threadId: number, content: string): ChatMessage {
  return {
    id: -Date.now(),
    thread_id: threadId,
    role: "user",
    content,
    citations: [],
    image_refs: [],
    tool_calls: [],
    created_at: new Date().toISOString(),
  };
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      // Initial data
      threads: [],
      activeThreadId: null,
      messages: {},
      documents: [],
      indexingStatus: null,
      indexingStatusError: null,

      // Initial UI state
      isThreadSidebarOpen: true,
      isPDFPanelOpen: false,
      activePDFDocumentId: null,
      activePDFPage: 1,

      // Initial web link preview state
      isWebLinkPanelOpen: false,
      activeWebLink: null,

      // Initial image preview state
      isImagePreviewOpen: false,
      previewImages: [],
      previewCurrentIndex: 0,

      // Initial loading states
      isLoadingThreads: false,
      isLoadingMessages: false,
      isSendingMessage: false,
      isEditingMessage: false,
      isRegeneratingMessage: false,
      isUploadingDocument: false,
      isDeletingThread: false,
      isDeletingDocument: false,

      // Initial generation state
      generatingThreadId: null,
      generationTaskId: null,
      generationElapsedSeconds: 0,

      // Initial error state
      error: null,

      // ==================== Thread Actions ====================

      loadThreads: async () => {
        set({ isLoadingThreads: true, error: null });
        try {
          const threads = await api.getChatThreads();
          set({ threads, isLoadingThreads: false });
        } catch (error) {
          set({
            error: error instanceof Error ? error.message : "Failed to load threads",
            isLoadingThreads: false,
          });
        }
      },

      createThread: async (name?: string) => {
        set({ error: null });
        try {
          const thread = await api.createChatThread(name);
          set((state) => ({
            threads: [thread, ...state.threads],
            activeThreadId: thread.id,
            messages: { ...state.messages, [thread.id]: [] },
          }));
          return thread;
        } catch (error) {
          set({
            error: error instanceof Error ? error.message : "Failed to create thread",
          });
          return null;
        }
      },

      selectThread: async (threadId: number) => {
        const { messages, checkGenerationStatus } = get();

        // Set active thread immediately
        set({ activeThreadId: threadId, isLoadingMessages: true, error: null });

        // Load messages if not cached
        if (!messages[threadId]) {
          try {
            const threadMessages = await api.getChatMessages(threadId);
            set((state) => ({
              messages: { ...state.messages, [threadId]: threadMessages },
              isLoadingMessages: false,
            }));
          } catch (error) {
            set({
              error: error instanceof Error ? error.message : "Failed to load messages",
              isLoadingMessages: false,
            });
          }
        } else {
          set({ isLoadingMessages: false });
        }

        // Check if there's ongoing generation for this thread (handles page refresh)
        try {
          const status = await api.getGenerationStatus(threadId);
          if (status.status === "generating") {
            set({
              generatingThreadId: threadId,
              generationTaskId: status.task_id || null,
              generationElapsedSeconds: status.elapsed_seconds || 0,
            });
            // Resume polling
            checkGenerationStatus(threadId);
          }
        } catch (error) {
          // Log non-404 errors for debugging (404 means no generation in progress, which is normal)
          // Check for both "404" and "Not Found" since API may return either
          if (error instanceof Error && !error.message.includes("404") && !error.message.includes("Not Found")) {
            console.warn("Failed to check generation status on thread select:", error.message);
          }
        }
      },

      renameThread: async (threadId: number, name: string) => {
        set({ error: null });
        try {
          await api.updateChatThread(threadId, name);
          set((state) => ({
            threads: state.threads.map((t) =>
              t.id === threadId ? { ...t, name } : t
            ),
          }));
        } catch (error) {
          set({
            error: error instanceof Error ? error.message : "Failed to rename thread",
          });
        }
      },

      deleteThread: async (threadId: number) => {
        set({ isDeletingThread: true, error: null });
        try {
          await api.deleteChatThread(threadId);
          set((state) => {
            const newMessages = { ...state.messages };
            delete newMessages[threadId];

            return {
              threads: state.threads.filter((t) => t.id !== threadId),
              messages: newMessages,
              activeThreadId:
                state.activeThreadId === threadId ? null : state.activeThreadId,
              isDeletingThread: false,
            };
          });
        } catch (error) {
          set({
            error: error instanceof Error ? error.message : "Failed to delete thread",
            isDeletingThread: false,
          });
        }
      },

      // ==================== Message Actions ====================

      sendMessage: async (content: string) => {
        const { activeThreadId, messages } = get();
        if (!activeThreadId) {
          set({ error: "No active thread selected" });
          return;
        }

        set({ isSendingMessage: true, error: null });

        // Optimistically add user message
        const tempUserMessage = createOptimisticUserMessage(activeThreadId, content);

        set((state) => ({
          messages: {
            ...state.messages,
            [activeThreadId]: [...(state.messages[activeThreadId] || []), tempUserMessage],
          },
        }));

        try {
          // Send message - returns immediately with user message and starts async generation
          const response = await api.sendChatMessage(activeThreadId, content);

          // Replace temp message with real user message
          set((state) => {
            const threadMessages = state.messages[activeThreadId] || [];
            const filteredMessages = threadMessages.filter(
              (m) => m.id !== tempUserMessage.id
            );

            return {
              messages: {
                ...state.messages,
                [activeThreadId]: [...filteredMessages, response.user_message],
              },
              isSendingMessage: false,
              generatingThreadId: activeThreadId,
              generationTaskId: response.task_id || null,
              generationElapsedSeconds: 0,
            };
          });

          // Start polling for generation status
          get().checkGenerationStatus(activeThreadId);

          // Optimistic thread reorder: move active thread to top without API call
          // This provides instant UI feedback instead of waiting for loadThreads()
          set((state) => {
            const activeThread = state.threads.find((t) => t.id === activeThreadId);
            if (!activeThread) return state;

            return {
              threads: [
                { ...activeThread, updated_at: new Date().toISOString() },
                ...state.threads.filter((t) => t.id !== activeThreadId),
              ],
            };
          });
        } catch (error) {
          // Remove optimistic message on error
          set((state) => ({
            messages: {
              ...state.messages,
              [activeThreadId]: (state.messages[activeThreadId] || []).filter(
                (m) => m.id !== tempUserMessage.id
              ),
            },
            error: error instanceof Error ? error.message : "Failed to send message",
            isSendingMessage: false,
          }));
        }
      },

      startConversation: async (message: string) => {
        set({ isSendingMessage: true, error: null });

        try {
          // Create thread
          const thread = await api.createChatThread();

          set((state) => ({
            threads: [thread, ...state.threads],
            activeThreadId: thread.id,
            messages: { ...state.messages, [thread.id]: [] },
          }));

          // Optimistically add user message
          const tempUserMessage = createOptimisticUserMessage(thread.id, message);

          set((state) => ({
            messages: {
              ...state.messages,
              [thread.id]: [tempUserMessage],
            },
          }));

          // Send message - starts async generation
          const response = await api.sendChatMessage(thread.id, message);

          // Replace temp message with real user message
          set((state) => {
            const threadMessages = state.messages[thread.id] || [];
            const filteredMessages = threadMessages.filter(
              (m) => m.id !== tempUserMessage.id
            );

            return {
              messages: {
                ...state.messages,
                [thread.id]: [...filteredMessages, response.user_message],
              },
              isSendingMessage: false,
              generatingThreadId: thread.id,
              generationTaskId: response.task_id || null,
              generationElapsedSeconds: 0,
            };
          });

          // Start polling for generation status
          get().checkGenerationStatus(thread.id);

          // Thread is already at top from creation - no need to reorder
          // Name will be updated on next selectThread or page reload
        } catch (error) {
          set({
            error: error instanceof Error ? error.message : "Failed to start conversation",
            isSendingMessage: false,
          });
        }
      },

      cancelGeneration: async () => {
        const { generatingThreadId } = get();
        if (!generatingThreadId) return;

        try {
          await api.cancelGeneration(generatingThreadId);
          set({
            generatingThreadId: null,
            generationTaskId: null,
            generationElapsedSeconds: 0,
          });
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : "Failed to cancel generation";
          // Always reset generation state on cancel failure to avoid stuck UI
          // The backend will handle cleanup eventually
          set({
            error: errorMessage.includes("404")
              ? "Generation already completed or cancelled"
              : errorMessage.includes("400")
              ? "Cannot cancel - generation not in progress"
              : `Cancellation failed: ${errorMessage}. The generation may still be running.`,
            generatingThreadId: null,
            generationTaskId: null,
            generationElapsedSeconds: 0,
          });
        }
      },

      checkGenerationStatus: async (threadId: number) => {
        /**
         * Poll for generation status until completed, cancelled, or error.
         *
         * Implements defensive polling with race condition protection:
         * 1. Check generatingThreadId before fetch (early exit if cancelled)
         * 2. Fetch status from server
         * 3. Check generatingThreadId again after fetch (handle cancel during fetch)
         * 4. Update UI based on status
         * 5. Schedule next poll after interval if still generating
         *
         * Retry logic: Uses exponential backoff for transient errors (network, 500s).
         * Timeout: Stops after 300 polls (~5 minutes) and shows actionable error.
         */
        const basePollInterval = 1000; // 1 second base interval
        const maxPolls = 300; // 5 minutes max
        const maxRetries = 3; // Max retries per poll for transient errors
        let pollCount = 0;
        let retryCount = 0;

        const poll = async () => {
          const { generatingThreadId } = get();

          // Stop polling if no longer generating this thread (handles cancellation)
          if (!generatingThreadId || generatingThreadId !== threadId) return;

          try {
            const status = await api.getGenerationStatus(threadId);

            // Reset retry count on successful fetch
            retryCount = 0;

            // Check again after API call in case cancelled during fetch
            const currentState = get();
            if (!currentState.generatingThreadId || currentState.generatingThreadId !== threadId) return;

            set({ generationElapsedSeconds: status.elapsed_seconds || 0 });

            if (status.status === "completed" && status.message) {
              // Add the new assistant message
              set((state) => ({
                messages: {
                  ...state.messages,
                  [threadId]: [...(state.messages[threadId] || []), status.message!],
                },
                generatingThreadId: null,
                generationTaskId: null,
                generationElapsedSeconds: 0,
              }));
              // Refresh thread list
              get().loadThreads();
              return;
            }

            if (status.status === "error") {
              set({
                error: status.error || "Generation failed",
                generatingThreadId: null,
                generationTaskId: null,
                generationElapsedSeconds: 0,
              });
              return;
            }

            if (status.status === "cancelled") {
              set({
                generatingThreadId: null,
                generationTaskId: null,
                generationElapsedSeconds: 0,
              });
              return;
            }

            // Continue polling if still generating
            if (status.status === "generating") {
              if (pollCount >= maxPolls) {
                console.warn(`Generation polling timeout for thread ${threadId} after ${maxPolls} polls`);
                set({
                  error: "Generation is taking longer than expected (>5 minutes). The AI may still be processing your request in the background. Try refreshing the thread in a moment, or cancel and try again with a simpler query.",
                  generatingThreadId: null,
                  generationTaskId: null,
                  generationElapsedSeconds: 0,
                });
                return;
              }
              pollCount++;
              setTimeout(poll, basePollInterval);
            }
          } catch (error) {
            const errorMessage = error instanceof Error ? error.message : "Unknown error";
            const isTransientError =
              errorMessage.includes("fetch") ||
              errorMessage.includes("network") ||
              errorMessage.includes("timeout") ||
              errorMessage.includes("500") ||
              errorMessage.includes("502") ||
              errorMessage.includes("503") ||
              errorMessage.includes("504");

            // Retry on transient errors with exponential backoff
            if (isTransientError && retryCount < maxRetries) {
              retryCount++;
              const backoffDelay = basePollInterval * Math.pow(2, retryCount);
              console.warn(`Polling failed (attempt ${retryCount}/${maxRetries}), retrying in ${backoffDelay}ms: ${errorMessage}`);
              setTimeout(poll, backoffDelay);
              return;
            }

            // Permanent failure after retries or non-transient error
            console.error(`Generation status polling failed after ${retryCount} retries:`, errorMessage);
            set({
              error: retryCount >= maxRetries
                ? "Lost connection to server while checking generation status. The AI may still be generating - try refreshing the page."
                : `Failed to check status: ${errorMessage}`,
              generatingThreadId: null,
              generationTaskId: null,
              generationElapsedSeconds: 0,
            });
          }
        };

        // Start polling
        poll();
      },

      editMessage: async (messageId: number, content: string) => {
        const { activeThreadId } = get();
        if (!activeThreadId) {
          set({ error: "No active thread selected" });
          return;
        }

        set({ isEditingMessage: true, error: null });

        try {
          await api.editChatMessage(activeThreadId, messageId, content);

          // Reload all messages for the thread (some were deleted)
          const threadMessages = await api.getChatMessages(activeThreadId);

          set((state) => ({
            messages: {
              ...state.messages,
              [activeThreadId]: threadMessages,
            },
            isEditingMessage: false,
          }));
        } catch (error) {
          set({
            error: error instanceof Error ? error.message : "Failed to edit message",
            isEditingMessage: false,
          });
        }
      },

      regenerateMessage: async (messageId: number) => {
        const { activeThreadId, messages } = get();
        if (!activeThreadId) {
          set({ error: "No active thread selected" });
          return;
        }

        // Optimistically remove the message being regenerated and all after it
        const currentMessages = messages[activeThreadId] || [];
        const messageIndex = currentMessages.findIndex((m) => m.id === messageId);
        const messagesBeforeRegenerate = messageIndex >= 0
          ? currentMessages.slice(0, messageIndex)
          : currentMessages;

        set((state) => ({
          isRegeneratingMessage: true,
          error: null,
          messages: {
            ...state.messages,
            [activeThreadId]: messagesBeforeRegenerate,
          },
        }));

        try {
          await api.regenerateChatMessage(activeThreadId, messageId);

          // Reload all messages for the thread
          const threadMessages = await api.getChatMessages(activeThreadId);

          set((state) => ({
            messages: {
              ...state.messages,
              [activeThreadId]: threadMessages,
            },
            isRegeneratingMessage: false,
          }));
        } catch (error) {
          // On error, reload messages to get correct state
          const errorMessage = error instanceof Error ? error.message : "Failed to regenerate message";
          try {
            const threadMessages = await api.getChatMessages(activeThreadId);
            set((state) => ({
              messages: {
                ...state.messages,
                [activeThreadId]: threadMessages,
              },
              error: errorMessage,
              isRegeneratingMessage: false,
            }));
          } catch (fetchError) {
            console.warn(
              "[ChatStore] Failed to fetch messages after regeneration error:",
              fetchError instanceof Error ? fetchError.message : fetchError
            );
            set({
              error: errorMessage,
              isRegeneratingMessage: false,
            });
          }
        }
      },

      // ==================== Document Actions ====================

      loadDocuments: async () => {
        set({ error: null });
        try {
          const documents = await api.getRAGDocuments();
          set({ documents });
        } catch (error) {
          set({
            error: error instanceof Error ? error.message : "Failed to load documents",
          });
        }
      },

      uploadDocument: async (file: File) => {
        set({ isUploadingDocument: true, error: null });
        try {
          const document = await api.uploadRAGDocument(file);
          set((state) => ({
            documents: [document, ...state.documents],
            isUploadingDocument: false,
          }));
          return document;
        } catch (error) {
          set({
            error: error instanceof Error ? error.message : "Failed to upload document",
            isUploadingDocument: false,
          });
          return null;
        }
      },

      deleteDocument: async (documentId: number) => {
        set({ isDeletingDocument: true, error: null });
        try {
          await api.deleteRAGDocument(documentId);
          set((state) => ({
            documents: state.documents.filter((d) => d.id !== documentId),
            // Close PDF viewer if this document was open
            isPDFPanelOpen:
              state.activePDFDocumentId === documentId ? false : state.isPDFPanelOpen,
            activePDFDocumentId:
              state.activePDFDocumentId === documentId ? null : state.activePDFDocumentId,
            isDeletingDocument: false,
          }));
        } catch (error) {
          set({
            error: error instanceof Error ? error.message : "Failed to delete document",
            isDeletingDocument: false,
          });
        }
      },

      refreshIndexingStatus: async () => {
        try {
          const indexingStatus = await api.getRAGIndexingStatus();
          set({ indexingStatus, indexingStatusError: null });
        } catch (error) {
          // Log but track error state for debugging - don't set main error as this is background refresh
          console.error("Failed to refresh indexing status:", error);
          set({
            indexingStatusError: error instanceof Error ? error.message : "Status unavailable",
          });
        }
      },

      // ==================== UI Actions ====================

      toggleThreadSidebar: () => {
        set((state) => ({ isThreadSidebarOpen: !state.isThreadSidebarOpen }));
      },

      clearActiveThread: () => {
        set({ activeThreadId: null });
      },

      openPDFViewer: (documentId: number, page = 1) => {
        set({
          isPDFPanelOpen: true,
          activePDFDocumentId: documentId,
          activePDFPage: page,
          // Close web link panel for mutual exclusivity
          isWebLinkPanelOpen: false,
          activeWebLink: null,
        });
      },

      closePDFViewer: () => {
        set({
          isPDFPanelOpen: false,
          activePDFDocumentId: null,
          activePDFPage: 1,
        });
      },

      setActivePDFPage: (page: number) => {
        set({ activePDFPage: page });
      },

      // ==================== Web Link Preview Actions ====================

      openWebLinkPreview: (url: string, title: string) => {
        // Validate URL before opening
        if (!url || typeof url !== "string") {
          console.error("openWebLinkPreview: Invalid URL provided:", url);
          return;
        }

        // Only allow http/https URLs for security
        try {
          const parsed = new URL(url);
          if (!["http:", "https:"].includes(parsed.protocol)) {
            console.warn("openWebLinkPreview: Non-HTTP URL, opening in new tab:", url);
            window.open(url, "_blank", "noopener,noreferrer");
            return;
          }
        } catch {
          console.error("openWebLinkPreview: Malformed URL rejected:", url);
          return;
        }

        set({
          isWebLinkPanelOpen: true,
          activeWebLink: { url, title: title || url },
          // Close PDF panel for mutual exclusivity
          isPDFPanelOpen: false,
        });
      },

      closeWebLinkPreview: () => {
        set({
          isWebLinkPanelOpen: false,
          activeWebLink: null,
        });
      },

      // ==================== Image Preview Actions ====================

      openImagePreview: (images: ChatImage[], index: number) => {
        set({
          isImagePreviewOpen: true,
          previewImages: images,
          previewCurrentIndex: index,
        });
      },

      closeImagePreview: () => {
        set({
          isImagePreviewOpen: false,
          previewImages: [],
          previewCurrentIndex: 0,
        });
      },

      navigateImagePreview: (index: number) => {
        set({ previewCurrentIndex: index });
      },

      // ==================== Error Actions ====================

      clearError: () => {
        set({ error: null });
      },
    }),
    {
      name: "chat-storage",
      partialize: (state) => ({
        // Only persist UI preferences and active selections
        isThreadSidebarOpen: state.isThreadSidebarOpen,
        activeThreadId: state.activeThreadId,
      }),
    }
  )
);
