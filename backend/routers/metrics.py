"""Metric routes - User-defined metrics for pairwise ranking."""
import os
import random
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, func, distinct, or_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from openskill.models import PlackettLuce

from database import get_db
from config import get_settings
from models.user import User
from models.experiment import Experiment
from models.image import Image
from models.cell_crop import CellCrop
from models.metric import Metric, MetricImage, MetricRating, MetricComparison
from schemas.metric import (
    MetricCreate,
    MetricUpdate,
    MetricResponse,
    MetricListResponse,
    MetricImageResponse,
    MetricImageForRanking,
    ImportCropsRequest,
    ImportCropsResponse,
    MetricPairResponse,
    MetricComparisonCreate,
    MetricComparisonResponse,
    MetricRankingItem,
    MetricRankingResponse,
    MetricProgressResponse,
    ExperimentForImport,
)
from utils.security import get_current_user

router = APIRouter()
settings = get_settings()
model = PlackettLuce()


# Helper functions

async def get_metric_for_user(
    db: AsyncSession,
    metric_id: int,
    user_id: int
) -> Metric:
    """Get metric and verify ownership."""
    result = await db.execute(
        select(Metric)
        .options(selectinload(Metric.images))
        .where(Metric.id == metric_id, Metric.user_id == user_id)
    )
    metric = result.scalar_one_or_none()
    if not metric:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric not found"
        )
    return metric


