/**
 * Reading the discriminant projection's honesty numbers.
 *
 * A supervised projection separates its classes by construction, so the picture
 * carries no information on its own — the balanced accuracy next to it does.
 * Everything here answers one question: is this score above the bar a shuffled
 * label set already clears? Getting it wrong does not throw, it paints a null
 * result green, so it lives in its own module and is pinned by tests.
 */
import type { DiscriminantMetrics } from "@/lib/api";

/** How a score compares with the bar it has to clear. */
export type DiscriminantVerdict = "none" | "weak" | "clear";

/**
 * Multiples of the null ceiling that separate the verdicts.
 *
 * At or below the ceiling there is no evidence of separation at all; the real
 * corpus sits at 0.259 against a ceiling of 0.078, i.e. 3.3x, which is the
 * "clear" band. These are ratios rather than absolute scores because chance
 * itself moves with the number of classes.
 */
export const CLEAR_RATIO = 2;

/** Two decimals, matching the way the numbers are quoted in the design. */
export function formatMetricValue(value: number): string {
  if (!Number.isFinite(value)) return "-";
  return value.toFixed(2);
}

/**
 * The bar the balanced accuracy has to clear.
 *
 * The permutation null is the meaningful bar, but chance is taken as a floor:
 * a null that was never run (or came back degenerate at 0) would otherwise make
 * every score look infinitely better than nothing.
 */
export function nullCeiling(metrics: DiscriminantMetrics): number {
  const bars = [metrics.null_max, metrics.null_mean, metrics.chance].filter(
    (value) => Number.isFinite(value) && value > 0
  );
  return bars.length > 0 ? Math.max(...bars) : NaN;
}

/**
 * How many times the null ceiling the score reaches, or null if that cannot be
 * computed. Used for the tooltip, and as the input to the verdict.
 */
export function separationRatio(metrics: DiscriminantMetrics): number | null {
  const ceiling = nullCeiling(metrics);
  if (!Number.isFinite(ceiling) || ceiling <= 0) return null;
  if (!Number.isFinite(metrics.balanced_accuracy)) return null;
  return metrics.balanced_accuracy / ceiling;
}

/**
 * Whether the separation on screen is worth anything.
 *
 * Deliberately conservative in both directions it can be wrong:
 * - a score at or below the null ceiling is "none", never a warmer verdict,
 *   because that is exactly the case a reader must not mistake for a finding;
 * - without a permutation null there is no ceiling to speak of, so the verdict
 *   is capped at "weak" however far above chance the score lands.
 */
export function discriminantVerdict(
  metrics: DiscriminantMetrics
): DiscriminantVerdict {
  const ratio = separationRatio(metrics);
  if (ratio === null || ratio <= 1) return "none";
  if (ratio <= CLEAR_RATIO) return "weak";
  // No null was run, so nothing here has been tested against shuffled labels.
  if (!Number.isFinite(metrics.n_permutations) || metrics.n_permutations < 1) {
    return "weak";
  }
  return "clear";
}

/**
 * Badge colours for the headline score.
 *
 * Follows getSilhouetteScoreStyle's vocabulary, but the thresholds are not
 * transferable: here green means "outside the permutation null", and a score
 * below the null must never reach it.
 */
export function getDiscriminantScoreStyle(metrics: DiscriminantMetrics): string {
  switch (discriminantVerdict(metrics)) {
    case "clear":
      return "bg-green-500/20 text-green-400";
    case "weak":
      return "bg-accent-amber/20 text-accent-amber";
    case "none":
      return "bg-accent-red/20 text-accent-red";
  }
}
