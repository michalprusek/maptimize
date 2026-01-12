"""Ranking routes - TrueSkill-based pairwise comparison."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, func, and_, distinct, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from openskill.models import PlackettLuce

from database import get_db
from config import get_settings
from models.user import User
from models.cell_crop import CellCrop
from models.image import Image
from models.experiment import Experiment
from models.ranking import UserRating, Comparison, RankingSource
from schemas.ranking import (
    PairResponse,
    ComparisonCreate,
    ComparisonResponse,
    RankingResponse,
    RankingItem,
    ProgressResponse,
    CellCropForRanking,
    ImportSourceResponse,
    ImportSourcesRequest,
    ImportResult,
)
from utils.security import get_current_user

router = APIRouter()
settings = get_settings()
model = PlackettLuce()


async def get_or_create_rating(
    db: AsyncSession,
    user_id: int,
    cell_crop_id: int
) -> UserRating:
    """Get existing rating or create new one with initial values."""
    result = await db.execute(
        select(UserRating).where(
            UserRating.user_id == user_id,
            UserRating.cell_crop_id == cell_crop_id
        )
    )
    rating = result.scalar_one_or_none()

    if not rating:
        rating = UserRating(
            user_id=user_id,
            cell_crop_id=cell_crop_id,
            mu=settings.initial_mu,
            sigma=settings.initial_sigma,
        )
        db.add(rating)
        await db.flush()

    return rating


def update_ratings(
    winner_mu: float,
    winner_sigma: float,
    loser_mu: float,
    loser_sigma: float
) -> tuple:
    """Update ratings using TrueSkill algorithm."""
    winner = model.rating(mu=winner_mu, sigma=winner_sigma)
    loser = model.rating(mu=loser_mu, sigma=loser_sigma)

    [[new_winner], [new_loser]] = model.rate([[winner], [loser]])

    return (
        (new_winner.mu, new_winner.sigma),
        (new_loser.mu, new_loser.sigma)
    )


@router.get("/pair", response_model=PairResponse)
async def get_next_pair(
    experiment_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get next pair of cells for comparison using active learning.

    If experiment_id is provided, filter to that experiment only.
    Otherwise, use experiments from user's ranking sources (import-sources).
    """
    # Determine which experiments to include
    if experiment_id:
        # Explicit experiment filter
        included_exp_ids = [experiment_id]
    else:
        # Use ranking sources
        sources_result = await db.execute(
            select(RankingSource.experiment_id)
            .where(
                RankingSource.user_id == current_user.id,
                RankingSource.included == True
            )
        )
        included_exp_ids = list(sources_result.scalars().all())

        if not included_exp_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No experiments selected for ranking. Use POST /api/ranking/import-sources to add experiments."
            )

    # Get all available cell crops from included experiments
    query = (
        select(CellCrop)
        .join(Image)
        .options(selectinload(CellCrop.image).selectinload(Image.map_protein))
        .where(
            CellCrop.excluded == False,
            Image.experiment_id.in_(included_exp_ids)
        )
    )

    result = await db.execute(query)
    crops = result.scalars().all()

    if len(crops) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Not enough cells for comparison (need at least 2)"
        )

    # Get user's comparison count
    count_result = await db.execute(
        select(func.count(Comparison.id))
        .where(
            Comparison.user_id == current_user.id,
            Comparison.undone == False
        )
    )
    total_comparisons = count_result.scalar() or 0

    # Get recent comparisons to avoid repetition
    recent_result = await db.execute(
        select(Comparison)
        .where(
            Comparison.user_id == current_user.id,
            Comparison.undone == False
        )
        .order_by(Comparison.timestamp.desc())
        .limit(50)
    )
    recent = recent_result.scalars().all()
    recent_pairs = {(c.crop_a_id, c.crop_b_id) for c in recent}
    recent_pairs.update({(c.crop_b_id, c.crop_a_id) for c in recent})

    # Get or create ratings for all crops
    ratings = {}
    for crop in crops:
        rating = await get_or_create_rating(db, current_user.id, crop.id)
        ratings[crop.id] = rating

    # Select pair using adaptive sampling utility
    from utils.pair_selection import select_pair
    crop_a, crop_b = select_pair(
        items=crops,
        ratings=ratings,
        total_comparisons=total_comparisons,
        recent_pairs=recent_pairs,
        randomize_order=True
    )

    await db.commit()

    return PairResponse(
        crop_a=CellCropForRanking(
            id=crop_a.id,
            image_id=crop_a.image_id,
            mip_url=f"/api/crops/{crop_a.id}/image",
            map_protein_name=crop_a.image.map_protein.name if crop_a.image.map_protein else None,
            bundleness_score=crop_a.bundleness_score,
        ),
        crop_b=CellCropForRanking(
            id=crop_b.id,
            image_id=crop_b.image_id,
            mip_url=f"/api/crops/{crop_b.id}/image",
            map_protein_name=crop_b.image.map_protein.name if crop_b.image.map_protein else None,
            bundleness_score=crop_b.bundleness_score,
        ),
        comparison_number=total_comparisons + 1,
        total_comparisons=total_comparisons,
    )


