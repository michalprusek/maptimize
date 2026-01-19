"""Shared helper functions for export and visualization services.

This module provides common utilities:
- Filename sanitization
- Timestamped filename generation
- Figure to base64 conversion
- DataFrame export helper
"""

import io
import base64
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd

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
