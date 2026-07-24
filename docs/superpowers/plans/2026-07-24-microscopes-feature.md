# Microscopes Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add lab-shared **microscopes** as reference data, assignable to experiments, with a dashboard UMAP filter-by-microscope switch.

**Architecture:** `Microscope` mirrors the shared, owner-less `MapProtein` model. `Experiment` gains a nullable `microscope_id` FK (no denormalization onto images/crops). The dashboard UMAP adds a `?microscope_id=` filter that narrows the existing precomputed-coordinate read path — no new UMAP fit. Frontend adds a sidebar tab + management page (cloned from the proteins page) + an experiment-form dropdown + a UMAP dropdown.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + pgvector (backend), Next.js App Router + TanStack Query + next-intl + Recharts (frontend), YAML-driven MCP tools.

## Global Constraints

- Migrations are NOT Alembic: model column → tuple in `database.ensure_schema_updates()` → numbered `backend/migrations/NNN_*.sql` with `ADD COLUMN IF NOT EXISTS`.
- Microscopes are shared reference data: **no `user_id`/`group_id`**, auth via `get_current_user` (never `require_interactive_user`), writes not owner-gated (same as proteins).
- SSOT: every new/changed app endpoint must be mirrored in `mcp-server/maptalk_mcp/tools.yaml`; bump `SERVER_VERSION` on tool-contract change.
- i18n: every UI string via `useTranslations`, keys added to **both** `frontend/messages/en.json` and `fr.json`. No hardcoded JSX text.
- `magnification` is a **String** (allows "63×", ranges).
- Backend unit tests live in `backend/tests/unit/`, run via the fast runner (memory `reference_fast_unit_test_runner`); handlers called directly with `current_user=SimpleNamespace(...)`, `db=mock_db`.
- Prod rebuild uses `docker-compose.prod.yml` (never dev, never `down -v`).
- Run code-simplifier after implementation (CLAUDE.md).

---

### Task 1: `Microscope` model + schemas + migration

**Files:**
- Create: `backend/models/microscope.py`
- Modify: `backend/models/__init__.py` (register `Microscope`)
- Create: `backend/schemas/microscope.py`
- Modify: `backend/database.py` (add tuples to `ensure_schema_updates` `updates` list, ~line 160)
- Create: `backend/migrations/009_add_microscope.sql`
- Test: `backend/tests/unit/test_microscope_model.py`

**Interfaces:**
- Produces: `models.microscope.Microscope` (cols `id, name, manufacturer, model, objective, magnification, description, color, created_at`); `schemas.microscope.{MicroscopeCreate, MicroscopeUpdate, MicroscopeResponse, MicroscopeDetailedResponse}`; `MicroscopeDetailedResponse.from_microscope(m, experiment_count)`.

- [ ] **Step 1: Write the failing test** — `backend/tests/unit/test_microscope_model.py`

```python
"""Microscope model + schema unit tests."""
from models.microscope import Microscope
from schemas.microscope import (
    MicroscopeCreate,
    MicroscopeDetailedResponse,
    MicroscopeResponse,
    MicroscopeUpdate,
)


def test_microscope_table_and_columns():
    assert Microscope.__tablename__ == "microscopes"
    cols = set(Microscope.__table__.columns.keys())
    assert cols == {
        "id", "name", "manufacturer", "model", "objective",
        "magnification", "description", "color", "created_at",
    }
    assert Microscope.__table__.columns["name"].unique is True


def test_microscope_shared_reference_no_owner():
    # Shared reference data: must NOT carry a user/group owner column.
    cols = set(Microscope.__table__.columns.keys())
    assert "user_id" not in cols
    assert "group_id" not in cols


def test_microscope_create_schema_defaults():
    m = MicroscopeCreate(name="Zeiss LSM 880")
    assert m.name == "Zeiss LSM 880"
    assert m.manufacturer is None and m.magnification is None


def test_microscope_detailed_from_model():
    class FakeM:
        id = 3
        name = "Leica SP8"
        manufacturer = "Leica"
        model = "SP8"
        objective = "HC PL APO 63×/1.40"
        magnification = "63×"
        description = None
        color = "#3b82f6"
        created_at = None
    resp = MicroscopeDetailedResponse.from_microscope(FakeM(), 5)
    assert resp.id == 3 and resp.experiment_count == 5
    assert resp.magnification == "63×"


def test_microscope_update_all_optional():
    assert MicroscopeUpdate().model_dump(exclude_unset=True) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run (fast unit runner, see memory `reference_fast_unit_test_runner`):
```bash
docker run --rm -v "$(pwd)/backend:/app" -w /app -e HF_HUB_OFFLINE=1 -e CUDA_VISIBLE_DEVICES= \
  --entrypoint python maptimize-backend:latest -m pytest tests/unit/test_microscope_model.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'models.microscope'`.

- [ ] **Step 3: Create the model** — `backend/models/microscope.py`

```python
"""Microscope model."""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Microscope(Base):
    """Microscope reference.

    Shared between all users (like MapProtein): reference data describing lab
    instruments that experiments can be assigned to. No user_id — one list for
    the whole lab.
    """

    __tablename__ = "microscopes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    objective: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # String, not numeric: magnifications are written "63×", "10×–100×", etc.
    magnification: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # Hex for UMAP legend
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Microscope(id={self.id}, name={self.name})>"
```

- [ ] **Step 4: Register the model** — `backend/models/__init__.py`

Add import after the experiment import and add `"Microscope"` to `__all__`:
```python
from .microscope import Microscope
```
```python
    "Microscope",
```

- [ ] **Step 5: Create schemas** — `backend/schemas/microscope.py`

```python
"""Microscope schemas."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MicroscopeCreate(BaseModel):
    """Schema for creating a microscope."""
    name: str = Field(..., min_length=1, max_length=100)
    manufacturer: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    objective: Optional[str] = Field(None, max_length=100)
    magnification: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class MicroscopeUpdate(BaseModel):
    """Schema for updating a microscope (all optional)."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    manufacturer: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    objective: Optional[str] = Field(None, max_length=100)
    magnification: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class MicroscopeResponse(BaseModel):
    """Basic microscope response (embedded in ExperimentResponse)."""
    id: int
    name: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    objective: Optional[str] = None
    magnification: Optional[str] = None
    color: Optional[str] = None

    class Config:
        from_attributes = True


class MicroscopeDetailedResponse(BaseModel):
    """Detailed microscope response with stats."""
    id: int
    name: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    objective: Optional[str] = None
    magnification: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    experiment_count: int = 0
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_microscope(cls, microscope, experiment_count: int = 0) -> "MicroscopeDetailedResponse":
        return cls(
            id=microscope.id,
            name=microscope.name,
            manufacturer=microscope.manufacturer,
            model=microscope.model,
            objective=microscope.objective,
            magnification=microscope.magnification,
            description=microscope.description,
            color=microscope.color,
            experiment_count=experiment_count,
            created_at=microscope.created_at,
        )
```

- [ ] **Step 6: Add migration tuples** — `backend/database.py`, in the `updates` list (after the `experiments.fasta_sequence` line, ~162)

```python
            # Microscope assignment at experiment level
            ("experiments", "microscope_id", "INTEGER REFERENCES microscopes(id)"),
