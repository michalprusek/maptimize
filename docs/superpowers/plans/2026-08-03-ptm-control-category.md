# PTM Control Category Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the PTM vocabulary a `Control` value and draw non-PTM / PTM / control samples as three visually distinct markers on every projection, without changing their colours.

**Architecture:** A `kind` column on `ptms` (`modification` | `control` | `none`) classifies each vocabulary row. The classification reaches the client through the existing `GET /api/ptms` call the plot already makes for its filter pills, so no projection endpoint changes and the point payload does not grow. On the client a pure module maps `kind` to a marker style, and a custom recharts `shape` draws it — colour stays in `styleOf`, geometry moves to the shape.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Pydantic v2 (backend), Next.js + React + recharts 2.15 + next-intl (frontend), Playwright runner for pure-logic unit tests, pytest for backend unit tests.

**Spec:** `docs/superpowers/specs/2026-08-03-ptm-control-category-design.md`

## Global Constraints

- Column values are exactly `modification`, `control`, `none`. Anything else — including `null` and an unrecognised string — is treated as `none` by the client.
- `ptms.kind` is `VARCHAR(20) NOT NULL DEFAULT 'modification'`. **Never a Postgres enum**: `ensure_schema_updates()` only issues `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` and cannot `CREATE TYPE`.
- **Never branch on a PTM row's `name`.** The class is read from `kind` only.
- Every user-visible string goes through `useTranslations()` and is added to **both** `frontend/messages/en.json` and `frontend/messages/fr.json`.
- ⚠️ Edit `messages/*.json` as **plain text** with the Edit tool. Never round-trip them through a JSON parser — it silently collapses duplicate keys and has caused a real UI bug in this repo.
- Do not add the class to any projection point payload (`UmapPointResponse`, `UmapFovPointResponse`, `DiscriminantPointResponse`) or to `UmapFacetRow`.
- Backend unit tests run with the fast runner (see `reference_fast_unit_test_runner` memory); `run-coverage.sh` is the full gate.
- The branch is `feat/ptm-control-category`, already created from `origin/main`.

---

### Task 1: The `kind` column, the enum, and the schemas

**Files:**
- Modify: `backend/models/ptm.py`
- Modify: `backend/schemas/ptm.py`
- Modify: `backend/database.py` (the `updates` list inside `ensure_schema_updates`, near the `document_folders` entries at the end)
- Test: `backend/tests/unit/test_ptms_router.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `models.ptm.PTMKind` — `str, Enum` with members `MODIFICATION = "modification"`, `CONTROL = "control"`, `NONE = "none"`.
  - `models.ptm.PTM.kind` — `Mapped[str]`.
  - `schemas.ptm.PTMCreate.kind: PTMKind` (default `PTMKind.MODIFICATION`), `PTMUpdate.kind: Optional[PTMKind]`, `PTMResponse.kind: str`, `PTMDetailedResponse.kind: str`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/unit/test_ptms_router.py`. First extend the existing `_ptm()` helper so it carries the new attribute — `PTMDetailedResponse.from_ptm` will read it:

```python
def _ptm(**kw):
    base = dict(
        id=3,
        name="Polyglutamylation",
        abbreviation="polyE",
        modified_residue="α/β-tubulin C-terminal tails",
        enzyme="TTLL1-TTLL7",
        description=None,
        color="#ec4899",
        kind="modification",
        created_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)
```

Then append these tests to the same file:

```python
async def test_create_defaults_to_a_modification(mock_db):
    """A PTM created without `kind` is an ordinary modification.

    The default has to live in the schema, not only in the column: the router
    builds PTM(**model_dump()), so an absent field would pass None and hit the
    NOT NULL column instead of falling back.
    """
    mock_db.execute.side_effect = [
        make_result(scalar=None),              # name uniqueness check
        make_result(fetchall=[("#3b82f6",)]),  # colours already in use
    ]
    _populate_pk(mock_db)
    out = await mod.create_ptm(
        data=PTMCreate(name="Acetylation"), current_user=_user(), db=mock_db
    )
    assert out.kind == "modification"
    assert mock_db.add.call_args[0][0].kind == "modification"


async def test_create_persists_the_control_kind(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=None),
        make_result(fetchall=[("#3b82f6",)]),
    ]
    _populate_pk(mock_db)
    out = await mod.create_ptm(
        data=PTMCreate(name="Control", kind="control"),
        current_user=_user(),
        db=mock_db,
    )
    assert out.kind == "control"
    # A str, not the enum member: it goes straight into a VARCHAR column, and an
    # enum object reaching asyncpg is the kind of thing that works until it does
    # not.
    assert type(mock_db.add.call_args[0][0].kind) is str


@pytest.mark.parametrize("bad", ["Control", "controls", "", "modification "])
def test_an_unrecognised_kind_is_refused(bad):
    """422, not a row with a class nothing can read.

    Case and whitespace included on purpose: 'Control' is the exact value a
    person would type, and silently storing it would leave the marker channel
    showing the plain symbol with no error anywhere.
    """
    with pytest.raises(ValidationError):
        PTMCreate(name="x", kind=bad)


async def test_update_can_change_the_kind(mock_db):
    ptm = _ptm(kind="modification")
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),        # get_or_404
        make_result(fetchall=[(3, 0)]),  # experiment count
    ]
    out = await mod.update_ptm(
        ptm_id=3, data=PTMUpdate(kind="none"), current_user=_user(), db=mock_db
    )
    assert ptm.kind == "none"
    assert out.kind == "none"


async def test_a_patch_that_omits_kind_leaves_it_alone(mock_db):
    """`exclude_unset` is what makes this work; a schema default would clobber."""
    ptm = _ptm(kind="control")
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),
        make_result(fetchall=[(3, 0)]),
    ]
    await mod.update_ptm(
        ptm_id=3, data=PTMUpdate(name="Renamed"), current_user=_user(), db=mock_db
    )
    assert ptm.kind == "control"
```

Add the import at the top of the file:

```python
from pydantic import ValidationError
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
docker run --rm -v "$PWD/backend:/app" -w /app --user root --entrypoint sh \
  maptimize-backend -c "pip install -q pytest pytest-asyncio 2>/dev/null; \
  HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES= python -m pytest tests/unit/test_ptms_router.py -q"
```
Expected: FAIL — `PTMCreate` has no field `kind` (extra="forbid" rejects it), and `test_create_defaults_to_a_modification` fails on the missing `out.kind`.

- [ ] **Step 3: Add `PTMKind` and the column**

In `backend/models/ptm.py`, add to the imports:

```python
from enum import Enum as PyEnum
```

and above the `PTM` class:

```python
class PTMKind(str, PyEnum):
    """What a row in this vocabulary actually is.

    The list is not homogeneous: `Unmodified` is the *absence* of a modification
    and `Control` is a transfection with an inactive enzyme, so neither is a
    tubulin mark. The projections draw the three kinds as three markers, and this
    is where that split is decided.

    ⚠️ Stored as a plain VARCHAR, not a Postgres enum: `ensure_schema_updates()`
    adds columns with raw ALTER TABLE and cannot CREATE TYPE, so an enum column
    would exist on a fresh database and be missing in production. Same choice, and
    the same reason, as `document_folders.kind`.

    ⚠️ The class is read from here and never from `name`. Renaming the row — or
    the lab creating "Control (inactive VASH)" — must not quietly return every
    control point to the plain marker.
    """

    MODIFICATION = "modification"
    CONTROL = "control"
    NONE = "none"
```

and inside `PTM`, after the `color` column:

```python
    kind: Mapped[str] = mapped_column(
        String(20),
        default=PTMKind.MODIFICATION.value,
        server_default=PTMKind.MODIFICATION.value,
        nullable=False,
    )
```

- [ ] **Step 4: Add the column to `ensure_schema_updates`**

In `backend/database.py`, append to the `updates` list, directly after the two `document_folders` entries:

```python
            # What a PTM row is: a tubulin mark, an inactive-enzyme control, or
            # the unmodified state. Existing rows are all marks, which is exactly
            # what the default says; `Unmodified` is corrected by the one-off in
            # scripts/ptm_control_backfill.sql.
            ("ptms", "kind", "VARCHAR(20) DEFAULT 'modification' NOT NULL"),
```

- [ ] **Step 5: Add `kind` to the schemas**

In `backend/schemas/ptm.py`, add the imports:

```python
from pydantic import BaseModel, ConfigDict, Field

from models.ptm import PTMKind
```

and the fields. `PTMCreate`:

```python
class PTMCreate(ReferenceCreate):
    """Schema for creating a PTM."""

    # `use_enum_values` so `model_dump()` yields the plain string the VARCHAR
    # column wants; the router feeds it straight into PTM(**values).
    # `extra="forbid"` is restated, not inherited, because overriding
    # model_config replaces it — and test_reference_schema_guards pins it.
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    name: str = Field(..., min_length=1, max_length=100)
    abbreviation: Optional[str] = Field(None, max_length=50)
    modified_residue: Optional[str] = Field(None, max_length=100)
    enzyme: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    kind: PTMKind = PTMKind.MODIFICATION
```

`PTMUpdate`:

```python
class PTMUpdate(ReferenceUpdate):
    """Schema for updating a PTM (all optional)."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    abbreviation: Optional[str] = Field(None, max_length=50)
    modified_residue: Optional[str] = Field(None, max_length=100)
    enzyme: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    kind: Optional[PTMKind] = None
```

Both responses get the field as a **plain `str`**:

```python
class PTMResponse(BaseModel):
    """Basic PTM response (embedded in ExperimentResponse)."""
    id: int
    name: str
    abbreviation: Optional[str] = None
    modified_residue: Optional[str] = None
    enzyme: Optional[str] = None
    color: Optional[str] = None
    # `str`, not PTMKind: the column has no CHECK constraint, so a hand-edited
    # row would otherwise 500 the whole list endpoint instead of degrading to the
    # plain marker. The client normalises anything it does not recognise.
    kind: str = PTMKind.MODIFICATION.value
```

and the same field on `PTMDetailedResponse`, plus `kind=ptm.kind` inside `from_ptm`.

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
docker run --rm -v "$PWD/backend:/app" -w /app --user root --entrypoint sh \
  maptimize-backend -c "pip install -q pytest pytest-asyncio 2>/dev/null; \
  HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES= python -m pytest \
  tests/unit/test_ptms_router.py tests/unit/test_reference_schema_guards.py \
  tests/unit/test_experiment_ptm.py -q"
```
Expected: PASS. If `test_experiment_ptm.py` fails on a missing `kind` attribute, add `kind="modification"` to its local `_ptm()` helper (line ~30) — the same reason as in Step 1.

- [ ] **Step 7: Commit**

```bash
git add backend/models/ptm.py backend/schemas/ptm.py backend/database.py \
        backend/tests/unit/test_ptms_router.py backend/tests/unit/test_experiment_ptm.py
git commit -m "Let the PTM list say which entries are not modifications"
```

---

### Task 2: The `Control` row — seeded for new databases, backfilled once for production

**Files:**
- Modify: `backend/models/ptm.py` (the `DEFAULT_PTMS` list)
- Create: `scripts/ptm_control_backfill.sql`
- Test: `backend/tests/unit/test_ptm_seed.py` (create)

**Interfaces:**
- Consumes: `PTMKind` from Task 1.
- Produces: a `DEFAULT_PTMS` list in which exactly one entry has `kind == "control"` and exactly one has `kind == "none"`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_ptm_seed.py`:

```python
"""The seeded tubulin code, and the two entries that are not modifications.

`seed_default_data()` only fires on an empty table, so these values are what a
fresh install and the test database start from — and the marker channel on the
projections is driven entirely by `kind`. A seed that shipped every row as
`modification` would draw every point identically with nothing failing.
"""
from models.ptm import DEFAULT_PTMS, PTMKind


def _by_kind(kind: PTMKind) -> list[dict]:
    return [p for p in DEFAULT_PTMS if p.get("kind") == kind.value]


def test_the_seed_offers_exactly_one_control():
    control = _by_kind(PTMKind.CONTROL)
    assert [p["name"] for p in control] == ["Control"]


def test_unmodified_is_seeded_as_the_absence_of_a_modification():
    # It was seeded as an ordinary modification, which is a category error: it is
    # what you get when nothing was done to the lattice.
    unmodified = _by_kind(PTMKind.NONE)
    assert [p["name"] for p in unmodified] == ["Unmodified"]


def test_every_other_entry_is_a_modification():
    rest = [
        p["name"]
        for p in DEFAULT_PTMS
        if p.get("kind", PTMKind.MODIFICATION.value) == PTMKind.MODIFICATION.value
    ]
    assert "Detyrosination" in rest
    assert "Control" not in rest and "Unmodified" not in rest


def test_every_seeded_entry_declares_a_kind():
    # Relying on the column default here would mean the seed and the column
    # disagree the moment the default changes.
    assert all("kind" in p for p in DEFAULT_PTMS)


def test_seeded_names_are_unique():
    names = [p["name"] for p in DEFAULT_PTMS]
    assert len(names) == len(set(names))
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
docker run --rm -v "$PWD/backend:/app" -w /app --user root --entrypoint sh \
  maptimize-backend -c "pip install -q pytest pytest-asyncio 2>/dev/null; \
  HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES= python -m pytest tests/unit/test_ptm_seed.py -q"
```
Expected: FAIL — no entry carries `kind` at all.

- [ ] **Step 3: Update `DEFAULT_PTMS`**

In `backend/models/ptm.py`, add `"kind": PTMKind.MODIFICATION.value` to each of the **nine** modification entries: Tyrosination, Detyrosination, Δ2-tubulin, Δ3-tubulin, Acetylation, Polyglutamylation, Monoglutamylation, Polyglycylation, Phosphorylation. (`Unmodified` is the tenth existing entry and is handled below.)

Change the `Unmodified` entry's description, which currently claims it *is* the control condition, and give it its kind:

```python
    {
        "name": "Unmodified",
        "abbreviation": "none",
        "modified_residue": None,
        "enzyme": None,
        "description": "Recombinant or subtilisin-treated tubulin carrying no modification.",
        "color": "#a855f7",
        "kind": PTMKind.NONE.value,
    },
    {
        "name": "Control",
        "abbreviation": "ctrl",
        "modified_residue": None,
        "enzyme": None,
        "description": (
            "Paired control for a PTM condition: the same transfection carried "
            "out with a catalytically inactive enzyme, so the lattice is "
            "unmodified. Run alongside the modified sample it is compared to."
        ),
        # Neutral grey on purpose. Under the default colour-by (protein) it is
        # never seen; under colour-by PTM, grey is the right thing for a value
        # that is not a modification.
        "color": "#94a3b8",
        "kind": PTMKind.CONTROL.value,
    },
```

Also extend the `DEFAULT_PTMS` docstring above the list to say that `kind` is what the projections read, and that the list is no longer only the tubulin code.

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
docker run --rm -v "$PWD/backend:/app" -w /app --user root --entrypoint sh \
  maptimize-backend -c "pip install -q pytest pytest-asyncio 2>/dev/null; \
  HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES= python -m pytest tests/unit/test_ptm_seed.py -q"
```
Expected: PASS (5 tests).

- [ ] **Step 5: Write the production one-off**

Create `scripts/ptm_control_backfill.sql`:

```sql
-- One-off, additive: give the existing PTM vocabulary its `kind` values.
--
-- NOT run at startup. `seed_default_data()` seeds PTMs only when the table is
-- empty, on the principle that re-adding a row the lab deliberately deleted is
-- worse than leaving the vocabulary short. Inserting `Control` on every boot
-- would break that for this one row, so production gets it once, here.
--
-- The `kind` column itself is added automatically by ensure_schema_updates(),
-- and every existing row lands on 'modification', which is correct for all of
-- them except `Unmodified`.
--
--   docker exec -i maptimize-db psql -U maptimize -d maptimize \
--     < scripts/ptm_control_backfill.sql
--
-- Idempotent: re-running it changes nothing.

BEGIN;

UPDATE ptms SET kind = 'none' WHERE name = 'Unmodified' AND kind <> 'none';

INSERT INTO ptms (name, abbreviation, description, color, kind)
SELECT 'Control', 'ctrl',
       'Paired control for a PTM condition: the same transfection carried out '
       'with a catalytically inactive enzyme, so the lattice is unmodified. '
       'Run alongside the modified sample it is compared to.',
       '#94a3b8', 'control'
WHERE NOT EXISTS (SELECT 1 FROM ptms WHERE name = 'Control');

COMMIT;

-- Expected afterwards: one row with kind='control', one with kind='none',
-- and the rest 'modification'.
SELECT kind, count(*), string_agg(name, ', ' ORDER BY name) FROM ptms GROUP BY kind;
```

- [ ] **Step 6: Commit**

```bash
git add backend/models/ptm.py backend/tests/unit/test_ptm_seed.py scripts/ptm_control_backfill.sql
git commit -m "Give the lab a word for the control it already runs"
```

---

### Task 3: Tell the agent about the column

**Files:**
- Modify: `backend/services/sql_query_service.py` (the `ptms(...)` line in `SQL_SCHEMA_HINT`, ~line 93)
- Modify: `mcp-server/maptalk_mcp/tools.yaml` (`create_ptm`, `update_ptm`, `list_ptms`, and the schema hint mirror at ~line 1517)
- Modify: `mcp-server/maptalk_mcp/server.py` (`SERVER_VERSION`, line 171)
- Modify: `mcp-server/tests/test_protocol.py` (the version assertion, line 36)

**Interfaces:**
- Consumes: the `kind` values from Task 1.
- Produces: no code interface; the MCP tool contract gains an optional `kind` argument on `create_ptm` and `update_ptm`.

- [ ] **Step 1: Update the SQL schema hint**

In `backend/services/sql_query_service.py`, replace the `ptms(...)` line:

```python
    "ptms(id, name, abbreviation, modified_residue, enzyme, kind)  -- microtubule post-translational modification; kind is 'modification' | 'control' (inactive-enzyme control) | 'none' (unmodified); shared reference data, no user filter\n"
