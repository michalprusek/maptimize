"""Rating update utilities using OpenSkill/Plackett-Luce model."""
from openskill.models import PlackettLuce

# Singleton model instance - PlackettLuce is stateless, so we reuse it
_model = PlackettLuce()


def update_ratings(
    winner_mu: float,
    winner_sigma: float,
    loser_mu: float,
    loser_sigma: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """
    Update ratings using the Plackett-Luce model (via openskill).

    Args:
        winner_mu: Current mu (skill estimate) of winner
        winner_sigma: Current sigma (uncertainty) of winner
        loser_mu: Current mu of loser
        loser_sigma: Current sigma of loser

    Returns:
        Tuple of ((new_winner_mu, new_winner_sigma), (new_loser_mu, new_loser_sigma))
    """
    winner = _model.rating(mu=winner_mu, sigma=winner_sigma)
    loser = _model.rating(mu=loser_mu, sigma=loser_sigma)

    [[new_winner], [new_loser]] = _model.rate([[winner], [loser]])

    return (
        (new_winner.mu, new_winner.sigma),
        (new_loser.mu, new_loser.sigma)
    )


def calculate_convergence(
    avg_sigma: float,
    initial_sigma: float,
    target_sigma: float
) -> float:
    """
    Calculate convergence percentage based on average sigma.

    Args:
        avg_sigma: Current average sigma across all ratings
        initial_sigma: Initial sigma value for new ratings
        target_sigma: Target sigma for full convergence

    Returns:
        Convergence percentage (0-100)
    """
    if avg_sigma <= target_sigma:
        return 100.0
    return max(0, min(100, (initial_sigma - avg_sigma) / (initial_sigma - target_sigma) * 100))


def estimate_remaining_comparisons(
    avg_sigma: float,
    initial_sigma: float,
    target_sigma: float,
    rated_items_count: int = 0,
    total_comparisons: int = 0
) -> int:
    """
    Estimate remaining comparisons needed for convergence.

    Combines two signals:
    1. Image count heuristic: ~N*5 total comparisons needed for N images
    2. Sigma ratio: how far sigma still needs to drop (actual convergence progress)

    The sigma-based estimate adapts dynamically — if convergence is faster/slower
    than the heuristic predicts, the estimate adjusts accordingly.

    Args:
        avg_sigma: Current average sigma
        initial_sigma: Initial sigma value
        target_sigma: Target sigma for full convergence
        rated_items_count: Number of rated items (cell crops) for this user/experiment
        total_comparisons: Number of comparisons already made

    Returns:
        Estimated number of remaining comparisons
    """
    if avg_sigma <= target_sigma:
        return 0

    # Estimate total comparisons needed based on rated item count
    # Rule of thumb: N * 5 comparisons for reasonable convergence
    # (each item needs ~10 comparisons, each comparison involves 2 items)
    if rated_items_count > 0:
        estimated_total = max(50, rated_items_count * 5)
    else:
        estimated_total = 200  # fallback

    # Scale by sigma ratio — reflects actual convergence progress
    # remaining_ratio: ~1.0 at start (no progress), approaches 0.0 near convergence
    sigma_range = initial_sigma - target_sigma
    if sigma_range > 0 and total_comparisons > 0:
        remaining_ratio = max(0.0, min(1.0, (avg_sigma - target_sigma) / sigma_range))
        remaining = int(estimated_total * remaining_ratio)
    else:
        # Fallback: no comparisons yet or invalid sigma range
        remaining = estimated_total - total_comparisons

    return max(0, remaining)
