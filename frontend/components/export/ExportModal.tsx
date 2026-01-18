"use client";

import { useCallback, useEffect, useState, useRef, useMemo } from "react";
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
  Box,
  Check,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { modalOverlayAnimation, modalContentAnimation } from "@/lib/animations";
import { formatBytes } from "@/lib/utils";
import {
  api,
  type Experiment,
  type ExportOptions,
  type BBoxFormat,
  type MaskFormat,
  type ExportPrepareResponse,
} from "@/lib/api";

interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  experiments: Experiment[];
  preSelectedIds?: number[];
}

type ExportStatus = "idle" | "preparing" | "downloading" | "completed" | "error";

// Styled checkbox component
function StyledCheckbox({
  checked,
  onChange,
  label,
  icon: Icon,
  small = false,
  disabled = false,
  disabledTooltip,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  icon?: React.ComponentType<{ className?: string }>;
  small?: boolean;
  disabled?: boolean;
  disabledTooltip?: string;
}) {
  return (
    <div
      className={`flex items-center gap-3 select-none ${small ? "py-1" : "p-3"} ${
        disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer group"
      }`}
      onClick={() => !disabled && onChange(!checked)}
      title={disabled ? disabledTooltip : undefined}
    >
      <div
        className={`relative flex items-center justify-center rounded-md border-2 transition-all duration-200 flex-shrink-0 ${
          small ? "w-4 h-4" : "w-5 h-5"
        } ${
          checked
            ? "bg-primary-500 border-primary-500"
            : disabled
              ? "bg-transparent border-white/20"
              : "bg-transparent border-white/30 group-hover:border-white/50"
        }`}
      >
        {checked && <Check className={small ? "w-3 h-3 text-white" : "w-3.5 h-3.5 text-white"} strokeWidth={3} />}
      </div>
      {Icon && <Icon className={`${small ? "w-3.5 h-3.5" : "w-4 h-4"} text-text-muted flex-shrink-0`} />}
      <span className={`${small ? "text-xs text-text-secondary" : "text-sm text-text-primary"}`}>{label}</span>
    </div>
  );
}

// Styled radio button component
function StyledRadio({
  checked,
  onChange,
  label,
  name,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
  name: string;
}) {
  return (
    <div
      className="flex items-center gap-2.5 cursor-pointer group py-1.5 select-none"
      onClick={onChange}
    >
      <div
        className={`relative w-4 h-4 rounded-full border-2 transition-all duration-200 flex items-center justify-center flex-shrink-0 ${
          checked
            ? "border-primary-500"
            : "border-white/30 group-hover:border-white/50"
        }`}
      >
        {checked && (
          <div className="w-2 h-2 rounded-full bg-primary-500" />
        )}
      </div>
      <span className="text-xs text-text-secondary group-hover:text-text-primary transition-colors">{label}</span>
    </div>
  );
}

