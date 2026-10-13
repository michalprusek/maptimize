import { expect, test } from "@playwright/test";

import {
  MINUS_SIGN,
  bundlenessBadgeForm,
  classMeansById,
  formatScoredCount,
  formatSigned,
  legendEntries,
} from "../../components/visualization/bundleness";
import { COLOR_BY_ORDER } from "../../components/visualization/umapFacets";
import en from "../../messages/en.json";
import fr from "../../messages/fr.json";
import type { Bundleness } from "../../lib/api";

/**
 * The bundleness summary's pure logic.
 *
 * Every failure here is a plausible-looking wrong number: a mean beside the
 * wrong class, a sign that flips, a partial mean printed as a whole one.
 */

function summary(over: Partial<Bundleness> = {}): Bundleness {
  return {
    mean: 0.42,
    sd: 0.9,
    n_scored: 312,
    n_points: 312,
    model_version: "v1",
    label_by: "protein",
    classes: [
      { label: 3, mean: 0.64, sd: 0.5, n_scored: 200 },
      { label: 7, mean: -0.31, sd: null, n_scored: 1 },
    ],
    ...over,
  };
}

test.describe("formatSigned", () => {
  test("positives carry an explicit plus", () => {
    expect(formatSigned(0.42)).toBe("+0.42");
    expect(formatSigned(1)).toBe("+1.00");
  });

  test("negatives use the true minus sign, not a hyphen", () => {
    expect(MINUS_SIGN).toBe("\u2212");
    expect(MINUS_SIGN.codePointAt(0)).toBe(0x2212);
    expect(formatSigned(-0.42)).toBe("−0.42");
    expect(formatSigned(-0.42)).not.toContain("-");
    // Same length as the positive, which is why the glyph matters.
    expect(formatSigned(-0.42)!.length).toBe(formatSigned(0.42)!.length);
  });

  test("rounds to two decimals by default, and honours another precision", () => {
    expect(formatSigned(0.426)).toBe("+0.43");
    expect(formatSigned(-1.234)).toBe("−1.23");
    expect(formatSigned(0.4267, 3)).toBe("+0.427");
  });

  test("a value that rounds to zero never claims to be negative", () => {
    expect(formatSigned(0)).toBe("+0.00");
    expect(formatSigned(-0)).toBe("+0.00");
    expect(formatSigned(-0.001)).toBe("+0.00");
    expect(formatSigned(-0.005)).toBe("−0.01");
  });

  test("anything that is not a finite number has no rendering", () => {
    // `null` is what recharts and JSON hand over for a missing number, and
    // Number(null) is 0 — it must not come out as a believable "+0.00".
    for (const value of [null, undefined, NaN, Infinity, -Infinity, "0.4"]) {
      expect(formatSigned(value)).toBeNull();
    }
  });
});

test.describe("formatScoredCount", () => {
  test("a fully scored selection prints one number", () => {
    expect(formatScoredCount(312, 312)).toBe("312");
  });

  test("a partly scored selection prints scored/total", () => {
    expect(formatScoredCount(312, 340)).toBe("312/340");
    expect(formatScoredCount(1, 2)).toBe("1/2");
  });
});

test.describe("bundlenessBadgeForm", () => {
  test("nothing plotted hides the badge, whatever the summary says", () => {
    expect(bundlenessBadgeForm(0, null)).toBe("hidden");
    expect(bundlenessBadgeForm(0, summary())).toBe("hidden");
  });

  test("points without a summary are said to be unscored, not hidden", () => {
    expect(bundlenessBadgeForm(5, null)).toBe("unscored");
    // A backend older than the field omits it altogether.
    expect(bundlenessBadgeForm(5, undefined)).toBe("unscored");
  });

  test("points with a summary are scored", () => {
    expect(bundlenessBadgeForm(5, summary())).toBe("scored");
  });
});

test.describe("classMeansById", () => {
  test("keys each class by its id on the axis", () => {
    const means = classMeansById(summary(), "protein", true);
    expect(Array.from(means.keys())).toEqual([3, 7]);
    expect(means.get(3)?.mean).toBe(0.64);
    expect(means.get(7)?.n_scored).toBe(1);
    // No entry for the unassigned sentinel or any id the backend did not send.
    expect(means.get(0)).toBeUndefined();
  });

  test("a response broken down by another axis yields nothing", () => {
    // Protein 3 and microscope 3 share a key and nothing else.
    for (const axis of COLOR_BY_ORDER.filter((candidate) => candidate !== "protein")) {
      expect(classMeansById(summary(), axis, true).size).toBe(0);
    }
  });

  test("a response that is not current yields nothing", () => {
    // Group indices from before a slot shift name other groups.
    const groups = summary({ label_by: "group", classes: [{ label: 0, mean: 1, sd: null, n_scored: 4 }] });
    expect(classMeansById(groups, "group", true).size).toBe(1);
    expect(classMeansById(groups, "group", false).size).toBe(0);
  });

  test("no summary yields nothing", () => {
    expect(classMeansById(null, "protein", true).size).toBe(0);
    expect(classMeansById(undefined, "protein", true).size).toBe(0);
  });

  test("a class whose mean is not a number is left out", () => {
    const broken = summary({
      classes: [
        { label: 3, mean: null as unknown as number, sd: null, n_scored: 2 },
        { label: 7, mean: 0.1, sd: null, n_scored: 2 },
      ],
    });
    expect(Array.from(classMeansById(broken, "protein", true).keys())).toEqual([7]);
  });
});

