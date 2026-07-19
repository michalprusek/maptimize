"""Data export service for exporting experiment data to CSV/Excel.

This service provides functions to export various data types:
- Experiment metadata
- Cell crop data with measurements
- Ranking comparisons
- Analysis results
"""

import logging
import secrets
from datetime import datetime
from typing import Optional, Literal
from pathlib import Path

import pandas as pd
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from models.experiment import Experiment
from models.image import Image, MapProtein
from models.cell_crop import CellCrop
from models.ranking import Comparison, UserRating
from utils.export_helpers import sanitize_filename, export_dataframe, cleanup_old_files

logger = logging.getLogger(__name__)
settings = get_settings()

# Export directory for temporary files
EXPORT_DIR = Path(settings.export_dir)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def prepare_export_target(user_id: int, stem: str, fmt: str) -> tuple[Path, str, str]:
    """Allocate a per-user export path and return (path, filename, download_url).

    The random suffix matters twice over: a second-resolution timestamp alone
    made export URLs guessable, and two exports of the same experiment within
    one second would silently overwrite each other.
    """
    user_dir = EXPORT_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{stem}_{timestamp}_{secrets.token_hex(8)}.{fmt}"
    return user_dir / filename, filename, f"/api/exports/{user_id}/{filename}"


# Maximum rows for export (safety limit)
MAX_EXPORT_ROWS = 50_000


