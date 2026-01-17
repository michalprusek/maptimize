"use client";

import { useState, useCallback, useEffect, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useDropzone } from "react-dropzone";
import { useTranslations } from "next-intl";
import { api, Image as ApiImage } from "@/lib/api";
import { MicroscopyImage } from "@/components/ui";
import {
  ArrowLeft,
  Upload,
  Loader2,
  Scan,
  CheckCircle,
  AlertCircle,
  Play,
  ImageIcon,
} from "lucide-react";

/**
 * Workflow phase states for the two-phase upload process:
 * - idle: Initial state, no uploads started
 * - uploading: Files being uploaded, Phase 1 processing (projections/thumbnails)
 * - uploaded: All files uploaded and Phase 1 complete, awaiting user configuration
 * - processing: Phase 2 processing (detection/feature extraction) in progress
 * - done: All processing complete, ready to view results
 */
type WorkflowPhase = "idle" | "uploading" | "uploaded" | "processing" | "done";

/** Polling interval for status updates (ms) - balances responsiveness with server load */
const STATUS_POLLING_INTERVAL_MS = 2000;

export default function UploadPage(): JSX.Element {
  const params = useParams();
  const router = useRouter();
  const experimentId = Number(params.id);
  const queryClient = useQueryClient();
  const t = useTranslations("images");
  const tCommon = useTranslations("common");

  // Workflow state
  const [phase, setPhase] = useState<WorkflowPhase>("idle");

  // Upload state
  const [uploadingFiles, setUploadingFiles] = useState<Map<string, number>>(new Map());
  const [uploadedImageIds, setUploadedImageIds] = useState<number[]>([]);
  const [failedUploads, setFailedUploads] = useState<{ name: string; error: string }[]>([]);

  // Progress tracking
  const [totalFilesToUpload, setTotalFilesToUpload] = useState(0);
  const [filesUploaded, setFilesUploaded] = useState(0);

  // Configuration state (Phase 2)
  const [detectCells, setDetectCells] = useState(true);

  const { data: experiment, isLoading: expLoading } = useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () => api.getExperiment(experimentId),
  });

  // Cache for already-fetched images (won't re-fetch completed ones)
  const [imageCache, setImageCache] = useState<Map<number, ApiImage>>(new Map());

  // Track last fetched IDs count to detect new uploads
  const [lastFetchedCount, setLastFetchedCount] = useState(0);

  // Fetch new images incrementally when uploadedImageIds changes
  useEffect(() => {
    const fetchNewImages = async () => {
      if (uploadedImageIds.length === 0) return;
      if (uploadedImageIds.length === lastFetchedCount) return;

      // Only fetch images that are new (not in cache)
      const newIds = uploadedImageIds.filter((id) => !imageCache.has(id));
      if (newIds.length === 0) {
        setLastFetchedCount(uploadedImageIds.length);
        return;
      }

      const results = await Promise.allSettled(
        newIds.map((id) => api.getImage(id))
      );

      const newImages = results
        .filter((r): r is PromiseFulfilledResult<ApiImage> => r.status === "fulfilled")
        .map((r) => r.value);

      if (newImages.length > 0) {
        setImageCache((prev) => {
          const next = new Map(prev);
          newImages.forEach((img) => next.set(img.id, img));
          return next;
        });
      }

      setLastFetchedCount(uploadedImageIds.length);
    };

    fetchNewImages();
  }, [uploadedImageIds, lastFetchedCount, imageCache]);

  // Poll for status updates on processing images only
  const { data: uploadedImages, error: uploadedImagesError } = useQuery({
    queryKey: ["uploaded-images-status", experimentId],
    queryFn: async () => {
      if (imageCache.size === 0) return [];

      // Find images that need status updates (still processing)
      const processingIds = Array.from(imageCache.values())
        .filter((img) => img.status === "UPLOADING" || img.status === "PROCESSING")
        .map((img) => img.id);

      // If no processing images, just return cached data
      if (processingIds.length === 0) {
        return uploadedImageIds.map((id) => imageCache.get(id)).filter(Boolean) as ApiImage[];
      }

      // Fetch only processing images for status update
      const results = await Promise.allSettled(
        processingIds.map((id) => api.getImage(id))
      );

      const updatedImages = results
        .filter((r): r is PromiseFulfilledResult<ApiImage> => r.status === "fulfilled")
        .map((r) => r.value);

      // Update cache with new statuses
      if (updatedImages.length > 0) {
        setImageCache((prev) => {
          const next = new Map(prev);
          updatedImages.forEach((img) => next.set(img.id, img));
          return next;
        });
      }

      // Return all images in upload order
      return uploadedImageIds.map((id) => imageCache.get(id)).filter(Boolean) as ApiImage[];
    },
    enabled: imageCache.size > 0,
    refetchInterval: () => {
      // Poll while any image is still processing Phase 1
      const hasProcessing = Array.from(imageCache.values()).some(
        (img) => img.status === "UPLOADING" || img.status === "PROCESSING"
      );
      return hasProcessing ? STATUS_POLLING_INTERVAL_MS : false;
    },
  });

  // Check if all Phase 1 is complete (all images are "uploaded" or better)
  const allPhase1Complete = useMemo(() => {
    if (!uploadedImages || uploadedImages.length === 0) return false;
    return uploadedImages.every(
      (img) => img.status !== "UPLOADING" && img.status !== "PROCESSING"
    );
  }, [uploadedImages]);

  // Calculate upload progress percentage
  const uploadProgress = useMemo(() => {
    if (totalFilesToUpload === 0) return 0;
    return Math.round((filesUploaded / totalFilesToUpload) * 100);
  }, [filesUploaded, totalFilesToUpload]);

  // Update phase when Phase 1 completes
  useEffect(() => {
    if (phase === "uploading" && uploadingFiles.size === 0 && uploadedImageIds.length > 0) {
      setPhase("uploaded");
    }
  }, [phase, uploadingFiles.size, uploadedImageIds.length]);

  // Poll for Phase 2 completion
  const { data: processingStatus } = useQuery({
    queryKey: ["processing-status", uploadedImageIds],
    queryFn: async () => {
      // Use allSettled to handle partial failures gracefully
      const results = await Promise.allSettled(
        uploadedImageIds.map((id) => api.getImage(id))
      );
      const images = results
        .filter((r): r is PromiseFulfilledResult<ApiImage> => r.status === "fulfilled")
        .map((r) => r.value);
      const fetchFailed = results.filter((r) => r.status === "rejected").length;
      if (fetchFailed > 0) {
        console.warn(`[ProcessingStatus] Failed to fetch ${fetchFailed} of ${uploadedImageIds.length} images`);
      }
      const readyOrError = images.filter(
        (img) => img.status === "READY" || img.status === "ERROR"
      );
      return {
        allDone: readyOrError.length === images.length && fetchFailed === 0,
        processing: images.length - readyOrError.length,
        errors: images.filter((img) => img.status === "ERROR").length,
        total: uploadedImageIds.length, // Use original count for accurate tracking
      };
    },
    enabled: phase === "processing",
    refetchInterval: (query) => {
      if (query.state.data?.allDone) return false;
      return STATUS_POLLING_INTERVAL_MS;
    },
  });

  // Calculate processing progress percentage
  const processingProgress = useMemo(() => {
    if (!processingStatus || processingStatus.total === 0) return 0;
    const completed = processingStatus.total - processingStatus.processing;
    return Math.round((completed / processingStatus.total) * 100);
  }, [processingStatus]);

  // Update phase when Phase 2 completes
  useEffect(() => {
    if (phase === "processing" && processingStatus?.allDone) {
      setPhase("done");
    }
  }, [phase, processingStatus?.allDone]);

  // Upload mutation (Phase 1)
  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      return api.uploadImage(experimentId, file);
    },
    onSuccess: (data) => {
      setUploadedImageIds((prev) => [...prev, data.id]);
    },
  });

  // Batch process mutation (Phase 2)
  // Note: Images inherit protein from experiment, set via experiment protein selector
  const batchProcessMutation = useMutation({
    mutationFn: async () => {
      return api.batchProcessImages(uploadedImageIds, detectCells);
    },
    onSuccess: () => {
      setPhase("processing");
    },
    onError: (err: Error) => {
      console.error("Batch process failed:", err);
    },
  });

  // Prevent navigation while processing
  useEffect(() => {
    const isProcessing = phase === "uploading" || phase === "processing";

    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isProcessing) {
        e.preventDefault();
        e.returnValue = "";
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [phase]);

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      setPhase("uploading");
      setTotalFilesToUpload((prev) => prev + acceptedFiles.length);

      // Parallel upload with semaphore-based concurrency control
      // Higher limit for better throughput with 20-200 images
      const CONCURRENCY_LIMIT = 8;

      // Use atomic index counter to avoid race conditions
      let currentIndex = 0;
      const files = acceptedFiles;

      const getNextFile = (): { file: File; index: number } | null => {
        const index = currentIndex++;
        if (index >= files.length) return null;
        return { file: files[index], index };
      };

      const uploadFile = async (file: File, index: number) => {
        const fileId = `upload-${index}-${file.name}`;
        setUploadingFiles((prev) => new Map(prev).set(fileId, 0));

        try {
          await uploadMutation.mutateAsync(file);
        } catch (err) {
          console.error(`Upload failed for ${file.name}:`, err);
          const errorMessage = err instanceof Error ? err.message : "Unknown error";
          setFailedUploads((prev) => [...prev, { name: file.name, error: errorMessage }]);
        } finally {
          setFilesUploaded((prev) => prev + 1);
          setUploadingFiles((prev) => {
            const next = new Map(prev);
            next.delete(fileId);
            return next;
          });
        }
      };

      // Worker function that processes files until none remain
      const worker = async () => {
        let next = getNextFile();
        while (next) {
          await uploadFile(next.file, next.index);
          next = getNextFile();
        }
      };

      // Start workers up to concurrency limit
      const workerCount = Math.min(CONCURRENCY_LIMIT, files.length);
      const workers = Array(workerCount).fill(null).map(() => worker());

      await Promise.all(workers);
    },
    [uploadMutation]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "image/tiff": [".tif", ".tiff"],
      "image/png": [".png"],
      "image/jpeg": [".jpg", ".jpeg"],
    },
    multiple: true,
    disabled: phase === "processing",
  });

  const handleProcess = () => {
    batchProcessMutation.mutate();
  };

  const handleViewImages = () => {
    queryClient.invalidateQueries({ queryKey: ["crops", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["images", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["fovs", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["embedding-status"] });
    queryClient.invalidateQueries({ queryKey: ["umap"] });
    router.push(`/dashboard/experiments/${experimentId}`);
  };

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

  const isBlocked = phase === "uploading" || phase === "processing";
  const canProcess = phase === "uploaded" && allPhase1Complete && uploadedImageIds.length > 0;

  return (
    <div className="space-y-8 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link
          href={isBlocked ? "#" : `/dashboard/experiments/${experimentId}`}
          className={`p-2 rounded-lg transition-colors ${
            isBlocked ? "cursor-not-allowed opacity-50" : "hover:bg-white/5"
          }`}
          onClick={(e) => {
            if (isBlocked) e.preventDefault();
          }}
        >
          <ArrowLeft className="w-5 h-5 text-text-secondary" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-display font-bold text-text-primary">
            Upload Images
          </h1>
          <p className="text-text-secondary mt-1">to {experiment.name}</p>
        </div>
      </div>

      {/* Upload Card */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-6"
      >
        {/* Dropzone - always visible */}
        <div
          {...getRootProps()}
          className={`border-2 border-dashed rounded-xl p-8 text-center transition-all ${
            phase === "processing"
              ? "opacity-50 cursor-not-allowed border-white/5"
              : isDragActive
              ? "border-primary-500 bg-primary-500/10 cursor-pointer"
              : "border-white/10 hover:border-primary-500/50 hover:bg-white/5 cursor-pointer"
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
            <p className="text-text-secondary">or click to browse</p>
            <p className="text-text-muted text-sm mt-2">
              Supported: TIFF (Z-stack), PNG, JPEG
            </p>
          </div>
        </div>

        {/* Upload progress */}
        {phase === "uploading" && totalFilesToUpload > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6 space-y-3"
          >
            <div className="flex items-center justify-between text-sm">
              <div className="flex items-center gap-2">
                <Loader2 className="w-4 h-4 text-primary-500 animate-spin" />
                <span className="text-text-primary">
                  Uploading {uploadingFiles.size > 1 ? `${uploadingFiles.size} files in parallel` : "file"}...
                </span>
              </div>
              <span className="text-text-muted">
                {filesUploaded} / {totalFilesToUpload} ({uploadProgress}%)
              </span>
            </div>
            {/* Progress bar */}
            <div className="relative h-2 bg-bg-secondary rounded-full overflow-hidden">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${uploadProgress}%` }}
                transition={{ duration: 0.3 }}
                className="absolute inset-y-0 left-0 bg-gradient-to-r from-primary-500 to-primary-400 rounded-full"
              />
            </div>
            {/* Currently uploading files */}
            {uploadingFiles.size > 0 && (
              <div className="text-xs text-text-muted">
                {uploadingFiles.size <= 3 ? (
                  <span>
                    Current: {Array.from(uploadingFiles.keys()).map(k => k.split("-").slice(2).join("-")).join(", ")}
                  </span>
                ) : (
                  <span>
                    {uploadingFiles.size} concurrent uploads active
                  </span>
                )}
              </div>
            )}
          </motion.div>
        )}

        {/* Failed uploads */}
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

      {/* Configuration Section - visible after all file uploads complete */}
      {phase === "uploaded" && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass-card p-6 overflow-visible relative z-20"
        >
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-4">
            Processing Settings
          </h3>

          <div className="space-y-4">
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
              <Scan
                className={`w-5 h-5 ${
                  detectCells ? "text-primary-400" : "text-text-muted"
                }`}
              />
              <div className="text-left flex-1">
                <p
                  className={`text-sm font-medium ${
                    detectCells ? "text-primary-400" : "text-text-secondary"
                  }`}
                >
                  Detect and crop cells
                </p>
                <p className="text-xs text-text-muted">
                  {detectCells
                    ? "YOLO detection will find cells in each image"
                    : "Images will be saved as FOV (Field of View) only"}
                </p>
              </div>
            </button>

            {/* Process button */}
            {allPhase1Complete && (
              <button
                onClick={handleProcess}
                disabled={!canProcess || batchProcessMutation.isPending}
                className="btn-primary w-full flex items-center justify-center gap-2 py-3"
              >
                {batchProcessMutation.isPending ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <Play className="w-5 h-5" />
                )}
                Process {uploadedImageIds.length} Images
              </button>
            )}

            {/* Batch process error message */}
            {batchProcessMutation.isError && (
              <div className="p-3 bg-accent-red/10 border border-accent-red/20 rounded-lg">
                <p className="text-accent-red text-sm">
                  {batchProcessMutation.error?.message || "Failed to start processing. Please try again."}
                </p>
              </div>
            )}

            {/* Upload images error message */}
            {uploadedImagesError && (
              <div className="p-3 bg-accent-red/10 border border-accent-red/20 rounded-lg">
                <p className="text-accent-red text-sm">
                  Failed to fetch image status: {uploadedImagesError.message}
                </p>
              </div>
            )}

            {/* Waiting for uploads message */}
            {!allPhase1Complete && !uploadedImagesError && (
              <div className="flex items-center gap-2 text-sm text-text-muted">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Waiting for uploads to complete...</span>
              </div>
            )}
          </div>
        </motion.div>
      )}

      {/* Processing status with progress bar - above gallery */}
      {phase === "processing" && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass-card p-4 space-y-3"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Loader2 className="w-5 h-5 text-accent-amber animate-spin" />
              <div>
                <span className="text-accent-amber font-medium">
                  Processing images...
                </span>
                <p className="text-xs text-text-muted mt-0.5">
                  {detectCells
                    ? "Running detection and feature extraction"
                    : "Finalizing FOV images"}
                </p>
              </div>
            </div>
            <span className="text-text-muted text-sm">
              {(processingStatus?.total ?? uploadedImageIds.length) - (processingStatus?.processing ?? uploadedImageIds.length)} / {processingStatus?.total ?? uploadedImageIds.length} ({processingProgress}%)
            </span>
          </div>
          {/* Processing progress bar */}
          <div className="relative h-2 bg-bg-primary/50 rounded-full overflow-hidden">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${processingProgress}%` }}
              transition={{ duration: 0.3 }}
              className="absolute inset-y-0 left-0 bg-gradient-to-r from-accent-amber to-accent-amber/80 rounded-full"
            />
          </div>
        </motion.div>
      )}

      {/* Done message - above gallery */}
      {phase === "done" && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass-card p-4"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <CheckCircle className="w-5 h-5 text-primary-400" />
              <span className="text-primary-400">
                {uploadedImageIds.length} images processed successfully
                {processingStatus?.errors != null && processingStatus.errors > 0 && (
                  <span className="text-red-400 ml-2">
                    ({processingStatus.errors} error{processingStatus.errors !== 1 ? "s" : ""})
                  </span>
                )}
              </span>
            </div>
            <button onClick={handleViewImages} className="btn-primary">
              View Images
            </button>
          </div>
        </motion.div>
      )}

      {/* Thumbnail Grid - visible after upload starts */}
      {uploadedImageIds.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass-card p-6 relative z-10"
        >
          <h2 className="text-lg font-display font-semibold text-text-primary mb-4">
            Uploaded Images ({uploadedImageIds.length})
          </h2>

          {/* Thumbnail grid */}
          <div className="grid grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-3">
            {uploadedImages?.map((img) => (
              <div
                key={img.id}
                className="aspect-square bg-bg-secondary rounded-lg overflow-hidden relative"
              >
                {img.status === "UPLOADING" || img.status === "PROCESSING" ? (
                  <div className="absolute inset-0 flex items-center justify-center bg-bg-secondary">
                    <Loader2 className="w-6 h-6 text-primary-500 animate-spin" />
                  </div>
                ) : img.status === "ERROR" ? (
                  <div
                    className="absolute inset-0 flex flex-col items-center justify-center bg-accent-red/10 cursor-help"
                    title={img.error_message || "Processing failed"}
                  >
                    <AlertCircle className="w-6 h-6 text-accent-red" />
                    <span className="text-xs text-accent-red mt-1 px-1 text-center truncate max-w-full">
                      {img.error_message ? (img.error_message.length > 30 ? img.error_message.slice(0, 30) + "..." : img.error_message) : "Error"}
                    </span>
                  </div>
                ) : (
                  <MicroscopyImage
                    src={api.getImageUrl(img.id, "thumbnail")}
                    alt={img.original_filename}
                    className="w-full h-full object-cover"
                    onError={(e) => {
                      console.warn(`[Upload] Thumbnail load failed for image: ${img.original_filename}`, e.type);
                      e.currentTarget.style.display = "none";
                      e.currentTarget.nextElementSibling?.classList.remove("hidden");
                    }}
                  />
                )}
                <ImageIcon className="w-8 h-8 text-text-muted hidden absolute inset-0 m-auto" />

                {/* Status badge */}
                {img.status !== "UPLOADED" && img.status !== "READY" && (
                  <div className="absolute bottom-1 right-1">
                    {img.status === "DETECTING" && (
                      <span className="text-xs bg-accent-amber/80 text-white px-1.5 py-0.5 rounded">
                        Detecting
                      </span>
                    )}
                    {img.status === "EXTRACTING_FEATURES" && (
                      <span className="text-xs bg-accent-blue/80 text-white px-1.5 py-0.5 rounded">
                        Features
                      </span>
                    )}
                  </div>
                )}
              </div>
            )) ?? (
              // Placeholder while loading
              uploadedImageIds.map((id) => (
                <div
                  key={id}
                  className="aspect-square bg-bg-secondary rounded-lg flex items-center justify-center"
                >
                  <Loader2 className="w-6 h-6 text-primary-500 animate-spin" />
                </div>
              ))
            )}
          </div>
        </motion.div>
      )}

      {/* Actions */}
      <div className="flex justify-between items-center">
        <Link
          href={isBlocked ? "#" : `/dashboard/experiments/${experimentId}`}
          className={`transition-colors ${
            isBlocked
              ? "text-text-muted cursor-not-allowed"
              : "text-text-secondary hover:text-text-primary"
          }`}
          onClick={(e) => {
            if (isBlocked) e.preventDefault();
          }}
        >
          Back to gallery
        </Link>
      </div>

    </div>
  );
}