```

- [ ] **Step 2: Add `kind` to the MCP tools**

In `mcp-server/maptalk_mcp/tools.yaml`, append to `create_ptm`'s `params`:

```yaml
      - name: kind
        in: body
        type: string
        enum: [modification, control, none]
        description: >
          What this entry is. "modification" (default) is a tubulin mark such as
          polyglutamylation; "control" is a paired inactive-enzyme control, run
          alongside a modified sample; "none" is the unmodified lattice. The
          projections draw the three as three different markers, so getting this
          wrong makes a control indistinguishable from the sample it controls.
```

Append the same block to `update_ptm`'s `params` (repeat it in full — the two lists are independent).

Extend `list_ptms`'s description with a sentence:

```yaml
      Each entry carries a `kind`: "modification" for a tubulin mark, "control"
      for a paired inactive-enzyme control, "none" for the unmodified lattice.
```

And update the `query_database` schema-hint mirror (~line 1517) to match Step 1:

```yaml
      ptms(id, name, abbreviation, modified_residue, enzyme, kind) [microtubule
      post-translational modification; kind is 'modification' | 'control' |
      'none'; shared reference data, no user filter];
```

- [ ] **Step 3: Bump the server version**

`mcp-server/maptalk_mcp/server.py` line 171: `SERVER_VERSION = "3.2.0"`.

`mcp-server/tests/test_protocol.py` line 36: `assert init.serverInfo.version == "3.2.0"`.

- [ ] **Step 4: Run the MCP tests**

Run:
```bash
cd mcp-server && .venv/bin/python -m pytest -q
```
Expected: PASS. The tool-name set is unchanged (no tools added or removed), so only the version pin moves.

- [ ] **Step 5: Run the SQL service tests**

Run:
```bash
docker run --rm -v "$PWD/backend:/app" -w /app --user root --entrypoint sh \
  maptimize-backend -c "pip install -q pytest pytest-asyncio 2>/dev/null; \
  HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES= python -m pytest tests/unit/test_sql_query_service.py -q"
```
Expected: PASS — `ptms` stays in `ALLOWED_SQL_TABLES` and in no scoping set, which is unchanged.

- [ ] **Step 6: Commit**

```bash
git add backend/services/sql_query_service.py mcp-server/maptalk_mcp/tools.yaml \
        mcp-server/maptalk_mcp/server.py mcp-server/tests/test_protocol.py
git commit -m "Show the agent the column it cannot guess"
```

---

### Task 4: The marker rules, as a pure module

**Files:**
- Create: `frontend/components/visualization/pointMarker.ts`
- Create: `frontend/e2e/unit/pointMarker.spec.ts`
- Modify: `frontend/lib/api.ts` (`PTM`, `PTMCreate`, `PTMUpdate` interfaces, ~lines 1792-1826)

**Interfaces:**
- Consumes: the wire values from Task 1.
- Produces:
  - `type PtmKind = "modification" | "control" | "none"`
  - `function ptmKindOf(kind: string | null | undefined): PtmKind`
  - `interface MarkerStyle { fillOpacity: number; stroke: string; strokeWidth: number; dotRatio: number; dotColor: string }`
  - `function markerStyle(kind: PtmKind, color: string): MarkerStyle`
  - `function pointRadius(size: number): number`
  - `const MARKER_KINDS: readonly PtmKind[]` — display order for the legend: `["none", "modification", "control"]`

- [ ] **Step 1: Write the failing test**

Create `frontend/e2e/unit/pointMarker.spec.ts`:

```typescript
import { expect, test } from "@playwright/test";

import {
  MARKER_KINDS,
  markerStyle,
  pointRadius,
  ptmKindOf,
  type PtmKind,
} from "../../components/visualization/pointMarker";

/**
 * The second visual channel on the projections.
 *
 * Colour says which protein a point is; this says whether the sample carried a
 * PTM, was the paired inactive-enzyme control, or had no PTM at all. Getting it
 * wrong produces a plot that is quietly misleading rather than one that errors —
 * a control drawn as a sample is exactly the comparison the lab is looking at.
 */

const COLOR = "#ef4444";

test("a recognised kind passes through untouched", () => {
  for (const kind of ["modification", "control", "none"] as PtmKind[]) {
    expect(ptmKindOf(kind)).toBe(kind);
  }
});

test("anything unrecognised falls back to the plain marker", () => {
  // Failing the other way would relabel every point as a control the moment a
  // reference list failed to load or the backend grew a fourth kind.
  for (const bad of [null, undefined, "", "Control", "ptm", "modification "]) {
    expect(ptmKindOf(bad)).toBe("none");
  }
});

test("only a modification gets the centre dot", () => {
  expect(markerStyle("modification", COLOR).dotRatio).toBeGreaterThan(0);
  expect(markerStyle("control", COLOR).dotRatio).toBe(0);
  expect(markerStyle("none", COLOR).dotRatio).toBe(0);
});

test("the centre dot is black, so it reads on every protein colour", () => {
  expect(markerStyle("modification", COLOR).dotColor).toBe("#000000");
});

test("only a control loses its fill", () => {
  const control = markerStyle("control", COLOR);
  const plain = markerStyle("none", COLOR);
  expect(control.fillOpacity).toBeLessThan(plain.fillOpacity);
  expect(markerStyle("modification", COLOR).fillOpacity).toBe(plain.fillOpacity);
});

test("a control is ringed in its own colour", () => {
  // Transparency alone is a weak channel under overplotting: a faded point on
  // top of three opaque ones looks opaque. The ring is what makes it readable,
  // and it must be the point's colour so the class costs no hue.
  expect(markerStyle("control", COLOR).stroke).toBe(COLOR);
  expect(markerStyle("none", COLOR).stroke).not.toBe(COLOR);
  expect(markerStyle("modification", COLOR).stroke).not.toBe(COLOR);
});

