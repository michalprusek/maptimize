import { create } from "zustand";
import {
  api,
  RAGDocument,
  RAGIndexingStatus,
  DiscoveredPaper,
  ImportResult,
} from "@/lib/api";

// Image preview types (page crops / passages opened in a lightbox)
export interface DocumentImage {
  src: string;
  alt: string;
  messageId: number;
}

interface DocumentState {
  // Data
  documents: RAGDocument[];
  indexingStatus: RAGIndexingStatus | null;
  indexingStatusError: string | null;

  // PDF viewer UI state
  isPDFPanelOpen: boolean;
  activePDFDocumentId: number | null;
  activePDFPage: number;

  // Image preview UI state
  isImagePreviewOpen: boolean;
  previewImages: DocumentImage[];
  previewCurrentIndex: number;

  // Discovery state
  discoverResults: DiscoveredPaper[];
  discoverEffectiveQuery: string | null;
  discoverRewriteFailed: boolean;
  isDiscovering: boolean;
  isImportingPapers: boolean;

  // Loading states
  isUploadingDocument: boolean;
  isDeletingDocument: boolean;

  // Error state
  error: string | null;

  // Actions - Documents
  loadDocuments: () => Promise<void>;
  // Returns the document either way; check `is_duplicate` on it to tell a
  // fresh upload from one that was recognised as already present. Deliberately
  // NOT tracked in the store: a batch uploads many files, so a single global
  // "last upload was a duplicate" slot loses every file but the last.
  uploadDocument: (file: File) => Promise<RAGDocument | null>;
  deleteDocument: (documentId: number) => Promise<void>;
  reindexDocument: (documentId: number) => Promise<void>;
  refreshIndexingStatus: () => Promise<void>;

  // Actions - Discovery
  discoverSources: (query: string) => Promise<void>;
  importDiscovered: (dois: string[]) => Promise<ImportResult>;

  // Actions - PDF viewer
  openPDFViewer: (documentId: number, page?: number) => void;
  closePDFViewer: () => void;
  setActivePDFPage: (page: number) => void;

  // Actions - Image preview
  openImagePreview: (images: DocumentImage[], index: number) => void;
  closeImagePreview: () => void;
  navigateImagePreview: (index: number) => void;

  // Actions - Error
  clearError: () => void;
}

export const useDocumentStore = create<DocumentState>()((set, get) => ({
  // Initial data
  documents: [],
  indexingStatus: null,
  indexingStatusError: null,

  // Initial PDF viewer state
  isPDFPanelOpen: false,
  activePDFDocumentId: null,
  activePDFPage: 1,

  // Initial image preview state
  isImagePreviewOpen: false,
  previewImages: [],
  previewCurrentIndex: 0,

  // Initial discovery state
  discoverResults: [],
  discoverEffectiveQuery: null,
  discoverRewriteFailed: false,
  isDiscovering: false,
  isImportingPapers: false,

  // Initial loading states
  isUploadingDocument: false,
  isDeletingDocument: false,

  // Initial error state
  error: null,

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
      // A library duplicate is normally already in `documents`, so prepending
      // would double the row and trip React's key warning; nothing new to add.
      if (document.is_duplicate) {
        set({ isUploadingDocument: false });
        return document;
      }
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

  reindexDocument: async (documentId: number) => {
    set({ error: null });
    try {
      await api.reindexRAGDocument(documentId);
      // Optimistically flip to processing so the row moves out of the failed
      // bucket immediately; the periodic refresh settles the real status.
      set((state) => ({
        documents: state.documents.map((d) =>
          d.id === documentId
            ? { ...d, status: "processing", progress: 0, error_message: undefined }
            : d
        ),
      }));
      await get().loadDocuments();
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : "Failed to reindex document",
      });
    }
  },

  refreshIndexingStatus: async () => {
    try {
      const indexingStatus = await api.getRAGIndexingStatus();
      set({ indexingStatus, indexingStatusError: null });
    } catch (error) {
      // Log but track error state for debugging - don't set main error as this
      // is a background refresh.
      console.error("Failed to refresh indexing status:", error);
      set({
        indexingStatusError: error instanceof Error ? error.message : "Status unavailable",
      });
    }
  },

  // ==================== Discovery Actions ====================

  discoverSources: async (query: string) => {
    set({ isDiscovering: true });
    try {
      const res = await api.discoverSources(query);
      set({
        discoverResults: res.results,
        discoverEffectiveQuery: res.effective_query ?? null,
        discoverRewriteFailed: res.rewrite_failed ?? false,
      });
    } catch (error) {
      console.error("Failed to discover sources:", error);
      set({ discoverResults: [], discoverEffectiveQuery: null, discoverRewriteFailed: false });
      throw error;
    } finally {
      set({ isDiscovering: false });
    }
  },

  importDiscovered: async (dois: string[]) => {
    set({ isImportingPapers: true });
    try {
      const result = await api.importDiscovered(dois);
      // Mark the DOIs that actually succeeded as already imported so the modal
      // stops showing them as importable (avoids duplicate imports on a second
      // click). Anything in result.failed did NOT succeed.
      const failedDois = new Set(result.failed.map((f) => f.doi));
      const succeededDois = new Set(dois.filter((doi) => !failedDois.has(doi)));
      set((state) => ({
        discoverResults: state.discoverResults.map((p) =>
          p.doi && succeededDois.has(p.doi) ? { ...p, already_imported: true } : p
        ),
      }));
      // Imported papers arrive as PENDING documents; reload so they show up in
      // the "processing" bucket with progress.
      await get().loadDocuments();
      return result;
    } catch (error) {
      console.error("Failed to import papers:", error);
      throw error;
    } finally {
      set({ isImportingPapers: false });
    }
  },

  // ==================== PDF Viewer Actions ====================

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

  openImagePreview: (images: DocumentImage[], index: number) => {
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
}));
