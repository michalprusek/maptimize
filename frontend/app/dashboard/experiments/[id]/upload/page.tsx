"use client";

import { useState, useCallback } from "react";
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
} from "lucide-react";

export default function UploadPage() {
  const params = useParams();
  const router = useRouter();
  const experimentId = Number(params.id);
  const queryClient = useQueryClient();

  const [selectedProtein, setSelectedProtein] = useState<number | undefined>();
  const [detectCells, setDetectCells] = useState(true);
  const [uploadingFiles, setUploadingFiles] = useState<Map<string, number>>(new Map());
  const [uploadedCount, setUploadedCount] = useState(0);

  const { data: experiment, isLoading: expLoading } = useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () => api.getExperiment(experimentId),
  });

  const { data: proteins } = useQuery({
    queryKey: ["proteins"],
    queryFn: () => api.getProteins(),
  });

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      return api.uploadImage(experimentId, file, selectedProtein, detectCells);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["images", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
      setUploadedCount((prev) => prev + 1);
    },
  });

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      for (const file of acceptedFiles) {
        const fileId = `${file.name}-${Date.now()}`;
        setUploadingFiles((prev) => new Map(prev).set(fileId, 0));

        try {
          await uploadMutation.mutateAsync(file);
        } catch (err) {
          console.error("Upload failed:", err);
        } finally {
          setUploadingFiles((prev) => {
            const next = new Map(prev);
            next.delete(fileId);
            return next;
          });
        }
      }
    },
    [uploadMutation, selectedProtein, detectCells]
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

  return (
    <div className="space-y-8 max-w-3xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link
          href={`/dashboard/experiments/${experimentId}`}
          className="p-2 hover:bg-white/5 rounded-lg transition-colors"
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
        {/* Protein selector */}
        <div className="mb-6">
          <label className="block text-sm font-medium text-text-secondary mb-2">
            Assign MAP Protein (optional)
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => setSelectedProtein(undefined)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                !selectedProtein
                  ? "bg-primary-500/20 text-primary-400 border border-primary-500/30"
                  : "bg-bg-secondary text-text-secondary hover:bg-bg-hover"
              }`}
            >
              None
            </button>
            {proteins?.map((p) => (
              <button
                key={p.id}
                onClick={() => setSelectedProtein(p.id)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                  selectedProtein === p.id
                    ? "bg-primary-500/20 text-primary-400 border border-primary-500/30"
                    : "bg-bg-secondary text-text-secondary hover:bg-bg-hover"
                }`}
                style={{
                  borderColor: selectedProtein === p.id ? p.color : undefined,
                }}
              >
                {p.name}
              </button>
            ))}
          </div>
        </div>

        {/* Cell detection toggle */}
        <div className="mb-6">
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
                Detect cells
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

        {/* Success message */}
        {uploadedCount > 0 && uploadingFiles.size === 0 && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6 p-4 bg-primary-500/10 border border-primary-500/20 rounded-lg flex items-center gap-3"
          >
            <CheckCircle className="w-5 h-5 text-primary-400" />
            <span className="text-primary-400">
              {uploadedCount} {uploadedCount === 1 ? "image" : "images"} uploaded successfully
            </span>
          </motion.div>
        )}
      </motion.div>

      {/* Actions */}
      <div className="flex justify-between items-center">
        <Link
          href={`/dashboard/experiments/${experimentId}`}
          className="text-text-secondary hover:text-text-primary transition-colors"
        >
          Back to gallery
        </Link>
        {uploadedCount > 0 && (
          <Link
            href={`/dashboard/experiments/${experimentId}`}
            className="btn-primary"
          >
            View Images
          </Link>
        )}
      </div>
    </div>
  );
}
