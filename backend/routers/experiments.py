"""Experiment routes."""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, func, distinct, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.user import User
from models.experiment import Experiment
from models.image import Image, MapProtein
from models.microscope import Microscope
from models.ptm import PTM
from models.cell_crop import CellCrop
from schemas.experiment import (
    ExperimentCreate,
    ExperimentUpdate,
    ExperimentResponse,
    ExperimentDetailResponse,
)
from utils.reference_data import get_or_404
from utils.security import get_current_user
from utils.groups import experiment_owner_filter, get_user_group_id

logger = logging.getLogger(__name__)

router = APIRouter()


async def load_experiment_response(
    db: AsyncSession,
    experiment_id: int,
) -> ExperimentResponse:
    """Re-read an experiment after a write and build its response.

    Do NOT replace this with `db.refresh(experiment, attribute_names=[...])`.
    `Experiment.updated_at` carries `onupdate=func.now()`, so an UPDATE leaves the
    attribute expired to be re-read from the server; refreshing only the
    relationships (what this code did until 2026-07-26) left it expired, and
    serialising an expired attribute in async context attempts lazy IO and raises
    `MissingGreenlet`. That made every rename/description edit return 500 in
    production while every unit test passed -- an AsyncMock session has no expiry
    semantics, so only a real database can catch it.
    """
    result = await db.execute(
        select(Experiment)
        .options(
            selectinload(Experiment.map_protein),
            selectinload(Experiment.microscope),
            selectinload(Experiment.ptm),
        )
        .where(Experiment.id == experiment_id)
    )
    return ExperimentResponse.model_validate(result.scalar_one())


async def get_experiment_for_user(
    db: AsyncSession,
    experiment_id: int,
    user_id: int
) -> Experiment:
    """Get experiment and verify ownership or group membership. Raises 404 if not found."""
    group_id = await get_user_group_id(user_id, db)

    result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            experiment_owner_filter(user_id, group_id),
        )
    )
    experiment = result.scalar_one_or_none()
    if not experiment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found"
        )
    return experiment


async def _verify_microscope_exists(microscope_id: int, db: AsyncSession) -> None:
    """Raise 404 if no microscope has this id."""
    await get_or_404(db, Microscope, microscope_id, "Microscope")


async def _verify_ptm_exists(ptm_id: int, db: AsyncSession) -> None:
    """Raise 404 if no PTM has this id."""
    await get_or_404(db, PTM, ptm_id, "PTM")