test.describe("legendEntries", () => {
  test("counts points per name, most first, and carries the class id", () => {
    const entries = legendEntries([
      { name: "Tau", color: "#111", classId: 7 },
      { name: "MAP2", color: "#222", classId: 3 },
      { name: "MAP2", color: "#222", classId: 3 },
    ]);
    expect(entries).toEqual([
      { name: "MAP2", color: "#222", count: 2, id: 3 },
      { name: "Tau", color: "#111", count: 1, id: 7 },
    ]);
  });

  test("an entry made only of class-less points has no id", () => {
    const [entry] = legendEntries([
      { name: "Unassigned", color: "#888", classId: null },
      { name: "Unassigned", color: "#888", classId: null },
    ]);
    expect(entry).toEqual({ name: "Unassigned", color: "#888", count: 2, id: null });
  });

  test("points whose id could not be pinned do not unset one that could", () => {
    const [entry] = legendEntries([
      { name: "MAP2", color: "#222", classId: null },
      { name: "MAP2", color: "#222", classId: 3 },
      { name: "MAP2", color: "#222", classId: null },
    ]);
    expect(entry.id).toBe(3);
    expect(entry.count).toBe(3);
  });

  test("two classes sharing a display name leave the entry without an id", () => {
    // Two experiments called "repeat": one swatch, and neither mean fits it.
    const [entry] = legendEntries([
      { name: "repeat", color: "#111", classId: 10 },
      { name: "repeat", color: "#111", classId: 11 },
      // Not even a later point agreeing with the first may restore it.
      { name: "repeat", color: "#111", classId: 10 },
    ]);
    expect(entry.id).toBeNull();
    expect(entry.count).toBe(3);
  });

  test("the id is the join key: a class is found by id whatever it is called", () => {
    const means = classMeansById(summary(), "protein", true);
    const entries = legendEntries([
      // Named like class 7's protein would be, but it IS class 3.
      { name: "Tau", color: "#111", classId: 3 },
      { name: "Unassigned", color: "#888", classId: null },
    ]);
    expect(means.get(entries[0].id!)?.mean).toBe(0.64);
    expect(entries[1].id).toBeNull();
  });
});

test.describe("bundleness messages", () => {
  const KEYS = [
    "bundleness",
    "bundlenessCount",
    "bundlenessUnscored",
    "bundlenessTooltip",
    "bundlenessTooltipNoSd",
    "bundlenessUnscoredTooltip",
    "bundlenessClassTitle",
  ] as const;

  for (const [locale, messages] of [["en", en], ["fr", fr]] as const) {
    test(`${locale} defines every string the badge renders`, () => {
      const umap = messages.umap as Record<string, string>;
      for (const key of KEYS) expect(umap[key], key).toBeTruthy();
      // The placeholders the components pass.
      expect(umap.bundlenessCount).toContain("{n}");
      expect(umap.bundlenessTooltip).toContain("{sd}");
      for (const key of ["bundlenessTooltip", "bundlenessTooltipNoSd"]) {
        expect(umap[key], key).toContain("{version}");
        // The scope line is not optional copy.
        expect(umap[key], key).toContain("Airyscan");
        expect(umap[key], key).toContain("extrapolation");
      }
      expect(umap.bundlenessTooltipNoSd).not.toContain("{sd}");
      expect(umap.bundlenessClassTitle).toContain("{mean}");
      expect(umap.bundlenessClassTitle).toContain("{n}");
    });
  }

  test("the FOV projection's strings are gone from both locales", () => {
    for (const messages of [en, fr]) {
      const umap = messages.umap as Record<string, string>;
      for (const key of ["fov", "fovTooltip", "cropped", "croppedTooltip", "fovImages", "noEmbeddingsFov", "imageId"]) {
        expect(umap[key], key).toBeUndefined();
      }
    }
  });

  test("the French strings are translations, not copies", () => {
    const [enUmap, frUmap] = [en.umap, fr.umap] as Array<Record<string, string>>;
    for (const key of KEYS.filter((key) => key !== "bundlenessCount")) {
      expect(frUmap[key], key).not.toBe(enUmap[key]);
    }
  });
});
