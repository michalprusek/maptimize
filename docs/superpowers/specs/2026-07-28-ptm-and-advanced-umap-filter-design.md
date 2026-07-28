# PTM entity + advanced dashboard UMAP filter — design spec

**Date:** 2026-07-28
**Branch:** `feature/ptm-and-advanced-dashboard-filter` (off `main`)
**Author:** Michal + Claude

## Goal

Two connected changes:

1. **PTM** — the post-translational modification of the microtubules the cells were
   grown on becomes first-class shared reference data, exactly like `Microscope`:
   own sidebar entry, own CRUD page, assignable per experiment.
2. **Advanced dashboard filter** — the dashboard UMAP gains a faceted filter over
   **experiment, microscope, protein and PTM**, replacing today's single-select
   microscope dropdown.

## Why PTM matters here

This is the Janke lab; the tubulin code *is* the research subject. Which PTM decorates
the microtubule lattice (tyrosination, detyrosination, Δ2, acetylation,
polyglutamylation, polyglycylation…) is a primary experimental variable for MAP binding
and bundling — at least as important as which MAP protein was added. Today it is
recorded nowhere, so the dashboard cannot separate a MAP effect from a lattice effect.

## Decisions (locked)

1. **PTM is shared reference data** — modelled on `Microscope`/`MapProtein`: no
   `user_id`, one list for the whole lab, any logged-in user may view/create/edit/delete.
   Consistent with the two existing reference tables.
2. **One PTM per experiment** — nullable FK `experiments.ptm_id`, mirroring
   `microscope_id`. *Not* a many-to-many. Rationale: the request was explicitly "another
   bullet next to microscope and protein", the acquisition metadata pattern already
   exists and is understood, and a join table would fork the filter, the UMAP query and
   the MCP surface for a combination the lab does not currently record. If combined
   modifications (e.g. deTyr **+** polyE) turn out to be needed, they can be modelled as
   their own named PTM row ("deTyr + polyE") without a schema change — and a true M2M
   remains a later, additive migration.
3. **Assignment is group-writable** — `PATCH /api/experiments/{id}/ptm` is a dedicated
   endpoint with `get_experiment_for_user` and **no owner re-check**, exactly like
   `update_experiment_microscope`. Same reasoning as CLAUDE.md records for microscopes:
   Theo owns 40 of 46 experiments, so an owner-only assignment would leave the PTM facet
   covering almost nothing and make the filter useless. It stays a **separate endpoint,
   not a field on `ExperimentUpdate`** — one field must not have two ACLs.
4. **No denormalization onto images/crops.** PTM lives on `Experiment` only. The UMAP
   queries already join `CellCrop → Image → Experiment` and `Image → Experiment`, so a
   `WHERE Experiment.ptm_id …` needs no cascade endpoint. (Same call the microscope spec
   made; protein is the exception because it colours individual points.)
5. **Filtering stays server-side.** The filter is applied in SQL, not in the browser, so
   `silhouette_score` and the totals reflect what is actually on screen. Given the
   measured batch effect (microscope is ~99.8% decodable from embeddings vs ~4.7% for
   protein), a silhouette computed over a *filtered* subset is the scientifically
   meaningful number, and that is only available server-side where the embeddings are.

## Data model

### New model `PTM` (`backend/models/ptm.py`)

Mirror of `Microscope`: shared reference data, no `user_id`, no `group_id`.

| column | type | notes |
|--------|------|-------|
| `id` | Integer PK | |
| `name` | String(100), **unique**, indexed, not null | e.g. "Polyglutamylation" |
| `abbreviation` | String(50), nullable | e.g. "polyE", "deTyr", "Δ2" |
| `modified_residue` | String(100), nullable | e.g. "α-tubulin K40", "α-tubulin C-terminal tail" |
| `enzyme` | String(255), nullable | writer/eraser, e.g. "TTLL1–TTLL7 / CCP1–CCP6" |
| `description` | Text, nullable | |
| `color` | String(7) hex, nullable | UMAP legend; auto-assigned via `utils/colors.py` |
| `created_at` | timezone-aware timestamp | |

