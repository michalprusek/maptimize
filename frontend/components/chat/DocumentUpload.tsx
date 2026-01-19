"use client";

import { useCallback, useState } from "react";
import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { Upload, FileUp, Loader2, CheckCircle2 } from "lucide-react";
import { useDropzone } from "react-dropzone";
import { clsx } from "clsx";

const ACCEPTED_TYPES = {
  "application/pdf": [".pdf"],
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "image/*": [".png", ".jpg", ".jpeg", ".gif", ".webp"],
};

export function DocumentUpload() {
  const t = useTranslations("chat");
  const { uploadDocument, isUploadingDocument } = useChatStore();
  const [uploadSuccess, setUploadSuccess] = useState(false);

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      for (const file of acceptedFiles) {
        await uploadDocument(file);
      }
      // Show success animation briefly
      setUploadSuccess(true);
      setTimeout(() => setUploadSuccess(false), 2000);
    },
    [uploadDocument]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_TYPES,
    maxSize: 100 * 1024 * 1024, // 100MB
    disabled: isUploadingDocument,
  });

  return (
    <div
      {...getRootProps()}
      className={clsx(
        "border-2 border-dashed rounded-xl p-4 text-center cursor-pointer",
        "transition-all duration-300",
        "bg-white/[0.02]",
        isDragActive
          ? [
              "border-primary-500 bg-primary-500/10",
              "animate-border-pulse",
              "scale-[1.02]",
            ]
          : "border-white/10 hover:border-primary-500/50 hover:bg-white/[0.04]",
        isUploadingDocument && "opacity-50 cursor-not-allowed",
        uploadSuccess && "border-green-500 bg-green-500/10"
      )}
    >
      <input {...getInputProps()} />

      {uploadSuccess ? (
        // Success state
        <div className="flex flex-col items-center gap-2 py-2 animate-scale-in">
          <CheckCircle2 className="w-8 h-8 text-green-400 animate-check-bounce" />
          <span className="text-sm text-green-400">{t("uploadSuccess") || "Uploaded!"}</span>
        </div>
      ) : isUploadingDocument ? (
        // Uploading state
        <div className="flex flex-col items-center gap-2 py-2">
          <Loader2 className="w-8 h-8 animate-spin text-primary-400" />
          <span className="text-sm text-text-secondary">{t("uploading")}</span>
        </div>
      ) : isDragActive ? (
        // Drag active state with bounce animation
        <div className="flex flex-col items-center gap-2 py-2 animate-scale-in">
          <FileUp className="w-8 h-8 text-primary-400 animate-float" />
          <span className="text-sm text-primary-400 font-medium">{t("dropHere")}</span>
        </div>
      ) : (
        // Default state
        <div className="flex flex-col items-center gap-2 py-2">
          <Upload className="w-8 h-8 text-text-secondary transition-transform duration-200 group-hover:scale-110" />
          <span className="text-sm text-text-secondary">{t("uploadDocuments")}</span>
          <span className="text-xs text-text-muted">
            PDF, DOCX, PPTX, XLSX, {t("orImages")}
          </span>
        </div>
      )}
    </div>
  );
}
