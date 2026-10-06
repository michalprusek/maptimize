"use client";

import type { Separability, SeparabilityUnscored } from "@/lib/api";
import { getSeparabilityScoreStyle } from "./chartConfig";
import type { Translate } from "./projectionShared";
import { LABEL_AXIS_KEY, unscoredMessageKey } from "./umapFacets";

interface SeparabilityBadgeProps {
  separability: Separability | null;
  unscored: SeparabilityUnscored | null;
  /** The number still describes the previous request; draw it dimmed. */
  stale: boolean;
  /** Test id for the "no score" form — one per place the badge is drawn. */
  unscoredTestId: string;
  t: Translate;
}

/**
 * The separability score with the counts it cannot be read without, or — when
 * there are points but no score — the reason why.
 *
 * One component for every place the score is shown, so the header and the
 * groups dock cannot print different numbers, or different excuses, for the
 * same plot.
 */
export function SeparabilityBadge({
  separability,
  unscored,
  stale,
  unscoredTestId,
  t,
}: SeparabilityBadgeProps): JSX.Element | null {
  const badgeClass = `px-2 py-0.5 rounded text-xs font-mono ${stale ? "opacity-50" : ""}`;

  if (separability !== null) {
    return (
      <span
        className={`${badgeClass} ${getSeparabilityScoreStyle(separability.score)}`}
        title={t("separabilityTooltip")}
      >
        {t("separability", { axis: t(LABEL_AXIS_KEY[separability.label_by]) })}
        : {separability.score.toFixed(3)}
        {" · "}
        {t("separabilityCounts", {
          classes: separability.n_classes,
          points: separability.n_points,
        })}
      </span>
    );
  }

  // Never just vanish. A missing score is a statement about the filter —
  // nearly always "one class left on this axis" — and an empty slot reads as
  // "still loading" or "broken" instead.
  if (unscored !== null) {
    return (
      <span
        className={`${badgeClass} bg-bg-secondary text-text-muted`}
        title={t("separabilityUnscoredTooltip")}
        data-testid={unscoredTestId}
      >
        {t("separability", { axis: t(LABEL_AXIS_KEY[unscored.label_by]) })}
        {": "}
        {t(unscoredMessageKey(unscored), {
          classes: unscored.n_classes,
          points: unscored.n_points,
        })}
      </span>
    );
  }

  return null;
}
