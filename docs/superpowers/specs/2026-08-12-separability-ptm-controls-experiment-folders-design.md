# Separability index, per-PTM controls, experiment folders

Date: 2026-08-12
Origin: three requests from Théo Buson (annotator, Janke Lab) in a message to Michal.

Three independent features, shipped as three sequential PRs. They share nothing
but the UMAP page, so the order below is by size, smallest first.

---

## 1 · Separability index selectable by colour-by

### What was asked

> "Could you add, for the UMAP, the separability index (I think you were able to
> calculate it to compare the 3D SIM and the Airy scan) — I would like to compare
> this index in different contexts (for example, compare it if I only take
> controls and if I only take simple transfections)."

### What already exists

`services/umap_service.py::compute_silhouette` computes a silhouette score over
the **raw 1024-D embeddings** with a cosine metric — not over the UMAP
coordinates. That is the right choice and stays: UMAP does not preserve distance,
so a silhouette on the 2-D projection measures how good the projection is, not
how separable the data are.

`routers/embeddings.py::_get_cropped_umap` already calls it **after**
`_apply_facets()`, so the score already describes the filtered subset. The
"subset selector" Théo asked for therefore already works through the existing
advanced filter — controls-only, single-PTM, per-microscope are all reachable
today.

Two things are genuinely missing:

1. The label axis is hardcoded to protein (`item.map_protein`). The 3D SIM vs
   Airyscan comparison Théo remembers was a *microscope* comparison, which the
   endpoint cannot express.
2. The number of classes and the number of points are not reported, so two
   values of the index cannot be compared honestly.

### Design

**Backend.**

`compute_silhouette(embeddings, items)` becomes
`compute_separability(embeddings, labels) -> Separability | None` where `labels`
is a plain `list[Optional[int]]`, positionally aligned with `embeddings`. `None`
entries are dropped before scoring, exactly as unlabeled items are dropped today.
The existing guards survive unchanged: at least 10 labeled items and at least 2
distinct classes, otherwise `None`.

`Separability` carries `score`, `label_by`, `n_classes`, `n_points`.

`GET /api/embeddings/umap` gains `label_by: FacetKey = "protein"`, accepting
`protein | microscope | ptm | experiment` — the same four dimensions the filter
panel and colour-by already offer, so the vocabulary cannot drift.

Label extraction adds **no join and no query**. Protein and experiment already
live on the point rows. Microscope and PTM come from the `_load_facets()`
summary, which is already loaded in the same handler and already maps
`experiment_id → (microscope_id, ptm_id)` over the whole readable scope — a
superset of any filtered point set.

The response field `silhouette_score: float | null` is replaced by
`separability: {...} | null` on both UMAP endpoints. In `routers/proteins.py` the
same field is **always** `None` (`compute_protein_umap_online` documents that
protein rows carry no labels); it is dead weight and is deleted rather than
carried forward.

**Frontend.**

`colorBy` drives `label_by` — one control, and the legend then shows exactly the
classes the index scored. Measuring microscope separability means colouring by
microscope, which is the honest way round: a number computed over classes the
reader cannot see on the plot is the situation where these values get
misread.

`colorBy` joins the react-query key so switching it refetches.

Header badge:

```
Separability (protein)  0.312 · 13 classes · 2 480 crops
```

The counts sit next to the score because the index is not comparable across
subsets of different size and class count. They come from the response, not
recomputed in the browser from `points`: `points` has already been pruned to
crops that carry coordinates, so a browser-side count would disagree with what
was scored.

### Testing

Unit tests for each label axis, for the unlabeled-drop, and for both guards
(<10 labeled, <2 classes). A perturbation check that removing the facet-derived
label mapping turns microscope scoring into `None` rather than silently scoring
protein.

---

## 2 · A control category per PTM

### What was asked

> "The controls will vary between each PTM (the control plasmid won't be the
> same), so there would need to be a control category for each PTM."

Correct, and already recorded as a known limitation in `CLAUDE.md`: *"Kontrola
nenese modifikaci, ke které patří … Vědomá cena za plochý slovník (rozhodnuto
2026-08-03)."* This PR pays that cost back.

### Production state

`ptms` holds 11 rows; three are in use by experiments: `Unmodified` (51
experiments), `Control` (28), `Detyrosination` (26). Every one of the 28
controls is a detyrosination control — their names say so
(`CLIP170 DÉTYR-CTRL`, `MAP7 ctrl détyr manip 2`, …). The migration is therefore
unambiguous and needs no guesswork.

### Design

`ptms.controls_ptm_id` — a nullable self-referencing FK, added through
`ensure_schema_updates()`. `backend/migrations/*.sql` is documentation that
nothing executes; a column added only there exists on a fresh database and is
silently missing in production.

Validation lives in the PTM router, on create and update:

- `controls_ptm_id` may be set **only** when `kind == "control"`. A modification
  that controls something, or an `Unmodified` row that does, is meaningless.
- the target must exist and must be `kind == "modification"` — no control of a
  control, no chains.
- no self-reference.

Production migration: an idempotent `scripts/ptm_control_link_backfill.sql`
renames row 15 `Control` → `Detyrosination control` (abbreviation `deTyr ctrl`)
and sets `controls_ptm_id = 2`. **No experiment row is touched** — all 28 keep
pointing at `ptm_id = 15`, which is now a properly-labelled row instead of a
pooled one. Future PTM conditions get their own control row.

`pointMarker.ts` needs **no change**: the marker class is read from `kind`, never
from `name` and never from the link, so `Acetylation control` draws as a
translucent ring the moment it is created. This is the property that made the
flat vocabulary survivable and it is why the fix is additive.

Alternative considered and rejected: an `is_control` boolean on the experiment,
with `ptm_id` always naming the modification. It reads well and makes
"filter deTyr, get both arms" free, but it cannot record *which* control
reagent was used — and the differing control plasmid is precisely the fact Théo
is reporting. It would also require migrating all 28 experiment rows and would
leave `PTMKind.CONTROL` dead.

**Frontend.** `/dashboard/ptms` shows a required "Controls for" select, listing
modification rows, whenever `kind = control`. Nothing else changes: the PTM name
carries the pairing everywhere it is displayed.

**MCP.** The PTM create/update tools gain the field; `SERVER_VERSION` goes up
because the tool contract changed. Tests pin the name set and the version.

### Testing

Both sides of the validation boundary: a control accepts a modification target,
and a modification rejects a target; a control rejects a `control` or `none`
target; self-reference rejected. The backfill script is run twice against a
scratch database to prove idempotence.

---

## 3 · Folders for experiments

### What was asked

> "Do you think it would be possible to add subfolders to the 'Experiment'
> section so I can organize everything by experiment? There are starting to be a
> lot of experiments, and I'm getting a bit lost."

105 experiments today, up from 46 two weeks ago. The complaint is real.

### Design

New model `ExperimentFolder` — `id, name, parent_id, group_id, user_id,
created_at` — plus `experiments.folder_id`. `parent_id` is a plain Integer, as
in `DocumentFolder`, so the tree reparents freely.

**Not a copy of `DocumentFolder`.** The `visibility` axis is deliberately
omitted. Experiments are group-readable through `experiment_owner_filter`
regardless of where they sit, so a "private folder" would mean the folder is
hidden while the experiment inside it is not — a control that lies about what it
does. A folder's group is stamped from `default_group_id()`: one group → shared,
several → left owner-only. Same rule and same reason as everywhere else in the
codebase.

**Placement is organisation, not publication.** Moving an experiment into a
folder does **not** touch `experiments.group_id`. This is the opposite of the
document library, where a folder move restamps the document — and the difference
is deliberate: documents derive their audience from their folder, experiments do
not. Without this, dropping a colleague's experiment into a folder would
silently change who can read it.

`PATCH /api/experiments/{id}/folder` is the **fifth group-writable exception**,
for the reason the previous four exist: Théo owns most of the corpus, so
owner-only filing would leave the tree empty for everyone else. It is a separate
endpoint rather than a field on `ExperimentUpdate`, which keeps `extra="forbid"`
— an old client sending `folder_id` to the generic PATCH gets a 422 instead of a
silent discard. Renaming and deleting an experiment stay owner-only: the
container belongs to whoever uploaded it.

Deleting a folder **dissolves** it — subfolders and experiments move up to the
parent — so nothing is ever lost behind a deleted folder.

`GET /api/experiments` gains `folder_id` and `include_subfolders`, with a
sentinel for "unfiled".

**MCP.** New tools for listing the tree, creating/renaming/moving/deleting a
folder, and filing an experiment. Mostly `tools.yaml` entries;
`SERVER_VERSION` up.

**Frontend.** `/dashboard/experiments` gains a folder sidebar reusing the tree
logic already written for the document library, a breadcrumb, and an "Unfiled"
bucket.

Explicitly out of scope: a folder facet in the UMAP filter panel.

### Testing

Folder ACL (a folder in a group you do not belong to is invisible), dissolve
semantics, and both sides of the write boundary — filing is group-writable while
`update_experiment` / `delete_experiment` keep their owner re-check.

---

## Cross-cutting

Every new write endpoint is exercised **once against the real database** before
it is called done. `mock_db` is an `AsyncMock` and models no attribute expiry, so
a fully green suite cannot rule out a `MissingGreenlet` 500 on a serialized
expired `updated_at`. Each handler call in such a probe gets its own session,
because `async_session_maker` has `expire_on_commit=False` and a shared session
produces failures that look like application bugs but are artefacts of the probe.

Deployment is the production compose file, per service, followed by a live check
through the connector.
