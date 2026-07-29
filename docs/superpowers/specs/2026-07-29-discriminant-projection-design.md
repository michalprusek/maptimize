# Supervised discriminant projection (LDA) — design spec

**Date:** 2026-07-29
**Branch:** `feature/discriminant-projection` (off `main`)
**Author:** Michal + Claude

## Goal

Alongside the unsupervised UMAP, show a projection that **maximally separates the
MAP proteins**, and report honestly how much of that separation is real.

The dashboard gets a UMAP ⇄ LDA switch. Same scope, same ACL, same facet filter.

## Why this needs more than "run LDA and plot it"

A supervised projection always looks separated — that is what supervising it
does. With 1024-dimensional embeddings and 1277 crops it can separate 14 classes
almost perfectly on pure noise. Shipping the picture without a number beside it
would manufacture a finding.

Measured on the real corpus before designing (`balanced accuracy`, 14 classes,
chance = 0.071):

| cross-validation split | protein, raw | protein, microscope-centred |
|---|---|---|
| random over crops | 0.679 | 0.665 |
| grouped by image | 0.653 | 0.655 |
| **grouped by experiment** | **0.183** | **0.300** |

Permutation null under the experiment split: mean 0.055, 95th percentile 0.076,
max 0.078 over the 20 shuffles that ship. ⚠️ Over 200 shuffles the max reaches
0.140 and 17.5% of individual draws clear 0.078 — the max of a small sample is a
draw, not a ceiling. The honest statement is p = 0.005 at 200 shuffles; the
shipped 20 can only report p ≤ 0.048.

Three findings drive the whole design:

1. **The leak is experiment-level, not image-level.** Grouping by image recovers
   only 0.026 of the 0.493 gap — about 5% — because 40% of images hold a single
   crop. What actually leaks is that each experiment carries one protein and one
   set of acquisition conditions, so any split that puts an experiment on both
   sides lets the model read the label off the batch. The honest split is by
   **experiment**, and it costs a factor of 2.5.
2. **The signal is real but modest, and unevenly spread.** 0.300 sits outside
   the permutation null (3.75× its 95th percentile, p = 0.048 which is the floor
   at 20 shuffles) — not the "almost perfect" separation the leaky split implied.
   And the mean hides its own shape: measured live, CLIP170 reaches 0.79 and
   TRIM46 0.47, while MAP2d manages 0.05 across 167 crops. The response therefore
   carries per-class recall, and the UI prints every protein rather than a
   headline.
3. **Microscope correction helps.** 0.183 → 0.300 under the honest split, while
   microscope decodability falls 0.548 → 0.116 (chance is 0.25 for four
   microscopes, so it is removed outright). Subtracting the batch offset removes
   a confounder that was actively hurting generalisation across experiments.

## Decisions (locked)

1. **LDA**, not supervised UMAP. It maximises the between-class to within-class
   scatter ratio by construction — the literal reading of "separate the MAPs" —
   and its axes are directions in embedding space rather than a nonlinear
   embedding whose axes mean nothing.
2. **Per-microscope centering before the fit.** Subtract each microscope's mean
   embedding. Not optional: `PRC1` and `MTCL1` exist only on AeryScan, so an
   uncorrected projection would separate them partly by instrument and present it
   as biology.
3. **PCA to 50 components before LDA.** 1024 features on 1277 samples is
   guaranteed overfitting — LDA reaches 0.998 training accuracy on the raw
   features. Not about singularity: the default svd solver never inverts the
   within-class scatter.
4. **Group-aware cross-validation by experiment** (`StratifiedGroupKFold`) for
   every reported number. Any other split reports leakage.
5. **The plot's geometry comes from the full-data fit; the numbers come from
   cross-validation.** Out-of-fold coordinates originate in different LDA fits,
   whose axes are related by no alignment at all (measured across folds: principal
   angles 1.3° and 67°, second axis 10.7× different in scale) — plotting them
   together produces a scatter that means nothing, and no Procrustes fixes it. Showing the in-sample geometry
   next to an out-of-fold score is what a careful paper does, and the caption
   says exactly that.
6. **The headline number is always on screen.** Balanced accuracy, chance, and
   the permutation null, rendered next to the plot rather than hidden in a
   tooltip. A reader must not be able to see the separation without seeing what
   it is worth.

