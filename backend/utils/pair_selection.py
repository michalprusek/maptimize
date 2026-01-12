"""Pair selection algorithms for ranking comparisons."""
import random
from typing import List, Tuple, Set, Dict, TypeVar, Protocol

from config import get_settings

settings = get_settings()


class HasId(Protocol):
    """Protocol for objects with an id attribute."""
    id: int


class HasSigmaAndMu(Protocol):
    """Protocol for rating objects."""
    sigma: float
    mu: float


T = TypeVar("T", bound=HasId)


def select_exploration_pair(
    items: List[T],
    recent_pairs: Set[Tuple[int, int]]
) -> Tuple[T, T]:
    """
    Select a pair during exploration phase (random selection).

    Avoids recently compared pairs if possible.

    Args:
        items: List of items to select from
        recent_pairs: Set of (id_a, id_b) tuples of recent comparisons

    Returns:
        Tuple of two selected items
    """
    # Find available pairs (not recently compared)
    available_pairs = [
        (items[i], items[j])
        for i in range(len(items))
        for j in range(i + 1, len(items))
        if (items[i].id, items[j].id) not in recent_pairs
    ]

    if not available_pairs:
        # If all pairs recently compared, allow any pair
        available_pairs = [
            (items[i], items[j])
            for i in range(len(items))
            for j in range(i + 1, len(items))
        ]

    return random.choice(available_pairs)


def select_exploitation_pair(
    items: List[T],
    ratings: Dict[int, HasSigmaAndMu],
    recent_pairs: Set[Tuple[int, int]],
    top_n: int = 10
) -> Tuple[T, T]:
    """
    Select a pair during exploitation phase (uncertainty sampling).

    Selects items with highest uncertainty (sigma) that have similar
    skill estimates (mu) to maximize information gain.

    Args:
        items: List of items to select from
        ratings: Dict mapping item id to rating object with sigma and mu
        recent_pairs: Set of (id_a, id_b) tuples of recent comparisons
        top_n: Number of most uncertain items to consider as candidates

    Returns:
        Tuple of two selected items
    """
    # Sort by uncertainty (highest sigma first)
    sorted_by_sigma = sorted(
        items,
        key=lambda item: ratings[item.id].sigma,
        reverse=True
    )

    # Take top uncertain items as candidates
    candidates = sorted_by_sigma[:min(top_n, len(sorted_by_sigma))]

    # Find pair with similar mu among uncertain items
    best_pair = None
    best_score = float('-inf')

    for i, item_a in enumerate(candidates):
        for item_b in candidates[i + 1:]:
            if (item_a.id, item_b.id) in recent_pairs:
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

    # Fallback to random selection
    return random.sample(items, 2)


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
        items: List of items to select from
        ratings: Dict mapping item id to rating object with sigma and mu
        total_comparisons: Number of comparisons made so far
        recent_pairs: Set of (id_a, id_b) tuples of recent comparisons
        randomize_order: Whether to randomly swap order of returned items

    Returns:
        Tuple of two selected items
    """
    if total_comparisons < settings.exploration_pairs:
        # Exploration phase
        item_a, item_b = select_exploration_pair(items, recent_pairs)
    else:
        # Exploitation phase
        item_a, item_b = select_exploitation_pair(items, ratings, recent_pairs)

    # Randomize order to avoid position bias
    if randomize_order and random.random() > 0.5:
        item_a, item_b = item_b, item_a

    return item_a, item_b
