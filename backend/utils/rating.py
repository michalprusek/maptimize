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
    full_convergence_estimate: int = 200
) -> int:
    """
    Estimate remaining comparisons needed for convergence.

    Args:
        avg_sigma: Current average sigma
        initial_sigma: Initial sigma value
        target_sigma: Target sigma for full convergence
        full_convergence_estimate: Estimated comparisons for full convergence

    Returns:
        Estimated number of remaining comparisons
    """
    if avg_sigma <= target_sigma:
        return 0
    remaining_ratio = (avg_sigma - target_sigma) / (initial_sigma - target_sigma)
    return int(remaining_ratio * full_convergence_estimate)
