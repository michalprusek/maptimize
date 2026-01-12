"""Experiment routes."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.user import User
from models.experiment import Experiment
from models.image import Image
from models.cell_crop import CellCrop
from schemas.experiment import (
    ExperimentCreate,
    ExperimentUpdate,
    ExperimentResponse,
    ExperimentDetailResponse,
)
from utils.security import get_current_user

router = APIRouter()


@router.get("", response_model=List[ExperimentResponse])
async def list_experiments(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's experiments with image and cell counts in a single query."""
    # Get experiments with counts using a single query with aggregates
    result = await db.execute(
        select(
            Experiment,
            func.count(distinct(Image.id)).label("image_count"),
            func.count(CellCrop.id).label("cell_count")
        )
        .outerjoin(Image, Experiment.id == Image.experiment_id)
        .outerjoin(CellCrop, Image.id == CellCrop.image_id)
        .where(Experiment.user_id == current_user.id)
        .group_by(Experiment.id)
        .order_by(Experiment.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = result.all()

    response = []
    for exp, image_count, cell_count in rows:
        exp_response = ExperimentResponse.model_validate(exp)
        exp_response.image_count = image_count or 0
        exp_response.cell_count = cell_count or 0
        response.append(exp_response)

    return response


@router.post("", response_model=ExperimentResponse, status_code=status.HTTP_201_CREATED)
async def create_experiment(
    data: ExperimentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new experiment."""
    experiment = Experiment(
        name=data.name,
        description=data.description,
        user_id=current_user.id,
    )
    db.add(experiment)
    await db.commit()
    await db.refresh(experiment)

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
        .options(selectinload(Experiment.images))
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

    response = ExperimentDetailResponse.model_validate(experiment)
    response.image_count = len(experiment.images)
    response.cell_count = cell_count

    return response


@router.patch("/{experiment_id}", response_model=ExperimentResponse)
async def update_experiment(
    experiment_id: int,
    data: ExperimentUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update an experiment."""
    result = await db.execute(
        select(Experiment).where(
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
    result = await db.execute(
        select(Experiment).where(
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

    await db.delete(experiment)
    await db.commit()