```
(The `microscopes` table itself is auto-created by `create_all` at startup, since the model is now registered.)

- [ ] **Step 7: Create SQL migration** — `backend/migrations/009_add_microscope.sql`

```sql
-- Migration: Add microscopes reference table + experiments.microscope_id FK.
-- Microscopes are shared reference data (like map_proteins): no user_id.
-- Also applied at runtime by database.ensure_schema_updates() + create_all.

CREATE TABLE IF NOT EXISTS microscopes (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE,
    manufacturer  VARCHAR(100),
    model         VARCHAR(100),
    objective     VARCHAR(100),
    magnification VARCHAR(50),
    description   TEXT,
    color         VARCHAR(7),
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_microscopes_name ON microscopes (name);

ALTER TABLE experiments
    ADD COLUMN IF NOT EXISTS microscope_id INTEGER REFERENCES microscopes(id);
```

- [ ] **Step 8: Run test to verify it passes**

Run: same command as Step 2. Expected: PASS (5 tests).

- [ ] **Step 9: Commit**

```bash
git add backend/models/microscope.py backend/models/__init__.py backend/schemas/microscope.py \
        backend/database.py backend/migrations/009_add_microscope.sql backend/tests/unit/test_microscope_model.py
git commit -m "Add Microscope model, schemas, and migration"
```

---

### Task 2: Extract shared color picker (DRY)

**Files:**
- Create: `backend/utils/colors.py`
- Modify: `backend/routers/proteins.py` (replace local palette/`_generated_color`/`pick_protein_color` with imports)
- Test: `backend/tests/unit/test_colors.py`

**Interfaces:**
- Produces: `utils.colors.COLOR_PALETTE: list[str]`, `utils.colors.generated_color(index: int) -> str`, `utils.colors.pick_unused_color(used: set[str]) -> str` (pure, no DB — caller supplies the used set).
- Consumes: proteins router's `pick_protein_color(db)` becomes a thin wrapper querying `MapProtein.color` then calling `pick_unused_color`.

- [ ] **Step 1: Write the failing test** — `backend/tests/unit/test_colors.py`

```python
"""Shared color-picker unit tests."""
from utils.colors import COLOR_PALETTE, generated_color, pick_unused_color


def test_palette_nonempty_and_hex():
    assert len(COLOR_PALETTE) >= 12
    assert all(c.startswith("#") and len(c) == 7 for c in COLOR_PALETTE)


def test_pick_first_unused_from_palette():
    used = {COLOR_PALETTE[0].lower()}
    assert pick_unused_color(used) == COLOR_PALETTE[1]


def test_pick_falls_through_to_generated_when_palette_exhausted():
    used = {c.lower() for c in COLOR_PALETTE}
    picked = pick_unused_color(used)
    assert picked.startswith("#") and len(picked) == 7
    assert picked.lower() not in used


def test_generated_color_is_deterministic():
    assert generated_color(5) == generated_color(5)
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
docker run --rm -v "$(pwd)/backend:/app" -w /app -e HF_HUB_OFFLINE=1 -e CUDA_VISIBLE_DEVICES= \
  --entrypoint python maptimize-backend:latest -m pytest tests/unit/test_colors.py -q
```
Expected: FAIL — `No module named 'utils.colors'`.

- [ ] **Step 3: Create `backend/utils/colors.py`** (move the logic verbatim from `routers/proteins.py:96-162`, made DB-free)

```python
"""Shared color-assignment helpers for reference data (proteins, microscopes).

pick_unused_color guarantees only that the exact hex is unused — not that it is
visually distinct from what is already on the plot.
"""
import colorsys
import logging

logger = logging.getLogger(__name__)

COLOR_PALETTE = [
    "#3b82f6", "#ef4444", "#00d4aa", "#f59e0b", "#8b5cf6", "#ec4899",
    "#22c55e", "#06b6d4", "#f97316", "#a855f7", "#84cc16", "#e11d48",
    "#6366f1", "#eab308", "#10b981", "#d946ef", "#0ea5e9", "#14b8a6",
    "#f43f5e", "#65a30d",
]

# Golden angle as a fraction of a turn (137.5°). Spreads generated hues evenly.
_HUE_STEP = 0.381966


def generated_color(index: int) -> str:
    """Hue-rotated fallback colour for when the palette runs out."""
    r, g, b = colorsys.hls_to_rgb((index * _HUE_STEP) % 1.0, 0.58, 0.65)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def pick_unused_color(used: set[str]) -> str:
    """Pick a colour not present in ``used`` (lower-cased hex strings)."""
    for color in COLOR_PALETTE:
        if color.lower() not in used:
            return color

    for offset in range(len(used) + 1):
        candidate = generated_color(len(COLOR_PALETTE) + offset)
        if candidate.lower() not in used:
            return candidate

    fallback = generated_color(len(used))
    logger.warning(
        "Colour palette exhausted (%d in use); reusing %s.", len(used), fallback,
    )
    return fallback
```

- [ ] **Step 4: Refactor `backend/routers/proteins.py`** — remove `import colorsys`, `PROTEIN_COLOR_PALETTE`, `_HUE_STEP`, `_generated_color` (lines 93-127), and replace `pick_protein_color` body (130-162) with:

```python
from utils.colors import pick_unused_color


async def pick_protein_color(db: AsyncSession) -> str:
    """Pick a colour no existing protein is using.

    Check-then-act: two concurrent creates can pick the same colour. Accepted for
    the same reason as the document dedup in CLAUDE.md — the cost is one duplicate
    marker, while a unique constraint on colour would reject legitimate values.
    """
    result = await db.execute(
        select(MapProtein.color).where(MapProtein.color.isnot(None))
    )
    used = {row[0].lower() for row in result.all() if row[0]}
    return pick_unused_color(used)
```

- [ ] **Step 5: Run tests to verify pass** (colors + proteins regression)

Run:
```bash
docker run --rm -v "$(pwd)/backend:/app" -w /app -e HF_HUB_OFFLINE=1 -e CUDA_VISIBLE_DEVICES= \
  --entrypoint python maptimize-backend:latest -m pytest tests/unit/test_colors.py tests/unit/test_proteins*.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/utils/colors.py backend/routers/proteins.py backend/tests/unit/test_colors.py
git commit -m "Extract shared color picker into utils/colors (DRY)"
```

---

### Task 3: Microscopes router (CRUD)

**Files:**
- Create: `backend/routers/microscopes.py`
- Modify: `backend/routers/__init__.py` (import + include at prefix `/microscopes`)
- Test: `backend/tests/unit/test_microscopes_router.py`

**Interfaces:**
- Produces endpoints: `GET/POST /api/microscopes`, `GET/PATCH/DELETE /api/microscopes/{id}`.
- Consumes: `Microscope`, `Experiment` (for count), `MicroscopeCreate/Update/DetailedResponse`, `utils.colors.pick_unused_color`.

- [ ] **Step 1: Write the failing test** — `backend/tests/unit/test_microscopes_router.py`

```python
"""Microscopes router unit tests (handlers called directly, mocked db)."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.unit.conftest import make_result
from routers import microscopes as mod
from schemas.microscope import MicroscopeCreate, MicroscopeUpdate


def _user():
    return SimpleNamespace(id=1, name="Tester")


async def test_create_microscope_auto_color(mock_db, monkeypatch):
    # No name conflict, no existing colors → auto-picks palette[0].
    mock_db.execute.side_effect = [
        make_result(scalar=None),          # name-unique check
        make_result(fetchall=[]),          # used-colors query
    ]
    data = MicroscopeCreate(name="Zeiss LSM 880")
    resp = await mod.create_microscope(data, current_user=_user(), db=mock_db)
    assert resp.name == "Zeiss LSM 880"
    assert resp.color and resp.color.startswith("#")
    assert mock_db.add.called and mock_db.commit.await_count == 1


async def test_create_microscope_duplicate_name_400(mock_db):
    mock_db.execute.return_value = make_result(scalar=SimpleNamespace(id=9))
    with pytest.raises(HTTPException) as ei:
        await mod.create_microscope(
            MicroscopeCreate(name="dup"), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400


async def test_delete_microscope_conflict_when_referenced(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=SimpleNamespace(id=2, name="m")),  # get_or_404
        make_result(scalar=3),                                # experiment count
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.delete_microscope(2, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 409


async def test_delete_microscope_ok_when_unreferenced(mock_db):
    m = SimpleNamespace(id=2, name="m")
    mock_db.execute.side_effect = [
        make_result(scalar=m),   # get_or_404
        make_result(scalar=0),   # experiment count
    ]
    await mod.delete_microscope(2, current_user=_user(), db=mock_db)
    assert mock_db.delete.await_count == 1 and mock_db.commit.await_count == 1


async def test_get_microscope_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await mod.get_microscope(99, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
docker run --rm -v "$(pwd)/backend:/app" -w /app -e HF_HUB_OFFLINE=1 -e CUDA_VISIBLE_DEVICES= \
  --entrypoint python maptimize-backend:latest -m pytest tests/unit/test_microscopes_router.py -q
```
Expected: FAIL — `No module named 'routers.microscopes'` (or attribute errors).

- [ ] **Step 3: Create `backend/routers/microscopes.py`**

```python
"""Microscope routes (shared reference data, like proteins)."""
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.experiment import Experiment
from models.microscope import Microscope
from models.user import User
from schemas.microscope import (
    MicroscopeCreate,
    MicroscopeDetailedResponse,
    MicroscopeUpdate,
)
from utils.colors import pick_unused_color
from utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_microscope_or_404(microscope_id: int, db: AsyncSession) -> Microscope:
    result = await db.execute(select(Microscope).where(Microscope.id == microscope_id))
    microscope = result.scalar_one_or_none()
    if not microscope:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Microscope not found")
    return microscope


async def get_experiment_count(microscope_id: int, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count(Experiment.id)).where(Experiment.microscope_id == microscope_id)
    )
    return result.scalar() or 0


async def get_experiment_counts(db: AsyncSession) -> Dict[int, int]:
    result = await db.execute(
        select(Experiment.microscope_id, func.count(Experiment.id))
        .where(Experiment.microscope_id.isnot(None))
        .group_by(Experiment.microscope_id)
    )
    return dict(result.all())


async def check_name_unique(name: str, db: AsyncSession, exclude_id: Optional[int] = None) -> None:
    query = select(Microscope).where(Microscope.name == name)
    if exclude_id:
        query = query.where(Microscope.id != exclude_id)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Microscope with this name already exists",
        )


async def pick_microscope_color(db: AsyncSession) -> str:
    result = await db.execute(select(Microscope.color).where(Microscope.color.isnot(None)))
    used = {row[0].lower() for row in result.all() if row[0]}
    return pick_unused_color(used)


@router.get("", response_model=List[MicroscopeDetailedResponse])
async def list_microscopes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Microscope).order_by(Microscope.name))
    microscopes = result.scalars().all()
    counts = await get_experiment_counts(db)
    return [
        MicroscopeDetailedResponse.from_microscope(m, counts.get(m.id, 0))
        for m in microscopes
    ]


@router.post("", response_model=MicroscopeDetailedResponse, status_code=status.HTTP_201_CREATED)
async def create_microscope(
    data: MicroscopeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_name_unique(data.name, db)
    values = data.model_dump()
    if not values.get("color"):
        values["color"] = await pick_microscope_color(db)
    microscope = Microscope(**values)
    db.add(microscope)
    await db.commit()
    await db.refresh(microscope)
    return MicroscopeDetailedResponse.from_microscope(microscope, 0)


@router.get("/{microscope_id}", response_model=MicroscopeDetailedResponse)
async def get_microscope(
    microscope_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    microscope = await get_microscope_or_404(microscope_id, db)
    count = await get_experiment_count(microscope_id, db)
    return MicroscopeDetailedResponse.from_microscope(microscope, count)


@router.patch("/{microscope_id}", response_model=MicroscopeDetailedResponse)
async def update_microscope(
    microscope_id: int,
    data: MicroscopeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    microscope = await get_microscope_or_404(microscope_id, db)
    if data.name and data.name != microscope.name:
        await check_name_unique(data.name, db, exclude_id=microscope_id)

    update_data = data.model_dump(exclude_unset=True)
    # Explicit null color means "assign an unused one"; omitting leaves unchanged.
    if "color" in update_data and not update_data["color"]:
        update_data["color"] = await pick_microscope_color(db)
    for field, value in update_data.items():
        setattr(microscope, field, value)

    await db.commit()
    await db.refresh(microscope)
    count = await get_experiment_count(microscope_id, db)
    return MicroscopeDetailedResponse.from_microscope(microscope, count)


@router.delete("/{microscope_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_microscope(
    microscope_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    microscope = await get_microscope_or_404(microscope_id, db)
    count = await get_experiment_count(microscope_id, db)
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete microscope with {count} associated experiments",
        )
    await db.delete(microscope)
    await db.commit()
```

- [ ] **Step 4: Register the router** — `backend/routers/__init__.py`

Add import (after proteins):
```python
from .microscopes import router as microscopes_router
```
Add include (after proteins include, line ~28):
```python
api_router.include_router(microscopes_router, prefix="/microscopes", tags=["Microscopes"])
```

- [ ] **Step 5: Run test to verify it passes**

Run: same as Step 2. Expected: PASS (5 tests).
> If `make_result(fetchall=[])` doesn't drive the `.all()` path the color query uses, check `tests/unit/conftest.py` for the correct kwarg (`fetchall=` maps to `.all()`); adjust the test to match the helper, not the reverse.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/microscopes.py backend/routers/__init__.py backend/tests/unit/test_microscopes_router.py
git commit -m "Add microscopes CRUD router"
```

---

### Task 4: Experiment ↔ microscope integration

**Files:**
- Modify: `backend/models/experiment.py` (add `microscope_id` col + `microscope` relationship)
- Modify: `backend/schemas/experiment.py` (add to `ExperimentCreate`, `ExperimentUpdate`, `ExperimentResponse`)
- Modify: `backend/routers/experiments.py` (verify-exists on create/update; selectinload microscope in list/get)
- Test: `backend/tests/unit/test_experiment_microscope.py`

**Interfaces:**
- Consumes: `models.microscope.Microscope`, `schemas.microscope.MicroscopeResponse`.
- Produces: `ExperimentCreate.microscope_id`, `ExperimentUpdate.microscope_id`, `ExperimentResponse.microscope`.

- [ ] **Step 1: Write the failing test** — `backend/tests/unit/test_experiment_microscope.py`

```python
"""Experiment ↔ microscope integration unit tests."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.unit.conftest import make_result
from routers import experiments as mod
from schemas.experiment import ExperimentCreate, ExperimentUpdate


def _user():
    return SimpleNamespace(id=1, name="Tester")


def test_schemas_have_microscope_id():
    assert "microscope_id" in ExperimentCreate.model_fields
    assert "microscope_id" in ExperimentUpdate.model_fields


async def test_create_experiment_missing_microscope_404(mock_db, monkeypatch):
    async def fake_group_id(uid, db):
        return None
    monkeypatch.setattr(mod, "get_user_group_id", fake_group_id)
    # protein not requested; microscope lookup returns None → 404
    mock_db.execute.return_value = make_result(scalar=None)
    data = ExperimentCreate(name="E", microscope_id=42)
    with pytest.raises(HTTPException) as ei:
        await mod.create_experiment(data, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 404
    assert "microscope" in ei.value.detail.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
docker run --rm -v "$(pwd)/backend:/app" -w /app -e HF_HUB_OFFLINE=1 -e CUDA_VISIBLE_DEVICES= \
  --entrypoint python maptimize-backend:latest -m pytest tests/unit/test_experiment_microscope.py -q
```
Expected: FAIL — `microscope_id` not a field / no 404.

- [ ] **Step 3: Model** — `backend/models/experiment.py`

In `TYPE_CHECKING` block add `from .microscope import Microscope`. Add column after `map_protein_id` (line 37) and relationship after `map_protein` (line 61):
```python
    microscope_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("microscopes.id"),
        nullable=True
    )
```
```python
    microscope: Mapped[Optional["Microscope"]] = relationship()
```

- [ ] **Step 4: Schemas** — `backend/schemas/experiment.py`

Add import: `from schemas.microscope import MicroscopeResponse`. Then:
- `ExperimentCreate`: add `microscope_id: Optional[int] = None`
- `ExperimentUpdate`: add `microscope_id: Optional[int] = None`
- `ExperimentResponse`: add `microscope: Optional[MicroscopeResponse] = None`

- [ ] **Step 5: Router** — `backend/routers/experiments.py`

Add import `from models.microscope import Microscope`. In `create_experiment`, after the protein-exists check (line 113), add:
```python
    if data.microscope_id is not None:
        micro_result = await db.execute(
            select(Microscope).where(Microscope.id == data.microscope_id)
        )
        if not micro_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Microscope not found",
            )
