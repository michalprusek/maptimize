# Bundleness score per crop, crop-only dashboard

Date: 2026-10-13. Status: approved in conversation, implemented on `feat/bundleness-score`.

## Goal

1. Every cell crop carries a precomputed bundleness score from a model trained on
   the lab's pairwise judgements.
2. The dashboard shows the mean score of the currently filtered points beside the
   separability index, and per class of the coloured axis.
3. The FOV-level embedding projection is removed from the dashboard, and FOV
   embeddings are no longer computed. Everything is per crop.

## Decisions taken with the user

| Question | Decision |
|---|---|
| Scale | z-score against the training crops (0 = average annotated crop), frozen at training time |
| Model lifecycle | versioned artifact committed to the repo + a manual training script; no automatic retraining |
| FOV removal depth | code removed, data left in the database untouched |
| Per-class means | included (same classes separability scores) |

## Model

Learning-to-rank on frozen features (RankNet / Bradley-Terry reward model):
`P(a ≻ b) = σ(w · (φ(a) − φ(b)))`, `φ` = StandardScaler → PCA(25, whiten) of the
1024-dim DINOv3-large crop embedding, no intercept, each comparison fed in both
orders. The chain is affine and is folded into one vector:
`score = weights · embedding + bias`, then standardised against the compared crops.

### Measured (2026-10-13, production data, read-only)

5090 non-undone comparisons of metric 7 over 361 crops with embeddings, from 4
annotators and 6 experiments (one per protein, all Airyscan, unmodified).

| Split | Result |
|---|---|
| 5-fold by crop, pooled Spearman vs feature-free BT strength | 0.86 |
| Leave-one-experiment-out, pooled | 0.83 |
| Leave-one-experiment-out, **within the held-out experiment** (mean / min) | **0.65 / 0.56** |
| Held-out pairwise accuracy | 0.66–0.69 (annotators agree with each other on 0.67–0.85 of shared pairs; Theo with himself on 0.61 of repeats) |
| Baseline: mean intensity | 0.21 |
| Baseline: knowing only the protein | 0.63 pooled, 0 within |

Choices decided by measurement:

- **All annotators pooled** rather than Theo alone: 0.859 vs 0.847 (crop split),
  0.835 vs 0.815 (experiment split), judged against the pooled ranking.
- **25 components**: 0.82 / 0.86 / 0.86 / 0.85 at 10 / 25 / 50 / 100; 25 is the
  smallest on the plateau with 361 crops.
- Regularisation `C` made no measurable difference over 0.01–1.0.

### Known limit

Training covers 361 of 2656 crops: Airyscan only, six MAPs, no PTM condition.
3D SIM (409 crops), "Airyscan calibrate" (1726) and every PTM condition have no
validation pair at all; 15 % of 3D SIM crops lie beyond the 99th percentile of
training distance in the PCA space, and the corpus has more variance outside the
25-component subspace than the training crops (median 0.44 vs 0.32). Scores there
are extrapolation. The badge tooltip and the MCP tool description say so. The fix
is data: have annotators rank crops from those conditions and retrain.

## Storage and writers

`cell_crops.bundleness_score` (existing column, previously always NULL) and new
`cell_crops.bundleness_model`. Written only by `services/bundleness_service.py`:
`apply_score` wherever an embedding is assigned, `backfill` at startup for rows
whose stored version differs from the active one.

## API

- `GET /api/embeddings/umap` → adds `bundleness {mean, sd, n_scored, n_points,
  model_version, label_by, classes[{label, mean, sd, n_scored}]}`; no `umap_type`
  (`umap_type=fov` → 410).
- `GET /api/embeddings/bundleness` → the summary alone, 404 when nothing is scored.
- MCP `measure_bundleness`; `measure_separability` loses `umap_type`; server 5.0.0.

## Verification

Unit tests for model, training, service, router and MCP, each guard proven by
perturbation (22 mutations, all caught); handlers and backfill probed against
last night's backup restored into a throwaway Postgres; browser check on the
deployed bundle.