## Backend

### New service `backend/services/discriminant_service.py`

Pure functions plus the same scope/refresh bookkeeping `umap_service` uses.

- `centre_per_microscope(X, microscope_ids)` — subtract each instrument's mean.
- `fit_projection(X, y)` → 2-D coordinates from `StandardScaler → PCA(50) → LDA`
  fitted on everything. Used for display geometry only.
- `score_projection(X, y, groups)` → out-of-fold balanced accuracy under
  `StratifiedGroupKFold(5)` on experiment id.
- `permutation_null(X, y, groups, n)` → the same score with labels shuffled **at
  experiment level**, so the null respects the grouping instead of being
  trivially destroyed by it.

`MIN_POINTS` (50), `MIN_CLASSES` (2) and `MIN_GROUPS` (5) guard degenerate
corpora: too few crops to fit, too few proteins to separate, too few experiments
to cross-validate across.

### Caching and the compute path

One cross-validation is ~28 s and the whole computation runs 21 of them — roughly
ten minutes — so this never runs inside a
request. It mirrors `umap_service`'s existing pattern rather than inventing one:
module-level dicts keyed by scope (`u{user}` / `g{group}`), an in-flight set to
dedupe concurrent dashboards, and a recorded failure so a doomed computation is
not rescheduled on every poll.

Results are cached **in process**, not in the database. The projection is a
scope-level analysis rather than a per-crop attribute, the metrics have nowhere
natural to live in the schema, and a restart simply recomputes. This avoids the
three-legged migration entirely.

### Endpoint `GET /api/embeddings/discriminant`

Takes the same `facet_selection` dependency as the UMAP endpoint and applies the
same ACL. Returns:

- `points` — `crop_id`, `image_id`, `experiment_id`, `x`, `y`, `protein_name`,
  `protein_color`, `thumbnail_url`
- `metrics` — `balanced_accuracy`, `chance`, `null_mean`, `null_max`,
  `null_p95`, `p_value`, `per_class`, `n_permutations`, `n_experiments`,
  `n_proteins`
- `facets` — reused unchanged, so the filter panel needs no special case
- `is_computing` / `is_stale` / `compute_error` — the same polling contract as the
  UMAP endpoint, including staleness: the fit is a snapshot while the labels beside
  it are read live, so a changed corpus is reported rather than silently mixed

⚠️ **The filter selects which points are returned, never which are fitted.** The
projection is fitted once per scope; filtering refits nothing. Otherwise two
filtered views would live in incomparable coordinate systems, and the axes would
silently change meaning as the user clicked.

## Frontend

A `UMAP ⇄ LDA` segmented control beside the existing FOV/Cropped toggle.
`UmapVisualization` keeps its current job; the discriminant view is a sibling
component sharing the filter panel, the legend and the tooltips, because the only
real differences are the data source and the metric strip.

FOV mode is not offered for LDA: the labels are per-crop protein assignments.

The metric strip reads, e.g.:

> **Separation 0.30** · chance 0.07 · shuffled labels 0.06 · p ≤ 0.048
> Geometry from all data; the score is cross-validated by experiment.
> Per protein: CLIP170 0.79 · TRIM46 0.47 · … · MAP2d 0.05

The verdict badge is driven by the p-value, not by the ratio: a ratio against the
null depends on the class count, so 1.1× can be decisive with two proteins and
meaningless with fourteen.

## Testing

- Unit: `centre_per_microscope` removes the per-instrument mean; the CV splitter
  never puts one experiment on both sides; the permutation null shuffles at
  experiment level; the guards fire on too few points or too few classes.
- The scientifically load-bearing assertion: on synthetic data with a planted
  class signal the score is high, and on synthetic data whose labels are random
  the score sits inside the permutation null. That pins the pipeline's honesty
  rather than its plumbing.
- Frontend pure-logic tests for the metric formatting on the existing runner.

## Out of scope

- Supervised UMAP, PLS-DA, or NCA as alternative projections.
- Per-facet refitting (deliberately rejected above).
- Persisting projections or metrics to the database.
- Using the discriminant directions to rank proteins — a separate question that
  needs its own design.