```
Add `microscope_id=data.microscope_id,` to the `Experiment(...)` constructor (after `map_protein_id`). Change the refresh to include microscope:
```python
    await db.refresh(experiment, attribute_names=["map_protein", "microscope"])
```
In `update_experiment`, after computing `update_data` (line 195), before the setattr loop, add a microscope existence check:
```python
    if update_data.get("microscope_id") is not None:
        micro_result = await db.execute(
            select(Microscope).where(Microscope.id == update_data["microscope_id"])
        )
        if not micro_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Microscope not found",
            )
```
Change `update_experiment`'s final refresh to load microscope:
```python
    await db.refresh(experiment, attribute_names=["map_protein", "microscope"])
    return ExperimentResponse.model_validate(experiment)
```
In `list_experiments` add `.options(selectinload(Experiment.microscope))` alongside the existing `selectinload(Experiment.map_protein)` (line 73), and in `get_experiment` add `selectinload(Experiment.microscope)` to its options block (line 147).

- [ ] **Step 6: Run test to verify it passes**

Run: same as Step 2. Expected: PASS.

- [ ] **Step 7: Run the experiments regression suite**

Run:
```bash
docker run --rm -v "$(pwd)/backend:/app" -w /app -e HF_HUB_OFFLINE=1 -e CUDA_VISIBLE_DEVICES= \
  --entrypoint python maptimize-backend:latest -m pytest tests/unit/test_experiment*.py -q
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/models/experiment.py backend/schemas/experiment.py backend/routers/experiments.py \
        backend/tests/unit/test_experiment_microscope.py
