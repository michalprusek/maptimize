# PTM control category and a marker channel on the projections

**Date:** 2026-08-03
**Requested by:** Theo Buson (Dr. Janke lab), via Michal

## The request

> For the PTMs, I always run a control alongside (i.e., transfecting with an
> inactive enzyme), so I was wondering if you could add a "control" category for
> the PTMs. I'd also like to be able to distinguish between PTMs, controls, and
> non-PTM samples on the UMAP plot. To do that, I was thinking — while keeping
> the same colors — we could perhaps make the controls slightly transparent and
> place a black dot in the center of the PTM points.

Two asks, one data and one visual:

1. The PTM vocabulary needs a value for "this sample went through the
   transfection, but with an inactive enzyme".
2. The projections need a **second visual channel**, independent of colour, that
   splits every point three ways: non-PTM sample / PTM / control.

## What already exists

- `ptms` table + CRUD page (`/dashboard/ptms`), 10 seeded rows of the tubulin
  code, including `Unmodified` (abbreviation `none`).
- `experiments.ptm_id` and the group-writable `PATCH /api/experiments/{id}/ptm`.
- PTM as a full UMAP facet: filter pills, `colorBy: ptm`, legend, tooltip row,
  and the MCP tool `assign_experiment_ptm`.

## What does not exist

- Any notion of "control" — the string appears nowhere in the repo.
- Any second visual channel. Every point in every projection is drawn
  identically (`fillOpacity 0.75`, `stroke rgba(255,255,255,0.3)`); the only
  thing that distinguishes points is hue.

## Production state (2026-08-03)

61 experiments: **49 assigned `Unmodified`**, **12 assigned `Detyrosination`**,
0 unassigned. So Theo already uses `Unmodified` exactly as "non-PTM sample" —
the class he wants to see as the plain marker.

## Design

### 1. Control is a row in the PTM vocabulary, not a flag on the experiment

Michal chose this over an orthogonal `experiments.is_ptm_control` boolean. The
consequence is recorded here so it is not later mistaken for an oversight:

⚠️ **A control does not carry the modification it controls for.** An experiment
is *either* `Detyrosination` *or* `Control`; the pairing between "MAP7 deTyr" and
its inactive-enzyme control lives in the experiment names, not in the schema.
Ticking `Detyrosination` in the filter therefore returns the samples **without**
their controls. This is the accepted cost of keeping the vocabulary flat.

### 2. The class is read from a column, never from the row's name

```
ptms.kind  VARCHAR(20) NOT NULL DEFAULT 'modification'
           -- 'modification' | 'control' | 'none'
```

⚠️ **Not a Postgres enum.** `ensure_schema_updates()` adds columns with raw
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`; an enum column would need a
`CREATE TYPE` first, which that mechanism does not do. `document_folders.kind`
and `.visibility` are the existing precedent for a validated-in-Python string
column, and this follows them exactly.

⚠️ **Matching on `name == "Control"` was explicitly rejected.** Renaming the row
— or Theo creating "Control (inactive VASH)" instead — would silently return
every control to the plain marker, with nothing failing anywhere.

`PTMKind` is a `str, Enum` in `models/ptm.py` and is the SSOT: the SQLAlchemy
default, the Pydantic field type, and the seed all read it.

Row assignment:

| Row | kind | Why |
|-----|------|-----|
| `Control` (new) | `control` | The inactive-enzyme condition |
| `Unmodified` | `none` | It is the *absence* of a modification, not one |
| the other 8 | `modification` | Filled by the column default |

### 3. No auto-seeding of the `Control` row

`seed_default_data()` seeds PTMs only when the table is empty, on the stated
principle that "re-adding a row the lab deliberately deleted would be worse than
leaving the vocabulary short". Inserting `Control` on every startup would break
that principle for this one row.

So: `Control` joins `DEFAULT_PTMS` for **fresh** databases (including the test
DB), and production gets a **one-off, additive** SQL step:

```sql
UPDATE ptms SET kind = 'none' WHERE name = 'Unmodified';
INSERT INTO ptms (name, abbreviation, description, color, kind)
VALUES ('Control', 'ctrl',
        'Paired control for a PTM condition: the same transfection carried out '
        'with a catalytically inactive enzyme, so the lattice is unmodified.',
        '#94a3b8', 'control');
