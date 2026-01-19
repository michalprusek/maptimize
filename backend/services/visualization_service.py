"""Visualization service for generating charts and plots.

This service creates statistical visualizations from experiment data:
- Histograms, bar charts, scatter plots
- Heatmaps and UMAP projections
- Statistical summaries
"""

import logging
from typing import Optional, Literal, Any
from pathlib import Path

import numpy as np
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

# Matplotlib with non-interactive backend
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

from config import get_settings
from models.experiment import Experiment
from models.image import Image
from models.cell_crop import CellCrop
from models.ranking import UserRating
from utils.export_helpers import fig_to_base64, generate_timestamped_filename

logger = logging.getLogger(__name__)
settings = get_settings()

# Set style defaults
sns.set_theme(style="whitegrid")
plt.rcParams["figure.dpi"] = 100
plt.rcParams["figure.figsize"] = (10, 6)

# Chart output directory
CHART_DIR = Path(settings.upload_dir) / "charts"
CHART_DIR.mkdir(parents=True, exist_ok=True)


def _fig_to_file(fig: plt.Figure, name: str) -> str:
    """Save figure to file and return URL."""
    filename = generate_timestamped_filename(name, "png")
    file_path = CHART_DIR / filename

    fig.savefig(file_path, format="png", dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    return f"/api/charts/{filename}"


async def create_cell_count_histogram(
    user_id: int,
    db: AsyncSession,
    experiment_id: Optional[int] = None,
    title: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create histogram of cell counts per image.

    Args:
        user_id: User ID for access control
        db: Database session
        experiment_id: Optional filter by experiment
        title: Optional chart title

    Returns:
        dict with image_base64, image_url, and statistics
    """
    # Query cell counts per image
    query = (
        select(
            Image.id,
            func.count(CellCrop.id).label("cell_count"),
        )
        .join(Experiment, Image.experiment_id == Experiment.id)
        .outerjoin(CellCrop, Image.id == CellCrop.image_id)
        .where(Experiment.user_id == user_id)
        .group_by(Image.id)
    )

    if experiment_id:
        query = query.where(Experiment.id == experiment_id)

    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return {"error": "No data found"}

    cell_counts = [row.cell_count for row in rows]

    # Create figure
    fig, ax = plt.subplots(figsize=(10, 6))

    ax.hist(cell_counts, bins=30, color="steelblue", edgecolor="white", alpha=0.8)
    ax.set_xlabel("Number of Cells per Image", fontsize=12)
    ax.set_ylabel("Frequency", fontsize=12)
    ax.set_title(title or "Distribution of Cell Counts per Image", fontsize=14)

    # Add statistics annotation
    mean_val = np.mean(cell_counts)
    median_val = np.median(cell_counts)
    std_val = np.std(cell_counts)

    stats_text = f"Mean: {mean_val:.1f}\nMedian: {median_val:.1f}\nStd: {std_val:.1f}\nN: {len(cell_counts)}"
    ax.annotate(
        stats_text,
        xy=(0.95, 0.95),
        xycoords="axes fraction",
        ha="right",
        va="top",
        fontsize=10,
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
    )

    plt.tight_layout()

    return {
        "success": True,
        "image_base64": fig_to_base64(fig),
        "image_url": _fig_to_file(plt.gcf(), "cell_count_histogram") if plt.get_fignums() else None,
        "statistics": {
            "mean": float(mean_val),
            "median": float(median_val),
            "std": float(std_val),
            "min": int(min(cell_counts)),
            "max": int(max(cell_counts)),
            "count": len(cell_counts),
        },
    }


async def create_experiment_comparison_bar(
    user_id: int,
    db: AsyncSession,
    experiment_ids: Optional[list[int]] = None,
    metric: Literal["cell_count", "image_count"] = "cell_count",
    title: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create bar chart comparing experiments.

    Args:
        user_id: User ID for access control
        db: Database session
        experiment_ids: Optional list of experiments to compare
        metric: Metric to compare
        title: Optional chart title

    Returns:
        dict with image_base64 and data
    """
    # Query experiment statistics
    query = (
        select(
            Experiment.id,
            Experiment.name,
            func.count(func.distinct(Image.id)).label("image_count"),
            func.count(CellCrop.id).label("cell_count"),
        )
        .outerjoin(Image, Experiment.id == Image.experiment_id)
        .outerjoin(CellCrop, Image.id == CellCrop.image_id)
        .where(Experiment.user_id == user_id)
        .group_by(Experiment.id)
    )

    if experiment_ids:
        query = query.where(Experiment.id.in_(experiment_ids))

    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return {"error": "No experiments found"}

    # Prepare data
    names = [row.name[:20] for row in rows]  # Truncate long names
    values = [getattr(row, metric) for row in rows]

    # Create figure
    fig, ax = plt.subplots(figsize=(max(8, len(names) * 0.8), 6))

    colors = sns.color_palette("husl", len(names))
    bars = ax.bar(names, values, color=colors, edgecolor="white")

    ax.set_xlabel("Experiment", fontsize=12)
    ax.set_ylabel(metric.replace("_", " ").title(), fontsize=12)
    ax.set_title(title or f"Experiment Comparison: {metric.replace('_', ' ').title()}", fontsize=14)

    # Rotate labels if needed
    if len(names) > 5:
        plt.xticks(rotation=45, ha="right")

    # Add value labels on bars
    for bar, value in zip(bars, values):
        ax.annotate(
            f"{value:,}",
            xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
            ha="center",
            va="bottom",
            fontsize=10,
        )

    plt.tight_layout()

    return {
        "success": True,
        "image_base64": fig_to_base64(fig),
        "data": [{"experiment": name, metric: value} for name, value in zip(names, values)],
    }


async def create_cell_area_scatter(
    user_id: int,
    db: AsyncSession,
    experiment_id: Optional[int] = None,
    title: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create scatter plot of cell bounding box areas.

    Args:
        user_id: User ID for access control
        db: Database session
        experiment_id: Optional filter by experiment
        title: Optional chart title

    Returns:
        dict with image_base64 and statistics
    """
    # Query cell dimensions
    query = (
        select(
            CellCrop.bbox_width,
            CellCrop.bbox_height,
            CellCrop.confidence,
            Experiment.name.label("experiment_name"),
        )
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(Experiment.user_id == user_id)
    )

    if experiment_id:
        query = query.where(Experiment.id == experiment_id)

    query = query.limit(5000)  # Limit for performance

    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return {"error": "No cell data found"}

    widths = [row.bbox_width for row in rows if row.bbox_width and row.bbox_height]
    heights = [row.bbox_height for row in rows if row.bbox_width and row.bbox_height]
    areas = [w * h for w, h in zip(widths, heights)]
    confidences = [row.confidence or 0.5 for row in rows if row.bbox_width and row.bbox_height]

    # Create figure
    fig, ax = plt.subplots(figsize=(10, 8))

    scatter = ax.scatter(
        widths,
        heights,
        c=confidences,
        s=30,
        alpha=0.6,
        cmap="viridis",
        edgecolor="white",
        linewidth=0.5,
    )

    ax.set_xlabel("Width (pixels)", fontsize=12)
    ax.set_ylabel("Height (pixels)", fontsize=12)
    ax.set_title(title or "Cell Bounding Box Dimensions", fontsize=14)

    # Add colorbar for confidence
    cbar = plt.colorbar(scatter, ax=ax)
    cbar.set_label("Detection Confidence", fontsize=10)

    # Add statistics
    stats_text = f"N: {len(widths)}\nMean Area: {np.mean(areas):.0f}px²"
    ax.annotate(
        stats_text,
        xy=(0.02, 0.98),
        xycoords="axes fraction",
        ha="left",
        va="top",
        fontsize=10,
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
    )

    plt.tight_layout()

    return {
        "success": True,
        "image_base64": fig_to_base64(fig),
        "statistics": {
            "count": len(widths),
            "mean_width": float(np.mean(widths)),
            "mean_height": float(np.mean(heights)),
            "mean_area": float(np.mean(areas)),
            "std_area": float(np.std(areas)),
        },
    }


async def create_ranking_heatmap(
    user_id: int,
    db: AsyncSession,
    experiment_id: Optional[int] = None,
    title: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create heatmap of cell rankings by experiment.

    Args:
        user_id: User ID for access control
        db: Database session
        experiment_id: Optional filter
        title: Optional chart title

    Returns:
        dict with image_base64
    """
    # Query ratings
    query = (
        select(
            UserRating.mu,
            UserRating.sigma,
            Experiment.name.label("experiment_name"),
        )
        .join(CellCrop, UserRating.cell_crop_id == CellCrop.id)
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(
            Experiment.user_id == user_id,
            UserRating.user_id == user_id,
        )
    )

    if experiment_id:
        query = query.where(Experiment.id == experiment_id)

    query = query.limit(1000)

    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return {"error": "No ranking data found"}

    # Group by experiment
    exp_data = {}
    for row in rows:
        exp_name = row.experiment_name[:15]
        if exp_name not in exp_data:
            exp_data[exp_name] = []
        exp_data[exp_name].append(row.mu)

    if len(exp_data) < 2:
        return {"error": "Need at least 2 experiments for comparison"}

    # Create violin plot instead of heatmap for single dimension
    fig, ax = plt.subplots(figsize=(10, 6))

    data = [exp_data[name] for name in exp_data]
    names = list(exp_data.keys())

    parts = ax.violinplot(data, positions=range(len(names)), showmeans=True, showmedians=True)

    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.set_ylabel("Rating (μ)", fontsize=12)
    ax.set_title(title or "Cell Rating Distribution by Experiment", fontsize=14)

    plt.tight_layout()

    return {
        "success": True,
        "image_base64": fig_to_base64(fig),
        "experiments": list(exp_data.keys()),
        "data_points": {name: len(vals) for name, vals in exp_data.items()},
    }


async def create_visualization(
    chart_type: str,
    user_id: int,
    db: AsyncSession,
    experiment_id: Optional[int] = None,
    experiment_ids: Optional[list[int]] = None,
    metric: Optional[str] = None,
    title: Optional[str] = None,
    data: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """
    Main entry point for creating visualizations.

    Args:
        chart_type: Type of chart (histogram, bar, scatter, heatmap, custom)
        user_id: User ID
        db: Database session
        experiment_id: Optional single experiment filter
        experiment_ids: Optional list of experiments
        metric: Metric to visualize
        title: Chart title
        data: Custom data for chart

    Returns:
        dict with image_base64 and metadata
    """
    try:
        if chart_type == "histogram" or chart_type == "cell_histogram":
            return await create_cell_count_histogram(
                user_id=user_id,
                db=db,
                experiment_id=experiment_id,
                title=title,
            )

        elif chart_type == "bar" or chart_type == "comparison":
            return await create_experiment_comparison_bar(
                user_id=user_id,
                db=db,
                experiment_ids=experiment_ids,
                metric=metric or "cell_count",
                title=title,
            )

        elif chart_type == "scatter" or chart_type == "cell_scatter":
            return await create_cell_area_scatter(
                user_id=user_id,
                db=db,
                experiment_id=experiment_id,
                title=title,
            )

        elif chart_type == "heatmap" or chart_type == "ranking":
            return await create_ranking_heatmap(
                user_id=user_id,
                db=db,
                experiment_id=experiment_id,
                title=title,
            )

        elif chart_type == "custom" and data:
            return await create_custom_chart(data=data, title=title)

        else:
            return {
                "error": f"Unknown chart type: {chart_type}",
                "available_types": ["histogram", "bar", "scatter", "heatmap", "custom"],
            }

    except Exception as e:
        logger.exception(f"Visualization error: {e}")
        return {"error": str(e)}


async def create_custom_chart(
    data: list[dict],
    chart_type: Literal["bar", "line", "scatter"] = "bar",
    x_column: Optional[str] = None,
    y_column: Optional[str] = None,
    title: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create chart from custom data.

    Args:
        data: List of dicts with data
        chart_type: Type of chart
        x_column: Column for x-axis
        y_column: Column for y-axis
        title: Chart title

    Returns:
        dict with image_base64
    """
    if not data:
        return {"error": "No data provided"}

    # Auto-detect columns if not specified
    columns = list(data[0].keys())
    x_col = x_column or columns[0]
    y_col = y_column or (columns[1] if len(columns) > 1 else columns[0])

    x_values = [row.get(x_col) for row in data]
    y_values = [row.get(y_col) for row in data]

    fig, ax = plt.subplots(figsize=(10, 6))

    if chart_type == "bar":
        ax.bar(range(len(x_values)), y_values, tick_label=x_values, color="steelblue")
    elif chart_type == "line":
        ax.plot(x_values, y_values, marker="o", color="steelblue")
    elif chart_type == "scatter":
        ax.scatter(x_values, y_values, color="steelblue", alpha=0.7)

    ax.set_xlabel(x_col, fontsize=12)
    ax.set_ylabel(y_col, fontsize=12)
    ax.set_title(title or f"{y_col} by {x_col}", fontsize=14)

    if len(x_values) > 5:
        plt.xticks(rotation=45, ha="right")

    plt.tight_layout()

    return {
        "success": True,
        "image_base64": fig_to_base64(fig),
    }