@router.post("/compare", response_model=ComparisonResponse)
async def submit_comparison(
    data: ComparisonCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Submit a comparison result."""
    # Validate winner is one of the crops
    if data.winner_id not in [data.crop_a_id, data.crop_b_id]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Winner must be one of the compared crops"
        )

    # Get ratings
    winner_id = data.winner_id
    loser_id = data.crop_a_id if data.winner_id == data.crop_b_id else data.crop_b_id

    winner_rating = await get_or_create_rating(db, current_user.id, winner_id)
    loser_rating = await get_or_create_rating(db, current_user.id, loser_id)

    # Update ratings using TrueSkill
    (new_winner_mu, new_winner_sigma), (new_loser_mu, new_loser_sigma) = update_ratings(
        winner_rating.mu, winner_rating.sigma,
        loser_rating.mu, loser_rating.sigma
    )

    winner_rating.mu = new_winner_mu
    winner_rating.sigma = new_winner_sigma
    winner_rating.comparison_count += 1

    loser_rating.mu = new_loser_mu
    loser_rating.sigma = new_loser_sigma
    loser_rating.comparison_count += 1

    # Create comparison record
    comparison = Comparison(
        user_id=current_user.id,
        crop_a_id=data.crop_a_id,
        crop_b_id=data.crop_b_id,
        winner_id=data.winner_id,
        response_time_ms=data.response_time_ms,
    )
    db.add(comparison)
    await db.commit()
    await db.refresh(comparison)

    return ComparisonResponse.model_validate(comparison)


@router.post("/undo", response_model=ComparisonResponse)
async def undo_last_comparison(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Undo the last comparison."""
    result = await db.execute(
        select(Comparison)
        .where(
            Comparison.user_id == current_user.id,
            Comparison.undone == False
        )
        .order_by(Comparison.timestamp.desc())
        .limit(1)
    )
    comparison = result.scalar_one_or_none()

    if not comparison:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No comparison to undo"
        )

    # Mark as undone
    comparison.undone = True

    # Revert ratings (simplified - in production would need to recalculate from history)
    # For now, just reduce comparison count
    for crop_id in [comparison.crop_a_id, comparison.crop_b_id]:
        result = await db.execute(
            select(UserRating).where(
                UserRating.user_id == current_user.id,
                UserRating.cell_crop_id == crop_id
            )
        )
        rating = result.scalar_one_or_none()
        if rating and rating.comparison_count > 0:
            rating.comparison_count -= 1

    await db.commit()

    return ComparisonResponse.model_validate(comparison)


