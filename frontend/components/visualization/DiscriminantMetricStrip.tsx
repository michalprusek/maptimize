"use client";

/**
 * What the discriminant separation is worth, rendered beside the plot.
 *
 * Not decoration. A supervised projection separates its classes by
 * construction, so the picture alone would manufacture a finding — this strip
 * exists so a reader cannot see the separation without seeing the score, the
 * chance level and the shuffled-label null next to it. It is therefore
 * unconditionally present in LDA mode, including while the fit is still
 * running and when it has failed.
 */
import { motion } from "framer-motion";
import { AlertCircle, RefreshCw } from "lucide-react";

import type { DiscriminantMetrics } from "@/lib/api";
import { Spinner } from "@/components/ui";
import {
  discriminantVerdict,
  formatMetricValue,
  getDiscriminantScoreStyle,
  separationRatio,
} from "./discriminantMetrics";
import type { Translate } from "./projectionShared";

const VERDICT_LABEL = {
  none: "verdictNone",
  weak: "verdictWeak",
  clear: "verdictClear",
} as const;

function Shell({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className="mb-3 px-3 py-2 rounded-md bg-bg-secondary/60 border border-white/5"
    >
      {children}
    </motion.div>
  );
}

export function DiscriminantMetricStrip({
  metrics,
  isComputing,
  computeError,
  onRetry,
  isRetrying = false,
  t,
}: {
  metrics: DiscriminantMetrics | null;
  isComputing: boolean;
  computeError: string | null;
  onRetry: () => void;
  isRetrying?: boolean;
  t: Translate;
}): JSX.Element {
  if (computeError) {
    return (
      <Shell>
        <div className="flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-accent-red flex-shrink-0" />
          <span className="text-xs text-text-secondary flex-1">
            {t("metricsFailed", { detail: computeError })}
          </span>
          <button
            onClick={onRetry}
            disabled={isRetrying}
            className="text-xs underline text-text-secondary hover:text-text-primary disabled:opacity-50 inline-flex items-center gap-1"
          >
            <RefreshCw className={`w-3 h-3 ${isRetrying ? "animate-spin" : ""}`} />
            {t("retry")}
          </button>
        </div>
      </Shell>
    );
  }

  if (!metrics) {
    return (
      <Shell>
        <div className="flex items-center gap-2">
          {isComputing && <Spinner size="sm" />}
          <span className="text-xs text-text-secondary">
            {isComputing ? t("metricsComputing") : t("metricsUnavailable")}
          </span>
        </div>
      </Shell>
    );
  }

  const ratio = separationRatio(metrics);
  const verdict = discriminantVerdict(metrics);

  return (
    <Shell>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span
          className={`px-2 py-0.5 rounded font-mono text-base font-semibold ${getDiscriminantScoreStyle(metrics)}`}
          title={
            ratio === null
              ? undefined
              : t("separationRatioTooltip", { ratio: ratio.toFixed(1) })
          }
        >
          {t("separation")} {formatMetricValue(metrics.balanced_accuracy)}
        </span>
        <span className="text-xs text-text-secondary">{t(VERDICT_LABEL[verdict])}</span>
        <span className="text-xs text-text-muted">
          {t("chanceLevel")} {formatMetricValue(metrics.chance)}
        </span>
        <span className="text-xs text-text-muted">
          {t("shuffledLabels")} {formatMetricValue(metrics.null_mean)}
        </span>
        {isComputing && <Spinner size="sm" />}
      </div>
      <p className="text-xs text-text-muted mt-1">{t("metricCaption")}</p>
      <p className="text-[11px] text-text-muted">
        {t("metricDetail", {
          proteins: metrics.n_proteins,
          experiments: metrics.n_experiments,
          permutations: metrics.n_permutations,
          nullMax: formatMetricValue(metrics.null_max),
        })}
      </p>
    </Shell>
  );
}
