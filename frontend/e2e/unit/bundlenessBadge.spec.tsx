import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import {
  BundlenessBadge,
  ClassBundleness,
} from "../../components/visualization/BundlenessBadge";
import { renderToStaticMarkup, t, text } from "./staticMarkup";
import type { Bundleness } from "../../lib/api";

/**
 * What the badge actually renders — the helpers are pinned in
 * `bundleness.spec.ts`; this is the wiring between them and the markup, plus
 * where the badge is placed.
 */

function summary(over: Partial<Bundleness> = {}): Bundleness {
  return {
    mean: 0.42,
    sd: 0.9,
    n_scored: 312,
    n_points: 312,
    model_version: "v1",
    label_by: "protein",
    classes: [],
    ...over,
  };
}

function badge(
  bundleness: Bundleness | null,
  pointCount: number,
  stale = false
): string {
  return renderToStaticMarkup(
    <BundlenessBadge
      bundleness={bundleness}
      pointCount={pointCount}
      stale={stale}
      unscoredTestId="bundleness-unscored"
      t={t}
    />
  );
}

test.describe("BundlenessBadge", () => {
  test("scored: signed mean and the count it rests on", () => {
    expect(text(badge(summary(), 312))).toBe("Bundleness: +0.42 · n=312");
  });

  test("a negative mean uses the true minus sign", () => {
    expect(text(badge(summary({ mean: -0.42 }), 312))).toBe(
      "Bundleness: −0.42 · n=312"
    );
  });

  test("a partly scored selection shows scored/total", () => {
    expect(text(badge(summary({ n_points: 340 }), 340))).toBe(
      "Bundleness: +0.42 · n=312/340"
    );
  });

  test("the tooltip states the unit, the SD, the model and the scope", () => {
    const html = badge(summary(), 312);
    const title = html.match(/title="([^"]*)"/)?.[1] ?? "";
    expect(title).toContain("z-score");
    expect(title).toContain("± 0.90 SD");
    expect(title).toContain("Model v1");
    expect(title).toContain(
      "The model was validated on Airyscan images of six MAPs on unmodified microtubules; elsewhere it is an extrapolation."
    );
  });

  test("a single scored crop has no SD to print", () => {
    const html = badge(summary({ sd: null, n_scored: 1, n_points: 1 }), 1);
    const title = html.match(/title="([^"]*)"/)?.[1] ?? "";
    expect(title).not.toContain("±");
    expect(title).toContain("Model v1");
    expect(title).toContain("extrapolation");
  });

  test("points but no summary: the muted n/a form, not nothing", () => {
    const html = badge(null, 5);
    expect(text(html)).toBe("Bundleness: n/a · no scored crops");
    expect(html).toContain('data-testid="bundleness-unscored"');
    expect(html).toContain("text-text-muted");
  });

  test("a mean that is not a number falls back to the n/a form", () => {
    const html = badge(summary({ mean: null as unknown as number }), 5);
    expect(text(html)).toBe("Bundleness: n/a · no scored crops");
  });

  test("nothing plotted: nothing rendered", () => {
    expect(badge(null, 0)).toBe("");
    expect(badge(summary(), 0)).toBe("");
  });

  test("stale dims it, and it never borrows the separability colour scale", () => {
    expect(badge(summary(), 312, true)).toContain("opacity-50");
    expect(badge(summary(), 312, false)).not.toContain("opacity-50");
    for (const mean of [-2, 0, 2]) {
      expect(badge(summary({ mean }), 312)).not.toMatch(/accent-(green|red|amber)/);
    }
  });
});

test.describe("ClassBundleness", () => {
  const render = (cls: Parameters<typeof ClassBundleness>[0]["cls"], stale = false) =>
    renderToStaticMarkup(<ClassBundleness cls={cls} stale={stale} t={t} />);

  test("prints the signed mean with n in the title", () => {
    const html = render({ label: 3, mean: 0.64, sd: 0.5, n_scored: 200 });
    expect(text(html)).toBe("+0.64");
    expect(html).toMatch(/title="[^"]*n=200[^"]*"/);
    expect(html).toContain("font-mono");
    expect(html).toContain("text-text-muted");
  });

  test("no class, or a class without a printable mean, renders nothing", () => {
    expect(render(undefined)).toBe("");
    expect(render({ label: 3, mean: NaN, sd: null, n_scored: 2 })).toBe("");
  });
});

test.describe("where the badge is drawn", () => {
  const source = (file: string): string =>
    readFileSync(join(process.cwd(), "components", "visualization", file), "utf8");

  test("every SeparabilityBadge has a BundlenessBadge right after it", () => {
    const plot = source("UmapVisualization.tsx");
    const separability = plot.match(/<SeparabilityBadge\b/g) ?? [];
    expect(separability.length).toBeGreaterThan(0);
    // Each one is followed by its sibling before any other element opens.
    const pairs = plot.match(/<SeparabilityBadge\b[^<]*\/>\s*<BundlenessBadge\b/g) ?? [];
    expect(pairs.length).toBe(separability.length);
    // And nobody else draws the separability badge on its own.
    for (const file of ["UmapFilterPanel.tsx", "UmapGroupsEditor.tsx", "projectionShared.tsx"]) {
      expect(source(file), file).not.toMatch(/<SeparabilityBadge\b/);
    }
  });

  test("the groups editor is handed means for the GROUP axis, never the legend's", () => {
    const plot = source("UmapVisualization.tsx");
    expect(plot).toMatch(/groupMeans=\{groupMeans\}/);
    expect(plot).toMatch(
      /const groupMeans = useMemo\(\s*\(\) => classMeansById\(bundleness, "group", colorBy === "group" && groupsInSync\)/
    );
  });
});
