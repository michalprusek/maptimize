"use client";

import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { Loader2 } from "lucide-react";
import { clsx } from "clsx";

export function IndexingProgress() {
  const t = useTranslations("chat");
  const { indexingStatus } = useChatStore();

  if (!indexingStatus) return null;

  const totalPending =
    indexingStatus.documents_pending + indexingStatus.documents_processing;
  const totalCompleted = indexingStatus.documents_completed;
  const total = totalPending + totalCompleted;

  if (totalPending === 0) return null;

  const progress = total > 0 ? (totalCompleted / total) * 100 : 0;

  return (
    <div
      className={clsx(
        "flex items-center gap-3 px-3 py-1.5 rounded-full",
        "bg-amber-500/10 border border-amber-500/20",
        "animate-fade-in"
      )}
    >
      <Loader2 className="w-4 h-4 animate-spin text-amber-400" />
      <span className="text-sm text-amber-400">
        {t("indexing", {
          count: totalPending,
          total: total,
        })}
      </span>
      {/* Progress bar with shimmer effect */}
      <div className="w-16 h-1.5 bg-amber-500/20 rounded-full overflow-hidden relative">
        {/* Shimmer overlay */}
        <div
          className={clsx(
            "absolute inset-0",
            "bg-gradient-to-r from-transparent via-amber-400/30 to-transparent",
            "bg-[length:200%_100%] animate-shimmer-subtle"
          )}
        />
        {/* Actual progress */}
        <div
          className="h-full bg-amber-500 transition-all duration-500 ease-out relative z-10"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}
