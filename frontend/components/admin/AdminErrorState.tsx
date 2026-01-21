"use client";

import { AlertCircle, RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";

interface AdminErrorStateProps {
  message?: string;
  onRetry?: () => void;
  height?: string;
  iconSize?: "sm" | "md" | "lg";
}

const iconSizes = {
  sm: "w-8 h-8",
  md: "w-10 h-10",
  lg: "w-12 h-12",
};

export function AdminErrorState({
  message,
  onRetry,
  height = "h-64",
  iconSize = "lg",
}: AdminErrorStateProps): JSX.Element {
  const t = useTranslations("admin");

  return (
    <div className={`flex flex-col items-center justify-center ${height} gap-4`}>
      <AlertCircle className={`${iconSizes[iconSize]} text-accent-red opacity-70`} />
      <p className="text-text-muted">{message || t("errors.loadFailed")}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="btn-secondary inline-flex items-center gap-2"
        >
          <RefreshCw className="w-4 h-4" />
          {t("common.retry")}
        </button>
      )}
    </div>
  );
}
