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
from models.cell_crop import CellCrop
from schemas.experiment import (
    ExperimentCreate,
    ExperimentUpdate,
    ExperimentResponse,
    ExperimentDetailResponse,
)
from utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_experiment_for_user(
    db: AsyncSession,
    experiment_id: int,
    user_id: int
) -> Experiment:
    """Get experiment and verify ownership. Raises 404 if not found."""
    result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.user_id == user_id
        )
    )
    experiment = result.scalar_one_or_none()
    if not experiment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found"
        )
    return experiment


@router.get("", response_model=List[ExperimentResponse])
async def list_experiments(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's experiments with image and cell counts in a single query."""
    # Get experiments with counts using a single query with aggregates
    # Also count images with sum projections (sum_path IS NOT NULL)
    result = await db.execute(
        select(
            Experiment,
            func.count(distinct(Image.id)).label("image_count"),
            func.count(CellCrop.id).label("cell_count"),
            func.count(distinct(Image.id)).filter(Image.sum_path.isnot(None)).label("sum_count")
        )
        .options(selectinload(Experiment.map_protein))
        .outerjoin(Image, Experiment.id == Image.experiment_id)
        .outerjoin(CellCrop, Image.id == CellCrop.image_id)
        .where(Experiment.user_id == current_user.id)
        .group_by(Experiment.id)
        .order_by(Experiment.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = result.unique().all()

    response = []
    for exp, image_count, cell_count, sum_count in rows:
        exp_response = ExperimentResponse.model_validate(exp)
        exp_response.image_count = image_count or 0
        exp_response.cell_count = cell_count or 0
        exp_response.has_sum_projections = (sum_count or 0) > 0
        response.append(exp_response)

    return response


@router.post("", response_model=ExperimentResponse, status_code=status.HTTP_201_CREATED)
async def create_experiment(
    data: ExperimentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new experiment."""
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

    experiment = Experiment(
        name=data.name,
        description=data.description,
        user_id=current_user.id,
        map_protein_id=data.map_protein_id,
        fasta_sequence=data.fasta_sequence,
    )
    db.add(experiment)
    await db.commit()
    await db.refresh(experiment, attribute_names=["map_protein"])

    return ExperimentResponse.model_validate(experiment)


@router.get("/{experiment_id}", response_model=ExperimentDetailResponse)
async def get_experiment(
    experiment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get experiment details with images."""
    result = await db.execute(
        select(Experiment)
        .options(
            selectinload(Experiment.images),
            selectinload(Experiment.map_protein)
        )
        .where(
            Experiment.id == experiment_id,
            Experiment.user_id == current_user.id
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
    """Update an experiment."""
    experiment = await get_experiment_for_user(db, experiment_id, current_user.id)

    # Update fields
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(experiment, field, value)

    await db.commit()
    await db.refresh(experiment)

    return ExperimentResponse.model_validate(experiment)


@router.delete("/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_experiment(
    experiment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete an experiment and all its images."""
    experiment = await get_experiment_for_user(db, experiment_id, current_user.id)
    await db.delete(experiment)
    await db.commit()


@router.patch("/{experiment_id}/protein")
async def update_experiment_protein(
    experiment_id: int,
    map_protein_id: Optional[int] = Query(default=None, description="MAP protein ID to assign"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update the MAP protein assignment for an experiment.

    This cascades the protein assignment to all images and cell crops in the experiment.
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