async def export_experiment_data(
    experiment_id: int,
    user_id: int,
    db: AsyncSession,
    format: Literal["csv", "xlsx"] = "csv",
) -> dict:
    """
    Export experiment data including images and cell statistics.

    Args:
        experiment_id: Experiment ID to export
        user_id: User ID for access control
        db: Database session
        format: Export format (csv or xlsx)

    Returns:
        dict with file_path, filename, and download_url
    """
    # Verify experiment access
    result = await db.execute(
        select(Experiment)
        .options(selectinload(Experiment.map_protein))
        .where(Experiment.id == experiment_id, Experiment.user_id == user_id)
    )
    experiment = result.scalar_one_or_none()

    if not experiment:
        return {"error": "Experiment not found or access denied"}

    # Get images with cell counts
    img_result = await db.execute(
        select(
            Image.id,
            Image.original_filename,
            Image.width,
            Image.height,
            Image.created_at,
            func.count(CellCrop.id).label("cell_count"),
        )
        .outerjoin(CellCrop, Image.id == CellCrop.image_id)
        .where(Image.experiment_id == experiment_id)
        .group_by(Image.id)
        .limit(MAX_EXPORT_ROWS)
    )
    rows = img_result.all()

    # Build DataFrame
    data = []
    for row in rows:
        data.append({
            "image_id": row.id,
            "filename": row.original_filename,
            "width": row.width,
            "height": row.height,
            "cell_count": row.cell_count,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    df = pd.DataFrame(data)

    # Add experiment metadata
    metadata = {
        "experiment_id": experiment.id,
        "experiment_name": experiment.name,
        "protein": experiment.map_protein.name if experiment.map_protein else None,
        "export_date": datetime.now().isoformat(),
        "total_images": len(data),
        "total_cells": df["cell_count"].sum() if not df.empty else 0,
    }

    # Generate filename
    safe_name = sanitize_filename(experiment.name)
    file_path, filename, download_url = prepare_export_target(
        user_id, f"experiment_{safe_name}", format)

    # Export to file
    if format == "xlsx":
        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            # Metadata sheet
            meta_df = pd.DataFrame([metadata])
            meta_df.to_excel(writer, sheet_name="Metadata", index=False)
            # Data sheet
            df.to_excel(writer, sheet_name="Images", index=False)
    else:
        df.to_csv(file_path, index=False)

    return {
        "success": True,
        "filename": filename,
        "file_path": str(file_path),
        "download_url": download_url,
        "metadata": metadata,
    }


async def export_cell_crops(
    user_id: int,
    db: AsyncSession,
    experiment_id: Optional[int] = None,
    format: Literal["csv", "xlsx"] = "csv",
) -> dict:
    """
    Export cell crop data with measurements.

    Args:
        user_id: User ID for access control
        db: Database session
        experiment_id: Optional filter by experiment
        format: Export format

    Returns:
        dict with file_path and metadata
    """
    # Build query - use correct column names from CellCrop model
    # Note: Model uses bbox_w/bbox_h (short) and detection_confidence
    query = (
        select(
            CellCrop.id,
            CellCrop.bbox_x,
            CellCrop.bbox_y,
            CellCrop.bbox_w,
            CellCrop.bbox_h,
            CellCrop.detection_confidence,
            CellCrop.mean_intensity,
            CellCrop.created_at,
            Image.id.label("image_id"),
            Image.original_filename.label("image_filename"),
            Experiment.id.label("experiment_id"),
            Experiment.name.label("experiment_name"),
            MapProtein.name.label("protein_name"),
        )
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .outerjoin(MapProtein, Experiment.map_protein_id == MapProtein.id)
        .where(Experiment.user_id == user_id)
    )

    if experiment_id:
        query = query.where(Experiment.id == experiment_id)

    query = query.limit(MAX_EXPORT_ROWS)

    result = await db.execute(query)
    rows = result.all()

    # Build DataFrame - use correct column names from query
    data = []
    for row in rows:
        data.append({
            "cell_id": row.id,
            "experiment_id": row.experiment_id,
            "experiment_name": row.experiment_name,
            "protein": row.protein_name,
            "image_id": row.image_id,
            "image_filename": row.image_filename,
            "bbox_x": row.bbox_x,
            "bbox_y": row.bbox_y,
            "bbox_width": row.bbox_w,
            "bbox_height": row.bbox_h,
            "area": row.bbox_w * row.bbox_h if row.bbox_w and row.bbox_h else None,
            "confidence": row.detection_confidence,
            "mean_intensity": row.mean_intensity,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    df = pd.DataFrame(data)

    # Generate filename
    exp_suffix = f"_exp{experiment_id}" if experiment_id else ""
    file_path, filename, download_url = prepare_export_target(
        user_id, f"cell_crops{exp_suffix}", format)

    # Export using shared helper
    export_dataframe(df, file_path, format)

    return {
        "success": True,
        "filename": filename,
        "file_path": str(file_path),
        "download_url": download_url,
        "row_count": len(data),
    }


async def export_ranking_comparisons(
    user_id: int,
    db: AsyncSession,
    format: Literal["csv", "xlsx"] = "csv",
) -> dict:
    """
    Export ranking comparison history.

    Args:
        user_id: User ID for access control
        db: Database session
        format: Export format

    Returns:
        dict with file_path and metadata
    """
    # Get comparisons. The Comparison model stores the two candidates as
    # crop_a_id/crop_b_id plus winner_id and a `timestamp` (there is no
    # loser_id/source/created_at column), so the loser is derived in Python.
    result = await db.execute(
        select(
            Comparison.id,
            Comparison.crop_a_id,
            Comparison.crop_b_id,
            Comparison.winner_id,
            Comparison.undone,
            Comparison.timestamp,
        )
        .where(Comparison.user_id == user_id)
        .order_by(Comparison.timestamp.desc())
        .limit(MAX_EXPORT_ROWS)
    )
    rows = result.all()

    data = []
    for row in rows:
        loser_id = row.crop_b_id if row.winner_id == row.crop_a_id else row.crop_a_id
        data.append({
            "comparison_id": row.id,
            "winner_cell_id": row.winner_id,
            "loser_cell_id": loser_id,
            "undone": bool(row.undone),
            "created_at": row.timestamp.isoformat() if row.timestamp else None,
        })

    df = pd.DataFrame(data)

    # Generate filename
    file_path, filename, download_url = prepare_export_target(
        user_id, "ranking_comparisons", format)

    export_dataframe(df, file_path, format)

    return {
        "success": True,
        "filename": filename,
        "file_path": str(file_path),
        "download_url": download_url,
        "row_count": len(data),
    }


async def export_analysis_results(
    data: list[dict],
    name: str,
    user_id: int,
    format: Literal["csv", "xlsx"] = "csv",
) -> dict:
    """
    Export arbitrary analysis results to file.

    Args:
        data: List of dicts to export
        name: Base name for the file
        user_id: Owner; exports are stored and served per user
        format: Export format

    Returns:
        dict with file_path and download_url
    """
    if not data:
        return {"error": "No data to export"}

    df = pd.DataFrame(data)

    # Generate filename
    safe_name = sanitize_filename(name)
    file_path, filename, download_url = prepare_export_target(
        user_id, f"analysis_{safe_name}", format)

    export_dataframe(df, file_path, format)

    return {
        "success": True,
        "filename": filename,
        "file_path": str(file_path),
        "download_url": download_url,
        "row_count": len(data),
        "columns": list(df.columns),
    }


def cleanup_old_exports(max_age_hours: int = 24) -> int:
    """Remove export files older than max_age_hours."""
    return cleanup_old_files(EXPORT_DIR, max_age_hours, log_prefix="export")
