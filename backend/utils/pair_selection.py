"""Pair selection algorithms for ranking comparisons."""
import logging
import random
from typing import List, Tuple, Set, Dict, TypeVar, Protocol

from config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class HasId(Protocol):
    """Protocol for objects with an id attribute."""
    id: int


class HasSigmaAndMu(Protocol):
    """Protocol for objects with sigma (uncertainty) and mu (skill estimate) attributes."""
    sigma: float
    mu: float


T = TypeVar("T", bound=HasId)


class InsufficientItemsError(ValueError):
    """Raised when there are not enough items for pair selection."""
    pass


def select_exploration_pair(
    items: List[T],
    recent_pairs: Set[Tuple[int, int]]
) -> Tuple[T, T]:
    """
    Select a pair during exploration phase (random selection).

    Avoids recently compared pairs if possible.

    Args:
        items: List of items to select from (must have at least 2 items)
        recent_pairs: Set of (id_a, id_b) tuples of recent comparisons

    Returns:
        Tuple of two selected items

    Raises:
        InsufficientItemsError: If items has fewer than 2 elements
    """
    if len(items) < 2:
        raise InsufficientItemsError(
            f"At least 2 items required for pair selection, got {len(items)}"
        )

    # Find available pairs (not recently compared in either order)
    available_pairs = [
        (items[i], items[j])
        for i in range(len(items))
        for j in range(i + 1, len(items))
        if (items[i].id, items[j].id) not in recent_pairs
        and (items[j].id, items[i].id) not in recent_pairs
    ]

    if not available_pairs:
        # If all pairs recently compared, allow any pair
        logger.debug(
            f"All {len(items) * (len(items) - 1) // 2} pairs recently compared, "
            "selecting from full set"
        )
        available_pairs = [
            (items[i], items[j])
            for i in range(len(items))
            for j in range(i + 1, len(items))
        ]

    selected = random.choice(available_pairs)
    return (selected[0], selected[1])


def select_exploitation_pair(
    items: List[T],
    ratings: Dict[int, HasSigmaAndMu],
    recent_pairs: Set[Tuple[int, int]],
    top_n: int = 10
) -> Tuple[T, T]:
    """
    Select a pair during exploitation phase (uncertainty sampling).

    Selects items with highest uncertainty (sigma), preferring pairs with
    similar skill estimates (mu). The scoring heuristic balances reducing
    uncertainty with selecting competitive matchups.

    Args:
        items: List of items to select from (must have at least 2 items)
        ratings: Dict mapping item id to rating object with sigma and mu
        recent_pairs: Set of (id_a, id_b) tuples of recent comparisons
        top_n: Number of most uncertain items to consider (default 10 balances
               diversity vs focus on uncertain items)

    Returns:
        Tuple of two selected items

    Raises:
        InsufficientItemsError: If items has fewer than 2 elements
    """
    if len(items) < 2:
        raise InsufficientItemsError(
            f"At least 2 items required for pair selection, got {len(items)}"
        )

    # Filter items that have ratings
    items_with_ratings = [item for item in items if item.id in ratings]

    if len(items_with_ratings) < 2:
        logger.warning(
            f"Only {len(items_with_ratings)} items have ratings, "
            "falling back to random selection"
        )
        pair = random.sample(items, 2)
        return (pair[0], pair[1])

    # Sort by uncertainty (highest sigma first)
    sorted_by_sigma = sorted(
        items_with_ratings,
        key=lambda item: ratings[item.id].sigma,
        reverse=True
    )

    # Take top uncertain items as candidates
    candidates = sorted_by_sigma[:min(top_n, len(sorted_by_sigma))]

    # Find pair with similar mu among uncertain items
    best_pair: Tuple[T, T] | None = None
    best_score = float('-inf')

    for i, item_a in enumerate(candidates):
        for item_b in candidates[i + 1:]:
            pair_key = (item_a.id, item_b.id)
            pair_key_rev = (item_b.id, item_a.id)
            if pair_key in recent_pairs or pair_key_rev in recent_pairs:
                continue

            rating_a = ratings[item_a.id]
            rating_b = ratings[item_b.id]

            # Score: high combined uncertainty + similar skill
            combined_sigma = rating_a.sigma + rating_b.sigma
            mu_diff = abs(rating_a.mu - rating_b.mu)
            score = combined_sigma - mu_diff

            if score > best_score:
                best_score = score
                best_pair = (item_a, item_b)

    if best_pair:
        return best_pair

    # Fallback: all candidate pairs were recently compared
    logger.info(
        f"Exploitation fallback: all {len(candidates)} candidate pairs "
        f"were in recent_pairs ({len(recent_pairs)} pairs), using random selection"
    )
    pair = random.sample(items, 2)
    return (pair[0], pair[1])


def select_pair(
    items: List[T],
    ratings: Dict[int, HasSigmaAndMu],
    total_comparisons: int,
    recent_pairs: Set[Tuple[int, int]],
    randomize_order: bool = True
) -> Tuple[T, T]:
    """
    Select a pair for comparison using adaptive sampling.

    Uses exploration (random) in early phase and exploitation
    (uncertainty sampling) in later phase.

    Args:
        items: List of items to select from (must have at least 2 items)
        ratings: Dict mapping item id to rating object with sigma and mu
        total_comparisons: Number of comparisons made so far
        recent_pairs: Set of (id_a, id_b) tuples of recent comparisons
        randomize_order: Whether to randomly swap order to avoid position bias

    Returns:
        Tuple of two selected items

    Raises:
        InsufficientItemsError: If items has fewer than 2 elements
    """
    if len(items) < 2:
        raise InsufficientItemsError(
            f"At least 2 items required for pair selection, got {len(items)}"
        )

    if total_comparisons < settings.exploration_pairs:
        # Exploration phase
        item_a, item_b = select_exploration_pair(items, recent_pairs)
    else:
        # Exploitation phase
        item_a, item_b = select_exploitation_pair(items, ratings, recent_pairs)

    # Randomize order to avoid position bias
    if randomize_order and random.random() > 0.5:
        item_a, item_b = item_b, item_a

    return (item_a, item_b)
