"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  Upload,
  Loader2,
  CheckCircle,
  AlertCircle,
  AlertTriangle,
  FileArchive,
  Image,
  Grid,
  Database,
  Layers,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { modalOverlayAnimation, modalContentAnimation } from "@/lib/animations";
import {
  api,
  type ImportFormat,
  type ImportValidationResult,
  type ImportStatusResponse,
} from "@/lib/api";

interface ImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImportComplete?: (experimentId: number) => void;
}

type ImportStatus = "idle" | "validating" | "validated" | "importing" | "completed" | "error";

/** Maps format to i18n key suffix */
const FORMAT_KEYS: Record<ImportFormat, string> = {
  maptimize: "Maptimize",
  coco: "Coco",
  yolo: "Yolo",
  voc: "Voc",
  csv: "Csv",
};

export function ImportModal({
  isOpen,
  onClose,
  onImportComplete,
}: ImportModalProps): React.ReactNode {
  const t = useTranslations("exportImport");
  const tCommon = useTranslations("common");
  const [mounted, setMounted] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // File state
  const [file, setFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  // Validation state
  const [status, setStatus] = useState<ImportStatus>("idle");
  const [validationResult, setValidationResult] = useState<ImportValidationResult | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  // Import options
  const [experimentName, setExperimentName] = useState("");
  const [createCrops, setCreateCrops] = useState(true);

  // Import result
  const [importResult, setImportResult] = useState<ImportStatusResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Reset when modal opens
  useEffect(() => {
    if (isOpen) {
      setFile(null);
      setStatus("idle");
      setValidationResult(null);
      setJobId(null);
      setExperimentName("");
      setCreateCrops(true);
      setImportResult(null);
      setErrorMessage(null);
    }
  }, [isOpen]);

  // Keyboard handling
  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === "Escape" && status !== "validating" && status !== "importing") {
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

  // Handle file selection
  const handleFileSelect = async (selectedFile: File) => {
    if (!selectedFile.name.toLowerCase().endsWith(".zip")) {
      setErrorMessage(t("zipOnlyError"));
      return;
    }

    setFile(selectedFile);
    setStatus("validating");
    setErrorMessage(null);

    try {
      const result = await api.validateImport(selectedFile);
      setValidationResult(result);
      setJobId(result.job_id);

      // Set default experiment name
      const defaultName = `${t("experimentNamePlaceholder")} ${new Date().toISOString().split("T")[0]}`;
      setExperimentName(defaultName);

      setStatus("validated");
    } catch (error) {
      console.error("Validation failed:", error);
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : t("validationFailed"));
    }
  };

  // Handle drag and drop
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);

    const droppedFile = e.dataTransfer.files[0];
    if (droppedFile) {
      handleFileSelect(droppedFile);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  // Handle import execution
  const handleImport = async () => {
    if (!validationResult || !experimentName.trim()) return;

    try {
      setStatus("importing");
      setErrorMessage(null);

      const result = await api.executeImport({
        job_id: jobId!,
        experiment_name: experimentName.trim(),
        import_as_format: validationResult.detected_format,
        create_crops_from_bboxes: createCrops,
      });

      setImportResult(result);
      setStatus("completed");

      if (result.experiment_id && onImportComplete) {
        onImportComplete(result.experiment_id);
      }
    } catch (error) {
      console.error("Import failed:", error);
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : t("importError"));
    }
  };

  // Derived state
  const isProcessing = status === "validating" || status === "importing";

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
            aria-labelledby="import-modal-title"
          >
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
              <h2
                id="import-modal-title"
                className="text-xl font-display font-semibold text-text-primary flex items-center gap-2"
              >
                <Upload className="w-5 h-5" />
                {t("importTitle")}
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

            {/* Dropzone */}
            {status === "idle" || status === "error" ? (
              <div
                onDrop={handleDrop}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onClick={() => fileInputRef.current?.click()}
                className={`
                  mb-6 p-8 rounded-lg border-2 border-dashed cursor-pointer
                  transition-colors flex flex-col items-center justify-center gap-3
                  ${
                    isDragOver
                      ? "border-primary-500 bg-primary-500/10"
                      : "border-white/20 hover:border-white/40 bg-bg-secondary/50"
                  }
                `}
              >
                <FileArchive
                  className={`w-12 h-12 ${isDragOver ? "text-primary-400" : "text-text-muted"}`}
                />
                <div className="text-center">
                  <p className="text-text-primary">
                    {isDragOver ? t("dropZoneActive") : t("dropZone")}
                  </p>
                  <p className="text-sm text-text-muted mt-1">{t("selectFiles")}</p>
                </div>
                <p className="text-xs text-text-muted">{t("supportedFormats")}</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".zip"
                  onChange={(e) => {
                    const selectedFile = e.target.files?.[0];
                    if (selectedFile) handleFileSelect(selectedFile);
                  }}
                  className="hidden"
                />
              </div>
            ) : null}

            {/* Validating state */}
            {status === "validating" && (
              <div className="mb-6 p-8 rounded-lg bg-bg-secondary/50 flex flex-col items-center justify-center gap-3">
                <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
                <p className="text-text-primary">{t("validating")}</p>
                <p className="text-sm text-text-muted">{file?.name}</p>
              </div>
            )}

            {/* Validation result */}
            {status === "validated" && validationResult && (
              <>
                {/* Format detection */}
                <div className="mb-6 p-4 rounded-lg bg-bg-secondary/50 border border-white/5">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-sm text-text-muted">{t("detectedFormat")}:</span>
                    <span className="text-sm font-mono text-primary-400">
                      {t(`format${FORMAT_KEYS[validationResult.detected_format]}`)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {validationResult.is_valid ? (
                      <>
                        <CheckCircle className="w-4 h-4 text-accent-green" />
                        <span className="text-sm text-accent-green">{t("validStructure")}</span>
                      </>
                    ) : (
                      <>
                        <AlertCircle className="w-4 h-4 text-accent-red" />
                        <span className="text-sm text-accent-red">{t("invalidStructure")}</span>
                      </>
                    )}
                  </div>
                </div>

                {/* Contents summary */}
                <div className="mb-6 p-4 rounded-lg bg-bg-secondary/50 border border-white/5">
                  <h3 className="text-sm font-medium text-text-secondary mb-3">
                    {t("contents")}:
                  </h3>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div className="flex items-center gap-2">
                      <Image className="w-4 h-4 text-text-muted" />
                      <span className="text-text-primary">
                        {t("imageCount", { count: validationResult.image_count })}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Grid className="w-4 h-4 text-text-muted" />
                      <span className="text-text-primary">
                        {t("annotationCount", { count: validationResult.annotation_count })}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Database className="w-4 h-4 text-text-muted" />
                      <span className={validationResult.has_embeddings ? "text-text-primary" : "text-text-muted"}>
                        {validationResult.has_embeddings ? t("embeddings") : t("noEmbeddings")}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Layers className="w-4 h-4 text-text-muted" />
                      <span className={validationResult.has_masks ? "text-text-primary" : "text-text-muted"}>
                        {validationResult.has_masks ? t("masks") : t("noMasks")}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Warnings */}
                {validationResult.warnings.length > 0 && (
                  <div className="mb-6 p-4 rounded-lg bg-accent-amber/10 border border-accent-amber/30">
                    <div className="flex items-center gap-2 mb-2">
                      <AlertTriangle className="w-4 h-4 text-accent-amber" />
                      <span className="text-sm font-medium text-accent-amber">
                        {t("warnings")} ({validationResult.warnings.length})
                      </span>
                    </div>
                    <ul className="text-xs text-text-muted space-y-1 max-h-24 overflow-y-auto">
                      {validationResult.warnings.slice(0, 5).map((warning, i) => (
                        <li key={i}>• {warning}</li>
                      ))}
                      {validationResult.warnings.length > 5 && (
                        <li className="text-text-muted">
                          {t("andMore", { count: validationResult.warnings.length - 5 })}
                        </li>
                      )}
                    </ul>
                  </div>
                )}

                {/* Errors */}
                {validationResult.errors.length > 0 && (
                  <div className="mb-6 p-4 rounded-lg bg-accent-red/10 border border-accent-red/30">
                    <div className="flex items-center gap-2 mb-2">
                      <AlertCircle className="w-4 h-4 text-accent-red" />
                      <span className="text-sm font-medium text-accent-red">
                        {t("errors")} ({validationResult.errors.length})
                      </span>
                    </div>
                    <ul className="text-xs text-text-muted space-y-1">
                      {validationResult.errors.map((error, i) => (
                        <li key={i}>• {error}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Import options */}
                {validationResult.is_valid && (
                  <div className="mb-6 space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-text-secondary mb-2">
                        {t("importAsExperiment")}:
                      </label>
                      <input
                        type="text"
                        value={experimentName}
                        onChange={(e) => setExperimentName(e.target.value)}
                        placeholder={t("experimentNamePlaceholder")}
                        className="w-full px-3 py-2 rounded-lg bg-bg-secondary border border-white/10 text-text-primary placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-primary-500"
                      />
                    </div>
                    <label className="flex items-center gap-3 p-3 rounded-lg bg-bg-secondary/50 cursor-pointer hover:bg-bg-secondary">
                      <input
                        type="checkbox"
                        checked={createCrops}
                        onChange={(e) => setCreateCrops(e.target.checked)}
                        className="w-4 h-4 rounded border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500"
                      />
                      <span className="text-sm text-text-primary">
                        {t("createCropsFromBboxes")}
                      </span>
                    </label>
                  </div>
                )}
              </>
            )}

            {/* Importing state */}
            {status === "importing" && (
              <div className="mb-6 p-8 rounded-lg bg-bg-secondary/50 flex flex-col items-center justify-center gap-3">
                <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
                <p className="text-text-primary">{t("importing")}</p>
              </div>
            )}

            {/* Completed state */}
            {status === "completed" && importResult && (
              <div className="mb-6 p-6 rounded-lg bg-accent-green/10 border border-accent-green/30">
                <div className="flex items-center gap-3 mb-4">
                  <CheckCircle className="w-6 h-6 text-accent-green" />
                  <span className="text-lg font-medium text-accent-green">
                    {t("importComplete")}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <span className="text-text-muted">{t("imagesImported")}:</span>
                    <span className="ml-2 text-text-primary font-mono">
                      {importResult.images_imported}
                    </span>
                  </div>
                  <div>
                    <span className="text-text-muted">{t("cropsCreated")}:</span>
                    <span className="ml-2 text-text-primary font-mono">
                      {importResult.crops_created}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Error state */}
            {status === "error" && errorMessage && (
              <div className="mb-6 p-4 rounded-lg bg-accent-red/10 border border-accent-red/30">
                <div className="flex items-center gap-2">
                  <AlertCircle className="w-5 h-5 text-accent-red" />
                  <span className="text-accent-red">{errorMessage}</span>
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 justify-end pt-4 border-t border-white/5">
              <button
                onClick={onClose}
                disabled={isProcessing}
                className="px-4 py-2 text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
              >
                {status === "completed" ? tCommon("close") : tCommon("cancel")}
              </button>
              {status === "validated" && validationResult?.is_valid && (
                <button
                  onClick={handleImport}
                  disabled={!experimentName.trim()}
                  className="px-4 py-2 rounded-lg bg-primary-500 hover:bg-primary-600 text-white font-medium flex items-center gap-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Upload className="w-4 h-4" />
                  {t("import")}
                </button>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body
  );
}