@router.get("", response_model=List[ExperimentResponse])
async def list_experiments(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's experiments (and group experiments) with image and cell counts."""
    group_id = await get_user_group_id(current_user.id, db)
    access_filter = experiment_owner_filter(current_user.id, group_id)

    # Get experiments with counts using a single query with aggregates
    # Also count images with sum projections (sum_path IS NOT NULL)
    result = await db.execute(
        select(
            Experiment,
            func.count(distinct(Image.id)).label("image_count"),
            func.count(CellCrop.id).label("cell_count"),
            func.count(distinct(Image.id)).filter(Image.sum_path.isnot(None)).label("sum_count"),
            User.name.label("creator_name")
        )
        .options(
            selectinload(Experiment.map_protein),
            selectinload(Experiment.microscope),
            selectinload(Experiment.ptm),
        )
        .outerjoin(Image, Experiment.id == Image.experiment_id)
        .outerjoin(CellCrop, Image.id == CellCrop.image_id)
        .join(User, Experiment.user_id == User.id)
        .where(access_filter)
        .group_by(Experiment.id, User.name)
        .order_by(Experiment.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = result.unique().all()

    response = []
    for exp, image_count, cell_count, sum_count, creator_name in rows:
        exp_response = ExperimentResponse.model_validate(exp)
        exp_response.image_count = image_count or 0
        exp_response.cell_count = cell_count or 0
        exp_response.has_sum_projections = (sum_count or 0) > 0
        exp_response.creator_name = creator_name
        response.append(exp_response)

    return response


@router.post("", response_model=ExperimentResponse, status_code=status.HTTP_201_CREATED)
async def create_experiment(
    data: ExperimentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new experiment. Auto-assigns group_id if user is in a group."""
    # Verify protein exists if provided
    if data.map_protein_id is not None:
        protein_result = await db.execute(
            select(MapProtein).where(MapProtein.id == data.map_protein_id)
        )
        if not protein_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="MAP protein not found"
            )

    if data.microscope_id is not None:
        await _verify_microscope_exists(data.microscope_id, db)

    if data.ptm_id is not None:
        await _verify_ptm_exists(data.ptm_id, db)

    group_id = await get_user_group_id(current_user.id, db)

    experiment = Experiment(
        name=data.name,
        description=data.description,
        user_id=current_user.id,
        group_id=group_id,
        map_protein_id=data.map_protein_id,
        microscope_id=data.microscope_id,
        ptm_id=data.ptm_id,
        fasta_sequence=data.fasta_sequence,
    )
    db.add(experiment)
    await db.commit()

    # Same re-read as the update paths, so all three writes share one response
    # shape and none of them can regress into the expired-attribute trap.
    exp_response = await load_experiment_response(db, experiment.id)
    exp_response.creator_name = current_user.name
    return exp_response


@router.get("/{experiment_id}", response_model=ExperimentDetailResponse)
async def get_experiment(
    experiment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get experiment details with images."""
    group_id = await get_user_group_id(current_user.id, db)

    result = await db.execute(
        select(Experiment)
        .options(
            selectinload(Experiment.images),
            selectinload(Experiment.map_protein),
            selectinload(Experiment.microscope),
            selectinload(Experiment.ptm),
            selectinload(Experiment.user)
        )
        .where(
            Experiment.id == experiment_id,
            experiment_owner_filter(current_user.id, group_id),
        )
    )
    experiment = result.scalar_one_or_none()

    if not experiment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found"
        )

    # Count cells
    cell_result = await db.execute(
        select(func.count(CellCrop.id))
        .join(Image)
        .where(Image.experiment_id == experiment.id)
    )
    cell_count = cell_result.scalar() or 0

    # Check if any images have sum projections
    has_sum = any(img.sum_path for img in experiment.images)

    response = ExperimentDetailResponse.model_validate(experiment)
    response.image_count = len(experiment.images)
    response.cell_count = cell_count
    response.has_sum_projections = has_sum

    return response


@router.patch("/{experiment_id}", response_model=ExperimentResponse)
async def update_experiment(
    experiment_id: int,
    data: ExperimentUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update an experiment (owner only)."""
    experiment = await get_experiment_for_user(db, experiment_id, current_user.id)
    if experiment.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the experiment owner can update it")

    # Update fields
    update_data = data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(experiment, field, value)

    await db.commit()

    return await load_experiment_response(db, experiment_id)


@router.delete("/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_experiment(
    experiment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete an experiment and all its images (owner only)."""
    experiment = await get_experiment_for_user(db, experiment_id, current_user.id)
    if experiment.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the experiment owner can delete it")
    await db.delete(experiment)
    await db.commit()


@router.patch("/{experiment_id}/microscope", response_model=ExperimentResponse)
async def update_experiment_microscope(
    experiment_id: int,
    microscope_id: Optional[int] = Query(
        default=None, description="Microscope ID to assign; omit to clear the assignment"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Assign the acquisition microscope for an experiment (owner OR group member).

    Deliberately wider than `PATCH /{experiment_id}`, which stays owner-only: the
    microscope is shared acquisition metadata (`microscopes` has no `user_id`),
    and the lab needs to backfill it across everyone's experiments to make the
    UMAP microscope filter useful. Scoping this to one nullable FK is why it is a
    separate endpoint -- widening the generic PATCH would also hand the group
    everyone's name and description.
    """
    experiment = await get_experiment_for_user(db, experiment_id, current_user.id)

    if microscope_id is not None:
        await _verify_microscope_exists(microscope_id, db)

    experiment.microscope_id = microscope_id
    await db.commit()

    logger.info(
        f"User {current_user.id} set microscope for experiment {experiment_id} "
        f"to {microscope_id}"
    )

    return await load_experiment_response(db, experiment_id)


@router.patch("/{experiment_id}/ptm", response_model=ExperimentResponse)
async def update_experiment_ptm(
    experiment_id: int,
    ptm_id: Optional[int] = Query(
        default=None, description="PTM ID to assign; omit to clear the assignment"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Assign the microtubule post-translational modification (owner OR group member).

    Group-writable for the same reason as the microscope endpoint: the PTM is
    shared sample-preparation metadata (`ptms` has no `user_id`), and the lab has
    to backfill it across everyone's experiments before the dashboard PTM filter
    covers anything -- every experiment starts unassigned. Keeping it a separate
    endpoint from the owner-only `PATCH /{experiment_id}` is deliberate: one
    field must not have two paths with two different ACLs.
    """
    experiment = await get_experiment_for_user(db, experiment_id, current_user.id)

    if ptm_id is not None:
        await _verify_ptm_exists(ptm_id, db)

    experiment.ptm_id = ptm_id
    await db.commit()

    logger.info(
        f"User {current_user.id} set PTM for experiment {experiment_id} to {ptm_id}"
    )

    return await load_experiment_response(db, experiment_id)


@router.patch("/{experiment_id}/protein")
async def update_experiment_protein(
    experiment_id: int,
    map_protein_id: Optional[int] = Query(default=None, description="MAP protein ID to assign"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Assign the MAP protein to an experiment (owner OR group member).

    The fourth deliberate group-writable exception, after the microscope and the
    PTM, and for the same reason: `map_proteins` has no `user_id`, and in
    production 40 of 46 experiments belong to the annotator, so an owner-only
    assignment would leave the label unmaintainable by the people who curate it.
    Theo uploads the batch, Michal corrects it -- whoever can SEE an experiment
    may say which protein it carries.

    ⚠️ Weightier than the other three: this label is what the discriminant
    projection is fitted on and what every plot colours by, so a wrong edit
    propagates into the science rather than into a facet. It also cascades to
    every image and cell crop below. That is a reason to log it, not a reason to
    lock the annotator out of their own data.

    ⚠️ Separate endpoint from the owner-only `PATCH /{experiment_id}` on purpose:
    one field must not have two paths with two different ACLs, or the narrower
    one gets reached by accident. `ExperimentUpdate` forbids extras, so an old
    client sending `map_protein_id` there gets 422 rather than a silent no-op.
    """
    experiment = await get_experiment_for_user(db, experiment_id, current_user.id)
    # Verify protein exists if provided
    protein = None
    if map_protein_id is not None:
        protein_result = await db.execute(
            select(MapProtein).where(MapProtein.id == map_protein_id)
        )
        protein = protein_result.scalar_one_or_none()
        if not protein:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="MAP protein not found"
            )

    # Update experiment, all images, and all cell crops in a single transaction
    try:
        # Update experiment
        experiment.map_protein_id = map_protein_id

        # Get all image IDs for this experiment
        image_ids_result = await db.execute(
            select(Image.id).where(Image.experiment_id == experiment_id)
        )
        image_ids = [row[0] for row in image_ids_result.all()]

        if image_ids:
            # Update all images
            await db.execute(
                update(Image)
                .where(Image.experiment_id == experiment_id)
                .values(map_protein_id=map_protein_id)
            )

            # Update all cell crops from these images
            await db.execute(
                update(CellCrop)
                .where(CellCrop.image_id.in_(image_ids))
                .values(map_protein_id=map_protein_id)
            )

        await db.commit()

        logger.info(
            f"Updated protein for experiment {experiment_id} to {map_protein_id}, "
            f"cascaded to {len(image_ids)} images"
        )

    except Exception as e:
        await db.rollback()
        logger.exception(f"Failed to update protein for experiment {experiment_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update protein assignment. Please try again."
        )

    return {
        "id": experiment.id,
        "map_protein_id": experiment.map_protein_id,
        "map_protein_name": protein.name if protein else None,
        "map_protein_color": protein.color if protein else None,
        "images_updated": len(image_ids) if image_ids else 0,
    }