git commit -m "Wire microscope_id into experiment create/update/response"
```

---

### Task 5: UMAP filter by microscope

**Files:**
- Modify: `backend/routers/embeddings.py` (`get_umap_visualization` + `_get_cropped_umap` + `_get_fov_umap`)
- Test: `backend/tests/unit/test_umap_microscope_filter.py`

**Interfaces:**
- Produces: `GET /api/embeddings/umap?microscope_id=<int>` adds `WHERE Experiment.microscope_id = <int>` to both corpora. `None` = all (unchanged behavior).

- [ ] **Step 1: Write the failing test** — `backend/tests/unit/test_umap_microscope_filter.py`

```python
"""UMAP microscope_id filter is applied to the query."""
import inspect

from routers import embeddings as mod


def test_umap_endpoint_accepts_microscope_id():
    sig = inspect.signature(mod.get_umap_visualization)
    assert "microscope_id" in sig.parameters


def test_cropped_helper_accepts_microscope_id():
    sig = inspect.signature(mod._get_cropped_umap)
    assert "microscope_id" in sig.parameters


def test_fov_helper_accepts_microscope_id():
    sig = inspect.signature(mod._get_fov_umap)
    assert "microscope_id" in sig.parameters
```
> Signature-level test keeps this offline-safe (no DB/greenlet). The filter's SQL correctness is covered live in Task 12.

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
docker run --rm -v "$(pwd)/backend:/app" -w /app -e HF_HUB_OFFLINE=1 -e CUDA_VISIBLE_DEVICES= \
  --entrypoint python maptimize-backend:latest -m pytest tests/unit/test_umap_microscope_filter.py -q
```
Expected: FAIL — parameter missing.

- [ ] **Step 3: Implement** — `backend/routers/embeddings.py`

