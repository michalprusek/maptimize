# Microscopes feature — design spec

**Date:** 2026-07-24
**Branch:** `feature/microscopes` (off `main`)
**Author:** Michal + Claude

## Goal

Let the lab define **microscopes** as shared reference data, assign a microscope to
each experiment, and on the dashboard UMAP **filter the plot by microscope** (switch
between "All" and each individual microscope).

## Decisions (locked)

1. **Microscope is shared reference data** — modelled like `MapProtein`: no `user_id`,
   one list for the whole lab, anyone logged in can view/create/edit/delete. Consistent
   with proteins. Writes are *not* owner-protected (same as proteins — this is deliberate,
   see CLAUDE.md note that `MapProtein` has no `user_id`).
2. **Dashboard UMAP = filter over the single shared fit**, NOT a separate fit per
   microscope. Selecting a microscope adds a `WHERE Experiment.microscope_id = X` to the
   existing precomputed-coordinate read path. "All" (default) shows everything. No new
   UMAP computation, no new scoping in `umap_service`.
3. **Minimal microscope fields** + `objective` + `magnification`.

## Data model

### New model `Microscope` (`backend/models/microscope.py`)

Mirror of `MapProtein` (shared reference data, no `user_id`, no `group_id`).

| column | type | notes |
|--------|------|-------|
| `id` | Integer PK | |
| `name` | String(100), **unique**, indexed, not null | e.g. "Zeiss LSM 880" |
| `manufacturer` | String(100), nullable | e.g. "Zeiss" |
| `model` | String(100), nullable | model designation |
| `objective` | String(100), nullable | e.g. "Plan-Apochromat 63×/1.4 Oil" |
| `magnification` | String(50), nullable | **String** for flexibility — "63×", ranges "10×–100×" |
| `description` | Text, nullable | |
| `color` | String(7) hex, nullable | for UMAP legend; auto-assigned like proteins |
| `created_at` | timezone-aware timestamp | |

Relationship: none needed back to experiments (one-directional FK from `Experiment`,
same as `Experiment.map_protein` today). Register the model in `models/__init__.py` so
`Base.metadata.create_all` picks it up.

### `Experiment.microscope_id`

Add a nullable FK column to `backend/models/experiment.py`:

```python
microscope_id: Mapped[Optional[int]] = mapped_column(
    ForeignKey("microscopes.id"), nullable=True
)
microscope: Mapped[Optional["Microscope"]] = relationship()
```

**No denormalization onto `images` / `cell_crops`.** Unlike proteins (which cascade
`map_protein_id` down to images+crops for per-point coloring), the microscope only needs
to live on `Experiment`: the UMAP queries already join `CellCrop → Image → Experiment`
(cropped) and `Image → Experiment` (fov), so `WHERE Experiment.microscope_id = X` is a
trivial filter with no denormalization and no assignment-cascade endpoint.

### Migration (existing pattern — NOT Alembic)

1. Add the `Mapped[]` column in `experiment.py`.
2. Add `("experiments", "microscope_id", "INTEGER REFERENCES microscopes(id)")` to the
   `updates` list in `database.ensure_schema_updates()`.
3. New `backend/migrations/009_add_microscope.sql` mirroring it: `CREATE TABLE IF NOT
   EXISTS microscopes (...)` + `ALTER TABLE experiments ADD COLUMN IF NOT EXISTS
   microscope_id INTEGER REFERENCES microscopes(id)`. (The `microscopes` table itself is
   auto-created at startup by `create_all`; the SQL file is for manual/prod parity and
   documentation, consistent with `008_add_bbox_angle.sql`.)

## Backend endpoints

### New router `backend/routers/microscopes.py` (`/api/microscopes`)

Mirror of `routers/proteins.py`. All require auth, none user-scoped (shared reference
data). Uses `get_current_user` (not `require_interactive_user`) so the MCP connector
passes.

- `GET ""` — list microscopes + per-microscope `experiment_count`.
- `POST ""` — create; unique-name check (400 on conflict); auto-pick unused color.
- `GET "/{id}"` — fetch one.
- `PATCH "/{id}"` — update; re-check name uniqueness.
- `DELETE "/{id}"` — refuse with **409 Conflict** if any experiment references it (mirrors
  proteins refusing delete when images reference the protein).

Register in `backend/routers/__init__.py` at prefix `/microscopes`.

### Schemas `backend/schemas/microscope.py`

- `MicroscopeCreate` — `name` (required), `manufacturer`, `model`, `objective`,
  `magnification`, `description`, `color` (regex `^#[0-9A-Fa-f]{6}$`).
- `MicroscopeUpdate` — all optional.
- `MicroscopeResponse` — `id`, `name`, `manufacturer`, `model`, `objective`,
  `magnification`, `color`. (Basic — embedded in `ExperimentResponse`.)
- `MicroscopeDetailedResponse` — full fields + `experiment_count`, `created_at`;
  `from_microscope(microscope, experiment_count)` classmethod.

### Experiment assignment

