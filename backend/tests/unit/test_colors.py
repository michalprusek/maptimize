"""Shared color-picker unit tests."""
from utils.colors import COLOR_PALETTE, generated_color, pick_unused_color


def test_palette_nonempty_and_hex():
    assert len(COLOR_PALETTE) >= 12
    assert all(c.startswith("#") and len(c) == 7 for c in COLOR_PALETTE)


def test_pick_first_unused_from_palette():
    used = {COLOR_PALETTE[0].lower()}
    assert pick_unused_color(used) == COLOR_PALETTE[1]


def test_pick_falls_through_to_generated_when_palette_exhausted():
    used = {c.lower() for c in COLOR_PALETTE}
    picked = pick_unused_color(used)
    assert picked.startswith("#") and len(picked) == 7
    assert picked.lower() not in used


def test_generated_color_is_deterministic():
    assert generated_color(5) == generated_color(5)