test("a PTM point is otherwise identical to a plain one", () => {
  // The dot is added to today's marker, not substituted for it: every existing
  // plot must keep looking the way it does.
  const { dotRatio: _a, dotColor: _b, ...ptm } = markerStyle("modification", COLOR);
  const { dotRatio: _c, dotColor: _d, ...plain } = markerStyle("none", COLOR);
  expect(ptm).toEqual(plain);
});

test("the radius matches the circle recharts draws for the same area", () => {
  // recharts sizes symbols by AREA (the ZAxis range), so the base point has to
  // be r = sqrt(size / pi) or the custom shape silently resizes every plot.
  expect(pointRadius(60)).toBeCloseTo(4.3708, 3);
  expect(Math.PI * pointRadius(60) ** 2).toBeCloseTo(60, 6);
});

test("the legend lists every kind exactly once, plain marker first", () => {
  expect(MARKER_KINDS).toEqual(["none", "modification", "control"]);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd frontend && npm run test:unit -- pointMarker
```
Expected: FAIL — cannot resolve `../../components/visualization/pointMarker`.

- [ ] **Step 3: Write the module**

Create `frontend/components/visualization/pointMarker.ts`:

```typescript
/**
 * The projections' second visual channel: what kind of sample a point is.
 *
 * Colour already carries the protein (or microscope, or experiment — whatever
 * colour-by is set to), so the PTM regime cannot use hue without taking that
 * channel away. It uses the marker instead: a control keeps its colour but is
 * drawn as a translucent ring, and a modified sample gets a black centre dot.
 *
 * Kept as plain functions, away from React and recharts, because the failure
 * mode here is a plot that quietly disagrees with the data rather than one that
 * throws — see e2e/unit/pointMarker.spec.ts.
 */

/** Mirrors `models.ptm.PTMKind`. One vocabulary, not two. */
export type PtmKind = "modification" | "control" | "none";

/**
 * Legend order: the marker the plot already draws comes first, then the two
 * that are new to the reader.
 */
export const MARKER_KINDS: readonly PtmKind[] = ["none", "modification", "control"];

/**
 * Normalise whatever the API gave us into a kind we can draw.
 *
 * Everything unrecognised — an unassigned experiment, a reference list that
 * failed to load, a value a newer backend added — becomes `none`, the marker the
 * plot draws today. Failing the other way would relabel every point as a
 * control, which is worse than showing nothing new.
 */
export function ptmKindOf(kind: string | null | undefined): PtmKind {
  return MARKER_KINDS.includes(kind as PtmKind) ? (kind as PtmKind) : "none";
}

export interface MarkerStyle {
  fillOpacity: number;
  stroke: string;
  strokeWidth: number;
  /** Centre-dot radius as a fraction of the point radius. 0 means no dot. */
  dotRatio: number;
  dotColor: string;
}

/** The stroke every point has today: a hairline so dense clusters stay legible. */
const PLAIN_STROKE = "rgba(255,255,255,0.3)";

const PLAIN: Omit<MarkerStyle, "stroke"> = {
  fillOpacity: 0.75,
  strokeWidth: 1,
  dotRatio: 0,
  dotColor: "",
};

export function markerStyle(kind: PtmKind, color: string): MarkerStyle {
  switch (kind) {
    case "control":
      // "Slightly transparent", as asked — plus a solid ring in the same colour,
      // because opacity alone disappears under overplotting.
      return {
        ...PLAIN,
        fillOpacity: 0.18,
        stroke: color,
        strokeWidth: 1.4,
      };
    case "modification":
      return { ...PLAIN, stroke: PLAIN_STROKE, dotRatio: 0.42, dotColor: "#000000" };
    case "none":
    default:
      return { ...PLAIN, stroke: PLAIN_STROKE };
  }
}

/**
 * The radius recharts draws for a circle symbol of the given area.
 *
 * `size` is the ZAxis range value and is an AREA, not a diameter. Computing it
 * as anything else resizes every point on every projection.
 */
export function pointRadius(size: number): number {
  return Math.sqrt(size / Math.PI);
}

/** Never let the dot vanish on a small point. */
export const MIN_DOT_RADIUS = 1.3;
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
cd frontend && npm run test:unit -- pointMarker
```
Expected: PASS (9 tests).

- [ ] **Step 5: Verify by perturbation**

Temporarily change `ptmKindOf` to `return (kind as PtmKind) ?? "none";` and re-run. Expected: the "anything unrecognised" test FAILS. Revert the change and re-run; expected PASS. A test that has never been seen red proves nothing.

- [ ] **Step 6: Add `kind` to the API types**

In `frontend/lib/api.ts`:

```typescript
/** Mirrors backend PTMKind. `pointMarker.ptmKindOf` normalises anything else. */
export type PTMKind = "modification" | "control" | "none";

/** Basic PTM shape — mirrors backend PTMResponse (embedded in Experiment). */
export interface PTM {
  id: number;
  name: string;
  abbreviation?: string;
  modified_residue?: string;
  enzyme?: string;
  color?: string;
  /** Widened to `string` on purpose: an older backend, or a newer kind, must
   *  degrade to the plain marker rather than fail to type-check. */
  kind?: string;
}
```

and `kind?: PTMKind;` on both `PTMCreate` and `PTMUpdate`.

- [ ] **Step 7: Typecheck and commit**

Run:
```bash
cd frontend && npx tsc --noEmit
```
Expected: no errors.

```bash
git add frontend/components/visualization/pointMarker.ts \
        frontend/e2e/unit/pointMarker.spec.ts frontend/lib/api.ts
git commit -m "Decide how a control should look before drawing one"
```

---

### Task 5: Draw it

**Files:**
- Modify: `frontend/components/visualization/projectionShared.tsx` (add `ProjectionMarker` and `MarkerLegend` after `ProjectionLegend`)
- Modify: `frontend/components/visualization/UmapVisualization.tsx` (imports; `ptmKindOfPoint`; the `<Scatter>` block at lines 566-580; the legend block at line 585)
- Modify: `frontend/messages/en.json`, `frontend/messages/fr.json` (the `umap` namespace)

**Interfaces:**
- Consumes: `ptmKindOf`, `markerStyle`, `pointRadius`, `MIN_DOT_RADIUS`, `MARKER_KINDS`, `PtmKind` from Task 4.
- Produces:
  - `ProjectionMarker(props: MarkerShapeProps): JSX.Element | null` in `projectionShared.tsx`, where `MarkerShapeProps` is `{ cx?: number; cy?: number; fill?: string; size?: number; payload?: ProjectionPoint; kind: PtmKind }`.
  - `MarkerLegend({ counts, t }: { counts: Map<PtmKind, number>; t: Translate }): JSX.Element | null`.

- [ ] **Step 1: Add the marker and its legend to `projectionShared.tsx`**

Add the imports at the top:

```typescript
import {
  MARKER_KINDS,
  MIN_DOT_RADIUS,
  markerStyle,
  pointRadius,
  type PtmKind,
} from "./pointMarker";
```

and append after `ProjectionLegend`:

```tsx
/**
 * What recharts hands a custom `shape`.
 *
 * The point object it builds carries the `<Cell>` props merged in, so `fill` is
 * whatever `styleOf` decided and this component never chooses a colour — it only
 * decides geometry. `size` is the ZAxis range value, an AREA.
 */
export interface MarkerShapeProps {
  cx?: number;
  cy?: number;
  fill?: string;
  size?: number;
  payload?: ProjectionPoint;
  kind: PtmKind;
}

/**
 * One point, drawn with its sample kind.
 *
 * Replaces recharts' default symbol rather than decorating it, because a centre
 * dot is a second element and `<Cell>` can only set attributes on one. The base
 * circle is deliberately identical to what recharts drew before — same radius
 * from the same area, same fill opacity, same hairline stroke — so a plot with
 * no PTM data looks untouched.
 */
export function ProjectionMarker({
  cx,
  cy,
  fill,
  size = 60,
  kind,
}: MarkerShapeProps): JSX.Element | null {
  if (cx === undefined || cy === undefined) return null;

  const color = fill || "#888888";
  const style = markerStyle(kind, color);
  const r = pointRadius(size);

  return (
    <g>
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill={color}
        fillOpacity={style.fillOpacity}
        stroke={style.stroke}
        strokeWidth={style.strokeWidth}
        cursor="pointer"
      />
      {style.dotRatio > 0 && (
        <circle
          cx={cx}
          cy={cy}
          r={Math.max(r * style.dotRatio, MIN_DOT_RADIUS)}
          fill={style.dotColor}
        />
      )}
    </g>
  );
}

/**
 * The key to the marker channel, drawn with the same code as the points.
 *
 * Hidden when every point is the same kind: a lab that has not recorded any PTM
 * would otherwise get a legend explaining a distinction its plot does not make.
 */
export function MarkerLegend({
  counts,
  t,
}: {
  counts: Map<PtmKind, number>;
  t: Translate;
}): JSX.Element | null {
  if (counts.size < 2) return null;

  const label: Record<PtmKind, string> = {
    none: t("markerNone"),
    modification: t("markerModification"),
    control: t("markerControl"),
  };

  return (
    <div className="flex flex-wrap items-center gap-3 mt-2">
      <span className="text-xs uppercase tracking-wide text-text-muted">
        {t("markerLegendTitle")}
      </span>
      {MARKER_KINDS.filter((kind) => counts.has(kind)).map((kind) => (
        <div key={kind} className="flex items-center gap-1.5">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            {/* Grey, not a protein colour: the swatch is about the marker, and
                borrowing a hue would read as a fourth colour group. */}
            <ProjectionMarker cx={8} cy={8} fill="#9ca3af" size={60} kind={kind} />
          </svg>
          <span className="text-xs text-text-secondary">
            {label[kind]} <span className="text-text-muted">({counts.get(kind)})</span>
          </span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Wire it into `UmapVisualization.tsx`**

Extend the `projectionShared` import to include `MarkerLegend`, `ProjectionMarker` and `type MarkerShapeProps`, and add:

```typescript
import { ptmKindOf, type PtmKind } from "./pointMarker";
```

After the existing `styleOf` callback (line ~306), add:

```tsx
  /**
   * Which sample kind a point is.
   *
   * Points carry only `experiment_id`; the PTM comes from the facet summary and
   * its kind from the reference list the filter panel already loads. Nothing is
   * added to the point payload for this — see the spec.
   */
  const ptmKindOfPoint = useCallback(
    (point: ProjectionPoint): PtmKind => {
      const meta = experimentMeta.get(point.experiment_id);
      const ptm = meta?.ptmId ? ptmById.get(meta.ptmId) : undefined;
      return ptmKindOf(ptm?.kind);
    },
    [experimentMeta, ptmById]
  );

  const renderMarker = useCallback(
    (props: MarkerShapeProps) => (
      <ProjectionMarker
        {...props}
        kind={props.payload ? ptmKindOfPoint(props.payload) : "none"}
      />
    ),
    [ptmKindOfPoint]
  );

  // How many points of each kind are on the plot, so the marker legend can
  // explain only the distinctions actually visible.
  const markerCounts = useMemo(() => {
    const counts = new Map<PtmKind, number>();
    (view?.points ?? []).forEach((point) => {
      const kind = ptmKindOfPoint(point);
      counts.set(kind, (counts.get(kind) ?? 0) + 1);
    });
    return counts;
  }, [view?.points, ptmKindOfPoint]);
```

Replace the `<Scatter>` block (lines 566-580) with:

```tsx
              <Scatter
                data={view.points}
                shape={renderMarker}
                {...UMAP_SCATTER_ANIMATION}
              >
                {view.points.map((point, index) => (
                  // Colour only. Opacity, stroke and the centre dot are the
                  // marker's job, and splitting them would give one point two
                  // places to disagree with itself.
                  <Cell key={`cell-${index}`} fill={styleOf(point).color} />
                ))}
              </Scatter>
```

Replace the legend line (line 585) with:

```tsx
        <ProjectionLegend groups={legendGroups} />
        <MarkerLegend counts={markerCounts} t={t} />
```

- [ ] **Step 3: Add the i18n keys**

In `frontend/messages/en.json`, inside the `"umap"` object, after `"facetPtm"`:

```json
    "markerLegendTitle": "Sample type",
    "markerNone": "Non-PTM",
    "markerModification": "PTM",
    "markerControl": "Control",
```

The same keys in `frontend/messages/fr.json`:

```json
    "markerLegendTitle": "Type d'échantillon",
    "markerNone": "Sans PTM",
    "markerModification": "PTM",
    "markerControl": "Contrôle",
```

⚠️ Edit both files as plain text. Do not parse and re-serialise them.

- [ ] **Step 4: Verify the keys landed in both locales and are not duplicated**

Run:
```bash
cd frontend && for k in markerLegendTitle markerNone markerModification markerControl; do
  echo -n "$k: "; grep -c "\"$k\"" messages/en.json messages/fr.json | tr '\n' ' '; echo
done
```
Expected: every count is exactly `1` in both files. A count of 2 means a duplicate key, which JSON resolves silently in favour of the last one.

- [ ] **Step 5: Typecheck and run the frontend unit suite**

Run:
```bash
cd frontend && npx tsc --noEmit && npm run test:unit
```
Expected: no type errors, all unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/visualization/projectionShared.tsx \
        frontend/components/visualization/UmapVisualization.tsx \
        frontend/messages/en.json frontend/messages/fr.json
git commit -m "Let the plot show which samples were controls"
```

---

### Task 6: Let the lab set the kind

**Files:**
- Modify: `frontend/app/dashboard/ptms/page.tsx` (`DEFAULT_FORM_DATA` ~line 27, `openEditModal` ~line 103, the card body ~line 203, the modal form ~line 246)
- Modify: `frontend/messages/en.json`, `frontend/messages/fr.json` (the `ptmsPage` namespace)

**Interfaces:**
- Consumes: `PTMKind` and the `kind` fields on `PTMCreate` / `PTMUpdate` from Task 4.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Add `kind` to the form state**

In `frontend/app/dashboard/ptms/page.tsx`:

```typescript
const DEFAULT_FORM_DATA: PTMCreate = {
  name: "",
  abbreviation: "",
  modified_residue: "",
  enzyme: "",
  description: "",
  color: "",
  // Most entries are tubulin marks; the two that are not are the exception the
  // user has to opt into.
  kind: "modification",
};
```

and in `openEditModal`, alongside the other fields:

```typescript
      kind: (p.kind as PTMKind) ?? "modification",
```

Import `PTMKind` from `@/lib/api` on the existing import line.

- [ ] **Step 2: Add the selector to the modal**

Insert after the `enzyme` field and before `description`:

```tsx
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{t("kind")}</label>
            <select value={formData.kind ?? "modification"}
              onChange={(e) => setFormData({ ...formData, kind: e.target.value as PTMKind })}
              className="input-field">
              <option value="modification">{t("kindModification")}</option>
              <option value="control">{t("kindControl")}</option>
              <option value="none">{t("kindNone")}</option>
            </select>
            <p className="text-xs text-text-muted mt-1.5">{t("kindHint")}</p>
          </div>
```

- [ ] **Step 3: Show it on the card**

In the card body, beside the abbreviation, so a mis-set kind is visible without opening the editor:

```tsx
                      {p.kind && p.kind !== "modification" && (
                        <span className="inline-block mt-1 px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide bg-white/5 text-text-muted">
                          {p.kind === "control" ? t("kindControl") : t("kindNone")}
                        </span>
                      )}
```

- [ ] **Step 4: Add the i18n keys**

In `frontend/messages/en.json`, inside `"ptmsPage"`:

```json
    "kind": "Kind",
    "kindModification": "Modification",
    "kindControl": "Control",
    "kindNone": "No modification",
    "kindHint": "Controls and the unmodified state are drawn as different markers on the projections.",
```

In `frontend/messages/fr.json`:

```json
    "kind": "Type",
    "kindModification": "Modification",
    "kindControl": "Contrôle",
    "kindNone": "Sans modification",
    "kindHint": "Les contrôles et l'état non modifié sont dessinés avec des marqueurs différents sur les projections.",
```

⚠️ Plain-text edits, as in Task 5.

- [ ] **Step 5: Verify keys and typecheck**

Run:
```bash
cd frontend && for k in kind kindModification kindControl kindNone kindHint; do
  echo -n "$k: "; grep -c "\"$k\"" messages/en.json messages/fr.json | tr '\n' ' '; echo
done
npx tsc --noEmit
```
Expected: `kindModification`, `kindControl`, `kindNone`, `kindHint` are `1` in each file. `"kind"` may legitimately appear more than once across the file if another namespace uses it — check that `ptmsPage` has exactly one. No type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/dashboard/ptms/page.tsx frontend/messages/en.json frontend/messages/fr.json
git commit -m "Make the kind of a PTM something you can set"
```

---

### Task 7: Deploy and verify against the real thing

**Files:**
- Modify: `CLAUDE.md` (the PTM section)

**Interfaces:**
- Consumes: everything above.
- Produces: a running deployment.

- [ ] **Step 1: Back up before touching the database**

Run:
```bash
cat /backup/maptimize/LAST_RESULT
```
If the date is not today, run `scripts/backup.sh` and wait for it. ⚠️ Read the **date**, not the word: a dead timer keeps its last `OK` forever.

- [ ] **Step 2: Run the full backend unit suite**

Run:
```bash
docker run --rm -v "$PWD/backend:/app" -w /app --user root --entrypoint sh \
  maptimize-backend -c "pip install -q pytest pytest-asyncio 2>/dev/null; \
  HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES= python -m pytest tests/unit -q"
```
Expected: PASS. Fix anything red before deploying — a failure here is a real regression, not a coverage-harness artefact (those only appear under the coverage tracer).

- [ ] **Step 3: Rebuild and restart**

Run:
```bash
docker compose -f docker-compose.prod.yml build maptimize-backend --no-cache
docker compose -f docker-compose.prod.yml up -d maptimize-backend
docker compose -f docker-compose.prod.yml build maptimize-frontend --no-cache
docker compose -f docker-compose.prod.yml up -d maptimize-frontend
docker compose -f docker-compose.prod.yml up -d maptimize-mcp
```
⚠️ Never `docker compose down -v`.

- [ ] **Step 4: Confirm the column arrived**

Run:
```bash
docker exec maptimize-db psql -U maptimize -d maptimize \
  -c "\d ptms" -c "SELECT kind, count(*) FROM ptms GROUP BY kind;"
```
Expected: a `kind` column, `character varying(20)`, `not null`, default `'modification'`; all 10 existing rows reporting `modification`.

- [ ] **Step 5: Run the one-off backfill**

Run:
```bash
docker exec -i maptimize-db psql -U maptimize -d maptimize < scripts/ptm_control_backfill.sql
```
Expected final SELECT: `control | 1 | Control`, `none | 1 | Unmodified`, `modification | 9 | ...`.

- [ ] **Step 6: Verify the API serves it**

Run:
```bash
docker exec maptimize-backend python -c "
import asyncio, json
from database import async_session_maker
from sqlalchemy import select
from models.ptm import PTM
from schemas.ptm import PTMDetailedResponse
async def main():
    async with async_session_maker() as db:
        rows = (await db.execute(select(PTM).order_by(PTM.name))).scalars().all()
        print(json.dumps([{'name': p.name, 'kind': PTMDetailedResponse.from_ptm(p, 0).kind} for p in rows], ensure_ascii=False))
asyncio.run(main())"
```
Expected: `Control` → `control`, `Unmodified` → `none`, the rest → `modification`.

- [ ] **Step 7: Verify in the browser**

At `https://maptimize.utia.cas.cz`:
1. `/dashboard/ptms` — `Control` is listed with a `CONTROL` badge; editing it shows Kind = Control.
2. On an experiment card, the PTM selector offers `Control`. Assign it to one experiment (e.g. `MAP7 calibrate`, id 334 — note which, to restore if the lab wants it back).
3. On the dashboard UMAP: that experiment's points are translucent rings, the other `deTyr` experiments' points have black centre dots, and the 49 `Unmodified` ones are unchanged. The marker legend appears under the colour legend with three entries.
4. Switch to LDA mode and to FOV mode — the markers follow.
5. Tick `Control` in the PTM filter — only those points remain.

- [ ] **Step 8: Update CLAUDE.md**

In the PTM section, after the paragraph on the vocabulary being editable rows, add:

```markdown
⚠️ **`ptms.kind` (od 2026-08-03) rozděluje slovník na tři druhy** — `modification`
(tubulinová značka), `control` (párová kontrola: táž transfekce s katalyticky
neaktivním enzymem) a `none` (`Unmodified`, tedy nepřítomnost modifikace). Řídí
**druhý vizuální kanál** na projekcích: kontrola se kreslí jako průsvitný
prstenec, modifikace dostane černou tečku ve středu, zbytek beze změny. Barva
zůstává na `colorBy`, takže se ty dva kanály nepřekrývají.

⚠️ **Třída se čte z `kind`, NIKDY z názvu řádku.** Přejmenování `Control` nebo
založení „Control (inactive VASH)" by jinak tiše vrátilo všechny kontroly na
obyčejný marker — bez chyby kdekoliv.

⚠️ **Kontrola nenese modifikaci, ke které patří.** Experiment je *buď*
`Detyrosination`, *nebo* `Control`; párování drží jen názvy experimentů. Filtr
`Detyrosination` tedy kontroly **nevrátí**. Vědomá cena za plochý slovník
(rozhodnuto 2026-08-03), ne opomenutí.

Seed `Control` **neběží při startu** — `seed_default_data()` seeduje jen prázdnou
tabulku a vracet řádek, který laboratoř smazala, je horší než kratší slovník.
Produkce ho dostala jednorázově přes `scripts/ptm_control_backfill.sql`.
```

Also correct the stale `SERVER_VERSION` mention in that file from `3.0.0` to `3.2.0`.

- [ ] **Step 9: Commit and open the PR**

```bash
git add CLAUDE.md
git commit -m "Write down how the third sample kind is decided"
git push -u origin feat/ptm-control-category
gh pr create --title "Give PTM samples a control category and the plot a marker channel" --body "..."
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 control is a vocabulary row | Task 2 |
| §2 `kind` column, not name matching | Task 1 |
| §3 no auto-seed, one-off backfill | Task 2 (steps 3, 5), Task 7 (step 5) |
| §4 no backend projection change | Enforced by Global Constraints; no task touches `routers/embeddings.py` |
| §5 marker channel | Tasks 4 (rules) and 5 (rendering) |
| §6 filter and colour-by for free | Verified in Task 7 step 7.5 — no code |
| §7 PTM CRUD page exposes `kind` | Task 6 |
| §8 MCP + SQL schema hint | Task 3 |
| Testing section | Tasks 1, 2, 4 write the tests; Task 5 step 5 and Task 7 step 2 run the suites |
| Deployment section | Task 7 |

**Type consistency:** `PtmKind` is the single type name across `pointMarker.ts`, `projectionShared.tsx` and `UmapVisualization.tsx`; `PTMKind` is the backend enum and its `api.ts` mirror for write payloads. `markerStyle` returns `MarkerStyle` with the same five fields everywhere it is used. `ProjectionMarker` takes `MarkerShapeProps` in both its definition (Task 5 step 1) and its two call sites (the `renderMarker` callback and the legend swatch).

**Known non-blocking risk:** recharts merges `<Cell>` props into the point entry before passing it to a custom `shape`, which is what lets `ProjectionMarker` read `fill`. This is behaviour of recharts 2.x (`^2.15.0` is pinned), verified by reading `Scatter.getComposedData`. If a point renders grey after Task 5, the cause is that merge, and the fix is to resolve the colour inside `renderMarker` via `styleOf(props.payload)` instead of reading `props.fill` — Task 5 step 5's typecheck will not catch it, so look at the plot.
