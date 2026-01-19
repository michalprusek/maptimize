"""Shared helper functions for export and visualization services.

This module provides common utilities:
- Filename sanitization
- Timestamped filename generation
- Figure to base64 conversion
- DataFrame export helper
- Old file cleanup
"""

import io
import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd

logger = logging.getLogger(__name__)

try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def sanitize_filename(name: str, max_length: int = 30) -> str:
    """
    Sanitize a string for use as a filename.

    Args:
        name: Original name to sanitize
        max_length: Maximum length of result

    Returns:
        Sanitized filename (alphanumeric + underscores only)
    """
    return "".join(c if c.isalnum() else "_" for c in name)[:max_length]


def generate_timestamped_filename(base_name: str, extension: str) -> str:
    """
    Generate a unique timestamped filename.

    Args:
        base_name: Base name for the file
        extension: File extension (without dot)

    Returns:
        Filename in format: {sanitized_name}_{timestamp}.{extension}
    """
    safe_name = sanitize_filename(base_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe_name}_{timestamp}.{extension}"


def fig_to_base64(fig, dpi: int = 100, facecolor: str = "white") -> str:
    """
    Convert a matplotlib figure to base64 encoded PNG string.

    Args:
        fig: Matplotlib figure object
        dpi: Resolution in dots per inch
        facecolor: Background color

    Returns:
        Base64 encoded PNG string with data URI prefix
    """
    if not HAS_MATPLOTLIB:
        raise ImportError("matplotlib is required for figure conversion")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=facecolor)
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return f"data:image/png;base64,{img_base64}"


def export_dataframe(
    df: pd.DataFrame,
    file_path: Path,
    format: Literal["csv", "xlsx"] = "csv"
) -> None:
    """
    Export a DataFrame to CSV or Excel file.

    Args:
        df: Pandas DataFrame to export
        file_path: Destination file path
        format: Export format ('csv' or 'xlsx')
    """
    if format == "xlsx":
        df.to_excel(file_path, index=False, engine="openpyxl")
    else:
        df.to_csv(file_path, index=False)


def cleanup_old_files(directory: Path, max_age_hours: int = 24, log_prefix: str = "temp") -> int:
    """
    Remove files older than max_age_hours from a directory.

    Args:
        directory: Directory to clean up
        max_age_hours: Maximum age in hours before files are deleted
        log_prefix: Prefix for log messages (e.g., "temp", "export")

    Returns:
        Number of files removed
    """
    if not directory.exists():
        return 0

    cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
    removed = 0

    for file_path in directory.glob("*"):
        if file_path.is_file() and file_path.stat().st_mtime < cutoff:
            try:
                file_path.unlink()
                removed += 1
            except Exception as e:
                logger.warning(f"Failed to remove old {log_prefix} file {file_path}: {e}")

    if removed > 0:
        logger.info(f"Cleaned up {removed} old {log_prefix} files")

    return removed