The four optional metadata fields mirror the microscope's four
(`manufacturer`/`model`/`objective`/`magnification`) in shape, so schema, router,
detailed response and CRUD page all follow the existing template exactly.

### `Experiment.ptm_id`

```python
ptm_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ptms.id"), nullable=True)
ptm: Mapped[Optional["PTM"]] = relationship()
```

matching how `microscope` is declared — a plain one-directional relationship with no
`back_populates` and no `lazy=`; eager loading is done per query site with
`selectinload(Experiment.ptm)`, which must be added at all three places that already
eager-load the microscope (`load_experiment_response`, `list_experiments`,
`get_experiment`).

### Migration (existing pattern — NOT Alembic)

1. `Mapped[]` column on `Experiment`.
2. `("experiments", "ptm_id", "INTEGER REFERENCES ptms(id)")` appended to the `updates`
   list in `database.ensure_schema_updates()`.
3. `backend/migrations/010_add_ptm.sql` mirroring it — `CREATE TABLE IF NOT EXISTS ptms
   (...)` + `ALTER TABLE experiments ADD COLUMN IF NOT EXISTS ptm_id INTEGER REFERENCES
   ptms(id)`, exactly as `009_add_microscope.sql` does.

**All 46 existing experiments start with `ptm_id = NULL`.** That is the single most
important consequence for the filter design: "unassigned" must be a first-class filter
value or the PTM facet is dead on arrival.

### Seeding

`010_add_ptm.sql` seeds the canonical tubulin-code modifications with
`ON CONFLICT (name) DO NOTHING`, so the lab starts from a usable vocabulary rather than
an empty page: Tyrosination (Tyr), Detyrosination (deTyr), Δ2-tubulin, Δ3-tubulin,
Acetylation (K40), Polyglutamylation (polyE), Monoglutamylation, Polyglycylation,
Phosphorylation, and "Unmodified / native". Names are editable — this is a starting
vocabulary, not a closed enum.

## Backend endpoints

### New router `backend/routers/ptms.py` (`/api/ptms`)

Straight mirror of `routers/microscopes.py`; `get_current_user`, not
`require_interactive_user`, so the MCP connector passes.

- `GET ""` — list + per-PTM `experiment_count`
- `POST ""` — create; 400 on duplicate name; auto-picks an unused colour
- `GET "/{id}"` — fetch one
- `PATCH "/{id}"` — update; re-checks name uniqueness
- `DELETE "/{id}"` — **409** if any experiment references it

### Experiment assignment

- `PATCH /api/experiments/{id}/ptm` — group-writable; `ptm_id` is a **query parameter**
  (`Optional[int] = Query(default=None)`, omit to clear), mirroring
  `update_experiment_microscope` exactly rather than inventing a body. 404 on unknown
  PTM. The response is rebuilt through `load_experiment_response()` — **never**
  serialise the session object, because `updated_at` is expired after the UPDATE and
  serialising an expired attribute in async context raises `MissingGreenlet` → HTTP 500.
- `ptm_id` accepted by **`ExperimentCreate`** (the owner creates it) but **not** by
  `ExperimentUpdate` (`extra="forbid"` → a stale client sending it gets 422, not a
  silent drop).
- `ptm: Optional[PTMResponse]` added to `ExperimentResponse`.

### UMAP filter (`backend/routers/embeddings.py`)

`GET /api/embeddings/umap` gains four **repeatable** query params, replacing the single
`microscope_id`:

| param | meaning |
|-------|---------|
| `experiment_id` | repeatable; was single-valued |
| `microscope_id` | repeatable; **replaces** the old single-select |
| `protein_id` | repeatable (matches the *crop's* protein, which is what colours the point) |
| `ptm_id` | repeatable |

Semantics: **OR within a facet, AND across facets** — the only combination that reads
naturally ("AeryScan or 3D SIM, with MAP7 or Tau4R").

**Unassigned** is expressed as the reserved id `0` (`UNASSIGNED_FACET_ID`) — ids are
positive serials, so `0` can never collide. One shared helper builds every facet clause:

```python
def facet_clause(column, ids):
    """OR within a facet; the reserved id 0 means 'not assigned'."""
```

so the four facets cannot drift apart. `experiment_id` uses the same helper but rejects
`0` (every crop has an experiment; an "unassigned experiment" filter is meaningless).

**The `MIN_POINTS_FOR_UMAP` 400 is scoped to unfiltered views.** Today any filter that
narrows below the threshold returns `400 "Need at least N crops with embeddings"`, which
is wrong: coordinates are pre-computed from one global fit and the read path never fits,
so plotting 3 points is valid. The gate exists to protect *fitting*, not *reading*. With
filters active the endpoint returns whatever matches (possibly zero) with
`silhouette_score = None`. Without filters the existing 400 is unchanged.

Validation of unknown facet ids keeps today's behaviour: a stale/deleted reference id
→ **404** with a clear message, rather than silently matching nothing. One shared
validator covers all four facets.

### Facet metadata

Both UMAP responses gain a `facets` block so the filter panel can show real point
counts and grey out empty options. It is computed by one cheap grouped query over the
*same scope but before facet filters*, selecting no embeddings:

```
experiment_id, experiment_name, microscope_id, protein_id, ptm_id, point_count
```

grouped per experiment (≈46 rows). Because microscope and PTM live on `Experiment`, the
frontend derives all four facets' counts from this one small payload. No extra endpoint,
one round trip, always consistent with the current view mode.

### Point payload

Points gain `experiment_name`, `microscope_id`/`microscope_name`/`microscope_color`,
`ptm_id`/`ptm_name`/`ptm_color` and `protein_id`. This is what lets the plot **colour by
any facet** — the fastest way to see the batch effect is to recolour the existing points
by microscope rather than re-query.

## MCP (SSOT rule — every new/changed endpoint goes into MCP)

`mcp-server/maptalk_mcp/tools.yaml`: `list_ptms`, `get_ptm`, `create_ptm`, `update_ptm`
(all generic handlers), `delete_ptm` (`destructiveHint`), and `assign_experiment_ptm`
mirroring `assign_experiment_microscope` including its "the whole group may set it"
wording. `create_experiment` gains `ptm_id`; `update_experiment` does **not** (it rejects
the field, like `microscope_id`). Bump `SERVER_VERSION` to **2.4.0** and update the tests
that pin the tool-name set and version.

`services/sql_query_service.py`: whitelist `ptms` **and** `microscopes` and extend
`SQL_SCHEMA_HINT` — with the mirrored copy in the `query_database` tool description
updated in the same change (CLAUDE.md: the hint is duplicated in two places and both
must move together). Whitelisting `ptms` without `microscopes` would leave the agent
able to ask about one acquisition dimension and not the other.

## Frontend

### Sidebar (`components/layout/AppSidebar.tsx`)

New nav item after Microscopes: `/dashboard/ptms`, lucide `Atom` icon, `navigation.ptms`
key in **both** `messages/en.json` and `messages/fr.json`.

### PTM page (`app/dashboard/ptms/page.tsx`)

Mirror of the microscopes page: TanStack Query `["ptms"]`, create/update/delete
mutations with `invalidateQueries`, card grid with edit/delete, one `Dialog` reused for
create+edit, `ConfirmModal` for delete, `EmptyState` when empty. New `ptmsPage` i18n
namespace in en + fr. New `lib/api.ts` methods and `PTM` types.

### Experiment assignment

PTM dropdown next to the microscope dropdown on the experiment detail page, driven by a
`useAssignPtm` hook mirroring `useAssignMicroscope`.

### Advanced filter panel (`components/visualization/UmapFilterPanel.tsx`)

Replaces the single-select microscope `<select>` at `UmapVisualization.tsx:508`. A new,
self-contained component so `UmapVisualization.tsx` does not grow a fourth
responsibility.

**Constraint that shapes this:** the project has no UI kit — no Radix, shadcn, MUI or
Headless UI. Everything is native elements plus hand-rolled Tailwind `@layer components`
classes (`glass-card`, `input-field`, `btn-ghost`), framer-motion and lucide icons. The
existing `ColorTagSelect` is strictly single-select (`value: number | null`), so it
cannot back a multi-select facet. The closest precedent is
`components/shared/ImageGalleryFilters.tsx` — a `Filter`-icon button that expands an
`AnimatePresence` height-0→auto panel of toggleable colour pills — and this panel follows
it, fixing two of its flaws (hardcoded English strings, single-select only).

- A **collapsed filter bar**: `Filter` button showing the active-filter count, the
  removable chips for what is currently applied, and **Clear all**.
- **Expanded panel**: four facet sections — Experiment, Microscope, Protein, PTM. Each is
  a wrap of toggleable colour pills carrying the value's colour dot, name and point
  count, plus an **Unassigned** pill (omitted for Experiment, where it is meaningless).
  Facets with more than ~12 values (Experiment, at 46) get a search input. Values with
  zero points in the current view render dimmed but remain clickable, so "no data" is
  visibly different from "does not exist".
- A **Colour by** selector: protein (default) / microscope / PTM / experiment. This
  recolours points already in memory — no refetch — and is the fastest way to eyeball the
  batch effect.
- Live "showing X of Y" readout next to the silhouette badge.
- Filter state mirrored into the URL query string (`useSearchParams` +
  `router.replace`), so a filtered view is a shareable link.

Two existing invariants must be preserved, both currently satisfied only for microscope:

1. **A filter whose control fails to load must clear itself** (`UmapVisualization.tsx:173`)
   — otherwise the plot stays silently constrained with no visible way to undo it.
   Generalised to all four facets instead of duplicated four times.
2. **Every filter value belongs in the React Query key** (`["umap", …]`), with arrays
   sorted for a stable key, or cached results bleed across filters. `useAssignPtm` must
   invalidate `["umap"]` for the same reason `useAssignMicroscope` does.

`api.getUmapData`'s positional signature (`experimentId, umapType, microscopeId`) does
not survive a fourth filter; it becomes a single options object. There is exactly one
call site.

Colouring and the legend are currently hardwired to `protein_color`/`protein_name`
(lines 238–246, 446, 460–474); they become a `colorBy` dimension reading the
corresponding pair off the point. The two tooltips gain PTM and microscope rows.

All strings via `useTranslations` — no hardcoded text (CLAUDE.md).

## Testing

- **Backend unit** (`backend/tests/unit/`): PTM router (create / duplicate-name 400 /
  update / delete-409-when-referenced / list-with-count); `facet_clause` semantics
  including the unassigned sentinel and the AND-across/OR-within combination; the
  `MIN_POINTS` gate firing only for unfiltered views; the PTM assignment endpoint's
  group-write ACL **and** the fact that the widening did not leak into
  `ExperimentUpdate` (mirroring `test_crop_curation_acl.py`'s both-sides discipline).
- **Real-DB probe** of every new write endpoint (`ptm` assignment, PTM CRUD) via
  `docker exec`, per CLAUDE.md — `AsyncMock` cannot model attribute expiry, so the
  `MissingGreenlet` class of bug is invisible to unit tests.
- **MCP**: registry test picks up the new tools; version pin updated.
- Then code-simplifier across the diff (CLAUDE.md), prod rebuild, live verification.

## Out of scope (YAGNI)

- Many-to-many PTM per experiment (see decision 2 — a combined PTM row covers it).
- PTM denormalised onto images/crops.
- PTM-aware bundleness modelling or stratified silhouette reporting.
- Saved/named filter presets.
- **Export/import round-trip.** `services/export_service.py`, `import_service.py` and
  `routers/export_import.py` never referenced `microscope_id` either, so they silently
  drop the assignment. PTM inherits that gap rather than fixing it here; worth a separate
  pass, since a round-tripped experiment currently loses both acquisition fields.