export function ExportModal({
  isOpen,
  onClose,
  experiments,
  preSelectedIds,
}: ExportModalProps): React.ReactNode {
  const t = useTranslations("exportImport");
  const tCommon = useTranslations("common");
  const [mounted, setMounted] = useState(false);

  // Track previous isOpen state to detect modal opening
  const prevIsOpenRef = useRef(isOpen);

  // Memoize experiment IDs to avoid reference changes
  const experimentIds = useMemo(() => experiments.map((e) => e.id), [experiments]);
  const stablePreSelectedIds = useMemo(() => preSelectedIds ?? [], [preSelectedIds]);

  // Selection state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => {
    const initial = stablePreSelectedIds.length > 0 ? stablePreSelectedIds : experimentIds;
    return new Set(initial);
  });

  // Export options
  const [includeFovImages, setIncludeFovImages] = useState(true);
  const [includeMipProjections, setIncludeMipProjections] = useState(true);
  const [includeSumProjections, setIncludeSumProjections] = useState(true);
  const [includeCropImages, setIncludeCropImages] = useState(true);
  const [includeEmbeddings, setIncludeEmbeddings] = useState(true);
  const [includeMasks, setIncludeMasks] = useState(true);
  const [maskFormat, setMaskFormat] = useState<MaskFormat>("png");
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

  // Reset when modal opens (transition from closed to open)
  useEffect(() => {
    const wasOpen = prevIsOpenRef.current;
    prevIsOpenRef.current = isOpen;

    // Only reset when modal is opening (was closed, now open)
    if (isOpen && !wasOpen) {
      setStatus("idle");
      setPrepareResponse(null);
      setErrorMessage(null);
      const initialIds = stablePreSelectedIds.length > 0 ? stablePreSelectedIds : experimentIds;
      setSelectedIds(new Set(initialIds));
    }
  }, [isOpen, experimentIds, stablePreSelectedIds]);

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
        mask_format: maskFormat,
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
  const hasSumProjections = selectedExperiments.some((e) => e.has_sum_projections);

  // Auto-uncheck SUM when no selected experiments have sum projections
  useEffect(() => {
    if (!hasSumProjections && includeSumProjections) {
      setIncludeSumProjections(false);
    }
  }, [hasSumProjections, includeSumProjections]);

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
                  <div className="rounded-lg bg-bg-secondary/50 hover:bg-bg-secondary/70 transition-colors">
                    <StyledCheckbox
                      checked={includeFovImages}
                      onChange={handleFovImagesChange}
                      label={t("fovImages")}
                      icon={Image}
                    />
                    {includeFovImages && (
                      <div className="pl-11 pb-3 pr-3 space-y-1">
                        <StyledCheckbox
                          checked={includeMipProjections}
                          onChange={handleMipChange}
                          label={t("mipProjections")}
                          small
                        />
                        <StyledCheckbox
                          checked={includeSumProjections}
                          onChange={handleSumChange}
                          label={t("sumProjections")}
                          small
                          disabled={!hasSumProjections}
                          disabledTooltip={t("noSumProjectionsAvailable")}
                        />
                      </div>
                    )}
                  </div>

                  {/* Masks checkbox */}
                  <div className="rounded-lg bg-bg-secondary/50 hover:bg-bg-secondary/70 transition-colors">
                    <StyledCheckbox
                      checked={includeMasks}
                      onChange={setIncludeMasks}
                      label={t("masks")}
                      icon={Layers}
                    />
                  </div>
                </div>

                {/* Crop Images and Embeddings */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-lg bg-bg-secondary/50 hover:bg-bg-secondary/70 transition-colors">
                    <StyledCheckbox
                      checked={includeCropImages}
                      onChange={setIncludeCropImages}
                      label={t("cropImages")}
                      icon={Grid}
                    />
                  </div>
                  <div className="rounded-lg bg-bg-secondary/50 hover:bg-bg-secondary/70 transition-colors">
                    <StyledCheckbox
                      checked={includeEmbeddings}
                      onChange={setIncludeEmbeddings}
                      label={t("embeddings")}
                      icon={Database}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Format Selection - Segmentation and Detection side by side */}
            <div className="mb-6">
              <h3 className="text-sm font-medium text-text-secondary mb-3">
                {t("annotationFormats")}
              </h3>
              <div className="grid grid-cols-2 gap-4">
                {/* Segmentation Format */}
                <div className="rounded-lg bg-bg-secondary/50 p-4">
                  <div className="flex items-center gap-2 mb-3 pb-2 border-b border-white/10">
                    <Layers className="w-4 h-4 text-primary-400" />
                    <span className="text-sm font-medium text-text-primary">{t("maskFormat")}</span>
                  </div>
                  <div className="space-y-1">
                    {(["png", "coco", "coco_rle", "polygon"] as MaskFormat[]).map((format) => (
                      <StyledRadio
                        key={format}
                        name="maskFormat"
                        checked={maskFormat === format}
                        onChange={() => setMaskFormat(format)}
                        label={t(`maskFormat${format.charAt(0).toUpperCase() + format.slice(1).replace(/_([a-z])/g, (_, c) => c.toUpperCase())}`)}
                      />
                    ))}
                  </div>
                </div>

                {/* Detection Format */}
                <div className="rounded-lg bg-bg-secondary/50 p-4">
                  <div className="flex items-center gap-2 mb-3 pb-2 border-b border-white/10">
                    <Box className="w-4 h-4 text-primary-400" />
                    <span className="text-sm font-medium text-text-primary">{t("bboxFormat")}</span>
                  </div>
                  <div className="space-y-1">
                    {(["coco", "yolo", "voc", "csv"] as BBoxFormat[]).map((format) => (
                      <StyledRadio
                        key={format}
                        name="bboxFormat"
                        checked={bboxFormat === format}
                        onChange={() => setBboxFormat(format)}
                        label={t(`bboxFormat${format.charAt(0).toUpperCase() + format.slice(1)}`)}
                      />
                    ))}
                  </div>
                </div>
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