In `get_umap_visualization` add the query param after `experiment_id` (line 46):
```python
    microscope_id: Optional[int] = Query(None, description="Filter by microscope"),
```
Thread it into both dispatch calls:
```python
    if umap_type is UmapType.FOV:
        return await _get_fov_umap(
            experiment_id, microscope_id, current_user, group_id, background_tasks, db
        )
    return await _get_cropped_umap(
        experiment_id, microscope_id, current_user, group_id, background_tasks, db
    )
```
Add `microscope_id: Optional[int]` as the 2nd param of both `_get_cropped_umap` and `_get_fov_umap` (after `experiment_id`). In `_get_cropped_umap`, after the `experiment_id` filter block (line 163), add:
```python
    if microscope_id is not None:
        query = query.where(Experiment.microscope_id == microscope_id)
```
In `_get_fov_umap`, after its `experiment_id` block (line 242), add the identical two lines.

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2. Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/embeddings.py backend/tests/unit/test_umap_microscope_filter.py
git commit -m "Add microscope_id filter to UMAP endpoint"
```

---

### Task 6: MCP tools + version bump

**Files:**
- Modify: `mcp-server/maptalk_mcp/tools.yaml` (add microscope tools; add `microscope_id` to `create_experiment`/`update_experiment`)
- Modify: `mcp-server/maptalk_mcp/server.py` (`SERVER_VERSION` → `2.2.0`)
- Test: run existing MCP registry test suite

**Interfaces:**
- Produces MCP tools: `list_microscopes`, `get_microscope`, `create_microscope`, `update_microscope`, `delete_microscope`; extends `create_experiment`/`update_experiment` with `microscope_id`.

- [ ] **Step 1: Add tools to `tools.yaml`** (place after the `delete_protein` block, ~line 872, mirroring the protein tools)

```yaml
  - name: list_microscopes
    annotations: {readOnlyHint: true, openWorldHint: false}
    description: >
      List all microscopes (shared reference data): id, name, manufacturer, model,
      objective, magnification, and per-microscope experiment count. Use it to get
      a microscope_id for create_experiment or update_experiment.
    handler: http_json
    method: GET
    path: /api/microscopes
    params: []

  - name: get_microscope
    annotations: {readOnlyHint: true, openWorldHint: false}
    description: Get one microscope by id, with its full metadata.
    handler: http_json
    method: GET
    path: /api/microscopes/{microscope_id}
    params:
      - name: microscope_id
        in: path
        type: integer
        required: true

  - name: create_microscope
    annotations: {readOnlyHint: false, destructiveHint: false}
    description: >
      Create a microscope (shared reference data — visible to everyone). Only
      `name` is required (must be unique); a display color is auto-picked if you
      omit it. Optionally set manufacturer, model, objective, magnification
      (e.g. "63×"), description, color (#rrggbb).
    handler: http_post_json
    method: POST
    path: /api/microscopes
    params:
      - name: name
        in: body
        type: string
        required: true
        description: Unique microscope name (1-100 chars).
      - name: manufacturer
        in: body
        type: string
      - name: model
        in: body
        type: string
      - name: objective
        in: body
        type: string
      - name: magnification
        in: body
        type: string
        description: Free text, e.g. "63×" or "10×–100×".
      - name: description
        in: body
        type: string
      - name: color
        in: body
        type: string
        description: Hex color like "#1f77b4"; auto-picked if omitted.

  - name: update_microscope
    annotations: {readOnlyHint: false, destructiveHint: false}
    description: >
      Update a microscope's fields (only the ones you pass are changed). Shared
      reference data — changes are visible to everyone.
    handler: http_post_json
    method: PATCH
    path: /api/microscopes/{microscope_id}
    params:
      - name: microscope_id
        in: path
        type: integer
        required: true
      - name: name
        in: body
        type: string
        description: New unique name (1-100 chars).
      - name: manufacturer
        in: body
        type: string
      - name: model
        in: body
        type: string
      - name: objective
        in: body
        type: string
      - name: magnification
        in: body
        type: string
      - name: description
        in: body
        type: string
      - name: color
        in: body
        type: string
        description: Hex color like "#1f77b4".

  - name: delete_microscope
    annotations: {readOnlyHint: false, destructiveHint: true, idempotentHint: false}
    description: >
      Permanently delete a microscope. IRREVERSIBLE. Refused (409) if any
      experiment still references it — reassign those first. Shared reference data.
    handler: http_json
    method: DELETE
    path: /api/microscopes/{microscope_id}
    params:
      - name: microscope_id
        in: path
        type: integer
        required: true
```

- [ ] **Step 2: Extend experiment tools** — in `create_experiment` params (after `map_protein_id`, ~line 532) add:
```yaml
      - name: microscope_id
        in: body
        type: integer
        description: Optional microscope to assign (id from list_microscopes).
```
In `update_experiment` params (after `fasta_sequence`, ~line 566) add the same block. Also append to `update_experiment`'s description: `You can also (re)assign a microscope with microscope_id.`

- [ ] **Step 3: Bump version** — `mcp-server/maptalk_mcp/server.py` line 158:
```python
SERVER_VERSION = "2.2.0"
```

- [ ] **Step 4: Run MCP tests**

Run:
```bash
cd mcp-server && .venv/bin/python -m pytest -q ; cd ..
```
Expected: PASS (registry loads the new YAML entries; no schema errors).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/maptalk_mcp/tools.yaml mcp-server/maptalk_mcp/server.py
git commit -m "MCP: add microscope tools + microscope_id on experiment tools (v2.2.0)"
```

---

### Task 7: Frontend API client (types + methods)

**Files:**
- Modify: `frontend/lib/api.ts`

**Interfaces:**
- Produces: TS types `Microscope`, `MicroscopeCreate`, `MicroscopeUpdate`; methods `getMicroscopes()`, `createMicroscope(data)`, `updateMicroscope(id, data)`, `deleteMicroscope(id)`; `getUmapData(experimentId?, umapType?, microscopeId?)`; `microscope_id?` on `createExperiment`/`updateExperiment` payloads; `microscope?` on the `Experiment` interface.

- [ ] **Step 1: Add types** (near the `MapProteinDetailed` block, ~line 1400)

```typescript
export interface Microscope {
  id: number;
  name: string;
  manufacturer?: string;
  model?: string;
  objective?: string;
  magnification?: string;
  description?: string;
  color?: string;
  experiment_count: number;
  created_at?: string;
}

export interface MicroscopeCreate {
  name: string;
  manufacturer?: string;
  model?: string;
  objective?: string;
  magnification?: string;
  description?: string;
  color?: string;
}

export interface MicroscopeUpdate {
  name?: string;
  manufacturer?: string;
  model?: string;
  objective?: string;
  magnification?: string;
  description?: string;
  /** null asks the backend to assign an unused colour; omit to leave unchanged. */
  color?: string | null;
}
```

- [ ] **Step 2: Add API methods** (after the proteins block, ~line 358)

```typescript
  // Microscopes
  async getMicroscopes() {
    return this.request<Microscope[]>("/api/microscopes");
  }

  async createMicroscope(data: MicroscopeCreate) {
    return this.request<Microscope>("/api/microscopes", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async updateMicroscope(id: number, data: MicroscopeUpdate) {
    return this.request<Microscope>(`/api/microscopes/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  async deleteMicroscope(id: number) {
    return this.request<void>(`/api/microscopes/${id}`, { method: "DELETE" });
  }
```

- [ ] **Step 3: Extend `getUmapData`** (lines 572-584) to accept an optional `microscopeId`:

```typescript
  async getUmapData(
    experimentId?: number,
    umapType: UmapType = "cropped",
    microscopeId?: number
  ): Promise<UmapDataResponse | UmapFovDataResponse> {
    const params = new URLSearchParams({ umap_type: umapType });
    if (experimentId) {
      params.append("experiment_id", experimentId.toString());
    }
    if (microscopeId) {
      params.append("microscope_id", microscopeId.toString());
    }
    if (umapType === "fov") {
      return this.request<UmapFovDataResponse>(`/api/embeddings/umap?${params}`);
    }
    return this.request<UmapDataResponse>(`/api/embeddings/umap?${params}`);
  }
```

- [ ] **Step 4: Extend experiment payloads** — add `microscope_id?: number;` to the `createExperiment` data type (line 163-168) and change `updateExperiment` signature (line 188) to:
```typescript
  async updateExperiment(id: number, data: { name?: string; description?: string; microscope_id?: number }) {
```
Also add `microscope?: Microscope | null;` to the `Experiment` interface (find `export interface Experiment` near line 1835, next to `map_protein_id`).

- [ ] **Step 5: Verify typecheck**

Run:
```bash
cd frontend && npx tsc --noEmit ; cd ..
```
Expected: no new errors referencing microscope types.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api.ts
git commit -m "Frontend API: microscope types, CRUD methods, UMAP filter param"
```

---

### Task 8: Sidebar tab + navigation i18n

**Files:**
- Modify: `frontend/components/layout/AppSidebar.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/fr.json` (`navigation` namespace)

- [ ] **Step 1: Add nav item** — `AppSidebar.tsx`: add `Microscope` to the lucide import block (line 24-36), and a nav entry after proteins (line 77):
```typescript
    { name: t("microscopes"), href: "/dashboard/microscopes", icon: Microscope },
```

- [ ] **Step 2: Add i18n keys** — in the `navigation` object of BOTH message files (en.json ~line 49-59), add after `"proteins"`:
  - en.json: `"microscopes": "Microscopes",`
  - fr.json: `"microscopes": "Microscopes",`
  (Edit as plain text — do not JSON round-trip, per memory `project_i18n_duplicate_keys`.)

- [ ] **Step 3: Verify** — `cd frontend && npx tsc --noEmit` (no errors); visually confirm the key exists in both files with `grep -n '"microscopes"' frontend/messages/en.json frontend/messages/fr.json`.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/layout/AppSidebar.tsx frontend/messages/en.json frontend/messages/fr.json
git commit -m "Add Microscopes sidebar tab + nav i18n"
```

---

### Task 9: Microscopes management page

**Files:**
- Create: `frontend/app/dashboard/microscopes/page.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/fr.json` (new `microscopesPage` namespace)

**Approach:** Clone `frontend/app/dashboard/proteins/page.tsx` and reduce it to the microscope shape. Concretely, the new page has NO: UMAP viz (`ProteinUmapVisualization`), UniProt/FASTA fetching, embedding logic/`computeEmbeddingMutation`, `ProteinEmbeddingStatus`. It KEEPS: header + create button, success/error banners, card grid + `EmptyState`, one `Dialog` for create/edit, `ConfirmModal`, TanStack query + create/update/delete mutations with invalidation.

- [ ] **Step 1: Create the page** — `frontend/app/dashboard/microscopes/page.tsx`

```tsx
"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, Microscope, MicroscopeCreate, MicroscopeUpdate } from "@/lib/api";
import { ConfirmModal, Dialog, EmptyState, LoadingContainer } from "@/components/ui";
import { staggerContainerVariants, staggerItemVariants } from "@/lib/animations";
import { Plus, Microscope as MicroscopeIcon, Loader2, Trash2, Edit3, AlertCircle, CheckCircle, X, FolderOpen } from "lucide-react";

const COLOR_PLACEHOLDER = "#64748b";

const DEFAULT_FORM_DATA: MicroscopeCreate = {
  name: "",
  manufacturer: "",
  model: "",
  objective: "",
  magnification: "",
  description: "",
  color: "",
};

export default function MicroscopesPage(): JSX.Element {
  const t = useTranslations("microscopesPage");
  const tCommon = useTranslations("common");
  const queryClient = useQueryClient();

  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Microscope | null>(null);
  const [toDelete, setToDelete] = useState<Microscope | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [formData, setFormData] = useState<MicroscopeCreate>(DEFAULT_FORM_DATA);

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["microscopes"] });
  }, [queryClient]);

  const showSuccess = useCallback((message: string) => {
    setSuccessMessage(message);
    setTimeout(() => setSuccessMessage(null), 3000);
  }, []);

  const { data: microscopes, isLoading } = useQuery({
    queryKey: ["microscopes"],
    queryFn: () => api.getMicroscopes(),
  });

  const createMutation = useMutation({
    mutationFn: (data: MicroscopeCreate) => api.createMicroscope(data),
    onSuccess: () => { invalidate(); closeModal(); },
    onError: (err: Error) => setError(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: MicroscopeUpdate }) =>
      api.updateMicroscope(id, data),
    onSuccess: () => { invalidate(); closeModal(); },
    onError: (err: Error) => setError(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteMicroscope(id),
    onSuccess: () => { invalidate(); setToDelete(null); showSuccess(t("deleteSuccess")); },
    onError: (err: Error) => { setError(err.message || t("deleteError")); setToDelete(null); },
  });

  const openCreateModal = () => {
    setEditing(null);
    setFormData(DEFAULT_FORM_DATA);
    setShowModal(true);
    setError(null);
  };

  const openEditModal = (m: Microscope) => {
    setEditing(m);
    setFormData({
      name: m.name,
      manufacturer: m.manufacturer || "",
      model: m.model || "",
      objective: m.objective || "",
      magnification: m.magnification || "",
      description: m.description || "",
      color: m.color || "",
    });
    setShowModal(true);
    setError(null);
  };

  const closeModal = () => { setShowModal(false); setEditing(null); setError(null); };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const { color, ...rest } = formData;
    if (editing) {
      updateMutation.mutate({ id: editing.id, data: { ...rest, color: color || null } });
    } else {
      createMutation.mutate(color ? { ...rest, color } : rest);
    }
  };

  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-bold text-text-primary">{t("title")}</h1>
          <p className="text-text-secondary mt-1">{t("subtitle")}</p>
        </div>
        <button onClick={openCreateModal} className="btn-primary flex items-center gap-2">
          <Plus className="w-5 h-5" />
          {t("create")}
        </button>
      </div>

      {/* Success / error banners */}
      <AnimatePresence>
        {successMessage && (
          <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}
            className="p-4 bg-green-500/10 border border-green-500/20 rounded-lg flex items-center gap-3">
            <CheckCircle className="w-5 h-5 text-green-400" />
            <span className="text-green-400">{successMessage}</span>
          </motion.div>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {error && (
          <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}
            className="p-4 bg-accent-red/10 border border-accent-red/20 rounded-lg flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-accent-red" />
            <span className="text-accent-red flex-1">{error}</span>
            <button onClick={() => setError(null)} className="text-text-muted hover:text-text-primary">
              <X className="w-4 h-4" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Grid */}
      <LoadingContainer isLoading={isLoading}>
        {microscopes && microscopes.length > 0 ? (
          <motion.div variants={staggerContainerVariants} initial="hidden" animate="visible"
            className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {microscopes.map((m) => (
              <motion.div key={m.id} variants={staggerItemVariants}
                className="glass-card p-6 group hover:border-primary-500/30 transition-all duration-300">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="w-4 h-4 rounded-full" style={{ backgroundColor: m.color || "#888" }} />
                    <div>
                      <h3 className="font-display font-semibold text-lg text-text-primary">{m.name}</h3>
                      {m.manufacturer && <p className="text-sm text-text-secondary">{m.manufacturer}</p>}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => openEditModal(m)} className="p-1.5 hover:bg-white/5 rounded-lg transition-colors" title={t("edit")}>
                      <Edit3 className="w-4 h-4 text-text-muted hover:text-primary-400" />
                    </button>
                    <button onClick={() => setToDelete(m)} className="p-1.5 hover:bg-accent-red/10 rounded-lg transition-colors"
                      title={tCommon("delete")} disabled={m.experiment_count > 0}>
                      <Trash2 className={`w-4 h-4 ${m.experiment_count > 0 ? "text-text-muted/30 cursor-not-allowed" : "text-text-muted hover:text-accent-red"}`} />
                    </button>
                  </div>
                </div>
                <div className="space-y-2 text-sm">
                  {m.model && <div className="flex items-center gap-2 text-text-secondary"><span className="text-text-muted">{t("model")}:</span><span>{m.model}</span></div>}
                  {m.objective && <div className="flex items-center gap-2 text-text-secondary"><span className="text-text-muted">{t("objective")}:</span><span>{m.objective}</span></div>}
                  {m.magnification && <div className="flex items-center gap-2 text-text-secondary"><span className="text-text-muted">{t("magnification")}:</span><span>{m.magnification}</span></div>}
                </div>
                <div className="flex items-center justify-between mt-4 pt-4 border-t border-white/5">
                  <div className="flex items-center gap-1 text-sm text-text-muted">
                    <FolderOpen className="w-4 h-4" />
                    <span>{m.experiment_count} {t("experiments")}</span>
                  </div>
                </div>
              </motion.div>
            ))}
          </motion.div>
        ) : (
          <EmptyState icon={MicroscopeIcon} title={t("noMicroscopes")} description={t("startFirst")}
            action={{ label: t("create"), onClick: openCreateModal, icon: Plus }} />
        )}
      </LoadingContainer>

      {/* Create/Edit modal */}
      <Dialog isOpen={showModal} onClose={closeModal} title={editing ? t("edit") : t("create")} maxWidth="lg">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{t("name")} *</label>
            <input type="text" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="input-field" placeholder="e.g., Zeiss LSM 880" required />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">{t("manufacturer")}</label>
              <input type="text" value={formData.manufacturer} onChange={(e) => setFormData({ ...formData, manufacturer: e.target.value })}
                className="input-field" placeholder="e.g., Zeiss" />
            </div>
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">{t("model")}</label>
              <input type="text" value={formData.model} onChange={(e) => setFormData({ ...formData, model: e.target.value })}
                className="input-field" placeholder="e.g., LSM 880" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">{t("objective")}</label>
              <input type="text" value={formData.objective} onChange={(e) => setFormData({ ...formData, objective: e.target.value })}
                className="input-field" placeholder="e.g., Plan-Apochromat 63×/1.4 Oil" />
            </div>
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-2">{t("magnification")}</label>
              <input type="text" value={formData.magnification} onChange={(e) => setFormData({ ...formData, magnification: e.target.value })}
                className="input-field" placeholder="e.g., 63×" />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{t("description")}</label>
            <textarea value={formData.description} onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              className="input-field min-h-[80px] resize-none" />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-2">{t("color")}</label>
            <div className="flex items-center gap-3">
              <input type="color" value={formData.color || COLOR_PLACEHOLDER} onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                className="w-10 h-10 rounded-lg cursor-pointer border-0 bg-transparent" aria-label={t("color")} />
              <input type="text" value={formData.color} onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                className="input-field flex-1 font-mono" placeholder={t("colorAutoPlaceholder")} />
              {formData.color && (
                <button type="button" onClick={() => setFormData({ ...formData, color: "" })}
                  className="px-3 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors">
                  {t("colorAuto")}
                </button>
              )}
            </div>
            {!formData.color && <p className="text-xs text-text-muted mt-1.5">{t("colorAutoHint")}</p>}
          </div>
          <div className="flex gap-3 pt-4">
            <button type="button" onClick={closeModal} className="btn-secondary flex-1">{tCommon("cancel")}</button>
            <button type="submit" disabled={isSubmitting || !formData.name.trim()}
              className="btn-primary flex-1 flex items-center justify-center gap-2">
              {isSubmitting ? <Loader2 className="w-5 h-5 animate-spin" /> : tCommon(editing ? "save" : "create")}
            </button>
          </div>
        </form>
      </Dialog>

      <ConfirmModal isOpen={!!toDelete} onClose={() => setToDelete(null)}
        onConfirm={() => toDelete && deleteMutation.mutate(toDelete.id)}
        title={tCommon("delete")} message={t("deleteConfirm")} detail={toDelete?.name}
        confirmLabel={tCommon("delete")} cancelLabel={tCommon("cancel")}
        isLoading={deleteMutation.isPending} variant="danger" />
    </div>
  );
}
```

- [ ] **Step 2: Add `microscopesPage` i18n namespace** to BOTH message files (top-level, sibling of `proteinsPage`). Keys used above:

en.json:
```json
  "microscopesPage": {
    "title": "Microscopes",
    "subtitle": "Define the microscopes your experiments were acquired on",
    "create": "New Microscope",
    "edit": "Edit Microscope",
    "name": "Name",
    "manufacturer": "Manufacturer",
    "model": "Model",
    "objective": "Objective",
    "magnification": "Magnification",
    "description": "Description",
    "color": "Color",
    "colorAuto": "Auto",
    "colorAutoPlaceholder": "Auto-assigned",
    "colorAutoHint": "Leave empty to auto-assign an unused color",
    "experiments": "experiments",
    "noMicroscopes": "No microscopes yet",
    "startFirst": "Define your first microscope to assign it to experiments",
    "deleteConfirm": "Delete this microscope? Experiments keep their data but lose the assignment.",
    "deleteSuccess": "Microscope deleted",
    "deleteError": "Failed to delete microscope"
  },
