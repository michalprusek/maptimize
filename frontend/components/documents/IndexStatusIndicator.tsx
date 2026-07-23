"use client";

import { useTranslations } from "next-intl";
import { clsx } from "clsx";
import type { RAGDocument } from "@/lib/api";

/**
 * Per-document indexing status, surfaced as a colored dot:
 *   green  = fully indexed (completed)
 *   yellow = partially indexed / in progress (processing, or pending with progress)
 *   red    = not indexed (failed, or pending with no progress yet)
 */
export type IndexState = "indexed" | "partial" | "notIndexed";

export function getIndexState(doc: Pick<RAGDocument, "status" | "progress">): {
  state: IndexState;
  percent: number;
} {
  const percent = Math.round((doc.progress ?? 0) * 100);
  if (doc.status === "completed") return { state: "indexed", percent: 100 };
  if (doc.status === "processing") return { state: "partial", percent };
  if (doc.status === "pending" && percent > 0) return { state: "partial", percent };
  // failed, or pending with no progress
  return { state: "notIndexed", percent };
}

const DOT_STYLES: Record<IndexState, string> = {
  indexed: "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.6)]",
  partial: "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.6)] animate-pulse-soft",
  notIndexed: "bg-rose-500 shadow-[0_0_6px_rgba(244,63,94,0.5)]",
};

const RING_STYLES: Record<IndexState, string> = {
  indexed: "ring-emerald-400/30",
  partial: "ring-amber-400/30",
  notIndexed: "ring-rose-500/30",
};

/** A small status dot with an accessible tooltip; optionally shows the % for partial. */
export function IndexStatusDot({
  doc,
  showPercent = false,
  className,
}: {
  doc: Pick<RAGDocument, "status" | "progress">;
  showPercent?: boolean;
  className?: string;
}) {
  const t = useTranslations("folders");
  const { state, percent } = getIndexState(doc);

  const label =
    state === "indexed"
      ? t("statusIndexed")
      : state === "partial"
        ? t("statusPartial", { percent })
        : t("statusNotIndexed");

  return (
    <span className={clsx("inline-flex items-center gap-1.5", className)} title={label}>
      <span
        className={clsx(
          "w-2.5 h-2.5 rounded-full ring-2 flex-shrink-0",
          DOT_STYLES[state],
          RING_STYLES[state]
        )}
        aria-label={label}
        role="img"
      />
      {showPercent && state === "partial" && (
        <span className="text-[11px] tabular-nums text-amber-400">{percent}%</span>
      )}
    </span>
  );
}

/** Compact legend explaining the three status colors. */
export function IndexStatusLegend() {
  const t = useTranslations("folders");
  const items: Array<{ state: IndexState; label: string }> = [
    { state: "indexed", label: t("legendIndexed") },
    { state: "partial", label: t("legendPartial") },
    { state: "notIndexed", label: t("legendNotIndexed") },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-text-muted">
      {items.map(({ state, label }) => (
        <span key={state} className="inline-flex items-center gap-1.5">
          <span className={clsx("w-2 h-2 rounded-full", DOT_STYLES[state])} />
          {label}
        </span>
      ))}
    </div>
  );
}
