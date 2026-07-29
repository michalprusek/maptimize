import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import {
  CLEAR_RATIO,
  discriminantVerdict,
  formatMetricValue,
  getDiscriminantScoreStyle,
  nullCeiling,
  separationRatio,
} from "../../components/visualization/discriminantMetrics";
import { appendFacetParams } from "../../lib/api";
import type { DiscriminantMetrics, UmapFacetSelection } from "../../lib/api";

/**
 * The discriminant projection's honesty numbers.
 *
 * A supervised projection separates its classes by construction, so the plot
 * carries no evidence on its own — the score beside it is the evidence. A bug
 * here does not throw and does not look wrong; it paints a null result green
 * and manufactures a finding. That is why this logic is pure and pinned here
 * rather than inlined in the component.
 *
 * The defaults below are the live corpus measured on the deployed pipeline:
 * balanced accuracy 0.300 over 14 proteins and 46 experiments, against a
 * permutation null of mean 0.061 / 95th percentile 0.080 over 20 shuffles,
 * chance 0.071, no shuffle reaching the score (so p sits at its floor, 1/21).
 */
function metrics(over: Partial<DiscriminantMetrics> = {}): DiscriminantMetrics {
  return {
    balanced_accuracy: 0.3,
    chance: 0.071,
    null_mean: 0.061,
    null_max: 0.081,
    null_p95: 0.08,
    p_value: 1 / 21,
    per_class: [
      { protein: "CLIP170", recall: 0.79, n_crops: 96 },
      { protein: "PRC1", recall: 0.07, n_crops: 41 },
    ],
    unscoreable_proteins: [],
    n_permutations: 20,
    n_proteins: 14,
    n_experiments: 46,
    // ⚠️ p is derived from the score unless overridden, because the two cannot
    // vary independently in reality: a score inside the null was, by definition,
    // reached by shuffled labels. Hand-written fixtures kept pairing a
    // below-null score with a significant p, which is an impossible payload —
    // and a verdict tested only on impossible payloads proves nothing.
    ...(over.balanced_accuracy !== undefined && over.p_value === undefined
      ? { p_value: over.balanced_accuracy > 0.081 ? 1 / 21 : 6 / 21 }
      : {}),
    ...over,
  };
}

test.describe("formatMetricValue", () => {
  test("renders the numbers the way the design quotes them", () => {
    // "Separation 0.26 - chance 0.07 - shuffled labels 0.05"
    expect(formatMetricValue(0.259)).toBe("0.26");
    expect(formatMetricValue(0.071)).toBe("0.07");
    expect(formatMetricValue(0.054)).toBe("0.05");
  });

  test("keeps both decimals so scores line up in the strip", () => {
    expect(formatMetricValue(0)).toBe("0.00");
    expect(formatMetricValue(0.5)).toBe("0.50");
    expect(formatMetricValue(1)).toBe("1.00");
  });

  test("rounds rather than truncates", () => {
    expect(formatMetricValue(0.0549)).toBe("0.05");
    expect(formatMetricValue(0.0551)).toBe("0.06");
  });

  test("does not print NaN or Infinity at the user", () => {
    expect(formatMetricValue(Number.NaN)).toBe("-");
    expect(formatMetricValue(Number.POSITIVE_INFINITY)).toBe("-");
  });
});

test.describe("nullCeiling", () => {
  test("is the highest bar on offer", () => {
    expect(nullCeiling(metrics())).toBeCloseTo(0.08, 10);
  });

  test("falls back to chance when the permutation null came back empty", () => {
    // A null of 0 would otherwise make every score look infinitely better than
    // nothing — chance is the floor that stops that.
    expect(nullCeiling(metrics({ null_p95: 0, null_max: 0, null_mean: 0 }))).toBeCloseTo(0.071, 10);
  });

  test("is not a number when there is no bar at all", () => {
    const ceiling = nullCeiling(
      metrics({ null_p95: 0, null_max: 0, null_mean: 0, chance: 0 })
    );
    expect(Number.isFinite(ceiling)).toBe(false);
  });
});

test.describe("separationRatio", () => {
  test("reports how many times the null ceiling the score reaches", () => {
    // 0.30 / 0.080 = 3.75 — the figure the tooltip quotes, and the one the
    // deployed pipeline reports. Against the null's 95th percentile, not its
    // max: the max is a lucky draw of the seed.
    expect(separationRatio(metrics())!).toBeCloseTo(3.75, 2);
  });

  test("is null when there is no ceiling to divide by", () => {
    expect(separationRatio(metrics({ null_p95: 0, null_max: 0, null_mean: 0, chance: 0 }))).toBeNull();
  });

  test("is null when the score itself is not a number", () => {
    expect(separationRatio(metrics({ balanced_accuracy: Number.NaN }))).toBeNull();
  });
});

