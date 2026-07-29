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
 * Multiples of the null bar that a score must reach to be called "clear".
 *
 * A ratio rather than an absolute score because chance itself moves with the
 * number of classes. It is only ever a secondary condition: the p-value decides
 * whether the score is outside the null at all, and the ratio then asks whether
 * the margin is worth drawing attention to.
 */
export const CLEAR_RATIO = 2;

/**
 * Largest p-value still treated as "outside the null".
 *
 * ⚠️ The backend's p cannot go below 1/(n_permutations + 1) — 0.048 at the
 * shipped 20 shuffles. So this threshold is very nearly "no shuffle reached the
 * score", and raising the shuffle count is the only way to make it stricter.
 */
export const MAX_SIGNIFICANT_P = 0.05;

/** Two decimals, matching the way the numbers are quoted in the design. */
export function formatMetricValue(value: number): string {
  if (!Number.isFinite(value)) return "-";
  return value.toFixed(2);
}

/**
 * The bar the balanced accuracy has to clear.
 *
 * The null's 95th percentile, NOT its maximum. A max over a handful of draws is
 * not a ceiling — measured on the real corpus, 17.5% of individual shuffles
 * exceed the max of the 20 that run, so a ratio quoted against it reports
 * whatever the seed happened to draw (3.3x on the shipped seed, ~2.4x honestly).
 * Older payloads carry no p95, hence the fallbacks; chance is the floor so a
 * null that never ran cannot make every score look infinitely good.
 */
export function nullCeiling(metrics: DiscriminantMetrics): number {
  const bars = [
    metrics.null_p95 ?? metrics.null_max,
    metrics.null_mean,
    metrics.chance,
  ].filter((value) => Number.isFinite(value) && (value as number) > 0);
  return bars.length > 0 ? Math.max(...(bars as number[])) : NaN;
}

/**
 * How many times the null bar the score reaches, or null if that cannot be
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
 * The p-value leads, because it is the statistic that answers the question:
 * a ratio against the null depends on how many classes there are, so 1.1x can
 * be decisive with two proteins and meaningless with fourteen. The ratio only
 * distinguishes "clear" from "weak" once significance is established.
 *
 * Conservative in both directions it can be wrong:
 * - a score any shuffle reached is "none", never warmer, because that is exactly
 *   the case a reader must not mistake for a finding;
 * - without a permutation null nothing has been tested, so the verdict is capped
 *   at "weak" however far above chance the score lands.
 */
export function discriminantVerdict(
  metrics: DiscriminantMetrics
): DiscriminantVerdict {
  const ratio = separationRatio(metrics);
  if (ratio === null || ratio <= 1) return "none";
  // No null was run, so nothing here has been tested against shuffled labels.
  if (!Number.isFinite(metrics.n_permutations) || metrics.n_permutations < 1) {
    return "weak";
  }
  const p = metrics.p_value;
  if (Number.isFinite(p) && (p as number) > MAX_SIGNIFICANT_P) return "none";
  return ratio > CLEAR_RATIO ? "clear" : "weak";
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