```

The column itself is added automatically by `ensure_schema_updates()`, so
existing rows land on `modification` without a data step.

### 4. The projections need no backend change at all

Points carry only `experiment_id`; the client already joins microscope and PTM
from the `facets` summary, and already fetches the full PTM list via
`GET /api/ptms` for the filter pills and `colorBy`. Once `kind` is on
`PTMResponse` / `PTMDetailedResponse`, the client can classify every point with
data it is already holding.

⚠️ **Do not put the class on the point payload.** It would repeat per crop
(hundreds per experiment) and create a second truth about which PTM regime an
experiment is in — the same reasoning that keeps microscope and PTM off the
point today.

`routers/embeddings.py`, `UmapFacetRow` and the UMAP/LDA services are therefore
untouched.

### 5. The marker channel

A pure module, `components/visualization/pointMarker.ts`:

```ts
export type PtmKind = "modification" | "control" | "none";
export function ptmKindOf(kind: string | null | undefined): PtmKind;
export function markerStyle(kind: PtmKind, color: string): MarkerStyle;
```

⚠️ **One vocabulary, not two.** The client reuses the backend's `kind` values
verbatim rather than translating them into a display-side enum
(`"ptm" | "control" | "plain"` or similar). A second set of names is a second
place to get the mapping wrong, and structural typing would let the wrong one
compile.

| `kind` | Meaning on the plot | Fill | Stroke | Centre dot |
|--------|--------------------|------|--------|-----------|
| `none` | non-PTM sample | colour @ 0.75 | `rgba(255,255,255,0.3)` | — |
| `modification` | PTM | colour @ 0.75 | `rgba(255,255,255,0.3)` | **black**, r = 0.42 × point radius |
| `control` | control | colour @ 0.18 | **the point's own colour**, full | — |

`ptmKindOf` maps anything else — an unassigned experiment, a `kind` the client
does not recognise, a reference list that failed to load — to `none`, i.e. the
plain marker the plot draws today. Failing the other way would silently relabel
every point as a control.

**Why controls also get a solid coloured ring.** Theo asked for "slightly
transparent". Opacity alone is a weak channel under overplotting: a translucent
point sitting on three opaque ones is indistinguishable from an opaque one. The
ring keeps his colour and his transparency while making the class readable in a
dense cluster.

**Rendering.** A custom `shape` on `<Scatter>` replaces the default symbol.
`<Cell>` props merge into the point object before reaching the shape, so colour
stays where it is today (`styleOf`) and the shape decides geometry only. Radius
is `√(size / π)` — 4.37 px at the current `ZAxis range={[60, 60]}` — so the base
point is pixel-identical to what recharts draws now.

**Reach.** The same `<Scatter>` renders the UMAP (cropped and FOV) and the LDA
discriminant, so all three get the channel from one change.

**Legend.** A second legend strip under the colour legend, with the three
markers drawn as they appear on the plot. Rendered **only when the plot contains
at least two classes**, so a lab with no PTM data sees no change.

### 6. Filter and colour-by come for free

`Control` is an ordinary `ptms` row, so it appears as a filter pill and a
`colorBy: ptm` group with no code. Its colour is a neutral grey `#94a3b8`:
invisible under the default `colorBy: protein`, and under `colorBy: ptm` grey is
the right thing for "not a modification".

### 7. The PTM CRUD page must expose `kind`

Without it, a `Control` row Theo creates himself gets `kind='modification'` and
the feature silently does nothing. `/dashboard/ptms` gets a "Kind" select
(Modification / Control / No modification) in the form and a badge on the card.
Both strings go through i18n into `en.json` and `fr.json`.

### 8. MCP

Per the SSOT rule, `create_ptm` and `update_ptm` gain a `kind` argument and
`list_ptms` explains the three values. `SERVER_VERSION` → `3.1.0` (the tool
contract changed); `tests/test_registry.py` and `tests/test_protocol.py` pin the
version and must be updated with it. `SQL_SCHEMA_HINT` in
`services/sql_query_service.py` and its mirror in the `query_database` tool
description both gain the column — the model cannot guess a column name.

## Testing

- **Backend unit** (`tests/unit/test_ptms_router.py`): `kind` round-trips through
  create and update, defaults to `modification`, an unknown value is 422, and
  `PTMResponse` carries it.
- **Frontend unit** (`e2e/unit/pointMarker.spec.ts`): `ptmKindOf` normalisation
  (including the unknown/null fallback to `none`) and the `kind` → style mapping,
  in particular that only `modification` gets the centre dot and only `control`
  loses its fill. Verified by perturbation — each assertion must be seen to fail
  with the mapping removed.
- **MCP** (`mcp-server/.venv/bin/python -m pytest`): tool set and version pins.

## Deployment

1. `scripts/backup.sh` if the last backup is not from today.
2. Rebuild backend and frontend with `docker-compose.prod.yml`; the restart adds
   the column.
3. Run the one-off SQL from §3.
4. Verify live: `Control` appears in the PTM select on an experiment card, in
   the UMAP filter, and a point assigned to it renders as a translucent ring.

## Explicitly out of scope

- Pairing a control to the PTM it controls for (see §1).
- A separate "PTM regime" filter facet — the PTM facet already covers it.
- Any change to how the LDA fit treats controls; `Control` is a PTM value, and
  the discriminant is fitted on **proteins**, so it is unaffected.