```
fr.json (same keys, French values):
```json
  "microscopesPage": {
    "title": "Microscopes",
    "subtitle": "Définissez les microscopes utilisés pour vos expériences",
    "create": "Nouveau microscope",
    "edit": "Modifier le microscope",
    "name": "Nom",
    "manufacturer": "Fabricant",
    "model": "Modèle",
    "objective": "Objectif",
    "magnification": "Grossissement",
    "description": "Description",
    "color": "Couleur",
    "colorAuto": "Auto",
    "colorAutoPlaceholder": "Attribuée automatiquement",
    "colorAutoHint": "Laisser vide pour attribuer automatiquement une couleur inutilisée",
    "experiments": "expériences",
    "noMicroscopes": "Aucun microscope",
    "startFirst": "Définissez votre premier microscope pour l'associer aux expériences",
    "deleteConfirm": "Supprimer ce microscope ? Les expériences conservent leurs données mais perdent l'association.",
    "deleteSuccess": "Microscope supprimé",
    "deleteError": "Échec de la suppression du microscope"
  },
```

- [ ] **Step 3: Verify** — `cd frontend && npx tsc --noEmit` (no errors). `grep -c '"microscopesPage"' frontend/messages/en.json frontend/messages/fr.json` → 1 each.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/dashboard/microscopes/page.tsx frontend/messages/en.json frontend/messages/fr.json
git commit -m "Add Microscopes management page + i18n"
```

