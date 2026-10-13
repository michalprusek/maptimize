"use client";

import type { Bundleness, BundlenessClass } from "@/lib/api";
import {
  bundlenessBadgeForm,
  formatScoredCount,
  formatSigned,
} from "./bundleness";
import type { Translate } from "./projectionShared";

interface BundlenessBadgeProps {
  bundleness: Bundleness | null;
  /** Points on the plot. Zero hides the badge; there is nothing to describe. */
  pointCount: number;
  /** The number still describes the previous request; draw it dimmed. */
  stale: boolean;
  /** Test id for the "no score" form — one per place the badge is drawn. */
  unscoredTestId: string;
  t: Translate;
}

/**
 * Mean predicted bundleness of the plotted crops, with the count it rests on.
 *
 * The sibling of `SeparabilityBadge`, and drawn wherever that one is. Neutral
 * on purpose: separability has a good end, bundleness does not — a higher mean
 * is a finding about the sample, not a better result.
 */
export function BundlenessBadge({
  bundleness,
  pointCount,
  stale,
  unscoredTestId,
  t,
}: BundlenessBadgeProps): JSX.Element | null {
  const form = bundlenessBadgeForm(pointCount, bundleness);
  if (form === "hidden") return null;

  const badgeClass = `px-2 py-0.5 rounded text-xs font-mono ${stale ? "opacity-50" : ""}`;
  const mean = bundleness ? formatSigned(bundleness.mean) : null;

  // Also the fallback for a mean that is not a finite number: printing nothing
  // would hide the badge, and printing "NaN" would look like a measurement.
  if (!bundleness || mean === null) {
    return (
      <span
        className={`${badgeClass} bg-bg-secondary text-text-muted`}
        title={t("bundlenessUnscoredTooltip")}
        data-testid={unscoredTestId}
      >
        {t("bundleness")}
        {": "}
        {t("bundlenessUnscored")}
      </span>
    );
  }

  // A single scored point has no SD; say nothing about spread rather than "±0".
  const { sd, model_version: version } = bundleness;
  const title =
    typeof sd === "number" && Number.isFinite(sd)
      ? t("bundlenessTooltip", { sd: sd.toFixed(2), version })
      : t("bundlenessTooltipNoSd", { version });

  return (
    <span
      className={`${badgeClass} bg-bg-secondary text-text-secondary`}
      title={title}
      data-testid="bundleness-badge"
    >
      {t("bundleness")}: {mean}
      {" · "}
      {t("bundlenessCount", {
        n: formatScoredCount(bundleness.n_scored, bundleness.n_points),
      })}
    </span>
  );
}

/**
 * One class' mean, beside that class in a legend: `+0.64`, muted and mono, with
 * the count behind it in the tooltip. Renders nothing without a printable mean.
 */
export function ClassBundleness({
  cls,
  stale,
  t,
}: {
  cls: BundlenessClass | undefined;
  stale: boolean;
  t: Translate;
}): JSX.Element | null {
  const mean = cls ? formatSigned(cls.mean) : null;
  if (!cls || mean === null) return null;
  return (
    <span
      className={`font-mono text-xs text-text-muted ${stale ? "opacity-50" : ""}`}
      title={t("bundlenessClassTitle", { mean, n: cls.n_scored })}
      data-testid="class-bundleness"
    >
      {mean}
    </span>
  );
}
