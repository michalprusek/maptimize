import { expect, test } from "@playwright/test";

import { SeparabilityBadge } from "../../components/visualization/SeparabilityBadge";
import { knnSkill } from "../../components/visualization/chartConfig";
import type { KnnSeparability, Separability } from "../../lib/api";
import { renderToStaticMarkup, t, text } from "./staticMarkup";

/** Production numbers for label_by=protein over the whole corpus (2026-10-17). */
const PROTEIN_KNN: KnnSeparability = {
  balanced_accuracy: 0.388,
  chance: 1 / 13,
  k: 20,
  n_classes: 13,
  n_points: 2400,
};

function scored(knn: KnnSeparability | null): Separability {
  return { score: -0.016, label_by: "protein", n_classes: 14, n_points: 2444, knn };
}

const badge = (separability: Separability): string =>
  renderToStaticMarkup(
    <SeparabilityBadge
      separability={separability}
      unscored={null}
      stale={false}
      unscoredTestId="separability-unscored"
      t={t}
    />
  );

test.describe("SeparabilityBadge — kNN beside the silhouette", () => {
  test("both numbers, each with its own counts and kNN against chance", () => {
    expect(text(badge(scored(PROTEIN_KNN)))).toBe(
      "Separability (Protein): -0.016 · 14 classes · 2444 points" +
        "kNN: 39% vs 8% chance · 13 classes · 2400 points"
    );
  });

  test("a missing kNN says the class is confounded with the experiment, never 0", () => {
    const html = badge(scored(null));
    expect(text(html)).toContain("kNN: n/a · each class comes from a single experiment");
    expect(text(html)).not.toMatch(/kNN: 0/);
    expect(html).toContain("text-text-muted");
  });

  test("kNN is coloured by its lift over chance, not by raw accuracy", () => {
    // 0.40 over 14 classes is real signal; 0.55 over 2 is barely any.
    expect(knnSkill(PROTEIN_KNN)).toBeCloseTo(0.337, 2);
    expect(knnSkill({ balanced_accuracy: 0.55, chance: 0.5 })).toBeCloseTo(0.1, 5);
    // Only the kNN half: the silhouette beside it (-0.016) is red either way.
    const knnClass = (separability: Separability): string =>
      badge(separability).match(/<span class="([^"]*)"[^>]*>kNN:/)?.[1] ?? "";
    expect(knnClass(scored(PROTEIN_KNN))).toContain("text-accent-amber");
    expect(
      knnClass(scored({ ...PROTEIN_KNN, balanced_accuracy: 0.55, chance: 0.5 }))
    ).toContain("text-accent-red");
  });
});
