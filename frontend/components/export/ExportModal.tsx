"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  Download,
  Loader2,
  CheckCircle,
  AlertCircle,
  Image,
  Grid,
  Database,
  Layers,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { modalOverlayAnimation, modalContentAnimation } from "@/lib/animations";
import { formatBytes } from "@/lib/utils";
import {
  api,
  type Experiment,
  type ExportOptions,
  type BBoxFormat,
  type ExportPrepareResponse,
} from "@/lib/api";

interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  experiments: Experiment[];
  preSelectedIds?: number[];
}

type ExportStatus = "idle" | "preparing" | "downloading" | "completed" | "error";

export function ExportModal({
  isOpen,
  onClose,
  experiments,
  preSelectedIds = [],
}: ExportModalProps): React.ReactNode {
  const t = useTranslations("exportImport");
  const tCommon = useTranslations("common");
  const [mounted, setMounted] = useState(false);

  // Selection state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(
    new Set(preSelectedIds.length > 0 ? preSelectedIds : experiments.map((e) => e.id))
  );

  // Export options
  const [includeFovImages, setIncludeFovImages] = useState(true);
  const [includeMipProjections, setIncludeMipProjections] = useState(true);
  const [includeSumProjections, setIncludeSumProjections] = useState(true);
  const [includeCropImages, setIncludeCropImages] = useState(true);
  const [includeEmbeddings, setIncludeEmbeddings] = useState(true);
  const [includeMasks, setIncludeMasks] = useState(true);
  const [bboxFormat, setBboxFormat] = useState<BBoxFormat>("coco");

  // Auto-uncheck FOV images when both projections are unchecked
  const handleMipChange = (checked: boolean) => {
    setIncludeMipProjections(checked);
    if (!checked && !includeSumProjections) {
      setIncludeFovImages(false);
    }
  };

  const handleSumChange = (checked: boolean) => {
    setIncludeSumProjections(checked);
    if (!checked && !includeMipProjections) {
      setIncludeFovImages(false);
    }
  };

  const handleFovImagesChange = (checked: boolean) => {
    setIncludeFovImages(checked);
    // When enabling FOV images, enable at least MIP by default
    if (checked && !includeMipProjections && !includeSumProjections) {
      setIncludeMipProjections(true);
      setIncludeSumProjections(true);
    }
  };

  // Export state
  const [status, setStatus] = useState<ExportStatus>("idle");
  const [prepareResponse, setPrepareResponse] = useState<ExportPrepareResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Reset when modal opens
  useEffect(() => {
    if (isOpen) {
      setStatus("idle");
      setPrepareResponse(null);
      setErrorMessage(null);
      const initialIds = preSelectedIds.length > 0 ? preSelectedIds : experiments.map((e) => e.id);
      setSelectedIds(new Set(initialIds));
    }
  }, [isOpen, experiments, preSelectedIds]);

  // Keyboard handling
  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === "Escape" && status !== "preparing" && status !== "downloading") {
        onClose();
      }
    },
    [onClose, status]
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [isOpen, handleKeyDown]);

  // Toggle experiment selection
  const toggleExperiment = (id: number) => {
    setSelectedIds((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  };

  // Select/deselect all
  const toggleAll = () => {
    if (selectedIds.size === experiments.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(experiments.map((e) => e.id)));
    }
  };

  // Handle export
  const handleExport = async () => {
    if (selectedIds.size === 0) return;

    try {
      setStatus("preparing");
      setErrorMessage(null);

      const options: ExportOptions = {
        include_fov_images: includeFovImages,
        include_crop_images: includeCropImages,
        include_embeddings: includeEmbeddings,
        include_masks: includeMasks,
        bbox_format: bboxFormat,
      };

      const response = await api.prepareExport(Array.from(selectedIds), options);
      setPrepareResponse(response);

      // Start download
      setStatus("downloading");
      const downloadUrl = api.getExportStreamUrl(response.job_id);

      // Create invisible link and trigger download
      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = ""; // Let server set filename
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      setStatus("completed");
    } catch (error) {
      console.error("Export failed:", error);
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "Export failed");
    }
  };

  // Derived state
  const selectedExperiments = experiments.filter((e) => selectedIds.has(e.id));
  const totalImages = selectedExperiments.reduce((sum, e) => sum + e.image_count, 0);
  const totalCrops = selectedExperiments.reduce((sum, e) => sum + e.cell_count, 0);
  const isProcessing = status === "preparing" || status === "downloading";

  if (!mounted) return null;

  return createPortal(
    <AnimatePresence>
      {isOpen && (
        <motion.div
          {...modalOverlayAnimation}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
          onClick={onClose}
        >
          <motion.div
            {...modalContentAnimation}
            onClick={(e) => e.stopPropagation()}
            className="glass-card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto"
            role="dialog"
            aria-modal="true"
            aria-labelledby="export-modal-title"
          >
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
              <h2
                id="export-modal-title"
                className="text-xl font-display font-semibold text-text-primary flex items-center gap-2"
              >
                <Download className="w-5 h-5" />
                {t("exportTitle")}
              </h2>
              <button
                onClick={onClose}
                disabled={isProcessing}
                className="p-2 hover:bg-white/10 rounded-lg transition-colors disabled:opacity-50"
                aria-label={tCommon("close")}
              >
                <X className="w-5 h-5 text-text-muted" />
              </button>
            </div>

            {/* Experiment Selection */}
            <div className="mb-6">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium text-text-secondary">
                  {t("selectExperiments")}
                </h3>
                <button
                  onClick={toggleAll}
                  className="text-xs text-primary-400 hover:text-primary-300"
                >
                  {selectedIds.size === experiments.length
                    ? tCommon("deselect")
                    : t("selectAllExperiments")}
                </button>
              </div>
              <div className="space-y-2 max-h-48 overflow-y-auto bg-bg-secondary/50 rounded-lg p-3">
                {experiments.map((exp) => (
                  <label
                    key={exp.id}
                    className="flex items-center gap-3 p-2 rounded hover:bg-white/5 cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={selectedIds.has(exp.id)}
                      onChange={() => toggleExperiment(exp.id)}
                      className="w-4 h-4 rounded border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-text-primary truncate">{exp.name}</span>
                        {exp.map_protein && (
                          <span className="flex items-center gap-1 text-xs text-text-muted">
                            <span
                              className="w-2 h-2 rounded-full flex-shrink-0"
                              style={{ backgroundColor: exp.map_protein.color || "#888" }}
                            />
                            {exp.map_protein.name}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-text-muted">
                        {t("imageCount", { count: exp.image_count })} ·{" "}
                        {t("cropCount", { count: exp.cell_count })}
                      </div>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* Export Options */}
            <div className="mb-6">
              <h3 className="text-sm font-medium text-text-secondary mb-3">
                {t("includeInExport")}
              </h3>
              <div className="space-y-3">
                {/* FOV Images and Masks - side by side with nested options */}
                <div className="grid grid-cols-2 gap-3">
                  {/* FOV Images with nested MIP/SUM options */}
                  <div className="rounded-lg bg-bg-secondary/50">
                    <label className="flex items-center gap-3 p-3 cursor-pointer hover:bg-bg-secondary rounded-t-lg">
                      <input
                        type="checkbox"
                        checked={includeFovImages}
                        onChange={(e) => handleFovImagesChange(e.target.checked)}
                        className="w-4 h-4 rounded border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500"
                      />
                      <Image className="w-4 h-4 text-text-muted" />
                      <span className="text-sm text-text-primary">{t("fovImages")}</span>
                    </label>
                    {includeFovImages && (
                      <div className="pl-10 pb-3 pr-3 space-y-2">
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={includeMipProjections}
                            onChange={(e) => handleMipChange(e.target.checked)}
                            className="w-3.5 h-3.5 rounded border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500"
                          />
                          <span className="text-xs text-text-secondary">{t("mipProjections")}</span>
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={includeSumProjections}
                            onChange={(e) => handleSumChange(e.target.checked)}
                            className="w-3.5 h-3.5 rounded border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500"
                          />
                          <span className="text-xs text-text-secondary">{t("sumProjections")}</span>
                        </label>
                      </div>
                    )}
                  </div>

                  {/* Masks */}
                  <label className="flex items-center gap-3 p-3 rounded-lg bg-bg-secondary/50 cursor-pointer hover:bg-bg-secondary h-fit">
                    <input
                      type="checkbox"
                      checked={includeMasks}
                      onChange={(e) => setIncludeMasks(e.target.checked)}
                      className="w-4 h-4 rounded border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500"
                    />
                    <Layers className="w-4 h-4 text-text-muted" />
                    <span className="text-sm text-text-primary">{t("masks")}</span>
                  </label>
                </div>

                {/* Crop Images and Embeddings */}
                <div className="grid grid-cols-2 gap-3">
                  <label className="flex items-center gap-3 p-3 rounded-lg bg-bg-secondary/50 cursor-pointer hover:bg-bg-secondary">
                    <input
                      type="checkbox"
                      checked={includeCropImages}
                      onChange={(e) => setIncludeCropImages(e.target.checked)}
                      className="w-4 h-4 rounded border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500"
                    />
                    <Grid className="w-4 h-4 text-text-muted" />
                    <span className="text-sm text-text-primary">{t("cropImages")}</span>
                  </label>
                  <label className="flex items-center gap-3 p-3 rounded-lg bg-bg-secondary/50 cursor-pointer hover:bg-bg-secondary">
                    <input
                      type="checkbox"
                      checked={includeEmbeddings}
                      onChange={(e) => setIncludeEmbeddings(e.target.checked)}
                      className="w-4 h-4 rounded border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500"
                    />
                    <Database className="w-4 h-4 text-text-muted" />
                    <span className="text-sm text-text-primary">{t("embeddings")}</span>
                  </label>
                </div>
              </div>
            </div>

            {/* BBox Format */}
            <div className="mb-6">
              <h3 className="text-sm font-medium text-text-secondary mb-3">
                {t("bboxFormat")}
              </h3>
              <div className="grid grid-cols-2 gap-2">
                {(["coco", "yolo", "voc", "csv"] as BBoxFormat[]).map((format) => (
                  <label
                    key={format}
                    className="flex items-center gap-3 p-2 rounded-lg bg-bg-secondary/50 cursor-pointer hover:bg-bg-secondary"
                  >
                    <input
                      type="radio"
                      name="bboxFormat"
                      value={format}
                      checked={bboxFormat === format}
                      onChange={() => setBboxFormat(format)}
                      className="w-4 h-4 border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500"
                    />
                    <span className="text-sm text-text-primary">
                      {t(`bboxFormat${format.charAt(0).toUpperCase() + format.slice(1)}`)}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {/* Summary */}
            <div className="mb-6 p-4 rounded-lg bg-bg-secondary/50 border border-white/5">
              <div className="flex items-center justify-between text-sm">
                <span className="text-text-muted">{t("estimatedSize")}:</span>
                <span className="text-text-primary font-mono">
                  {prepareResponse
                    ? formatBytes(prepareResponse.estimated_size_bytes)
                    : `~${formatBytes(totalImages * 2 * 1024 * 1024 + totalCrops * 100 * 1024)}`}
                </span>
              </div>
              <div className="mt-2 text-xs text-text-muted">
                {t("experimentCount", { count: selectedIds.size })} ·{" "}
                {t("imageCount", { count: totalImages })} ·{" "}
                {t("cropCount", { count: totalCrops })}
              </div>
            </div>

            {/* Status */}
            {status !== "idle" && (
              <div className="mb-6 p-4 rounded-lg border border-white/10">
                {isProcessing && (
                  <div className="flex items-center gap-3 text-primary-400">
                    <Loader2 className="w-5 h-5 animate-spin" />
                    <span>{status === "preparing" ? t("preparing") : t("streaming")}</span>
                  </div>
                )}
                {status === "completed" && (
                  <div className="flex items-center gap-3 text-accent-green">
                    <CheckCircle className="w-5 h-5" />
                    <span>{t("downloadReady")}</span>
                  </div>
                )}
                {status === "error" && (
                  <div className="flex items-center gap-3 text-accent-red">
                    <AlertCircle className="w-5 h-5" />
                    <span>{errorMessage || t("error")}</span>
                  </div>
                )}
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 justify-end pt-4 border-t border-white/5">
              <button
                onClick={onClose}
                disabled={isProcessing}
                className="px-4 py-2 text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
              >
                {tCommon("cancel")}
              </button>
              <button
                onClick={handleExport}
                disabled={selectedIds.size === 0 || isProcessing}
                className="px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white font-medium flex items-center gap-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isProcessing ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    {status === "preparing" ? t("preparing") : t("streaming")}
                  </>
                ) : (
                  <>
                    <Download className="w-4 h-4" />
                    {t("export")}
                  </>
                )}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body
  );
}