async def get_or_create_metric_rating(
    db: AsyncSession,
    metric_id: int,
    metric_image_id: int
) -> MetricRating:
    """Get existing rating or create new one."""
    result = await db.execute(
        select(MetricRating).where(
            MetricRating.metric_id == metric_id,
            MetricRating.metric_image_id == metric_image_id
        )
    )
    rating = result.scalar_one_or_none()

    if not rating:
        rating = MetricRating(
            metric_id=metric_id,
            metric_image_id=metric_image_id,
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
    """Update ratings using Plackett-Luce model (via openskill)."""
    winner = model.rating(mu=winner_mu, sigma=winner_sigma)
    loser = model.rating(mu=loser_mu, sigma=loser_sigma)

    [[new_winner], [new_loser]] = model.rate([[winner], [loser]])

    return (
        (new_winner.mu, new_winner.sigma),
        (new_loser.mu, new_loser.sigma)
    )


# CRUD Endpoints

@router.get("", response_model=MetricListResponse)
async def list_metrics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all metrics for current user."""
    # Get metrics with counts
    result = await db.execute(
        select(Metric)
        .where(Metric.user_id == current_user.id)
        .order_by(Metric.created_at.desc())
    )
    metrics = result.scalars().all()

    items = []
    for metric in metrics:
        # Count images
        img_result = await db.execute(
            select(func.count(MetricImage.id))
            .where(MetricImage.metric_id == metric.id)
        )
        image_count = img_result.scalar() or 0

        # Count comparisons
        comp_result = await db.execute(
            select(func.count(MetricComparison.id))
            .where(
                MetricComparison.metric_id == metric.id,
                MetricComparison.undone == False
            )
        )
        comparison_count = comp_result.scalar() or 0

        items.append(MetricResponse(
            id=metric.id,
            name=metric.name,
            description=metric.description,
            image_count=image_count,
            comparison_count=comparison_count,
            created_at=metric.created_at,
            updated_at=metric.updated_at,
        ))

    return MetricListResponse(items=items, total=len(items))


@router.post("", response_model=MetricResponse, status_code=status.HTTP_201_CREATED)
async def create_metric(
    data: MetricCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new metric."""
    metric = Metric(
        user_id=current_user.id,
        name=data.name,
        description=data.description,
    )
    db.add(metric)
    await db.commit()
    await db.refresh(metric)

    return MetricResponse(
        id=metric.id,
        name=metric.name,
        description=metric.description,
        image_count=0,
        comparison_count=0,
        created_at=metric.created_at,
        updated_at=metric.updated_at,
    )


@router.get("/{metric_id}", response_model=MetricResponse)
async def get_metric(
    metric_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get metric details."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    # Count images
    img_result = await db.execute(
        select(func.count(MetricImage.id))
        .where(MetricImage.metric_id == metric.id)
    )
    image_count = img_result.scalar() or 0

    # Count comparisons
    comp_result = await db.execute(
        select(func.count(MetricComparison.id))
        .where(
            MetricComparison.metric_id == metric.id,
            MetricComparison.undone == False
        )
    )
    comparison_count = comp_result.scalar() or 0

    return MetricResponse(
        id=metric.id,
        name=metric.name,
        description=metric.description,
        image_count=image_count,
        comparison_count=comparison_count,
        created_at=metric.created_at,
        updated_at=metric.updated_at,
    )


@router.patch("/{metric_id}", response_model=MetricResponse)
async def update_metric(
    metric_id: int,
    data: MetricUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update metric."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    if data.name is not None:
        metric.name = data.name
    if data.description is not None:
        metric.description = data.description

    await db.commit()
    await db.refresh(metric)

    return await get_metric(metric_id, current_user, db)


@router.delete("/{metric_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_metric(
    metric_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete metric and all associated data."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    # Delete uploaded files
    for img in metric.images:
        if img.file_path and os.path.exists(img.file_path):
            os.remove(img.file_path)

    await db.delete(metric)
    await db.commit()


# Image Endpoints

@router.get("/{metric_id}/images", response_model=List[MetricImageResponse])
async def list_metric_images(
    metric_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all images in a metric."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    result = await db.execute(
        select(MetricImage)
        .options(
            selectinload(MetricImage.cell_crop),
            selectinload(MetricImage.rating)
        )
        .where(MetricImage.metric_id == metric_id)
        .order_by(MetricImage.created_at.desc())
    )
    images = result.scalars().all()

    response = []
    for img in images:
        rating = img.rating
        response.append(MetricImageResponse(
            id=img.id,
            metric_id=img.metric_id,
            cell_crop_id=img.cell_crop_id,
            file_path=img.file_path,
            original_filename=img.original_filename,
            image_url=img.image_url,
            created_at=img.created_at,
            mu=rating.mu if rating else None,
            sigma=rating.sigma if rating else None,
            ordinal_score=rating.ordinal_score if rating else None,
            comparison_count=rating.comparison_count if rating else 0,
        ))

    return response


@router.post("/{metric_id}/images/import", response_model=ImportCropsResponse)
async def import_crops_to_metric(
    metric_id: int,
    data: ImportCropsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Import cell crops from experiments into metric."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    if not data.experiment_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No experiment IDs provided"
        )

    # Verify experiments belong to user
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

    # Get existing cell_crop_ids in metric
    existing_result = await db.execute(
        select(MetricImage.cell_crop_id)
        .where(
            MetricImage.metric_id == metric_id,
            MetricImage.cell_crop_id.isnot(None)
        )
    )
    existing_crop_ids = set(existing_result.scalars().all())

    # Get crops from experiments
    crops_result = await db.execute(
        select(CellCrop)
        .join(Image)
        .where(
            Image.experiment_id.in_(data.experiment_ids),
            CellCrop.excluded == False
        )
    )
    crops = crops_result.scalars().all()

    imported_count = 0
    skipped_count = 0

    for crop in crops:
        if crop.id in existing_crop_ids:
            skipped_count += 1
            continue

        metric_image = MetricImage(
            metric_id=metric_id,
            cell_crop_id=crop.id,
        )
        db.add(metric_image)
        imported_count += 1

    await db.commit()

    return ImportCropsResponse(
        imported_count=imported_count,
        skipped_count=skipped_count,
    )


@router.get("/{metric_id}/experiments", response_model=List[ExperimentForImport])
async def list_experiments_for_import(
    metric_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List experiments available for importing crops into this metric."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

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

    # Count already imported crops per experiment
    imported_query = (
        select(
            Image.experiment_id,
            func.count(MetricImage.id).label("imported_count")
        )
        .select_from(MetricImage)
        .join(CellCrop, MetricImage.cell_crop_id == CellCrop.id)
        .join(Image, CellCrop.image_id == Image.id)
        .where(MetricImage.metric_id == metric_id)
        .group_by(Image.experiment_id)
    )
    imported_result = await db.execute(imported_query)
    imported_counts = {row.experiment_id: row.imported_count for row in imported_result.all()}

    return [
        ExperimentForImport(
            id=exp.id,
            name=exp.name,
            image_count=exp.image_count or 0,
            crop_count=exp.crop_count or 0,
            already_imported=imported_counts.get(exp.id, 0)
        )
        for exp in experiments
    ]


@router.delete("/{metric_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_metric_image(
    metric_id: int,
    image_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove an image from a metric."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    result = await db.execute(
        select(MetricImage).where(
            MetricImage.id == image_id,
            MetricImage.metric_id == metric_id
        )
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found in metric"
        )

    # Delete related comparisons first (where this image is involved)
    await db.execute(
        delete(MetricComparison).where(
            or_(
                MetricComparison.image_a_id == image_id,
                MetricComparison.image_b_id == image_id,
                MetricComparison.winner_id == image_id
            )
        )
    )

    # Delete related rating
    await db.execute(
        delete(MetricRating).where(MetricRating.metric_image_id == image_id)
    )

    # Delete file if it's a direct upload
    if image.file_path and os.path.exists(image.file_path):
        os.remove(image.file_path)

    await db.delete(image)
    await db.commit()


# Ranking Endpoints

@router.get("/{metric_id}/pair", response_model=MetricPairResponse)
async def get_metric_pair(
    metric_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get next pair of images for comparison in this metric."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    # Get all images in metric
    result = await db.execute(
        select(MetricImage)
        .options(selectinload(MetricImage.cell_crop))
        .where(MetricImage.metric_id == metric_id)
    )
    images = result.scalars().all()

    if len(images) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Not enough images for comparison (need at least 2)"
        )

    # Get comparison count
    count_result = await db.execute(
        select(func.count(MetricComparison.id))
        .where(
            MetricComparison.metric_id == metric_id,
            MetricComparison.undone == False
        )
    )
    total_comparisons = count_result.scalar() or 0

    # Get recent comparisons to avoid repetition
    recent_result = await db.execute(
        select(MetricComparison)
        .where(
            MetricComparison.metric_id == metric_id,
            MetricComparison.undone == False
        )
        .order_by(MetricComparison.created_at.desc())
        .limit(50)
    )
    recent = recent_result.scalars().all()
    recent_pairs = {(c.image_a_id, c.image_b_id) for c in recent}
    recent_pairs.update({(c.image_b_id, c.image_a_id) for c in recent})

    # Get or create ratings for all images
    ratings = {}
    for img in images:
        rating = await get_or_create_metric_rating(db, metric_id, img.id)
        ratings[img.id] = rating

    # Select pair based on phase
    if total_comparisons < settings.exploration_pairs:
        # Exploration phase: random selection
        available_pairs = [
            (images[i], images[j])
            for i in range(len(images))
            for j in range(i + 1, len(images))
            if (images[i].id, images[j].id) not in recent_pairs
        ]

        if not available_pairs:
            available_pairs = [
                (images[i], images[j])
                for i in range(len(images))
                for j in range(i + 1, len(images))
            ]

        img_a, img_b = random.choice(available_pairs)
    else:
        # Exploitation phase: uncertainty sampling
        sorted_by_sigma = sorted(
            images,
            key=lambda i: ratings[i.id].sigma,
            reverse=True
        )

        candidates = sorted_by_sigma[:min(10, len(sorted_by_sigma))]

        best_pair = None
        best_score = float('-inf')

        for i, img_a in enumerate(candidates):
            for img_b in candidates[i + 1:]:
                if (img_a.id, img_b.id) in recent_pairs:
                    continue

                rating_a = ratings[img_a.id]
                rating_b = ratings[img_b.id]

                combined_sigma = rating_a.sigma + rating_b.sigma
                mu_diff = abs(rating_a.mu - rating_b.mu)
                score = combined_sigma - mu_diff

                if score > best_score:
                    best_score = score
                    best_pair = (img_a, img_b)

        if best_pair:
            img_a, img_b = best_pair
        else:
            img_a, img_b = random.sample(images, 2)

    # Randomize order
    if random.random() > 0.5:
        img_a, img_b = img_b, img_a

    await db.commit()

    def get_image_url(img: MetricImage) -> Optional[str]:
        if img.cell_crop:
            return f"/api/images/crops/{img.cell_crop_id}/image"
        return f"/api/metrics/{metric_id}/images/{img.id}/file"

    return MetricPairResponse(
        image_a=MetricImageForRanking(
            id=img_a.id,
            image_url=get_image_url(img_a),
            cell_crop_id=img_a.cell_crop_id,
            original_filename=img_a.original_filename,
        ),
        image_b=MetricImageForRanking(
            id=img_b.id,
            image_url=get_image_url(img_b),
            cell_crop_id=img_b.cell_crop_id,
            original_filename=img_b.original_filename,
        ),
        comparison_number=total_comparisons + 1,
        total_comparisons=total_comparisons,
    )


@router.post("/{metric_id}/compare", response_model=MetricComparisonResponse)
async def submit_metric_comparison(
    metric_id: int,
    data: MetricComparisonCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Submit a comparison result for this metric."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    # Validate winner
    if data.winner_id not in [data.image_a_id, data.image_b_id]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Winner must be one of the compared images"
        )

    # Verify images belong to metric
    for img_id in [data.image_a_id, data.image_b_id]:
        result = await db.execute(
            select(MetricImage).where(
                MetricImage.id == img_id,
                MetricImage.metric_id == metric_id
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Image {img_id} not found in metric"
            )

    # Get ratings
    winner_id = data.winner_id
    loser_id = data.image_a_id if data.winner_id == data.image_b_id else data.image_b_id

    winner_rating = await get_or_create_metric_rating(db, metric_id, winner_id)
    loser_rating = await get_or_create_metric_rating(db, metric_id, loser_id)

    # Update ratings
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
    comparison = MetricComparison(
        metric_id=metric_id,
        image_a_id=data.image_a_id,
        image_b_id=data.image_b_id,
        winner_id=data.winner_id,
        response_time_ms=data.response_time_ms,
    )
    db.add(comparison)
    await db.commit()
    await db.refresh(comparison)

    return MetricComparisonResponse.model_validate(comparison)


@router.post("/{metric_id}/undo", response_model=MetricComparisonResponse)
async def undo_metric_comparison(
    metric_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Undo the last comparison in this metric."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    result = await db.execute(
        select(MetricComparison)
        .where(
            MetricComparison.metric_id == metric_id,
            MetricComparison.undone == False
        )
        .order_by(MetricComparison.created_at.desc())
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

    # Reduce comparison counts
    for img_id in [comparison.image_a_id, comparison.image_b_id]:
        result = await db.execute(
            select(MetricRating).where(
                MetricRating.metric_id == metric_id,
                MetricRating.metric_image_id == img_id
            )
        )
        rating = result.scalar_one_or_none()
        if rating and rating.comparison_count > 0:
            rating.comparison_count -= 1

    await db.commit()

    return MetricComparisonResponse.model_validate(comparison)


@router.get("/{metric_id}/leaderboard", response_model=MetricRankingResponse)
async def get_metric_leaderboard(
    metric_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(500, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get ranking leaderboard for this metric (all images by default)."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    # Get total count
    count_result = await db.execute(
        select(func.count(MetricRating.id))
        .where(MetricRating.metric_id == metric_id)
    )
    total = count_result.scalar() or 0

    # Get paginated ratings
    result = await db.execute(
        select(MetricRating)
        .options(
            selectinload(MetricRating.metric_image)
            .selectinload(MetricImage.cell_crop)
        )
        .where(MetricRating.metric_id == metric_id)
        .order_by((MetricRating.mu - 3 * MetricRating.sigma).desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    ratings = result.scalars().all()

    items = []
    for i, rating in enumerate(ratings):
        img = rating.metric_image

        def get_url() -> Optional[str]:
            if img.cell_crop:
                return f"/images/crops/{img.cell_crop_id}/image"
            return f"/metrics/{metric_id}/images/{img.id}/file"

        items.append(MetricRankingItem(
            rank=(page - 1) * per_page + i + 1,
            metric_image_id=img.id,
            image_url=get_url(),
            cell_crop_id=img.cell_crop_id,
            original_filename=img.original_filename,
            mu=rating.mu,
            sigma=rating.sigma,
            ordinal_score=rating.ordinal_score,
            comparison_count=rating.comparison_count,
        ))

    return MetricRankingResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{metric_id}/progress", response_model=MetricProgressResponse)
async def get_metric_progress(
    metric_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get ranking progress for this metric."""
    metric = await get_metric_for_user(db, metric_id, current_user.id)

    # Count comparisons
    count_result = await db.execute(
        select(func.count(MetricComparison.id))
        .where(
            MetricComparison.metric_id == metric_id,
            MetricComparison.undone == False
        )
    )
    total_comparisons = count_result.scalar() or 0

    # Count images
    img_count_result = await db.execute(
        select(func.count(MetricImage.id))
        .where(MetricImage.metric_id == metric_id)
    )
    image_count = img_count_result.scalar() or 0

    # Get average sigma
    sigma_result = await db.execute(
        select(func.avg(MetricRating.sigma))
        .where(MetricRating.metric_id == metric_id)
    )
    avg_sigma = sigma_result.scalar() or settings.initial_sigma

    # Calculate convergence
    max_sigma = settings.initial_sigma
    target_sigma = settings.target_sigma

    if avg_sigma <= target_sigma:
        convergence = 100.0
    else:
        convergence = max(0, min(100, (max_sigma - avg_sigma) / (max_sigma - target_sigma) * 100))

    # Estimate remaining
    if avg_sigma <= target_sigma:
        estimated_remaining = 0
    else:
        remaining_ratio = (avg_sigma - target_sigma) / (max_sigma - target_sigma)
        estimated_remaining = int(remaining_ratio * 200)

    # Determine phase
    phase = "exploration" if total_comparisons < settings.exploration_pairs else "exploitation"

    return MetricProgressResponse(
        total_comparisons=total_comparisons,
        convergence_percent=round(convergence, 1),
        estimated_remaining=estimated_remaining,
        average_sigma=round(avg_sigma, 3),
        target_sigma=settings.target_sigma,
        phase=phase,
        image_count=image_count,
    )


# Image file endpoint

@router.get("/{metric_id}/images/{image_id}/file")
async def get_metric_image_file(
    metric_id: int,
    image_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get the image file for a directly uploaded metric image."""
    from fastapi.responses import FileResponse

    metric = await get_metric_for_user(db, metric_id, current_user.id)

    result = await db.execute(
        select(MetricImage).where(
            MetricImage.id == image_id,
            MetricImage.metric_id == metric_id
        )
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )

    if not image.file_path or not os.path.exists(image.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image file not found"
        )

    return FileResponse(image.file_path)
