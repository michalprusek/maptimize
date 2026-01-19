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
          if (error instanceof Error && !error.message.includes("404")) {
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

          // Update thread list
          get().loadThreads();
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

          // Refresh thread list
          get().loadThreads();
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
          set({
            error: error instanceof Error ? error.message : "Failed to cancel generation",
          });
        }
      },

      checkGenerationStatus: async (threadId: number) => {
        // Poll for generation status until completed, cancelled, or error
        const pollInterval = 1000; // 1 second
        const maxPolls = 300; // 5 minutes max
        let pollCount = 0;

        const poll = async () => {
          const { generatingThreadId } = get();

          // Stop polling if no longer generating this thread (handles cancellation)
          if (!generatingThreadId || generatingThreadId !== threadId) return;

          try {
            const status = await api.getGenerationStatus(threadId);

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
                set({
                  error: "Generation took too long. Please try again.",
                  generatingThreadId: null,
                  generationTaskId: null,
                  generationElapsedSeconds: 0,
                });
                return;
              }
              pollCount++;
              setTimeout(poll, pollInterval);
            }
          } catch (error) {
            // On error, stop polling
            set({
              error: error instanceof Error ? error.message : "Failed to check status",
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
          const response = await api.editChatMessage(activeThreadId, messageId, content);

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
          const response = await api.regenerateChatMessage(activeThreadId, messageId);

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
          try {
            const threadMessages = await api.getChatMessages(activeThreadId);
            set((state) => ({
              messages: {
                ...state.messages,
                [activeThreadId]: threadMessages,
              },
              error: error instanceof Error ? error.message : "Failed to regenerate message",
              isRegeneratingMessage: false,
            }));
          } catch {
            set({
              error: error instanceof Error ? error.message : "Failed to regenerate message",
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