- Add `microscope_id: Optional[int]` to **`ExperimentCreate`** and **`ExperimentUpdate`**
  (microscope has no cascade → no dedicated endpoint needed, unlike protein). On create/
  update, verify the microscope exists (404 otherwise) when provided.
- Add `microscope: Optional[MicroscopeResponse]` to `ExperimentResponse`.

### UMAP filter (`backend/routers/embeddings.py`)

- Add `microscope_id: Optional[int] = Query(None)` to `GET /api/embeddings/umap`.
- In both `_get_cropped_umap` and `_get_fov_umap`, add `WHERE Experiment.microscope_id ==
  microscope_id` when provided. Precomputed coords only — no fit. The existing
  `experiment_owner_filter` still applies.

### DRY: shared color picker

Extract the palette + "pick unused color" logic from `routers/proteins.py` into a shared
`backend/utils/colors.py` (e.g. `pick_unused_color(existing_colors)`), used by both the
proteins and microscopes routers. Run code-simplifier afterwards (CLAUDE.md).

## MCP (SSOT rule — every new/changed endpoint goes into MCP)

In `mcp-server/maptalk_mcp/tools.yaml` (hot-reloaded YAML, no rebuild for tool text):

- `list_microscopes` (`http_json` GET), `create_microscope` (`http_post_json` POST),
  `update_microscope` (`http_post_json` PATCH), `delete_microscope` (`http_json` DELETE),
  `get_microscope` (`http_json` GET). Annotations: reads `readOnlyHint`, delete
  `destructiveHint`.
- Update `create_experiment` / `update_experiment` tool params to include `microscope_id`.
- Bump `SERVER_VERSION` in `server.py` (tool-contract change → rebuild `maptimize-mcp`
  image since server.py changes).

*(Optional, out of scope unless asked: whitelist `microscopes` in
`sql_query_service` + schema hint. Skipped for YAGNI.)*

## Frontend

### Sidebar (`components/layout/AppSidebar.tsx`)

Add nav item `{ name: t("microscopes"), href: "/dashboard/microscopes", icon: Microscope }`
(lucide `Microscope` icon) to the `navigation` array. Add `microscopes` key to the
`navigation` namespace in **both** `messages/en.json` and `messages/fr.json`.

### Microscopes page (`app/dashboard/microscopes/page.tsx`)

Copy the proteins-page pattern (`app/dashboard/proteins/page.tsx`):
- TanStack Query `["microscopes"]` + create/update/delete mutations with
  `invalidateQueries`.
- Header + "New microscope" button; responsive card grid with edit/delete per card;
  `EmptyState` when empty.
- One `Dialog` reused for create + edit; `ConfirmModal` for delete.
- New i18n namespace `microscopesPage` in en.json + fr.json.
- New API methods + types in `lib/api.ts`: `getMicroscopes`, `createMicroscope`,
  `updateMicroscope`, `deleteMicroscope`; types `Microscope`, `MicroscopeCreate`,
  `MicroscopeUpdate`.
- All UI strings via i18n (CLAUDE.md — no hardcoded text).

### Experiment form

Add a "Microscope" dropdown (fetches `/api/microscopes`, optional/"none" allowed) to the
experiment create/edit form on the experiments page. Sends `microscope_id`.

### Dashboard UMAP switcher (`components/visualization/UmapVisualization.tsx`)

Next to the existing fov/cropped segmented toggle, add a **microscope dropdown** (a
`Select`, not buttons — avoids overflow with many microscopes): default option "All" +
one option per microscope (from `GET /api/microscopes`). Selection drives a
`microscopeId` state that is (a) part of the React Query key `["umap", experimentId,
viewMode, microscopeId]` and (b) passed to `api.getUmapData(experimentId, viewMode,
microscopeId)` → `?microscope_id=`.

## Testing

- **Backend unit** (`backend/tests/unit/`): microscopes router — create, unique-name
  conflict (400), update, delete-409-when-referenced, list-with-count. Handlers called
  directly with `current_user=SimpleNamespace(...)`, `db=mock_db`; services mocked at the
  router boundary. Plus a test for the embeddings UMAP `microscope_id` filter (asserts the
  filter is applied to the query).
- **MCP**: registry loads the new `tools.yaml` entries (existing MockTransport test
  harness).
- **Frontend**: follow existing patterns; e2e optional. Priority is backend coverage
  (per project testing posture) and keeping the suite green.
- After implementation: run **code-simplifier** across changed code (DRY/SSOT per
  CLAUDE.md), then rebuild via `docker-compose.prod.yml` (backend + mcp + frontend) and
  live-verify: create microscope → assign to experiment → dashboard UMAP filter → MCP
  `list_microscopes`.

## Out of scope (YAGNI)

- Separate UMAP fit per microscope (explicitly rejected — filter only).
- Per-point microscope denormalization onto images/crops.
- Microscopy parameters beyond objective/magnification (NA, camera, pixel size) — can be
  added later via the same migration pattern.
- `sql_query_service` whitelist for `microscopes`.
- Owner-scoped microscopes (shared reference data chosen instead).
