"""Data export service for exporting experiment data to CSV/Excel.

This service provides functions to export various data types:
- Experiment metadata
- Cell crop data with measurements
- Ranking comparisons
- Analysis results
"""

import logging
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
from utils.export_helpers import sanitize_filename, export_dataframe

logger = logging.getLogger(__name__)
settings = get_settings()

# Export directory for temporary files
EXPORT_DIR = Path(settings.upload_dir) / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

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
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"experiment_{safe_name}_{timestamp}.{format}"
    file_path = EXPORT_DIR / filename

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
        "download_url": f"/uploads/exports/{filename}",
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
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    exp_suffix = f"_exp{experiment_id}" if experiment_id else ""
    filename = f"cell_crops{exp_suffix}_{timestamp}.{format}"
    file_path = EXPORT_DIR / filename

    # Export using shared helper
    export_dataframe(df, file_path, format)

    return {
        "success": True,
        "filename": filename,
        "file_path": str(file_path),
        "download_url": f"/uploads/exports/{filename}",
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
    # Get comparisons
    result = await db.execute(
        select(
            Comparison.id,
            Comparison.winner_id,
            Comparison.loser_id,
            Comparison.source,
            Comparison.created_at,
        )
        .where(Comparison.user_id == user_id)
        .order_by(Comparison.created_at.desc())
        .limit(MAX_EXPORT_ROWS)
    )
    rows = result.all()

    data = []
    for row in rows:
        data.append({
            "comparison_id": row.id,
            "winner_cell_id": row.winner_id,
            "loser_cell_id": row.loser_id,
            "source": row.source.value if hasattr(row.source, "value") else str(row.source),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    df = pd.DataFrame(data)

    # Generate filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ranking_comparisons_{timestamp}.{format}"
    file_path = EXPORT_DIR / filename

    export_dataframe(df, file_path, format)

    return {
        "success": True,
        "filename": filename,
        "file_path": str(file_path),
        "download_url": f"/uploads/exports/{filename}",
        "row_count": len(data),
    }


async def export_analysis_results(
    data: list[dict],
    name: str,
    format: Literal["csv", "xlsx"] = "csv",
) -> dict:
    """
    Export arbitrary analysis results to file.

    Args:
        data: List of dicts to export
        name: Base name for the file
        format: Export format

    Returns:
        dict with file_path and download_url
    """
    if not data:
        return {"error": "No data to export"}

    df = pd.DataFrame(data)

    # Generate filename
    safe_name = sanitize_filename(name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"analysis_{safe_name}_{timestamp}.{format}"
    file_path = EXPORT_DIR / filename

    export_dataframe(df, file_path, format)

    return {
        "success": True,
        "filename": filename,
        "file_path": str(file_path),
        "download_url": f"/uploads/exports/{filename}",
        "row_count": len(data),
        "columns": list(df.columns),
    }


def cleanup_old_exports(max_age_hours: int = 24) -> int:
    """Remove export files older than max_age_hours."""
    cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
    removed = 0

    for file_path in EXPORT_DIR.glob("*"):
        if file_path.is_file() and file_path.stat().st_mtime < cutoff:
            try:
                file_path.unlink()
                removed += 1
            except Exception as e:
                logger.warning(f"Failed to remove old export {file_path}: {e}")

    return removed