test.describe("discriminantVerdict", () => {
  test("calls the real corpus a clear separation", () => {
    expect(discriminantVerdict(metrics())).toBe("clear");
  });

  test("a score below the null is no evidence at all", () => {
    expect(discriminantVerdict(metrics({ balanced_accuracy: 0.06 }))).toBe("none");
  });

  test("a score exactly at the null ceiling is still no evidence", () => {
    // The boundary is the case that matters: a score shuffled labels reached is
    // not a finding, however far above chance it sits. It is the p-value that
    // says so — the ratio alone puts 0.081/0.080 above 1 and would say "weak".
    expect(discriminantVerdict(metrics({ balanced_accuracy: 0.081 }))).toBe("none");
    expect(
      discriminantVerdict(metrics({ balanced_accuracy: 0.5, p_value: 4 / 21 }))
    ).toBe("none");
  });

  test("clearing the null only just is weak, not clear", () => {
    expect(
      discriminantVerdict(metrics({ balanced_accuracy: 0.09, p_value: 1 / 21 }))
    ).toBe("weak");
    // Exactly CLEAR_RATIO times the ceiling is the last weak value.
    expect(
      discriminantVerdict(metrics({ balanced_accuracy: 0.08 * CLEAR_RATIO }))
    ).toBe("weak");
    expect(
      discriminantVerdict(metrics({ balanced_accuracy: 0.08 * CLEAR_RATIO + 0.001 }))
    ).toBe("clear");
  });

  test("without a permutation null nothing is ever clear", () => {
    // 0.259 against chance 0.071 is 3.6x, but no shuffled-label run happened,
    // so nothing on screen has been tested and the verdict must not say it has.
    expect(
      discriminantVerdict(
        metrics({ n_permutations: 0, null_p95: 0, null_max: 0, null_mean: 0, p_value: null })
      )
    ).toBe("weak");
  });

  test("degenerate metrics are no evidence rather than a divide by zero", () => {
    expect(
      discriminantVerdict(metrics({ null_p95: 0, null_max: 0, null_mean: 0, chance: 0 }))
    ).toBe("none");
    expect(discriminantVerdict(metrics({ balanced_accuracy: Number.NaN }))).toBe("none");
  });
});

test.describe("getDiscriminantScoreStyle", () => {
  test("a score that shuffled labels already reach is never green", () => {
    // The load-bearing assertion of the whole strip: green next to a null
    // result is the one failure mode that turns nothing into a discovery.
    for (const accuracy of [0, 0.01, 0.06, 0.071, 0.0809, 0.081]) {
      const style = getDiscriminantScoreStyle(metrics({ balanced_accuracy: accuracy }));
      expect(style, `balanced_accuracy=${accuracy}`).not.toContain("green");
      expect(style, `balanced_accuracy=${accuracy}`).toContain("red");
    }
  });

  test("marks a real separation green and a marginal one amber", () => {
    expect(getDiscriminantScoreStyle(metrics())).toContain("green");
    expect(
      getDiscriminantScoreStyle(metrics({ balanced_accuracy: 0.1, p_value: 1 / 21 }))
    ).toContain("amber");
  });

  test("an untested score is never green however high it is", () => {
    expect(
      getDiscriminantScoreStyle(
        metrics({ balanced_accuracy: 0.99, n_permutations: 0, null_p95: 0, null_max: 0, null_mean: 0, p_value: null })
      )
    ).not.toContain("green");
  });
});

test.describe("appendFacetParams", () => {
  const selection: UmapFacetSelection = {
    experiment: [3, 4],
    microscope: [10],
    protein: [],
    ptm: [0],
  };

  test("spells the filter the same way for every projection endpoint", () => {
    // The discriminant endpoint takes the identical four repeatable params, so
    // a divergence here would silently show the two views different subsets.
    const params = appendFacetParams(new URLSearchParams(), selection);
    expect(params.getAll("experiment_id")).toEqual(["3", "4"]);
    expect(params.getAll("microscope_id")).toEqual(["10"]);
    expect(params.getAll("protein_id")).toEqual([]);
  });

  test("keeps the unassigned sentinel", () => {
    // Id 0 means "not assigned"; dropping it as falsy would make the PTM facet
    // useless, since experiments start unassigned.
    const params = appendFacetParams(new URLSearchParams(), selection);
    expect(params.getAll("ptm_id")).toEqual(["0"]);
  });

  test("leaves parameters already on the query string alone", () => {
    const params = appendFacetParams(
      new URLSearchParams({ umap_type: "cropped" }),
      selection
    );
    expect(params.get("umap_type")).toBe("cropped");
  });

  test("adds nothing when no filter is set", () => {
    expect(appendFacetParams(new URLSearchParams(), undefined).toString()).toBe("");
  });
});

test.describe("discriminant translations", () => {
  const KEYS = [
    "umapMode",
    "umapModeTooltip",
    "ldaMode",
    "ldaModeTooltip",
    "ldaTitle",
    "ldaLoading",
    "ldaLoadingHint",
    "ldaComputing",
    "ldaComputingHint",
    "ldaComputingPartial",
    "ldaComputeFailed",
    "ldaComputeFailedHint",
    "ldaNoProjection",
    "ldaNoProjectionHint",
    "separation",
    "chanceLevel",
    "shuffledLabels",
    "metricCaption",
    "metricDetail",
    "metricsComputing",
    "metricsUnavailable",
    "metricsFailed",
    "separationRatioTooltip",
    "verdictNone",
    "verdictWeak",
    "verdictClear",
  ];

  // Read as raw text on purpose: a duplicate key silently shadows once parsed,
  // which has already caused a real UI bug here, so the count is what is
  // checked rather than the parsed object.
  for (const locale of ["en", "fr"]) {
    test(`${locale}.json defines each key exactly once`, () => {
      const raw = readFileSync(
        join(__dirname, "..", "..", "messages", `${locale}.json`),
        "utf8"
      );
      for (const key of KEYS) {
        const occurrences = raw.split(`"${key}":`).length - 1;
        expect(occurrences, `${locale}.json key ${key}`).toBe(1);
      }
    });
  }
});
