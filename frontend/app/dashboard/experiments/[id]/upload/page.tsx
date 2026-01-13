"use client";

import { useState, useCallback, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useDropzone } from "react-dropzone";
import { api } from "@/lib/api";
import {
  ArrowLeft,
  Upload,
  Loader2,
  Scan,
  CheckCircle,
  AlertCircle,
  ChevronDown,
  Dna,
} from "lucide-react";

export default function UploadPage(): JSX.Element {
  const params = useParams();
  const router = useRouter();
  const experimentId = Number(params.id);
  const queryClient = useQueryClient();

  const [detectCells, setDetectCells] = useState(true);
  const [selectedProteinId, setSelectedProteinId] = useState<number | undefined>(undefined);
  const [proteinDropdownOpen, setProteinDropdownOpen] = useState(false);
  const [uploadingFiles, setUploadingFiles] = useState<Map<string, number>>(new Map());
  const [uploadedCount, setUploadedCount] = useState(0);
  const [batchImageIds, setBatchImageIds] = useState<number[]>([]);
  const [failedUploads, setFailedUploads] = useState<{ name: string; error: string }[]>([]);

  const { data: experiment, isLoading: expLoading } = useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () => api.getExperiment(experimentId),
  });

  const { data: proteins } = useQuery({
    queryKey: ["proteins"],
    queryFn: () => api.getProteins(),
  });

  const selectedProtein = proteins?.find((p) => p.id === selectedProteinId);

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      return api.uploadImage(experimentId, file, selectedProteinId, detectCells);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["images", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
      setUploadedCount((prev) => prev + 1);
      // Track image ID for batch status monitoring
      setBatchImageIds((prev) => [...prev, data.id]);
    },
  });

  // Poll for batch processing status
  const { data: batchStatus } = useQuery({
    queryKey: ["batch-status", batchImageIds],
    queryFn: async () => {
      const images = await Promise.all(
        batchImageIds.map((id) => api.getImage(id))
      );
      const readyOrError = images.filter(
        (img) => img.status === "ready" || img.status === "error"
      );
      const errors = images.filter((img) => img.status === "error");
      return {
        allReady: readyOrError.length === images.length,
        processing: images.length - readyOrError.length,
        errors: errors.length,
        total: images.length,
      };
    },
    enabled: batchImageIds.length > 0 && uploadingFiles.size === 0,
    refetchInterval: (query) => {
      // Stop polling when all images are ready
      if (query.state.data?.allReady) return false;
      return 2000; // Poll every 2 seconds
    },
  });

  // Prevent navigation while batch is processing
  useEffect(() => {
    const isProcessing =
      uploadingFiles.size > 0 ||
      (batchImageIds.length > 0 && !batchStatus?.allReady);

    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isProcessing) {
        e.preventDefault();
        e.returnValue = "";
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [uploadingFiles.size, batchImageIds.length, batchStatus?.allReady]);

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      for (const file of acceptedFiles) {
        const fileId = `${file.name}-${Date.now()}`;
        setUploadingFiles((prev) => new Map(prev).set(fileId, 0));

        try {
          await uploadMutation.mutateAsync(file);
        } catch (err) {
          console.error("Upload failed:", err);
          const errorMessage = err instanceof Error ? err.message : "Unknown error";
          setFailedUploads((prev) => [...prev, { name: file.name, error: errorMessage }]);
        } finally {
          setUploadingFiles((prev) => {
            const next = new Map(prev);
            next.delete(fileId);
            return next;
          });
        }
      }
    },
    [uploadMutation, detectCells, selectedProteinId]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "image/tiff": [".tif", ".tiff"],
      "image/png": [".png"],
      "image/jpeg": [".jpg", ".jpeg"],
    },
    multiple: true,
  });

  if (expLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-10 h-10 text-primary-500 animate-spin" />
      </div>
    );
  }

  if (!experiment) {
    return (
      <div className="text-center py-12">
        <p className="text-text-secondary">Experiment not found</p>
        <Link href="/dashboard/experiments" className="btn-primary mt-4">
          Back to Experiments
        </Link>
      </div>
    );
  }

  // Computed state for navigation blocking
  const isProcessing =
    uploadingFiles.size > 0 ||
    (batchImageIds.length > 0 && !batchStatus?.allReady);

  return (
    <div className="space-y-8 max-w-3xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link
          href={isProcessing ? "#" : `/dashboard/experiments/${experimentId}`}
          className={`p-2 rounded-lg transition-colors ${
            isProcessing
              ? "cursor-not-allowed opacity-50"
              : "hover:bg-white/5"
          }`}
          onClick={(e) => {
            if (isProcessing) e.preventDefault();
          }}
        >
          <ArrowLeft className="w-5 h-5 text-text-secondary" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-display font-bold text-text-primary">
            Upload Images
          </h1>
          <p className="text-text-secondary mt-1">
            to {experiment.name}
          </p>
        </div>
      </div>

      {/* Upload Card */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-6"
      >
        {/* Upload settings */}
        <div className="space-y-4 mb-6">
          {/* MAP Protein selector */}
          <div className="relative">
            <label className="block text-sm text-text-secondary mb-2">
              MAP Protein (applied to all uploaded images)
            </label>
            <button
              onClick={() => setProteinDropdownOpen(!proteinDropdownOpen)}
              className="flex items-center justify-between w-full px-4 py-3 bg-bg-secondary border border-white/10 rounded-lg hover:bg-bg-hover transition-colors"
            >
              <div className="flex items-center gap-3">
                <Dna className="w-5 h-5 text-text-muted" />
                {selectedProtein ? (
                  <div className="flex items-center gap-2">
                    <span
                      className="w-3 h-3 rounded-full"
                      style={{ backgroundColor: selectedProtein.color || "#888" }}
                    />
                    <span className="text-text-primary">{selectedProtein.name}</span>
                  </div>
                ) : (
                  <span className="text-text-muted">No protein selected</span>
                )}
              </div>
              <ChevronDown
                className={`w-4 h-4 text-text-muted transition-transform ${
                  proteinDropdownOpen ? "rotate-180" : ""
                }`}
              />
            </button>

            {proteinDropdownOpen && (
              <motion.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="absolute z-10 mt-1 w-full bg-bg-elevated border border-white/10 rounded-lg shadow-xl overflow-hidden"
              >
                <button
                  onClick={() => {
                    setSelectedProteinId(undefined);
                    setProteinDropdownOpen(false);
                  }}
                  className={`w-full px-4 py-2 text-left hover:bg-white/5 transition-colors ${
                    !selectedProteinId ? "bg-primary-500/10" : ""
                  }`}
                >
                  <span className="text-text-muted">No protein</span>
                </button>
                {proteins?.map((protein) => (
                  <button
                    key={protein.id}
                    onClick={() => {
                      setSelectedProteinId(protein.id);
                      setProteinDropdownOpen(false);
                    }}
                    className={`w-full px-4 py-2 text-left hover:bg-white/5 transition-colors flex items-center gap-2 ${
                      selectedProteinId === protein.id ? "bg-primary-500/10" : ""
                    }`}
                  >
                    <span
                      className="w-3 h-3 rounded-full"
                      style={{ backgroundColor: protein.color || "#888" }}
                    />
                    <span className="text-text-primary">{protein.name}</span>
                    {protein.full_name && (
                      <span className="text-text-muted text-xs ml-auto">
                        {protein.full_name}
                      </span>
                    )}
                  </button>
                ))}
              </motion.div>
            )}
          </div>

          {/* Cell detection toggle */}
          <button
            onClick={() => setDetectCells(!detectCells)}
            className={`flex items-center gap-3 px-4 py-3 rounded-lg transition-all w-full ${
              detectCells
                ? "bg-primary-500/20 border border-primary-500/30"
                : "bg-bg-secondary border border-transparent hover:bg-bg-hover"
            }`}
          >
            <div
              className={`relative w-11 h-6 rounded-full transition-colors ${
                detectCells ? "bg-primary-500" : "bg-bg-elevated"
              }`}
            >
              <div
                className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                  detectCells ? "translate-x-6" : "translate-x-1"
                }`}
              />
            </div>
            <Scan className={`w-5 h-5 ${detectCells ? "text-primary-400" : "text-text-muted"}`} />
            <div className="text-left flex-1">
              <p className={`text-sm font-medium ${detectCells ? "text-primary-400" : "text-text-secondary"}`}>
                Detect and crop cells
              </p>
              <p className="text-xs text-text-muted">
                {detectCells ? "YOLO detection will run after upload" : "Upload without detection"}
              </p>
            </div>
          </button>
        </div>

        {/* Dropzone */}
        <div
          {...getRootProps()}
          className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-all ${
            isDragActive
              ? "border-primary-500 bg-primary-500/10"
              : "border-white/10 hover:border-primary-500/50 hover:bg-white/5"
          }`}
        >
          <input {...getInputProps()} />
          <div className="flex flex-col items-center">
            <div
              className={`p-4 rounded-2xl mb-4 ${
                isDragActive ? "bg-primary-500/20" : "bg-bg-elevated"
              }`}
            >
              <Upload
                className={`w-10 h-10 ${
                  isDragActive ? "text-primary-400" : "text-text-muted"
                }`}
              />
            </div>
            <p className="text-text-primary font-medium text-lg mb-1">
              {isDragActive ? "Drop files here" : "Drag & drop images here"}
            </p>
            <p className="text-text-secondary">
              or click to browse
            </p>
            <p className="text-text-muted text-sm mt-2">
              Supported: TIFF (Z-stack), PNG, JPEG
            </p>
          </div>
        </div>

        {/* Upload progress */}
        {uploadingFiles.size > 0 && (
          <div className="mt-6 space-y-2">
            {Array.from(uploadingFiles.entries()).map(([fileId]) => (
              <div
                key={fileId}
                className="flex items-center gap-3 p-3 bg-bg-secondary rounded-lg"
              >
                <Loader2 className="w-5 h-5 text-primary-500 animate-spin" />
                <span className="text-sm text-text-primary flex-1 truncate">
                  {fileId.split("-")[0]}
                </span>
                <span className="text-sm text-text-muted">Uploading...</span>
              </div>
            ))}
          </div>
        )}

        {/* Processing status */}
        {batchImageIds.length > 0 && !batchStatus?.allReady && uploadingFiles.size === 0 && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6 p-4 bg-accent-amber/10 border border-accent-amber/20 rounded-lg"
          >
            <div className="flex items-center gap-3">
              <Loader2 className="w-5 h-5 text-accent-amber animate-spin" />
              <div className="flex-1">
                <span className="text-accent-amber font-medium">
                  Processing {batchStatus?.processing ?? batchImageIds.length} of {batchStatus?.total ?? batchImageIds.length} images...
                </span>
                <p className="text-xs text-text-muted mt-1">
                  Please wait. Do not close this page.
                </p>
              </div>
            </div>
          </motion.div>
        )}

        {/* Success message - all done */}
        {uploadedCount > 0 && uploadingFiles.size === 0 && batchStatus?.allReady && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6 p-4 bg-primary-500/10 border border-primary-500/20 rounded-lg"
          >
            <div className="flex items-center gap-3">
              <CheckCircle className="w-5 h-5 text-primary-400" />
              <span className="text-primary-400">
                {uploadedCount} {uploadedCount === 1 ? "image" : "images"} processed successfully
                {batchStatus.errors > 0 && (
                  <span className="text-red-400 ml-2">
                    ({batchStatus.errors} {batchStatus.errors === 1 ? "error" : "errors"})
                  </span>
                )}
              </span>
            </div>
          </motion.div>
        )}

        {/* Error indicator during processing */}
        {batchStatus && batchStatus.errors > 0 && !batchStatus.allReady && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-3 p-3 bg-red-500/10 border border-red-500/20 rounded-lg flex items-center gap-3"
          >
            <AlertCircle className="w-4 h-4 text-red-400" />
            <span className="text-red-400 text-sm">
              {batchStatus.errors} {batchStatus.errors === 1 ? "image" : "images"} failed processing
            </span>
          </motion.div>
        )}

        {/* Failed uploads list */}
        {failedUploads.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6 p-4 bg-accent-red/10 border border-accent-red/20 rounded-lg"
          >
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <AlertCircle className="w-5 h-5 text-accent-red" />
                <span className="text-accent-red font-medium">
                  {failedUploads.length} upload{failedUploads.length !== 1 ? "s" : ""} failed
                </span>
              </div>
              <button
                onClick={() => setFailedUploads([])}
                className="text-sm text-text-muted hover:text-text-primary"
              >
                Dismiss
              </button>
            </div>
            <div className="space-y-2 max-h-40 overflow-y-auto">
              {failedUploads.map((failed, i) => (
                <div key={i} className="text-sm">
                  <span className="text-text-primary">{failed.name}</span>
                  <span className="text-text-muted ml-2">— {failed.error}</span>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </motion.div>

      {/* Actions */}
      <div className="flex justify-between items-center">
        <Link
          href={isProcessing ? "#" : `/dashboard/experiments/${experimentId}`}
          className={`transition-colors ${
            isProcessing
              ? "text-text-muted cursor-not-allowed"
              : "text-text-secondary hover:text-text-primary"
          }`}
          onClick={(e) => {
            if (isProcessing) e.preventDefault();
          }}
        >
          Back to gallery
        </Link>
        {uploadedCount > 0 && batchStatus?.allReady && (
          <button
            onClick={() => {
              // Invalidate all related queries to ensure fresh data
              queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
              queryClient.invalidateQueries({ queryKey: ["images", experimentId] });
              queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
              queryClient.invalidateQueries({ queryKey: ["embedding-status"] });
              queryClient.invalidateQueries({ queryKey: ["umap"] });
              router.push(`/dashboard/experiments/${experimentId}`);
            }}
            className="btn-primary"
          >
            View Images
          </button>
        )}
      </div>
    </div>
  );
}