---

### Task 10: Experiment-form microscope dropdown

**Files:**
- Modify: `frontend/app/dashboard/experiments/page.tsx`

**Approach:** Mirror the existing protein selector (`selectedProteinId`/`proteinDropdownOpen`, lines 38-39, 52, 322-370, and the create payload at 106). Add a parallel `selectedMicroscopeId` fed by `api.getMicroscopes()`, render a matching dropdown, and include `microscope_id: selectedMicroscopeId ?? undefined` in the create-experiment payload + reset it on success (line 78).

- [ ] **Step 1: Read the current protein selector** to copy its exact markup:
```bash
sed -n '30,115p;315,380p' frontend/app/dashboard/experiments/page.tsx
```

- [ ] **Step 2: Add state + query** — beside the protein equivalents:
```typescript
  const [selectedMicroscopeId, setSelectedMicroscopeId] = useState<number | null>(null);
  const [microscopeDropdownOpen, setMicroscopeDropdownOpen] = useState(false);
```
```typescript
  const { data: microscopes } = useQuery({
    queryKey: ["microscopes"],
    queryFn: () => api.getMicroscopes(),
  });
  const selectedMicroscope = microscopes?.find((m) => m.id === selectedMicroscopeId);
```
Extend the create mutation's `mutationFn` type to include `microscope_id?: number` and add `microscope_id: selectedMicroscopeId ?? undefined` to the payload built in `handleCreate` (line ~106). Reset `setSelectedMicroscopeId(null)` in the create `onSuccess` (line ~78). Close both dropdowns in the outside-click handler (line ~59).