@router.get("/leaderboard", response_model=RankingResponse)
async def get_leaderboard(
    experiment_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get ranking leaderboard."""
    query = (
        select(UserRating)
        .join(CellCrop)
        .join(Image)
        .options(
            selectinload(UserRating.cell_crop)
            .selectinload(CellCrop.image)
            .selectinload(Image.map_protein)
        )
        .where(UserRating.user_id == current_user.id)
    )

    if experiment_id:
        query = query.where(Image.experiment_id == experiment_id)

    # Get total count
    count_query = (
        select(func.count(UserRating.id))
        .join(CellCrop)
        .join(Image)
        .where(UserRating.user_id == current_user.id)
    )
    if experiment_id:
        count_query = count_query.where(Image.experiment_id == experiment_id)

    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Get paginated results ordered by ordinal score
    result = await db.execute(
        query
        .order_by((UserRating.mu - 3 * UserRating.sigma).desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    ratings = result.scalars().all()

    items = []
    for i, rating in enumerate(ratings):
        crop = rating.cell_crop
        items.append(RankingItem(
            rank=(page - 1) * per_page + i + 1,
            cell_crop_id=crop.id,
            image_id=crop.image_id,
            mip_url=f"/api/crops/{crop.id}/image",
            map_protein_name=crop.image.map_protein.name if crop.image.map_protein else None,
            mu=rating.mu,
            sigma=rating.sigma,
            ordinal_score=rating.ordinal_score,
            comparison_count=rating.comparison_count,
            bundleness_score=crop.bundleness_score,
        ))

    return RankingResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/progress", response_model=ProgressResponse)
async def get_progress(
    experiment_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get ranking progress and convergence info."""
    # Count comparisons
    count_query = (
        select(func.count(Comparison.id))
        .where(
            Comparison.user_id == current_user.id,
            Comparison.undone == False
        )
    )
    count_result = await db.execute(count_query)
    total_comparisons = count_result.scalar() or 0

    # Get average sigma
    sigma_query = (
        select(func.avg(UserRating.sigma))
        .join(CellCrop)
        .join(Image)
        .where(UserRating.user_id == current_user.id)
    )
    if experiment_id:
        sigma_query = sigma_query.where(Image.experiment_id == experiment_id)

    sigma_result = await db.execute(sigma_query)
    avg_sigma = sigma_result.scalar() or settings.initial_sigma

    # Calculate convergence (0-100%)
    max_sigma = settings.initial_sigma
    target_sigma = settings.target_sigma

    if avg_sigma <= target_sigma:
        convergence = 100.0
    else:
        convergence = max(0, min(100, (max_sigma - avg_sigma) / (max_sigma - target_sigma) * 100))

    # Estimate remaining comparisons
    if avg_sigma <= target_sigma:
        estimated_remaining = 0
    else:
        # Rough estimate based on typical convergence rate
        remaining_ratio = (avg_sigma - target_sigma) / (max_sigma - target_sigma)
        estimated_remaining = int(remaining_ratio * 200)  # Assume ~200 comparisons for full convergence

    # Determine phase
    phase = "exploration" if total_comparisons < settings.exploration_pairs else "exploitation"

    return ProgressResponse(
        total_comparisons=total_comparisons,
        convergence_percent=round(convergence, 1),
        estimated_remaining=estimated_remaining,
        average_sigma=round(avg_sigma, 3),
        target_sigma=settings.target_sigma,
        phase=phase,
    )


# Import source endpoints

@router.get("/import-sources", response_model=List[ImportSourceResponse])
async def list_import_sources(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List all experiments available for ranking import.

    Returns experiments owned by the user with image and crop counts,
    and whether they're already included in ranking sources.
    """
    # Get all user's experiments with image and crop counts
    experiments_query = (
        select(
            Experiment.id,
            Experiment.name,
            func.count(distinct(Image.id)).label("image_count"),
            func.count(CellCrop.id).label("crop_count")
        )
        .outerjoin(Image, Experiment.id == Image.experiment_id)
        .outerjoin(CellCrop, Image.id == CellCrop.image_id)
        .where(Experiment.user_id == current_user.id)
        .group_by(Experiment.id, Experiment.name)
        .order_by(Experiment.name)
    )
    result = await db.execute(experiments_query)
    experiments = result.all()

    # Get user's current ranking sources
    sources_result = await db.execute(
        select(RankingSource.experiment_id)
        .where(
            RankingSource.user_id == current_user.id,
            RankingSource.included == True
        )
    )
    included_ids = set(sources_result.scalars().all())

    return [
        ImportSourceResponse(
            experiment_id=exp.id,
            experiment_name=exp.name,
            image_count=exp.image_count or 0,
            crop_count=exp.crop_count or 0,
            included=exp.id in included_ids
        )
        for exp in experiments
    ]


@router.post("/import-sources", response_model=ImportResult)
async def add_import_sources(
    data: ImportSourcesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Add experiments as ranking sources.

    Images/crops from these experiments will be available for ranking comparisons.
    """
    if not data.experiment_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No experiment IDs provided"
        )

    # Verify all experiments belong to user
    result = await db.execute(
        select(Experiment.id)
        .where(
            Experiment.id.in_(data.experiment_ids),
            Experiment.user_id == current_user.id
        )
    )
    valid_ids = set(result.scalars().all())

    invalid_ids = set(data.experiment_ids) - valid_ids
    if invalid_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Experiments not found or not owned: {invalid_ids}"
        )

    # Get existing sources
    existing_result = await db.execute(
        select(RankingSource.experiment_id)
        .where(
            RankingSource.user_id == current_user.id,
            RankingSource.experiment_id.in_(data.experiment_ids)
        )
    )
    existing_ids = set(existing_result.scalars().all())

    # Add new sources
    added_count = 0
    for exp_id in data.experiment_ids:
        if exp_id in existing_ids:
            # Update existing to included=True
            result = await db.execute(
                select(RankingSource)
                .where(
                    RankingSource.user_id == current_user.id,
                    RankingSource.experiment_id == exp_id
                )
            )
            source = result.scalar_one_or_none()
            if source and not source.included:
                source.included = True
                added_count += 1
        else:
            # Create new source
            source = RankingSource(
                user_id=current_user.id,
                experiment_id=exp_id,
                included=True
            )
            db.add(source)
            added_count += 1

    await db.commit()

    # Get total images and crops from added experiments
    stats_result = await db.execute(
        select(
            func.count(distinct(Image.id)).label("image_count"),
            func.count(CellCrop.id).label("crop_count")
        )
        .select_from(Image)
        .outerjoin(CellCrop, Image.id == CellCrop.image_id)
        .where(Image.experiment_id.in_(data.experiment_ids))
    )
    stats = stats_result.one()

    return ImportResult(
        added_experiments=added_count,
        total_images=stats.image_count or 0,
        total_crops=stats.crop_count or 0
    )


@router.delete("/import-sources/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_import_source(
    experiment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove an experiment from ranking sources."""
    result = await db.execute(
        select(RankingSource)
        .where(
            RankingSource.user_id == current_user.id,
            RankingSource.experiment_id == experiment_id
        )
    )
    source = result.scalar_one_or_none()

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ranking source not found"
        )

    # Mark as not included (soft delete to preserve history)
    source.included = False
    await db.commit()
