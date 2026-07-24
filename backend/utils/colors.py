"""Shared color-assignment helpers for reference data (proteins, microscopes).

pick_unused_color guarantees only that the exact hex is unused — not that it is
visually distinct from what is already on the plot.
"""
import colorsys
import logging

logger = logging.getLogger(__name__)

COLOR_PALETTE = [
    "#3b82f6", "#ef4444", "#00d4aa", "#f59e0b", "#8b5cf6", "#ec4899",
    "#22c55e", "#06b6d4", "#f97316", "#a855f7", "#84cc16", "#e11d48",
    "#6366f1", "#eab308", "#10b981", "#d946ef", "#0ea5e9", "#14b8a6",
    "#f43f5e", "#65a30d",
]

# Golden angle as a fraction of a turn (137.5°). Spreads generated hues evenly.
_HUE_STEP = 0.381966


def generated_color(index: int) -> str:
    """Hue-rotated fallback colour for when the palette runs out."""
    r, g, b = colorsys.hls_to_rgb((index * _HUE_STEP) % 1.0, 0.58, 0.65)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def pick_unused_color(used: set[str]) -> str:
    """Pick a colour not present in ``used`` (lower-cased hex strings).

    Palette first, then hue-rotated generated colours. The generator is injective
    over this range, so a free colour is found in practice; the loop bound only
    stops a pathological colour set from spinning. If every candidate collides,
    reuse rather than fail the caller's create.
    """
    for color in COLOR_PALETTE:
        if color.lower() not in used:
            return color

    for offset in range(len(used) + 1):
        candidate = generated_color(len(COLOR_PALETTE) + offset)
        if candidate.lower() not in used:
            return candidate

    fallback = generated_color(len(used))
    logger.warning(
        "Colour palette exhausted (%d in use); reusing %s.", len(used), fallback,
    )
    return fallback