- [ ] **Step 3: Render the dropdown** — duplicate the protein selector block (lines 322-370) below it, substituting: `t("assignMicroscope")` label, `microscopes` list, `selectedMicroscope`, `setSelectedMicroscopeId`, `microscopeDropdownOpen`/`setMicroscopeDropdownOpen`, and `t("unassignedMicroscope")` for the "none" option. (No color swatch needed unless present; keep parity with protein markup.)

- [ ] **Step 4: i18n** — add to the `experiments` namespace in en.json + fr.json:
  - en: `"assignMicroscope": "Microscope",` and `"unassignedMicroscope": "No microscope",`
  - fr: `"assignMicroscope": "Microscope",` and `"unassignedMicroscope": "Aucun microscope",`

- [ ] **Step 5: Verify** — `cd frontend && npx tsc --noEmit`. Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/dashboard/experiments/page.tsx frontend/messages/en.json frontend/messages/fr.json
git commit -m "Add microscope selector to experiment create form"
```

---

### Task 11: Dashboard UMAP microscope switcher

**Files:**
- Modify: `frontend/components/visualization/UmapVisualization.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/fr.json` (`umap` namespace)

**Approach:** Add a `microscopeId` state + a `<select>` in the header (next to the fov/cropped toggle). Fetch microscopes with TanStack Query. Feed `microscopeId` into the query key and `getUmapData`.

- [ ] **Step 1: Imports + query + state** — in `UmapVisualization.tsx`:
  - Extend the `@/lib/api` import (line 18-26) with `Microscope`.
  - Add after `const [viewMode, ...]` (line 159):
```typescript
  const [microscopeId, setMicroscopeId] = useState<number | null>(null);

  const { data: microscopes } = useQuery({
    queryKey: ["microscopes"],
    queryFn: () => api.getMicroscopes(),
    staleTime: 1000 * 60 * 5,
  });
```
  - Change the umap query (lines 163-172) key + fn:
```typescript
    queryKey: ["umap", experimentId, viewMode, microscopeId],
    queryFn: () => api.getUmapData(experimentId, viewMode, microscopeId ?? undefined),
```

- [ ] **Step 2: Render the dropdown** — inside the header's right-hand `<div className="flex items-center gap-3">` (line 489), before the fov/cropped toggle, add:
```tsx
          {microscopes && microscopes.length > 0 && (
            <select
              value={microscopeId ?? ""}
              onChange={(e) => setMicroscopeId(e.target.value ? Number(e.target.value) : null)}
              className="input-field py-1.5 text-sm max-w-[180px]"
              title={t("microscopeFilter")}
            >
              <option value="">{t("allMicroscopes")}</option>
              {microscopes.map((m) => (
                <option key={m.id} value={m.id}>{m.name}</option>
              ))}
            </select>
          )}
```

- [ ] **Step 3: i18n** — add to the `umap` namespace in both message files:
  - en: `"allMicroscopes": "All microscopes",` and `"microscopeFilter": "Filter by microscope",`
  - fr: `"allMicroscopes": "Tous les microscopes",` and `"microscopeFilter": "Filtrer par microscope",`

- [ ] **Step 4: Verify** — `cd frontend && npx tsc --noEmit`. Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/visualization/UmapVisualization.tsx frontend/messages/en.json frontend/messages/fr.json
git commit -m "Add microscope filter dropdown to dashboard UMAP"
```

---

### Task 12: Simplify, full test pass, rebuild & live verify

**Files:** whole changeset.

- [ ] **Step 1: Run code-simplifier** on the changed backend + frontend + MCP files (DRY/SSOT check per CLAUDE.md). Apply safe simplifications; re-run tests after.

- [ ] **Step 2: Full backend unit suite**

Run:
```bash
docker run --rm -v "$(pwd)/backend:/app" -w /app -e HF_HUB_OFFLINE=1 -e CUDA_VISIBLE_DEVICES= \
  --entrypoint python maptimize-backend:latest -m pytest tests/unit -q
```
Expected: all green.

- [ ] **Step 3: Frontend build**

Run: `cd frontend && npx tsc --noEmit && npm run build ; cd ..`. Expected: builds clean.

- [ ] **Step 4: MCP tests** — `cd mcp-server && .venv/bin/python -m pytest -q ; cd ..`. Expected: green.

- [ ] **Step 5: Rebuild prod** (backend + mcp + frontend)

```bash
docker compose -f docker-compose.prod.yml build maptimize-backend maptimize-mcp maptimize-frontend --no-cache
docker compose -f docker-compose.prod.yml up -d maptimize-backend maptimize-mcp maptimize-frontend
```
> `maptimize-mcp` rebuild is required because `server.py` (SERVER_VERSION) changed; `tools.yaml` alone hot-reloads.

- [ ] **Step 6: Live verify** — confirm startup migration created the table and the flow works:
```bash
docker exec maptimize-db psql -U maptimize -d maptimize -c "\d microscopes" -c "\d experiments" | grep -i microscope
```
Then via UI/API (logged in): create a microscope → assign it to an experiment → dashboard UMAP dropdown filters points → MCP `list_microscopes` returns it and ACL holds. Confirm the site loads (recreate spheroseg-nginx proxy if a container was recreated — memory `architecture_public_proxy_coupling`).

- [ ] **Step 7: Final commit (if simplifier changed anything)**

```bash
git add -A && git commit -m "Simplify microscopes feature; verified prod"
```

---

## Self-Review

**Spec coverage:**
- Microscope model (shared, objective+magnification) → Task 1 ✓
- Experiment.microscope_id, no denormalization, no cascade endpoint → Task 4 ✓
- Microscopes CRUD router, delete-409 → Task 3 ✓
- UMAP filter (WHERE, no new fit) → Task 5 ✓
- DRY color util → Task 2 ✓
- MCP tools + version bump → Task 6 ✓
- Sidebar tab + i18n → Task 8 ✓
- Management page (proteins template) + i18n → Task 9 ✓
- Experiment form dropdown → Task 10 ✓
- Dashboard UMAP dropdown → Task 11 ✓
- Tests + simplifier + prod rebuild + live verify → Tasks 1-6, 12 ✓

**Type consistency:** `Microscope`/`MicroscopeCreate`/`MicroscopeUpdate` consistent across api.ts (Task 7) and all consumers (Tasks 9-11). `MicroscopeDetailedResponse.from_microscope` used in Task 3 as defined in Task 1. `microscope_id` param consistent across backend (Tasks 4-6) and frontend (Task 7). `getMicroscopes()` returns `Microscope[]` (has `experiment_count`), used by all three frontend consumers.

**Placeholder scan:** No TBD/TODO; all code blocks concrete. Frontend page given in full; experiment-form and UMAP edits given as exact snippets against read line numbers.

**Note on frontend testing:** the codebase has no React component unit harness (Playwright e2e only), so frontend tasks verify via `tsc --noEmit` + build; behavior is confirmed live in Task 12.
